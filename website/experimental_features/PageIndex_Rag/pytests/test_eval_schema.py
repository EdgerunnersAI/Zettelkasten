import pytest

# Phase 3.6: PageIndex_Rag.data_access was retired pending v2 redesign;
# cli.py transitively imports from it, so skip the schema tests at collection
# time until the module is rewritten against the v2 schema.
pytest.skip(
    "PageIndex_Rag.cli/data_access retired in Phase 3.6; "
    "tests will be re-enabled when the module is rewritten against the v2 schema",
    allow_module_level=True,
)

from website.experimental_features.PageIndex_Rag.config import REPO_ROOT, PageIndexRagConfig, load_config  # noqa: E402
from website.experimental_features.PageIndex_Rag.answer_strength import (  # noqa: E402
    build_answer_strength_payload,
    enforce_answer_strength_gate,
    normalize_final_answer,
)
from website.experimental_features.PageIndex_Rag.eval_runner import build_eval_payload  # noqa: E402
from website.experimental_features.PageIndex_Rag.generator import build_answer_prompt  # noqa: E402
from website.experimental_features.PageIndex_Rag.types import AnswerCandidate, EvidenceItem, PageIndexQueryResult  # noqa: E402
from website.experimental_features.PageIndex_Rag.cli import run_eval  # noqa: E402


def test_answer_prompt_requires_json_and_citations():
    prompt = build_answer_prompt(
        query="What is this?",
        style="direct",
        evidence=[EvidenceItem("n", "d", "Title", "https://x", "Summary", "1", "Evidence", 1.0)],
    )
    assert "Return JSON" in prompt
    assert "cited_node_ids" in prompt
    assert "n" in prompt


def test_eval_payload_records_three_answers_and_metrics():
    answers = (
        AnswerCandidate("a1", "direct", "one", ("n",), ({"node_id": "n"},)),
        AnswerCandidate("a2", "comparative", "two", ("n",), ({"node_id": "n"},)),
        AnswerCandidate("a3", "exploratory", "three", ("n",), ({"node_id": "n"},)),
    )
    result = PageIndexQueryResult(
        query_id="q1",
        query="question",
        retrieved_node_ids=("n",),
        reranked_node_ids=("n",),
        evidence=(),
        answers=answers,
        timings_ms={"total_ms": 12.0},
        memory_rss_mb={},
    )
    payload = build_eval_payload(
        queries=[{"qid": "q1", "expected_primary_citation": "n"}],
        results=[result],
    )
    assert payload["total_queries"] == 1
    assert payload["per_query"][0]["answer_count"] == 3
    assert payload["summary"]["recall_at_5"] == 1.0


def test_eval_payload_records_production_fields_and_counts_infra_failures():
    answers = (
        AnswerCandidate("a1", "direct", "one", ("n",), ({"node_id": "n"},)),
        AnswerCandidate("a2", "comparative", "two", ("n",), ({"node_id": "n"},)),
        AnswerCandidate("a3", "exploratory", "three", ("n",), ({"node_id": "n"},)),
    )
    result = PageIndexQueryResult(
        query_id="q1",
        query="question",
        retrieved_node_ids=("n",),
        reranked_node_ids=("n",),
        evidence=(),
        answers=answers,
        timings_ms={"total_ms": 100.0},
        memory_rss_mb={},
    )
    payload = build_eval_payload(
        queries=[
            {"qid": "q1", "expected_primary_citation": "n"},
            {"qid": "q2", "expected_primary_citation": "m"},
        ],
        results=[result],
        failures=[{"query_id": "q2", "http_status": 500, "elapsed_ms": 250.0, "error": "boom"}],
        iter_id="PageIndex/knowledge-management/iter-02",
    )
    assert payload["iter_id"] == "PageIndex/knowledge-management/iter-02"
    assert payload["per_query"][0]["http_status"] == 200
    assert payload["per_query"][0]["elapsed_ms"] == 100.0
    assert payload["per_query"][0]["gold_at_1"] is True
    assert payload["per_query"][0]["primary_citation"] == "n"
    assert payload["per_query"][0]["critic_verdict"] == "supported"
    assert payload["per_query"][1]["infra_failure"] is True
    assert payload["summary"]["infra_failures"] == 1
    assert payload["summary"]["end_to_end_gold_at_1"] == 0.5
    assert payload["summary"]["p95_latency_ms"] >= 100.0
    assert payload["answer_strength"]["ragas_proxy_score"] < 1.0


