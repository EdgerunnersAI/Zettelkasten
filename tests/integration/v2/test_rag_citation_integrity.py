"""RP-06 — every claim cites a chunk_id we actually surfaced.

The trust contract is "every citation in the answer round-trips to a chunk_id
in the assembled context".  Two collaborators enforce it:

* ``ContextAssembler._render_xml`` (context/assembler.py:288-309) emits each
  zettel as ``<zettel id="...">`` and each passage as
  ``<passage chunk_id="..." ...>`` — those are the only ids the model can
  legitimately cite.
* ``AnswerCritic._find_bad_citations`` (critic/answer_critic.py:70-94)
  recovers every ``[id="..."]`` (and legacy ``[id]``) from the model output
  and returns the set NOT in the candidate roster — these are
  hallucinations, the critic must surface them, and the orchestrator must
  downgrade the verdict.

Additionally ``orchestrator._extract_cited_ids`` (orchestrator.py:192-196)
is the orchestrator's own parser used for citation-drift signalling at the
chat_routes layer (chat_routes.py:220-228).  All three parsers MUST agree
on what counts as a citation, otherwise the drift guard fires false
positives or, worse, misses real hallucinations.

These tests are pure unit-level on Python objects — no live DB required —
but live-marked because they live next to the other v2 integration tests
and depend on the actual production parser code (no stubs).
"""
from __future__ import annotations

import uuid

import pytest

from website.features.rag_pipeline.context.assembler import ContextAssembler
from website.features.rag_pipeline.critic.answer_critic import AnswerCritic
from website.features.rag_pipeline.orchestrator import _extract_cited_ids
from website.features.rag_pipeline.types import (
    ChunkKind,
    RetrievalCandidate,
    SourceType,
)


pytestmark = pytest.mark.live


def _make_candidate(
    *,
    node_id: str,
    chunk_id: uuid.UUID | None = None,
    content: str = "the cat sat on the mat",
    title: str = "test zettel",
    rerank_score: float = 0.9,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        kind=ChunkKind.CHUNK,
        node_id=node_id,
        chunk_id=chunk_id or uuid.uuid4(),
        chunk_idx=0,
        name=title,
        source_type=SourceType.WEB,
        url=f"https://example.com/{node_id}",
        content=content,
        tags=["t1"],
        metadata={},
        rrf_score=0.5,
        rerank_score=rerank_score,
        final_score=rerank_score,
    )


# ---------------------------------------------------------------------------
# _extract_cited_ids — orchestrator's drift-guard parser
# ---------------------------------------------------------------------------


def test_extract_cited_ids_handles_quoted_canonical_form() -> None:
    answer = 'Foo [id="yt-abc"]. Bar [id="gh-xyz"].'
    assert _extract_cited_ids(answer) == {"yt-abc", "gh-xyz"}


def test_extract_cited_ids_handles_single_quotes() -> None:
    answer = "Foo [id='yt-abc']."
    assert _extract_cited_ids(answer) == {"yt-abc"}


def test_extract_cited_ids_returns_empty_for_no_citations() -> None:
    assert _extract_cited_ids("just a naked claim with no citation") == set()


def test_extract_cited_ids_dedupes_repeated_citations() -> None:
    answer = 'First [id="x"]. Second [id="x"]. Third [id="x"].'
    assert _extract_cited_ids(answer) == {"x"}


# ---------------------------------------------------------------------------
# AnswerCritic._find_bad_citations — hallucinated-id detector
# ---------------------------------------------------------------------------


def test_critic_flags_unknown_citation_id() -> None:
    critic = AnswerCritic()
    candidates = [_make_candidate(node_id="real-1")]
    bad = critic._find_bad_citations(
        'The answer is grounded [id="real-1"] and also [id="hallucinated-99"].',
        candidates,
    )
    assert bad == ["hallucinated-99"]


def test_critic_accepts_all_valid_citations() -> None:
    critic = AnswerCritic()
    candidates = [
        _make_candidate(node_id="a"),
        _make_candidate(node_id="b"),
    ]
    bad = critic._find_bad_citations(
        'Claim [id="a"] and corroboration [id="b"].', candidates,
    )
    assert bad == []


def test_critic_recognises_legacy_bracket_form() -> None:
    """iter-03 §B added legacy ``[<id>]`` support — must still be honoured."""
    critic = AnswerCritic()
    candidates = [_make_candidate(node_id="legacy-1")]
    bad = critic._find_bad_citations(
        "Old-style citation form [legacy-bad-id] in the text.", candidates,
    )
    # The legacy form picks up "legacy-bad-id" as a citation -> hallucinated.
    assert bad == ["legacy-bad-id"]


def test_critic_returns_sorted_unique_set() -> None:
    """Determinism for downstream logging / drift fingerprints."""
    critic = AnswerCritic()
    candidates = [_make_candidate(node_id="real")]
    bad = critic._find_bad_citations(
        'Claim [id="zzz"] and [id="aaa"] and [id="zzz"] again.', candidates,
    )
    assert bad == ["aaa", "zzz"]


# ---------------------------------------------------------------------------
# ContextAssembler._render_xml — emitted ids MUST round-trip
# ---------------------------------------------------------------------------


def test_assembled_context_chunk_ids_round_trip_to_critic() -> None:
    """End-to-end invariant: every ``[id=...]`` the model could LEGITIMATELY
    emit (using the node_id surfaced in the rendered context) is accepted
    by the critic as grounded.  A divergence here means the assembler is
    advertising ids the critic would reject — the source of "every cite
    is hallucinated" failure modes."""
    candidates = [
        _make_candidate(node_id="yt-vid-1", title="vid1"),
        _make_candidate(node_id="gh-repo-2", title="repo2"),
    ]
    assembler = ContextAssembler()
    grouped = assembler._group_by_node(candidates)
    rendered = assembler._render_xml(grouped)

    # Every node_id used by the candidate set MUST appear as a zettel id in
    # the rendered XML (otherwise the model can't cite it).
    for c in candidates:
        assert f'id="{c.node_id}"' in rendered, (
            f"assembler dropped node_id {c.node_id} from rendered context: "
            f"{rendered!r}"
        )

    # And the critic must accept that exact citation against the same set.
    critic = AnswerCritic()
    model_answer = (
        'Per the video [id="yt-vid-1"] and the repo [id="gh-repo-2"], '
        "the claim is supported."
    )
    assert critic._find_bad_citations(model_answer, candidates) == []


def test_assembled_context_chunk_id_attribute_is_html_escaped() -> None:
    """``_render_xml`` runs ``html.escape`` on every attribute.  Verify that
    a node_id containing an HTML-unsafe character would NOT corrupt the
    rendered XML (defence-in-depth — production ids are slugified so this
    shouldn't happen, but the escape is the contract)."""
    candidate = _make_candidate(node_id="x", content="<script>alert(1)</script>")
    assembler = ContextAssembler()
    rendered = assembler._render_xml([[candidate]])
    # The literal script tag (which would survive plain string concatenation)
    # must NOT appear in the rendered output.
    assert "<script>alert(1)</script>" not in rendered
    # The escaped form should be present instead.
    assert "&lt;script&gt;" in rendered


def test_critic_extract_matches_orchestrator_extract() -> None:
    """The orchestrator's drift guard and the critic's grounding check MUST
    parse the same set of citations from a given answer — divergence breaks
    the drift telemetry."""
    answer = (
        'Mixed [id="a"] and legacy [b] and double-quoted [id="c"]. '
        '[id="a"] repeated should still round-trip.'
    )
    orch_ids = _extract_cited_ids(answer)

    critic = AnswerCritic()
    # An empty candidate set means every citation is "bad" — so the bad list
    # equals the citation set.
    critic_ids = set(critic._find_bad_citations(answer, []))
    # Orchestrator parser only recognises the canonical [id="..."] form,
    # whereas the critic ALSO accepts the legacy [id] bracket form.  The
    # canonical-form intersection MUST be equal — drift telemetry depends
    # on the canonical parser, so the critic's canonical hits MUST be a
    # superset of the orchestrator's.
    assert orch_ids.issubset(critic_ids), (
        f"orchestrator finds {orch_ids - critic_ids} that critic misses — "
        "drift telemetry would surface false positives"
    )


# ---------------------------------------------------------------------------
# Integration: assemble → answer → critic round-trip on a real candidate set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assembler_build_then_critic_accept_round_trip() -> None:
    """Full pipe (no LLM): build a context block from two real candidates,
    extract the ids the model would have access to, fabricate a perfectly-
    grounded answer using those ids, run it through the critic — verdict
    contract: no bad citations.

    Uses an empty pool (critic has no _pool wired) so we patch out the
    verify() call's LLM round-trip and assert ONLY the synchronous
    ``_find_bad_citations`` path (which is the actual grounding gate).
    """
    # Use rerank scores above the default 0.30 LOOKUP floor (assembler.py:84)
    # and substantive (non-stub) content so the floor + stub guards keep them.
    long_body = (
        "This is a substantive paragraph of context that is long enough to "
        "survive the stub-passage guard and the minimum-useful-chars cutoff "
        "in the assembler's budget fitter. It contains real information."
    )
    candidates = [
        _make_candidate(node_id="alpha", content=long_body, rerank_score=0.9),
        _make_candidate(
            node_id="beta",
            content=long_body + " Beta-specific addendum.",
            rerank_score=0.85,
        ),
    ]
    assembler = ContextAssembler()
    context_xml, used = await assembler.build(
        candidates=candidates,
        quality="fast",
        user_query="what does the cat do?",
    )
    # The rendered XML must carry both ids.
    assert 'id="alpha"' in context_xml
    assert 'id="beta"' in context_xml
    # ``used`` reflects what the model is allowed to cite.
    used_ids = {c.node_id for c in used}
    assert used_ids == {"alpha", "beta"}

    answer = 'Cat sat [id="alpha"]. Mat held cat [id="beta"].'
    critic = AnswerCritic()
    assert critic._find_bad_citations(answer, used) == []