def test_eval_payload_counts_empty_expected_refusal_as_supported():
    answers = (
        AnswerCandidate("a1", "direct", "The evidence does not contain information about Notion.", ("n",), ({"node_id": "n"},)),
        AnswerCandidate("a2", "comparative", "The evidence does not contain information about Notion.", ("n",), ({"node_id": "n"},)),
        AnswerCandidate("a3", "exploratory", "The evidence does not contain information about Notion.", ("n",), ({"node_id": "n"},)),
    )
    result = PageIndexQueryResult(
        query_id="q1",
        query="Notion?",
        retrieved_node_ids=("n",),
        reranked_node_ids=("n",),
        evidence=(),
        answers=answers,
        timings_ms={"total_ms": 12.0},
        memory_rss_mb={},
    )
    payload = build_eval_payload(
        queries=[{"qid": "q1", "expected_primary_citation": []}],
        results=[result],
    )
    assert payload["per_query"][0]["gold_at_1"] is True
    assert payload["per_query"][0]["primary_citation"] is None
    assert payload["per_query"][0]["critic_verdict"] == "supported_refusal"


def test_answer_strength_parses_fenced_json_and_scores_coverage():
    answer = AnswerCandidate(
        "a1",
        "direct",
        '```json\n{"text":"zk is written in Go and uses Markdown notes.","cited_node_ids":["gh-zk-org-zk"],"citations":[]}\n```',
        (),
        (),
    )
    text, cited, _ = normalize_final_answer(answer)
    assert text == "zk is written in Go and uses Markdown notes."
    assert cited == ("gh-zk-org-zk",)
    result = PageIndexQueryResult(
        query_id="q1",
        query="Which language and note format?",
        retrieved_node_ids=("gh-zk-org-zk",),
        reranked_node_ids=("gh-zk-org-zk",),
        evidence=(),
        answers=(answer, answer, answer),
        timings_ms={"total_ms": 10.0},
        memory_rss_mb={},
    )
    payload = build_answer_strength_payload(
        queries=[
            {
                "qid": "q1",
                "text": "Which language and note format?",
                "expected_primary_citation": "gh-zk-org-zk",
                "ground_truth": "Go; Markdown.",
            }
        ],
        results=[result],
        eval_per_query=[
            {
                "query_id": "q1",
                "infra_failure": False,
                "recall_at_5": 1.0,
                "ndcg_at_5": 1.0,
            }
        ],
    )
    assert payload["summary"]["coverage"] == 1.0
    assert payload["summary"]["citation_grounding"] == 1.0
    assert payload["summary"]["overall_strength"] > 0.8


def test_answer_strength_gate_requires_iter03_improvement():
    baseline = {
        "coverage": 0.14539601167420718,
        "answer_correctness_proxy": 0.08133148527885369,
    }
    improved = {"coverage": 0.2, "answer_correctness_proxy": 0.1}
    enforce_answer_strength_gate(improved, baseline)
    weak = {"coverage": 0.1, "answer_correctness_proxy": 0.1}
    with pytest.raises(AssertionError):
        enforce_answer_strength_gate(weak, baseline)


def test_config_accepts_iter_02_env_overrides(monkeypatch, tmp_path):
    queries = tmp_path / "queries.json"
    eval_dir = tmp_path / "iter-02"
    monkeypatch.setenv("PAGEINDEX_RAG_ITER_ID", "iter-02")
    monkeypatch.setenv("PAGEINDEX_RAG_EVAL_DIR", str(eval_dir))
    monkeypatch.setenv("PAGEINDEX_RAG_QUERIES_PATH", str(queries))
    config = load_config()
    assert config.iter_id == "iter-02"
    assert config.eval_dir == eval_dir
    assert config.queries_path == queries
    assert str(REPO_ROOT) in str(config.login_details_path)


def test_config_accepts_pageindex_api_mode(monkeypatch):
    monkeypatch.setenv("PAGEINDEX_RAG_PAGEINDEX_API_MODE", "markdown_api")
    monkeypatch.setenv("PAGEINDEX_RAG_PAGEINDEX_API_KEY", "test-key")
    config = load_config()
    assert config.pageindex_api_mode == "markdown_api"
    assert config.pageindex_api_key == "test-key"


@pytest.mark.asyncio
async def test_eval_cli_exits_cleanly_when_login_details_missing(tmp_path, monkeypatch):
    config = PageIndexRagConfig(
        enabled=True,
        mode="local",
        iter_id="iter-02",
        workspace=tmp_path / "workspace",
        eval_dir=tmp_path / "eval",
        queries_path=tmp_path / "queries.json",
        login_details_path=tmp_path / "login_details.txt",
        kasten_slug="knowledge-management",
        kasten_name="Knowledge Management",
        candidate_limit=7,
        pageindex_api_mode="local",
        pageindex_api_key=None,
        enforce_answer_strength_gate=False,
        answer_strength_baseline_path=tmp_path / "answer_strength.json",
    )
    monkeypatch.setattr("website.experimental_features.PageIndex_Rag.cli.load_config", lambda: config)
    with pytest.raises(SystemExit) as exc:
        await run_eval()
    assert "Missing" in str(exc.value)
    assert "password" not in str(exc.value).lower()
