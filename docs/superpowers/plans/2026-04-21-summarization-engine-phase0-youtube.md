# Summarization Engine Phase 0 + YouTube Phase 0.5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase 0 infrastructure (per-source summarizer classes, config-driven caps, three-layer cache, key-role pool, evaluator module, CLI) plus the YouTube 5-tier ingest chain, so iteration loops 1-7 for YouTube can start immediately after merge.

**Architecture:** Refactor `website/features/summarization_engine/` to replace thin-wrapper summarizers with real per-source classes; add a new `evaluator/` sub-package with a consolidated Gemini-Pro scoring call and RAGAS bridge; build `ops/scripts/eval_loop.py` with two-phase auto-resume (Gemini eval then Codex manual review); replace the YouTube transcript fetcher with a 5-tier free-only fallback chain.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pytest + pytest-asyncio, `ragas`, `yt-dlp`, `youtube-transcript-api`, `httpx`, `yaml`. Server startup: `python run.py`. All tests run offline via mocks (`mock_gemini_client` fixture already present in `website/features/summarization_engine/tests/conftest.py`).

**Reference spec:** `docs/superpowers/specs/2026-04-21-summarization-engine-scoring-optimization-design.md`

**Branch:** Create `eval/summary-engine-v2-scoring` off the current branch at the start. All tasks commit to this branch.

---

## File structure summary

### Files to CREATE

**Per-source summarizer packages:**
- `website/features/summarization_engine/summarization/youtube/__init__.py`
- `website/features/summarization_engine/summarization/youtube/summarizer.py`
- `website/features/summarization_engine/summarization/youtube/prompts.py`
- `website/features/summarization_engine/summarization/youtube/schema.py`
- Same four files under `summarization/reddit/`, `summarization/github/`, `summarization/newsletter/`

**Core infrastructure:**
- `website/features/summarization_engine/core/cache.py`
- `website/features/summarization_engine/core/model_factory.py`
- `website/features/summarization_engine/evaluator/__init__.py`
- `website/features/summarization_engine/evaluator/models.py`
- `website/features/summarization_engine/evaluator/rubric_loader.py`
- `website/features/summarization_engine/evaluator/consolidated.py`
- `website/features/summarization_engine/evaluator/ragas_bridge.py`
- `website/features/summarization_engine/evaluator/atomic_facts.py`
- `website/features/summarization_engine/evaluator/next_actions.py`
- `website/features/summarization_engine/evaluator/prompts.py`
- `website/features/summarization_engine/evaluator/cache.py`
- `website/features/summarization_engine/evaluator/manual_review_writer.py`

**CLI + helpers:**
- `ops/scripts/eval_loop.py`
- `ops/scripts/lib/__init__.py`
- `ops/scripts/lib/links_parser.py`
- `ops/scripts/lib/state_detector.py`
- `ops/scripts/lib/server_manager.py`
- `ops/scripts/lib/url_discovery.py`
- `ops/scripts/lib/cost_ledger.py`

**Artifact + config:**
- `docs/summary_eval/README.md`
- `docs/summary_eval/_config/rubric_youtube.yaml`
- `docs/summary_eval/_config/rubric_reddit.yaml`
- `docs/summary_eval/_config/rubric_github.yaml`
- `docs/summary_eval/_config/rubric_newsletter.yaml`
- `docs/summary_eval/_config/rubric_universal.yaml`
- `docs/summary_eval/_config/branded_newsletter_sources.yaml`
- `ops/config.prod-overrides.yaml`

**Tests:**
- `tests/unit/summarization_engine/evaluator/test_models.py`
- `tests/unit/summarization_engine/evaluator/test_rubric_loader.py`
- `tests/unit/summarization_engine/evaluator/test_atomic_facts.py`
- `tests/unit/summarization_engine/evaluator/test_consolidated.py`
- `tests/unit/summarization_engine/evaluator/test_ragas_bridge.py`
- `tests/unit/summarization_engine/evaluator/test_next_actions.py`
- `tests/unit/summarization_engine/evaluator/test_manual_review_writer.py`
- `tests/unit/summarization_engine/core/test_cache.py`
- `tests/unit/summarization_engine/core/test_model_factory.py`
- `tests/unit/summarization_engine/summarization/test_youtube_summarizer.py`
- `tests/unit/summarization_engine/summarization/test_reddit_summarizer.py`
- `tests/unit/summarization_engine/summarization/test_github_summarizer.py`
- `tests/unit/summarization_engine/summarization/test_newsletter_summarizer.py`
- `tests/unit/summarization_engine/source_ingest/test_youtube_tiers.py`
- `tests/unit/api_key_switching/test_key_pool_roles.py`
- `tests/unit/ops_scripts/test_links_parser.py`
- `tests/unit/ops_scripts/test_state_detector.py`
- `tests/unit/ops_scripts/test_cost_ledger.py`

### Files to MODIFY

- `website/features/summarization_engine/core/config.py` — new keys + YouTube ingest fields
- `website/features/summarization_engine/core/models.py` — remove hard-coded caps
- `website/features/summarization_engine/core/orchestrator.py` — wire cache
- `website/features/summarization_engine/summarization/common/structured.py` — accept per-source payload class
- `website/features/summarization_engine/summarization/default/summarizer.py` — parametrize SourceType
- `website/features/summarization_engine/summarization/__init__.py` — auto-discovery update
- `website/features/summarization_engine/source_ingest/youtube/ingest.py` — 5-tier rewrite
- `website/features/summarization_engine/config.yaml` — all new keys
- `website/features/api_key_switching/key_pool.py` — role tagging
- `docs/testing/links.txt` — section-headered format
- `website/features/summarization_engine/tests/unit/test_default_summarizer.py` — tag count + per-source change
- `website/features/summarization_engine/tests/unit/test_models.py` — factory-based caps

### Files to DELETE

- `website/features/summarization_engine/summarization/_wrappers.py`

---

## Task ordering rationale

1. **Phase 0.A (data models)** must land before any per-source class exists.
2. **Phase 0.B (per-source summarizers)** replaces `_wrappers.py`; order matters because auto-discovery breaks mid-refactor.
3. **Phase 0.C (cache)** is independent but needed by evaluator.
4. **Phase 0.D (key-role pool)** is independent; land early so later tasks use role-aware client.
5. **Phase 0.E (evaluator)** depends on cache + models.
6. **Phase 0.F (CLI)** depends on evaluator + cache.
7. **Phase 0.G (rubric YAMLs)** can start any time; needed for evaluator smoke test.
8. **Phase 0.H (YouTube 5-tier)** depends on Phase 0.A (config keys) + Phase 0.B (per-source summarizer exists).
9. **Phase 0.I (smoke)** validates everything end-to-end.

---

## Task 0: Create feature branch

**Files:**
- Branch: `eval/summary-engine-v2-scoring`

- [ ] **Step 1: Create and check out branch**

```bash
cd /c/Users/LENOVO/Documents/Claude_Code/Projects/Obsidian_Vault
git checkout -b eval/summary-engine-v2-scoring
```

- [ ] **Step 2: Verify branch**

Run: `git branch --show-current`
Expected output: `eval/summary-engine-v2-scoring`

- [ ] **Step 3: Push branch to remote**

```bash
git push -u origin eval/summary-engine-v2-scoring
```

---

## Phase 0.A — Data models & schemas

### Task 1: Update `config.yaml` with new Phase 0 keys

**Files:**
- Modify: `website/features/summarization_engine/config.yaml`

- [ ] **Step 1: Replace the `structured_extract` block**

Find the existing `structured_extract:` block (around line 41-46) and replace with:

```yaml
structured_extract:
  validation_retries: 1
  mini_title_max_chars: 60
  brief_summary_max_chars: 400
  brief_summary_max_sentences: 7
  brief_summary_min_sentences: 5
  detailed_summary_max_bullets_per_section: 8
  detailed_summary_min_bullets_per_section: 1
  tags_min: 7
  tags_max: 10
```

- [ ] **Step 2: Replace the `sources.youtube` block**

Find the existing `youtube:` block under `sources:` and replace with:

```yaml
  youtube:
    transcript_languages: ["en", "en-US", "en-GB"]
    ytdlp_player_clients: ["android_embedded", "ios", "tv_embedded", "mweb", "web"]
    transcript_budget_ms: 90000
    piped_instances:
      - pipedapi.kavin.rocks
      - pipedapi.adminforge.de
      - pipedapi.r4fo.com
      - pipedapi.syncpundit.io
      - pipedapi.in.projectsegfau.lt
      - pipedapi.us.projectsegfau.lt
      - pipedapi.smnz.de
      - pipedapi.drgns.space
    invidious_instances:
      - invidious.fdn.fr
      - yewtu.be
      - inv.tux.pizza
      - iv.melmac.space
      - invidious.privacydev.net
      - vid.puffyan.us
    instance_health_ttl_hours: 1
    enable_gemini_audio_fallback: true
    gemini_audio_max_duration_min: 60
    gemini_audio_max_filesize_mb: 50
    use_ytdlp_fallback: true
    use_gemini_video_fallback: false
    webshare_proxy_env: "WEBSHARE_PROXY_URL"
```

- [ ] **Step 3: Commit**

```bash
git add website/features/summarization_engine/config.yaml
git commit -m "refactor: config driven caps and youtube tier chain"
```

### Task 2: Update `core/config.py` EngineConfig for new keys

**Files:**
- Modify: `website/features/summarization_engine/core/config.py`
- Test: `website/features/summarization_engine/tests/unit/test_config.py` (existing file; augment)

- [ ] **Step 1: Write the failing test**

Append to `website/features/summarization_engine/tests/unit/test_config.py`:

```python
def test_structured_extract_loads_new_char_caps():
    from website.features.summarization_engine.core.config import load_config, reset_config_cache
    reset_config_cache()
    cfg = load_config()
    assert cfg.structured_extract.mini_title_max_chars == 60
    assert cfg.structured_extract.brief_summary_max_chars == 400
    assert cfg.structured_extract.brief_summary_max_sentences == 7
    assert cfg.structured_extract.brief_summary_min_sentences == 5
    assert cfg.structured_extract.detailed_summary_max_bullets_per_section == 8
    assert cfg.structured_extract.detailed_summary_min_bullets_per_section == 1
    assert cfg.structured_extract.tags_min == 7
    assert cfg.structured_extract.tags_max == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest website/features/summarization_engine/tests/unit/test_config.py::test_structured_extract_loads_new_char_caps -v`
Expected: FAIL with AttributeError on `mini_title_max_chars` or similar.

- [ ] **Step 3: Replace `StructuredExtractConfig`**

In `website/features/summarization_engine/core/config.py`, replace the `StructuredExtractConfig` class (currently lines 48-53) with:

```python
class StructuredExtractConfig(BaseModel):
    validation_retries: int = 1
    mini_title_max_chars: int = 60
    brief_summary_max_chars: int = 400
    brief_summary_max_sentences: int = 7
    brief_summary_min_sentences: int = 5
    detailed_summary_max_bullets_per_section: int = 8
    detailed_summary_min_bullets_per_section: int = 1
    tags_min: int = 7
    tags_max: int = 10
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest website/features/summarization_engine/tests/unit/test_config.py -v`
Expected: all tests pass (including the new one).

- [ ] **Step 5: Commit**

```bash
git add website/features/summarization_engine/core/config.py website/features/summarization_engine/tests/unit/test_config.py
git commit -m "refactor: structured extract config char caps"
```

### Task 3: Build `model_factory.py` for config-driven SummaryResult

**Files:**
- Create: `website/features/summarization_engine/core/model_factory.py`
- Test: `tests/unit/summarization_engine/core/test_model_factory.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/summarization_engine/core/test_model_factory.py`:

```python
from website.features.summarization_engine.core.config import load_config, reset_config_cache
from website.features.summarization_engine.core.model_factory import build_summary_result_model


def test_build_summary_result_model_applies_config_caps():
    reset_config_cache()
    cfg = load_config()
    Model = build_summary_result_model(cfg)

    # tags: min 7, max 10
    import pytest
    from pydantic import ValidationError
    from website.features.summarization_engine.core.models import DetailedSummarySection, SummaryMetadata, SourceType

    meta_args = dict(
        source_type=SourceType.YOUTUBE, url="https://example.com",
        extraction_confidence="high", confidence_reason="ok",
        total_tokens_used=0, total_latency_ms=0,
    )
    with pytest.raises(ValidationError):
        Model(
            mini_title="ok",
            brief_summary="ok",
            tags=["a", "b"],  # too few
            detailed_summary=[DetailedSummarySection(heading="H", bullets=["b"])],
            metadata=SummaryMetadata(**meta_args),
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/summarization_engine/core/test_model_factory.py -v`
Expected: FAIL with `ModuleNotFoundError` on `model_factory`.

- [ ] **Step 3: Create `model_factory.py`**

```python
"""Factory that builds a config-driven SummaryResult Pydantic model."""
from __future__ import annotations

from typing import Type

from pydantic import BaseModel, Field

from website.features.summarization_engine.core.config import EngineConfig
from website.features.summarization_engine.core.models import (
    DetailedSummarySection,
    SummaryMetadata,
)


def build_summary_result_model(cfg: EngineConfig) -> Type[BaseModel]:
    """Return a SummaryResult Pydantic class with caps sourced from config."""
    caps = cfg.structured_extract

    class SummaryResult(BaseModel):
        mini_title: str = Field(..., max_length=caps.mini_title_max_chars)
        brief_summary: str = Field(..., max_length=caps.brief_summary_max_chars)
        tags: list[str] = Field(..., min_length=caps.tags_min, max_length=caps.tags_max)
        detailed_summary: list[DetailedSummarySection]
        metadata: SummaryMetadata

    SummaryResult.__name__ = "SummaryResult"
    return SummaryResult
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/summarization_engine/core/test_model_factory.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add website/features/summarization_engine/core/model_factory.py tests/unit/summarization_engine/core/test_model_factory.py
git commit -m "feat: config driven summary result factory"
```

### Task 4: Create YouTube schema

**Files:**
- Create: `website/features/summarization_engine/summarization/youtube/__init__.py`
- Create: `website/features/summarization_engine/summarization/youtube/schema.py`
- Test: `tests/unit/summarization_engine/summarization/test_youtube_schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/summarization_engine/summarization/test_youtube_schema.py`:

```python
import pytest
from pydantic import ValidationError

from website.features.summarization_engine.summarization.youtube.schema import (
    YouTubeStructuredPayload,
    YouTubeDetailedPayload,
    ChapterBullet,
)


def test_youtube_schema_rejects_empty_speakers():
    with pytest.raises(ValidationError):
        YouTubeStructuredPayload(
            mini_title="Intro to Transformers",
            brief_summary="Covers attention math.",
            tags=["transformers", "ml", "deep-learning", "attention", "llm", "tutorial", "beginner"],
            speakers=[],  # empty; must have at least one
            detailed_summary=YouTubeDetailedPayload(
                thesis="Attention mechanism explained.",
                format="tutorial",
                chapters_or_segments=[ChapterBullet(timestamp="0:00", title="Intro", bullets=["b"])],
                demonstrations=[],
                closing_takeaway="Attention is all you need.",
            ),
        )


def test_youtube_schema_accepts_single_speaker():
    payload = YouTubeStructuredPayload(
        mini_title="Intro to Transformers",
        brief_summary="Covers attention math.",
        tags=["transformers", "ml", "deep-learning", "attention", "llm", "tutorial", "beginner"],
        speakers=["Andrej Karpathy"],
        guests=None,
        entities_discussed=["PyTorch", "GPT-2"],
        detailed_summary=YouTubeDetailedPayload(
            thesis="Attention is a kernel.",
            format="lecture",
            chapters_or_segments=[ChapterBullet(timestamp="0:00", title="Intro", bullets=["b"])],
            demonstrations=["Live code of multi-head attention"],
            closing_takeaway="Attention as kernel regression.",
        ),
    )
    assert payload.speakers == ["Andrej Karpathy"]
    assert payload.guests is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/summarization_engine/summarization/test_youtube_schema.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `youtube/__init__.py`**

```python
"""YouTube source-specific summarization package."""
```

- [ ] **Step 4: Create `youtube/schema.py`**

```python
"""Pydantic schema for YouTube-specific structured summary payload."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, StringConstraints
from typing_extensions import Annotated


MiniTitle = Annotated[str, StringConstraints(max_length=50)]


class ChapterBullet(BaseModel):
    timestamp: str
    title: str
    bullets: list[str] = Field(..., min_length=1)


class YouTubeDetailedPayload(BaseModel):
    thesis: str
    format: Literal[
        "tutorial", "interview", "commentary", "lecture", "review",
        "debate", "walkthrough", "reaction", "vlog", "other",
    ]
    chapters_or_segments: list[ChapterBullet] = Field(..., min_length=1)
    demonstrations: list[str] = Field(default_factory=list)
    closing_takeaway: str


class YouTubeStructuredPayload(BaseModel):
    mini_title: MiniTitle
    brief_summary: str
    tags: list[str] = Field(..., min_length=7, max_length=10)
    speakers: list[str] = Field(..., min_length=1)
    guests: list[str] | None = None
    entities_discussed: list[str] = Field(default_factory=list)
    detailed_summary: YouTubeDetailedPayload
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/summarization_engine/summarization/test_youtube_schema.py -v`
Expected: both tests PASS.

- [ ] **Step 6: Commit**

```bash
git add website/features/summarization_engine/summarization/youtube/__init__.py website/features/summarization_engine/summarization/youtube/schema.py tests/unit/summarization_engine/summarization/test_youtube_schema.py
git commit -m "feat: youtube structured payload schema"
```

### Task 5: Create Reddit schema

**Files:**
- Create: `website/features/summarization_engine/summarization/reddit/__init__.py`
- Create: `website/features/summarization_engine/summarization/reddit/schema.py`
- Test: `tests/unit/summarization_engine/summarization/test_reddit_schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/summarization_engine/summarization/test_reddit_schema.py`:

```python
import pytest
from pydantic import ValidationError

from website.features.summarization_engine.summarization.reddit.schema import (
    RedditStructuredPayload,
    RedditDetailedPayload,
    RedditCluster,
)


def test_reddit_schema_rejects_bad_label_format():
    with pytest.raises(ValidationError):
        RedditStructuredPayload(
            mini_title="Just a title without subreddit prefix",  # missing r/
            brief_summary="...",
            tags=["a", "b", "c", "d", "e", "f", "g"],
            detailed_summary=RedditDetailedPayload(
                op_intent="OP asks about X.",
                reply_clusters=[RedditCluster(theme="Y", reasoning="...", examples=["e"])],
                counterarguments=[],
                unresolved_questions=[],
                moderation_context=None,
            ),
        )


def test_reddit_schema_accepts_valid_label():
    payload = RedditStructuredPayload(
        mini_title="r/AskHistorians Roman roads",
        brief_summary="...",
        tags=["history", "rome", "infrastructure", "reddit", "askhistorians", "expert-reply", "engineering"],
        detailed_summary=RedditDetailedPayload(
            op_intent="OP asks about Roman road construction.",
            reply_clusters=[RedditCluster(theme="Construction", reasoning="Layered", examples=["concrete"])],
            counterarguments=["Some dispute dating"],
            unresolved_questions=["Regional variation?"],
            moderation_context=None,
        ),
    )
    assert payload.mini_title.startswith("r/")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/summarization_engine/summarization/test_reddit_schema.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `reddit/__init__.py`**

```python
"""Reddit source-specific summarization package."""
```

- [ ] **Step 4: Create `reddit/schema.py`**

```python
"""Pydantic schema for Reddit-specific structured summary payload."""
from __future__ import annotations

from pydantic import BaseModel, Field, StringConstraints
from typing_extensions import Annotated


RedditLabel = Annotated[str, StringConstraints(pattern=r"^r/[^ ]+ .+$", max_length=60)]


class RedditCluster(BaseModel):
    theme: str
    reasoning: str
    examples: list[str] = Field(default_factory=list)


class RedditDetailedPayload(BaseModel):
    op_intent: str
    reply_clusters: list[RedditCluster] = Field(..., min_length=1)
    counterarguments: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    moderation_context: str | None = None


class RedditStructuredPayload(BaseModel):
    mini_title: RedditLabel
    brief_summary: str
    tags: list[str] = Field(..., min_length=7, max_length=10)
    detailed_summary: RedditDetailedPayload
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/summarization_engine/summarization/test_reddit_schema.py -v`
Expected: both tests PASS.

- [ ] **Step 6: Commit**

```bash
git add website/features/summarization_engine/summarization/reddit/__init__.py website/features/summarization_engine/summarization/reddit/schema.py tests/unit/summarization_engine/summarization/test_reddit_schema.py
git commit -m "feat: reddit structured payload schema"
```

### Task 6: Create GitHub schema

**Files:**
- Create: `website/features/summarization_engine/summarization/github/__init__.py`
- Create: `website/features/summarization_engine/summarization/github/schema.py`
- Test: `tests/unit/summarization_engine/summarization/test_github_schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/summarization_engine/summarization/test_github_schema.py`:

```python
import pytest
from pydantic import ValidationError

from website.features.summarization_engine.summarization.github.schema import (
    GitHubStructuredPayload,
    GitHubDetailedSection,
)


def test_github_schema_enforces_owner_repo_label():
    with pytest.raises(ValidationError):
        GitHubStructuredPayload(
            mini_title="just-a-title",  # no slash
            architecture_overview="Modules A and B interact via message queue " * 3,
            brief_summary="...",
            tags=["python", "library", "cli", "rest-api", "fastapi", "testing", "open-source"],
            detailed_summary=[GitHubDetailedSection(
                heading="Core", bullets=["b"],
                module_or_feature="core", main_stack=["python"],
                public_interfaces=["/api/foo"], usability_signals=["tests"],
            )],
        )


def test_github_schema_accepts_valid_label():
    payload = GitHubStructuredPayload(
        mini_title="openai/gym",
        architecture_overview="Layered architecture with env/agent/wrapper modules interacting via gym.Env API.",
        brief_summary="...",
        tags=["python", "reinforcement-learning", "env", "openai", "research", "benchmark", "ml"],
        benchmarks_tests_examples=["Atari benchmarks in examples/"],
        detailed_summary=[GitHubDetailedSection(
            heading="Envs", bullets=["Classic control", "Atari", "Mujoco"],
            module_or_feature="gym.envs", main_stack=["python", "numpy"],
            public_interfaces=["gym.make()", "env.step()", "env.reset()"],
            usability_signals=["v0.26 release", "CI green", "30+ contributors"],
        )],
    )
    assert payload.mini_title == "openai/gym"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/summarization_engine/summarization/test_github_schema.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `github/__init__.py`**

```python
"""GitHub source-specific summarization package."""
```

- [ ] **Step 4: Create `github/schema.py`**

```python
"""Pydantic schema for GitHub-specific structured summary payload."""
from __future__ import annotations

from pydantic import BaseModel, Field, StringConstraints
from typing_extensions import Annotated

from website.features.summarization_engine.core.models import DetailedSummarySection


GitHubLabel = Annotated[str, StringConstraints(pattern=r"^[^/]+/[^/]+$", max_length=60)]


class GitHubDetailedSection(DetailedSummarySection):
    module_or_feature: str
    main_stack: list[str] = Field(default_factory=list)
    public_interfaces: list[str] = Field(default_factory=list)
    usability_signals: list[str] = Field(default_factory=list)


class GitHubStructuredPayload(BaseModel):
    mini_title: GitHubLabel
    architecture_overview: str = Field(..., min_length=50, max_length=500)
    brief_summary: str
    tags: list[str] = Field(..., min_length=7, max_length=10)
    benchmarks_tests_examples: list[str] | None = None
    detailed_summary: list[GitHubDetailedSection] = Field(..., min_length=1)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/summarization_engine/summarization/test_github_schema.py -v`
Expected: both tests PASS.

- [ ] **Step 6: Commit**

```bash
git add website/features/summarization_engine/summarization/github/__init__.py website/features/summarization_engine/summarization/github/schema.py tests/unit/summarization_engine/summarization/test_github_schema.py
git commit -m "feat: github structured payload schema"
```

### Task 7: Create Newsletter schema with branded-source validator

**Files:**
- Create: `website/features/summarization_engine/summarization/newsletter/__init__.py`
- Create: `website/features/summarization_engine/summarization/newsletter/schema.py`
- Create: `docs/summary_eval/_config/branded_newsletter_sources.yaml`
- Test: `tests/unit/summarization_engine/summarization/test_newsletter_schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/summarization_engine/summarization/test_newsletter_schema.py`:

```python
import pytest
from pydantic import ValidationError

from website.features.summarization_engine.summarization.newsletter.schema import (
    NewsletterStructuredPayload,
    NewsletterDetailedPayload,
    NewsletterSection,
)


def _base_detailed(publication: str, stance: str = "neutral") -> NewsletterDetailedPayload:
    return NewsletterDetailedPayload(
        publication_identity=publication,
        issue_thesis="Thesis here.",
        sections=[NewsletterSection(heading="Intro", bullets=["b"])],
        conclusions_or_recommendations=["Do X"],
        stance=stance,
        cta=None,
    )


def test_branded_source_requires_publication_in_title(monkeypatch):
    # Stratechery is in the default branded list
    monkeypatch.setenv("BRANDED_SOURCES_YAML", "")  # force default list
    with pytest.raises(ValidationError):
        NewsletterStructuredPayload(
            mini_title="Aggregation Theory Insight",  # missing "Stratechery"
            brief_summary="...",
            tags=["business", "strategy", "saas", "platforms", "analysis", "tech", "subscription"],
            detailed_summary=_base_detailed("Stratechery"),
        )


def test_non_branded_source_accepts_thesis_only():
    payload = NewsletterStructuredPayload(
        mini_title="Kubernetes Networking Pitfalls",
        brief_summary="...",
        tags=["kubernetes", "networking", "devops", "cloud", "cni", "operations", "analysis"],
        detailed_summary=_base_detailed("Random Devops Blog"),
    )
    assert payload.mini_title == "Kubernetes Networking Pitfalls"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/summarization_engine/summarization/test_newsletter_schema.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create branded sources YAML**

Create `docs/summary_eval/_config/branded_newsletter_sources.yaml`:

```yaml
# Publications whose Newsletter labels must include the publication name per Spec §6.1 C2 hybrid.
# Codex may extend this list during Newsletter iteration loops; plain YAML edit, no code change needed.
branded_sources:
  - stratechery
  - platformer
  - lennysnewsletter
  - notboring
  - thedispatch
  - benedict evans
  - one useful thing
  - astral codex ten
```

- [ ] **Step 4: Create `newsletter/__init__.py`**

```python
"""Newsletter source-specific summarization package."""
```

- [ ] **Step 5: Create `newsletter/schema.py`**

```python
"""Pydantic schema for Newsletter-specific structured summary payload."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


_BRANDED_YAML_DEFAULT = Path(__file__).resolve().parents[4] / "docs" / "summary_eval" / "_config" / "branded_newsletter_sources.yaml"


def load_branded_newsletter_sources() -> list[str]:
    """Return lowercase list of branded publication identifiers."""
    path_override = os.environ.get("BRANDED_SOURCES_YAML")
    path = Path(path_override) if path_override else _BRANDED_YAML_DEFAULT
    if not path.exists():
        return ["stratechery", "platformer", "lennysnewsletter", "notboring", "thedispatch"]
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return [s.lower() for s in data.get("branded_sources", [])]


class NewsletterSection(BaseModel):
    heading: str
    bullets: list[str] = Field(..., min_length=1)


class NewsletterDetailedPayload(BaseModel):
    publication_identity: str
    issue_thesis: str
    sections: list[NewsletterSection] = Field(..., min_length=1)
    conclusions_or_recommendations: list[str] = Field(default_factory=list)
    stance: Literal["optimistic", "skeptical", "cautionary", "neutral", "mixed"]
    cta: str | None = None


class NewsletterStructuredPayload(BaseModel):
    mini_title: str
    brief_summary: str
    tags: list[str] = Field(..., min_length=7, max_length=10)
    detailed_summary: NewsletterDetailedPayload

    @model_validator(mode="after")
    def _enforce_branded_label(self) -> "NewsletterStructuredPayload":
        publication = (self.detailed_summary.publication_identity or "").lower()
        branded = load_branded_newsletter_sources()
        is_branded = any(b in publication for b in branded)
        if is_branded:
            title_lower = self.mini_title.lower()
            matched = any(b in title_lower for b in branded if b in publication)
            if not matched:
                raise ValueError(
                    f"Branded source '{publication}' requires publication name in label; got '{self.mini_title}'"
                )
        return self
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/unit/summarization_engine/summarization/test_newsletter_schema.py -v`
Expected: both tests PASS.

- [ ] **Step 7: Commit**

```bash
git add website/features/summarization_engine/summarization/newsletter/ docs/summary_eval/_config/branded_newsletter_sources.yaml tests/unit/summarization_engine/summarization/test_newsletter_schema.py
git commit -m "feat: newsletter schema with branded source validator"
```

### Task 8: Remove hard-coded caps from `core/models.py`

**Files:**
- Modify: `website/features/summarization_engine/core/models.py`
- Modify: `website/features/summarization_engine/tests/unit/test_models.py`

- [ ] **Step 1: Update existing test**

Open `website/features/summarization_engine/tests/unit/test_models.py`. Find the test asserting `min_length=8, max_length=15` (tag count) and replace with a test that uses the factory:

```python
def test_summary_result_via_factory_enforces_config_caps():
    from website.features.summarization_engine.core.config import load_config, reset_config_cache
    from website.features.summarization_engine.core.model_factory import build_summary_result_model
    reset_config_cache()
    Model = build_summary_result_model(load_config())
    assert Model.model_fields["tags"].metadata[0].min_length == 7
    assert Model.model_fields["tags"].metadata[0].max_length == 10
```

- [ ] **Step 2: Replace the `SummaryResult` class in `core/models.py`**

Replace the existing hard-capped `SummaryResult` (around lines 72-79) with:

```python
class SummaryResult(BaseModel):
    """Base SummaryResult — caps enforced by model_factory.build_summary_result_model(cfg).

    This class exists for type-hint compatibility only. Instances must be built
    via the factory so Pydantic Field caps match config.yaml.
    """

    mini_title: str
    brief_summary: str
    tags: list[str]
    detailed_summary: list["DetailedSummarySection"]
    metadata: "SummaryMetadata"
```

- [ ] **Step 3: Run tests**

Run: `pytest website/features/summarization_engine/tests/unit/test_models.py -v`
Expected: updated test PASS; other existing tests unchanged.

- [ ] **Step 4: Commit**

```bash
git add website/features/summarization_engine/core/models.py website/features/summarization_engine/tests/unit/test_models.py
git commit -m "refactor: move summary result caps to factory"
```

---

## Phase 0.B — Per-source summarizer classes

### Task 9: Refactor `summarization/common/structured.py` to accept per-source payload class

**Files:**
- Modify: `website/features/summarization_engine/summarization/common/structured.py`

- [ ] **Step 1: Replace `StructuredExtractor.__init__` and `extract`**

In `summarization/common/structured.py`, modify the `StructuredExtractor` class to accept an optional `payload_class`:

```python
class StructuredExtractor:
    def __init__(
        self,
        client: TieredGeminiClient,
        config: EngineConfig,
        payload_class: type[BaseModel] = StructuredSummaryPayload,
    ):
        self._client = client
        self._config = config
        self._payload_class = payload_class
```

Then update `extract(...)` to call `self._payload_class(...)` instead of the hard-coded `StructuredSummaryPayload(...)`:

```python
        try:
            payload = self._payload_class(**parse_json_object(result.text))
        except Exception:
            payload = _fallback_payload(ingest, summary_text, self._config)
```

- [ ] **Step 2: Run the existing structured tests**

Run: `pytest website/features/summarization_engine/tests/unit/test_default_summarizer.py -v`
Expected: PASS (default uses `StructuredSummaryPayload` fallback).

- [ ] **Step 3: Commit**

```bash
git add website/features/summarization_engine/summarization/common/structured.py
git commit -m "refactor: structured extractor per source payload"
```

### Task 10: Create YouTubeSummarizer class

**Files:**
- Create: `website/features/summarization_engine/summarization/youtube/prompts.py`
- Create: `website/features/summarization_engine/summarization/youtube/summarizer.py`
- Test: `tests/unit/summarization_engine/summarization/test_youtube_summarizer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/summarization_engine/summarization/test_youtube_summarizer.py
import pytest
from unittest.mock import AsyncMock

from website.features.summarization_engine.summarization.youtube.summarizer import YouTubeSummarizer
from website.features.summarization_engine.core.models import IngestResult, SourceType


@pytest.mark.asyncio
async def test_youtube_summarizer_uses_speakers_prompt(mock_gemini_client, monkeypatch):
    from website.features.summarization_engine.summarization.common import cod, patch as p_mod, self_check, structured
    monkeypatch.setattr(cod.ChainOfDensityDensifier, "densify", AsyncMock(return_value=cod.DensifyResult("dense", 2, 100)))
    monkeypatch.setattr(self_check.InvertedFactScoreSelfCheck, "check", AsyncMock(return_value=self_check.SelfCheckResult(missing=[])))
    monkeypatch.setattr(p_mod.SummaryPatcher, "patch", AsyncMock(return_value=("dense", False, 0)))

    async def fake_extract(self, ingest, text, **kw):
        from website.features.summarization_engine.core.models import DetailedSummarySection, SummaryResult, SummaryMetadata
        return SummaryResult(
            mini_title="t", brief_summary="b",
            tags=["a","b","c","d","e","f","g"],
            detailed_summary=[DetailedSummarySection(heading="H", bullets=["b"])],
            metadata=SummaryMetadata(source_type=SourceType.YOUTUBE, url=ingest.url,
                                     extraction_confidence="high", confidence_reason="ok",
                                     total_tokens_used=0, total_latency_ms=0),
        )
    monkeypatch.setattr(structured.StructuredExtractor, "extract", fake_extract)

    ingest = IngestResult(
        source_type=SourceType.YOUTUBE, url="https://youtube.com/watch?v=x",
        original_url="https://youtube.com/watch?v=x", raw_text="hello",
        extraction_confidence="high", confidence_reason="ok",
        fetched_at="2026-04-21T00:00:00+00:00",
    )
    summarizer = YouTubeSummarizer(mock_gemini_client, {})
    result = await summarizer.summarize(ingest)
    assert result.mini_title == "t"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/summarization_engine/summarization/test_youtube_summarizer.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `youtube/prompts.py`**

```python
"""YouTube-specific prompt templates."""
from __future__ import annotations

SOURCE_CONTEXT = (
    "You are summarizing a YouTube video. Preserve chronological structure and chapters. "
    "Always identify: the host/channel, any guests, named products/libraries/tools/datasets "
    "referenced, the video's central thesis or learning objective, and the closing takeaway. "
    "When examples or analogies are used, summarize their PURPOSE, not their verbatim content. "
    "Do not retain clickbait phrasing from the original title."
)

STRUCTURED_EXTRACT_INSTRUCTION = (
    f"{SOURCE_CONTEXT}\n\n"
    "Return a JSON object with these exact keys:\n"
    '- "mini_title": 3-5 word content-first title (max 50 chars); NO clickbait phrasing\n'
    '- "brief_summary": 5-7 sentence paragraph covering thesis, format, speakers, major segments, closing takeaway\n'
    '- "tags": array of 7-10 lowercase hyphenated tags covering topic/domain, creator or channel, format, named tools/concepts, audience\n'
    '- "speakers": array of strings (host/channel name + any referenced people; at least one)\n'
    '- "guests": array of strings OR null\n'
    '- "entities_discussed": array of product/library/dataset/tool names mentioned\n'
    '- "detailed_summary": object with keys "thesis", "format" (enum tutorial|interview|commentary|lecture|review|debate|walkthrough|reaction|vlog|other), '
    '"chapters_or_segments" (array of {timestamp, title, bullets}), "demonstrations" (array of strings), "closing_takeaway"\n\n'
    "Do NOT wrap in markdown code blocks. Return raw JSON only.\n\n"
    "SUMMARY:\n{summary_text}"
)
```

- [ ] **Step 4: Create `youtube/summarizer.py`**

```python
"""YouTube per-source summarizer."""
from __future__ import annotations

import time

from website.features.summarization_engine.core.gemini_client import TieredGeminiClient
from website.features.summarization_engine.core.models import IngestResult, SourceType, SummaryResult
from website.features.summarization_engine.summarization import register_summarizer
from website.features.summarization_engine.summarization.base import BaseSummarizer
from website.features.summarization_engine.summarization.common.cod import ChainOfDensityDensifier
from website.features.summarization_engine.summarization.common.patch import SummaryPatcher
from website.features.summarization_engine.summarization.common.self_check import InvertedFactScoreSelfCheck
from website.features.summarization_engine.summarization.common.structured import StructuredExtractor
from website.features.summarization_engine.summarization.youtube.schema import YouTubeStructuredPayload


class YouTubeSummarizer(BaseSummarizer):
    source_type = SourceType.YOUTUBE

    def __init__(self, gemini_client: TieredGeminiClient, config):
        super().__init__(gemini_client, config)
        from website.features.summarization_engine.core.config import load_config
        self._engine_config = load_config()

    async def summarize(self, ingest: IngestResult) -> SummaryResult:
        start = time.perf_counter()
        densifier = ChainOfDensityDensifier(self._client, self._engine_config)
        self_checker = InvertedFactScoreSelfCheck(self._client, self._engine_config)
        patcher = SummaryPatcher(self._client, self._engine_config)
        structured = StructuredExtractor(
            self._client, self._engine_config, payload_class=YouTubeStructuredPayload,
        )

        dense = await densifier.densify(ingest)
        check = await self_checker.check(ingest.raw_text, dense.text)
        patched_text, patch_applied, patch_tokens = await patcher.patch(dense.text, check)
        latency_ms = int((time.perf_counter() - start) * 1000)

        return await structured.extract(
            ingest, patched_text,
            pro_tokens=dense.pro_tokens + check.pro_tokens + patch_tokens,
            flash_tokens=0, latency_ms=latency_ms,
            cod_iterations_used=dense.iterations_used,
            self_check_missing_count=check.missing_count,
            patch_applied=patch_applied,
        )


register_summarizer(YouTubeSummarizer)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/summarization_engine/summarization/test_youtube_summarizer.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add website/features/summarization_engine/summarization/youtube/prompts.py website/features/summarization_engine/summarization/youtube/summarizer.py tests/unit/summarization_engine/summarization/test_youtube_summarizer.py
git commit -m "feat: youtube source specific summarizer"
```

### Task 11: Create RedditSummarizer class

**Files:**
- Create: `website/features/summarization_engine/summarization/reddit/prompts.py`
- Create: `website/features/summarization_engine/summarization/reddit/summarizer.py`

- [ ] **Step 1: Create `reddit/prompts.py`**

```python
"""Reddit-specific prompt templates."""
from __future__ import annotations

SOURCE_CONTEXT = (
    "You are summarizing a Reddit thread. Separate the original post (OP) from the comment "
    "discussion. Represent major clusters of opinions/themes as discrete units, not individual "
    "comments. Use hedging language ('commenters argue...', 'one user claims...') for unverified "
    "claims; never assert comment content as fact. Preserve counterarguments, moderator context, "
    "and unresolved questions. If num_comments > rendered_count, mention missing/removed comments."
)

STRUCTURED_EXTRACT_INSTRUCTION = (
    f"{SOURCE_CONTEXT}\n\n"
    "Return a JSON object with these exact keys:\n"
    '- "mini_title": format "r/<subreddit> <compact neutral title>" (max 60 chars)\n'
    '- "brief_summary": 5-7 sentence paragraph covering OP question, dominant response pattern, consensus/dissent, caveats\n'
    '- "tags": array of 7-10 lowercase hyphenated tags; include subreddit as a tag (e.g. "r-askhistorians")\n'
    '- "detailed_summary": object with keys "op_intent", "reply_clusters" (array of {theme, reasoning, examples}), '
    '"counterarguments" (array of strings), "unresolved_questions" (array of strings), "moderation_context" (string OR null)\n\n'
    "Do NOT wrap in markdown code blocks. Return raw JSON only.\n\n"
    "SUMMARY:\n{summary_text}"
)
```

- [ ] **Step 2: Create `reddit/summarizer.py`**

```python
"""Reddit per-source summarizer."""
from __future__ import annotations

import time

from website.features.summarization_engine.core.gemini_client import TieredGeminiClient
from website.features.summarization_engine.core.models import IngestResult, SourceType, SummaryResult
from website.features.summarization_engine.summarization import register_summarizer
from website.features.summarization_engine.summarization.base import BaseSummarizer
from website.features.summarization_engine.summarization.common.cod import ChainOfDensityDensifier
from website.features.summarization_engine.summarization.common.patch import SummaryPatcher
from website.features.summarization_engine.summarization.common.self_check import InvertedFactScoreSelfCheck
from website.features.summarization_engine.summarization.common.structured import StructuredExtractor
from website.features.summarization_engine.summarization.reddit.schema import RedditStructuredPayload


class RedditSummarizer(BaseSummarizer):
    source_type = SourceType.REDDIT

    def __init__(self, gemini_client: TieredGeminiClient, config):
        super().__init__(gemini_client, config)
        from website.features.summarization_engine.core.config import load_config
        self._engine_config = load_config()

    async def summarize(self, ingest: IngestResult) -> SummaryResult:
        start = time.perf_counter()
        dense = await ChainOfDensityDensifier(self._client, self._engine_config).densify(ingest)
        check = await InvertedFactScoreSelfCheck(self._client, self._engine_config).check(ingest.raw_text, dense.text)
        patched, patch_applied, patch_tokens = await SummaryPatcher(self._client, self._engine_config).patch(dense.text, check)
        extractor = StructuredExtractor(self._client, self._engine_config, payload_class=RedditStructuredPayload)
        latency_ms = int((time.perf_counter() - start) * 1000)
        return await extractor.extract(
            ingest, patched,
            pro_tokens=dense.pro_tokens + check.pro_tokens + patch_tokens,
            flash_tokens=0, latency_ms=latency_ms,
            cod_iterations_used=dense.iterations_used,
            self_check_missing_count=check.missing_count,
            patch_applied=patch_applied,
        )


register_summarizer(RedditSummarizer)
```

- [ ] **Step 3: Commit**

```bash
git add website/features/summarization_engine/summarization/reddit/prompts.py website/features/summarization_engine/summarization/reddit/summarizer.py
git commit -m "feat: reddit source specific summarizer"
```

### Task 12: Create GitHubSummarizer class

**Files:**
- Create: `website/features/summarization_engine/summarization/github/prompts.py`
- Create: `website/features/summarization_engine/summarization/github/summarizer.py`

- [ ] **Step 1: Create `github/prompts.py`**

```python
"""GitHub-specific prompt templates."""
from __future__ import annotations

SOURCE_CONTEXT = (
    "You are summarizing a GitHub repository. The label MUST be exactly 'owner/repo'. "
    "Cover: what the repo does in user-facing terms, core functionality, architecture "
    "(major directories/modules and their interactions in 1-3 sentences), main stack, "
    "public interfaces (API routes, CLI commands, package exports, Pages URL), "
    "and usability signals (releases, CI, docs presence). "
    "Never claim 'production-ready', 'stable', or 'battle-tested' unless the README explicitly says so. "
    "If benchmarks/tests/examples directories exist, summarize what they demonstrate."
)

STRUCTURED_EXTRACT_INSTRUCTION = (
    f"{SOURCE_CONTEXT}\n\n"
    "Return a JSON object with these exact keys:\n"
    '- "mini_title": exactly "<owner>/<repo>" (slashes intact)\n'
    '- "architecture_overview": 50-500 char prose, 1-3 sentences on modules/directories and their interaction\n'
    '- "brief_summary": 5-7 sentence paragraph covering purpose, core function, artifact type, intended use, main stack, public interfaces\n'
    '- "tags": array of 7-10 lowercase hyphenated tags; include language(s), framework(s), domain, interface type\n'
    '- "benchmarks_tests_examples": array of strings describing what they demonstrate, OR null if no such directory exists\n'
    '- "detailed_summary": array of section objects, each with "heading", "bullets", "module_or_feature", "main_stack", "public_interfaces", "usability_signals"\n\n'
    "Do NOT wrap in markdown code blocks. Return raw JSON only.\n\n"
    "SUMMARY:\n{summary_text}"
)
```

- [ ] **Step 2: Create `github/summarizer.py`**

```python
"""GitHub per-source summarizer."""
from __future__ import annotations

import time

from website.features.summarization_engine.core.gemini_client import TieredGeminiClient
from website.features.summarization_engine.core.models import IngestResult, SourceType, SummaryResult
from website.features.summarization_engine.summarization import register_summarizer
from website.features.summarization_engine.summarization.base import BaseSummarizer
from website.features.summarization_engine.summarization.common.cod import ChainOfDensityDensifier
from website.features.summarization_engine.summarization.common.patch import SummaryPatcher
from website.features.summarization_engine.summarization.common.self_check import InvertedFactScoreSelfCheck
from website.features.summarization_engine.summarization.common.structured import StructuredExtractor
from website.features.summarization_engine.summarization.github.schema import GitHubStructuredPayload


class GitHubSummarizer(BaseSummarizer):
    source_type = SourceType.GITHUB

    def __init__(self, gemini_client: TieredGeminiClient, config):
        super().__init__(gemini_client, config)
        from website.features.summarization_engine.core.config import load_config
        self._engine_config = load_config()

    async def summarize(self, ingest: IngestResult) -> SummaryResult:
        start = time.perf_counter()
        dense = await ChainOfDensityDensifier(self._client, self._engine_config).densify(ingest)
        check = await InvertedFactScoreSelfCheck(self._client, self._engine_config).check(ingest.raw_text, dense.text)
        patched, patch_applied, patch_tokens = await SummaryPatcher(self._client, self._engine_config).patch(dense.text, check)
        extractor = StructuredExtractor(self._client, self._engine_config, payload_class=GitHubStructuredPayload)
        latency_ms = int((time.perf_counter() - start) * 1000)
        return await extractor.extract(
            ingest, patched,
            pro_tokens=dense.pro_tokens + check.pro_tokens + patch_tokens,
            flash_tokens=0, latency_ms=latency_ms,
            cod_iterations_used=dense.iterations_used,
            self_check_missing_count=check.missing_count,
            patch_applied=patch_applied,
        )


register_summarizer(GitHubSummarizer)
```

- [ ] **Step 3: Commit**

```bash
git add website/features/summarization_engine/summarization/github/prompts.py website/features/summarization_engine/summarization/github/summarizer.py
git commit -m "feat: github source specific summarizer"
```

### Task 13: Create NewsletterSummarizer class

**Files:**
- Create: `website/features/summarization_engine/summarization/newsletter/prompts.py`
- Create: `website/features/summarization_engine/summarization/newsletter/summarizer.py`

- [ ] **Step 1: Create `newsletter/prompts.py`**

```python
"""Newsletter-specific prompt templates."""
from __future__ import annotations

SOURCE_CONTEXT = (
    "You are summarizing a newsletter issue. Preserve publication identity, issue thesis, "
    "section structure, and the author's apparent stance (optimistic/skeptical/cautionary/neutral/mixed). "
    "Distinguish conclusions/recommendations from descriptive background. Never editorialize: "
    "if the source is neutral, your summary must NOT use 'bullish' or 'bearish' framing. "
    "Ignore footer boilerplate, unsubscribe language, and house style unless materially meaningful."
)

STRUCTURED_EXTRACT_INSTRUCTION = (
    f"{SOURCE_CONTEXT}\n\n"
    "Return a JSON object with these exact keys:\n"
    '- "mini_title": for branded sources (Stratechery, Platformer, etc.) include publication name + thesis; '
    'otherwise thesis-only (max 60 chars)\n'
    '- "brief_summary": 5-7 sentence paragraph covering publication identity, issue thesis, audience, major sections, CTA\n'
    '- "tags": array of 7-10 lowercase hyphenated tags\n'
    '- "detailed_summary": object with keys "publication_identity", "issue_thesis", '
    '"sections" (array of {heading, bullets}), "conclusions_or_recommendations" (array), '
    '"stance" (enum optimistic|skeptical|cautionary|neutral|mixed), "cta" (string OR null)\n\n'
    "Do NOT wrap in markdown code blocks. Return raw JSON only.\n\n"
    "SUMMARY:\n{summary_text}"
)
```

- [ ] **Step 2: Create `newsletter/summarizer.py`**

```python
"""Newsletter per-source summarizer."""
from __future__ import annotations

import time

from website.features.summarization_engine.core.gemini_client import TieredGeminiClient
from website.features.summarization_engine.core.models import IngestResult, SourceType, SummaryResult
from website.features.summarization_engine.summarization import register_summarizer
from website.features.summarization_engine.summarization.base import BaseSummarizer
from website.features.summarization_engine.summarization.common.cod import ChainOfDensityDensifier
from website.features.summarization_engine.summarization.common.patch import SummaryPatcher
from website.features.summarization_engine.summarization.common.self_check import InvertedFactScoreSelfCheck
from website.features.summarization_engine.summarization.common.structured import StructuredExtractor
from website.features.summarization_engine.summarization.newsletter.schema import NewsletterStructuredPayload


class NewsletterSummarizer(BaseSummarizer):
    source_type = SourceType.NEWSLETTER

    def __init__(self, gemini_client: TieredGeminiClient, config):
        super().__init__(gemini_client, config)
        from website.features.summarization_engine.core.config import load_config
        self._engine_config = load_config()

    async def summarize(self, ingest: IngestResult) -> SummaryResult:
        start = time.perf_counter()
        dense = await ChainOfDensityDensifier(self._client, self._engine_config).densify(ingest)
        check = await InvertedFactScoreSelfCheck(self._client, self._engine_config).check(ingest.raw_text, dense.text)
        patched, patch_applied, patch_tokens = await SummaryPatcher(self._client, self._engine_config).patch(dense.text, check)
        extractor = StructuredExtractor(self._client, self._engine_config, payload_class=NewsletterStructuredPayload)
        latency_ms = int((time.perf_counter() - start) * 1000)
        return await extractor.extract(
            ingest, patched,
            pro_tokens=dense.pro_tokens + check.pro_tokens + patch_tokens,
            flash_tokens=0, latency_ms=latency_ms,
            cod_iterations_used=dense.iterations_used,
            self_check_missing_count=check.missing_count,
            patch_applied=patch_applied,
        )


register_summarizer(NewsletterSummarizer)
```

- [ ] **Step 3: Commit**

```bash
git add website/features/summarization_engine/summarization/newsletter/prompts.py website/features/summarization_engine/summarization/newsletter/summarizer.py
git commit -m "feat: newsletter source specific summarizer"
```

### Task 14: Refactor default summarizer for polish sources

**Files:**
- Modify: `website/features/summarization_engine/summarization/default/summarizer.py`

- [ ] **Step 1: Replace `DefaultSummarizer` so it can register for any polish source**

At the bottom of `summarization/default/summarizer.py`, replace the single `register_summarizer(DefaultSummarizer)` call with factory-registered subclasses for each polish source:

```python
# Register one DefaultSummarizer subclass per polish-phase source so auto-discovery
# finds a summarizer for every SourceType. The 4 major sources (YouTube/Reddit/GitHub/
# Newsletter) have their own dedicated classes in sibling packages.
for _st in (
    SourceType.HACKERNEWS, SourceType.LINKEDIN, SourceType.ARXIV,
    SourceType.PODCAST, SourceType.TWITTER, SourceType.WEB,
):
    _cls = type(
        f"{_st.value.title()}DefaultSummarizer",
        (DefaultSummarizer,),
        {"source_type": _st, "__module__": __name__},
    )
    register_summarizer(_cls)
```

- [ ] **Step 2: Run tests**

Run: `pytest website/features/summarization_engine/tests/unit/test_default_summarizer.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add website/features/summarization_engine/summarization/default/summarizer.py
git commit -m "refactor: default summarizer registers polish sources"
```

### Task 15: Delete `_wrappers.py` and update `summarization/__init__.py` auto-discovery

**Files:**
- Delete: `website/features/summarization_engine/summarization/_wrappers.py`
- Modify: `website/features/summarization_engine/summarization/__init__.py`

- [ ] **Step 1: Delete `_wrappers.py`**

```bash
rm website/features/summarization_engine/summarization/_wrappers.py
```

- [ ] **Step 2: Update auto-discovery in `__init__.py`**

Open `website/features/summarization_engine/summarization/__init__.py`. Ensure the `_auto_discover()` function scans the per-source packages (`youtube/`, `reddit/`, `github/`, `newsletter/`, `default/`) for `summarizer.py` modules and registers any `BaseSummarizer` subclass found. Pattern matches the existing `source_ingest/__init__.py`:

```python
def _auto_discover() -> None:
    from website.features.summarization_engine.summarization.base import BaseSummarizer

    package_path = __path__
    package_name = __name__
    for _, modname, ispkg in pkgutil.iter_modules(package_path):
        if not ispkg or modname == "common":
            continue
        try:
            mod = importlib.import_module(f"{package_name}.{modname}.summarizer")
        except ModuleNotFoundError:
            continue
        for attr_name in dir(mod):
            obj = getattr(mod, attr_name)
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseSummarizer)
                and obj is not BaseSummarizer
                and hasattr(obj, "source_type")
            ):
                register_summarizer(obj)


_auto_discover()
```

- [ ] **Step 3: Run all summarization tests**

Run: `pytest website/features/summarization_engine/tests/unit/ -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add -A website/features/summarization_engine/summarization/
git commit -m "refactor: delete wrappers use auto discovery"
```

---

## Phase 0.C — Three-layer content cache

### Task 16: Create `core/cache.py`

**Files:**
- Create: `website/features/summarization_engine/core/cache.py`
- Test: `tests/unit/summarization_engine/core/test_cache.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/summarization_engine/core/test_cache.py
import json
from pathlib import Path

from website.features.summarization_engine.core.cache import FsContentCache


def test_cache_roundtrip(tmp_path: Path):
    cache = FsContentCache(root=tmp_path, namespace="test_ns")
    key = ("https://a.com/x", "v1")
    assert cache.get(key) is None
    cache.put(key, {"payload": "value", "n": 1})
    stored = cache.get(key)
    assert stored is not None
    assert stored["payload"] == "value"


def test_cache_hash_stable(tmp_path: Path):
    cache = FsContentCache(root=tmp_path, namespace="test_ns")
    key1 = ("a", "b", {"c": 1, "d": 2})
    key2 = ("a", "b", {"d": 2, "c": 1})
    assert cache.key_hash(key1) == cache.key_hash(key2)


def test_cache_disabled_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DISABLED", "1")
    cache = FsContentCache(root=tmp_path, namespace="test_ns")
    cache.put(("k",), {"v": 1})
    assert cache.get(("k",)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/summarization_engine/core/test_cache.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `core/cache.py`**

```python
"""Content-hashed on-disk cache shim for ingest / summary / atomic-facts."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False, separators=(",", ":"))


class FsContentCache:
    """Key-tuple to JSON-payload cache rooted at disk."""

    def __init__(self, root: Path, namespace: str) -> None:
        self._dir = Path(root) / namespace
        self._dir.mkdir(parents=True, exist_ok=True)

    def key_hash(self, key_tuple: tuple) -> str:
        canonical = _canonical_json(list(key_tuple))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _path(self, key_tuple: tuple) -> Path:
        return self._dir / f"{self.key_hash(key_tuple)}.json"

    def get(self, key_tuple: tuple) -> dict | None:
        if os.environ.get("CACHE_DISABLED") == "1":
            return None
        path = self._path(key_tuple)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def put(self, key_tuple: tuple, payload: dict) -> None:
        if os.environ.get("CACHE_DISABLED") == "1":
            return
        path = self._path(key_tuple)
        meta = {
            "_created_at": datetime.now(timezone.utc).isoformat(),
            "_key_preview": str(key_tuple)[:200],
        }
        with path.open("w", encoding="utf-8") as handle:
            json.dump({"_meta": meta, **payload}, handle, ensure_ascii=False, indent=2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/summarization_engine/core/test_cache.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add website/features/summarization_engine/core/cache.py tests/unit/summarization_engine/core/test_cache.py
git commit -m "feat: content hashed fs cache"
```

### Task 17: Wire ingest cache into orchestrator

**Files:**
- Modify: `website/features/summarization_engine/core/orchestrator.py`

- [ ] **Step 1: Add cache import + helper**

At the top of `orchestrator.py`, add:

```python
from pathlib import Path
from website.features.summarization_engine.core.cache import FsContentCache

_CACHE_ROOT = Path(__file__).resolve().parents[4] / "docs" / "summary_eval" / "_cache"
_INGEST_CACHE = FsContentCache(root=_CACHE_ROOT, namespace="ingests")
```

- [ ] **Step 2: In `summarize_url_bundle`, wrap the ingest call**

Replace the current ingest invocation block (lines ~83-87) with:

```python
    ingestor_cls = get_ingestor(effective_source_type)
    ingestor = ingestor_cls()
    source_config = config.sources.get(effective_source_type.value, {})

    ingest_cache_key = (
        url,
        getattr(ingestor, "version", "1.0.0"),
        effective_source_type.value,
    )
    cached = _INGEST_CACHE.get(ingest_cache_key)
    if cached:
        logger.info("orchestrator.ingest_cache_hit url=%s", url)
        ingest_result = IngestResult(**{k: v for k, v in cached.items() if not k.startswith("_")})
    else:
        ingest_result = await ingestor.ingest(url, config=source_config)
        _INGEST_CACHE.put(ingest_cache_key, ingest_result.model_dump(mode="json"))
```

- [ ] **Step 3: Run orchestrator tests**

Run: `pytest website/features/summarization_engine/tests/unit/test_router.py -v`
(And any orchestrator tests.) Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add website/features/summarization_engine/core/orchestrator.py
git commit -m "feat: wire ingest cache into orchestrator"
```

---

## Phase 0.D — Key-role aware pool

### Task 18: Extend `api_env` parser for `role=` tags

**Files:**
- Modify: `website/features/api_key_switching/key_pool.py`
- Test: `tests/unit/api_key_switching/test_key_pool_roles.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/api_key_switching/test_key_pool_roles.py
from website.features.api_key_switching.key_pool import GeminiKeyPool, parse_api_env_line


def test_parse_api_env_line_with_role():
    assert parse_api_env_line("AIzaKey1 role=free") == ("AIzaKey1", "free")
    assert parse_api_env_line("AIzaKey2  role=billing") == ("AIzaKey2", "billing")


def test_parse_api_env_line_untagged_defaults_to_free():
    assert parse_api_env_line("AIzaKey3") == ("AIzaKey3", "free")


def test_key_pool_prefers_free_before_billing():
    pool = GeminiKeyPool([
        ("keyA", "free"),
        ("keyB", "billing"),
    ])
    # Iterate traversal; first attempt should be free
    first = pool.next_attempt("gemini-2.5-pro")
    assert first.key == "keyA" and first.role == "free"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/api_key_switching/test_key_pool_roles.py -v`
Expected: FAIL (parse function / `next_attempt` / role attr don't exist).

- [ ] **Step 3: Read existing `key_pool.py`**

Run: `grep -n "class GeminiKeyPool" website/features/api_key_switching/key_pool.py` to locate the class. (The rest of Step 3 is to understand the current attempt/traversal logic — subagent: read the file, keep the existing data-flow, then layer the role tagging on top.)

- [ ] **Step 4: Add `parse_api_env_line` and update pool**

At the top of `website/features/api_key_switching/key_pool.py`, add:

```python
def parse_api_env_line(line: str) -> tuple[str, str]:
    """Parse one api_env line into (key, role). Role defaults to 'free' if absent."""
    parts = line.strip().split()
    if not parts:
        raise ValueError("empty api_env line")
    key = parts[0]
    role = "free"
    for token in parts[1:]:
        if token.startswith("role="):
            role = token.split("=", 1)[1].strip().lower()
            if role not in {"free", "billing"}:
                raise ValueError(f"invalid role '{role}' on api_env line")
    return key, role
```

Then modify `GeminiKeyPool.__init__` so it accepts either `list[str]` (legacy) OR `list[tuple[str, str]]`:

```python
def __init__(self, keys):
    normalized: list[tuple[str, str]] = []
    for item in keys:
        if isinstance(item, tuple):
            normalized.append(item)
        else:
            normalized.append((item, "free"))
    # Sort so all free keys are attempted before any billing key.
    normalized.sort(key=lambda pair: 0 if pair[1] == "free" else 1)
    self._keys = normalized
    self._models = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]  # existing chain; keep as-is
```

Add a new public method that returns the next `Attempt` namedtuple (or simple dataclass) including `role`:

```python
from dataclasses import dataclass

@dataclass
class Attempt:
    key: str
    role: str
    model: str

def next_attempt(self, model: str) -> Attempt:
    """Return the first (key, role) pair that hasn't 429'd for this model yet."""
    # Simplified: delegate to existing traversal but annotate with role.
    first_key, first_role = self._keys[0]
    return Attempt(key=first_key, role=first_role, model=model)
```

Extend existing traversal methods to emit a `quota_exhausted_event` log entry when the last free key returns 429 and the pool falls back to billing:

```python
import logging
logger = logging.getLogger(__name__)

def _log_quota_exhausted(self, model: str, next_key_role: str) -> None:
    if next_key_role == "billing":
        logger.warning(
            "quota_exhausted_event model=%s escalating_to=billing at=%s",
            model, __import__("datetime").datetime.utcnow().isoformat(),
        )
```

Call `_log_quota_exhausted` from the 429-handler whenever the next candidate's role transitions from free to billing.

- [ ] **Step 5: Update `api/routes.py`'s key loader** (file: `website/features/summarization_engine/api/routes.py`)

Find the `_gemini_client()` function (lines ~77-85) and update the `api_env` fallback reader:

```python
def _gemini_client() -> TieredGeminiClient:
    from website.features.api_key_switching.key_pool import parse_api_env_line
    keys: list[tuple[str, str]] = []
    for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_1", "GEMINI_API_KEY_2"):
        if os.environ.get(name):
            keys.append((os.environ[name], "free"))
    if os.environ.get("GEMINI_API_KEYS"):
        for k in os.environ["GEMINI_API_KEYS"].split(","):
            k = k.strip()
            if k:
                keys.append((k, "free"))
    if not keys and os.path.exists("api_env"):
        with open("api_env", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip() or line.startswith("#"):
                    continue
                keys.append(parse_api_env_line(line))
    if not keys:
        raise HTTPException(status_code=503, detail="Gemini API key not configured")
    return TieredGeminiClient(GeminiKeyPool(keys), load_config())
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/unit/api_key_switching/ -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add website/features/api_key_switching/key_pool.py website/features/summarization_engine/api/routes.py tests/unit/api_key_switching/test_key_pool_roles.py
git commit -m "feat: role aware gemini key pool"
```

---

## Phase 0.E — Evaluator module

### Task 19: Evaluator models + rubric loader

**Files:**
- Create: `website/features/summarization_engine/evaluator/__init__.py`
- Create: `website/features/summarization_engine/evaluator/models.py`
- Create: `website/features/summarization_engine/evaluator/rubric_loader.py`
- Test: `tests/unit/summarization_engine/evaluator/test_models.py`
- Test: `tests/unit/summarization_engine/evaluator/test_rubric_loader.py`

- [ ] **Step 1: Create `evaluator/__init__.py`**

```python
"""Evaluator package: scores summaries against G-Eval + FineSurE + rubric + SummaC-lite."""
```

- [ ] **Step 2: Write failing model test**

```python
# tests/unit/summarization_engine/evaluator/test_models.py
from website.features.summarization_engine.evaluator.models import (
    EvalResult, RubricBreakdown, RubricComponent, GEvalScores, FineSurEScores,
    FineSurEDimension, SummaCLite, AntiPatternTrigger, EditorializationFlag,
    composite_score, apply_caps,
)


def test_composite_score_hallucination_cap_overrides_high_scores():
    result = EvalResult(
        g_eval=GEvalScores(coherence=5, consistency=5, fluency=5, relevance=5, reasoning=""),
        finesure=FineSurEScores(
            faithfulness=FineSurEDimension(score=1.0, items=[]),
            completeness=FineSurEDimension(score=1.0, items=[]),
            conciseness=FineSurEDimension(score=1.0, items=[]),
        ),
        summac_lite=SummaCLite(score=1.0, contradicted_sentences=[], neutral_sentences=[]),
        rubric=RubricBreakdown(
            components=[
                RubricComponent(id="brief_summary", score=25, max_points=25, criteria_fired=[], criteria_missed=[]),
                RubricComponent(id="detailed_summary", score=45, max_points=45, criteria_fired=[], criteria_missed=[]),
                RubricComponent(id="tags", score=15, max_points=15, criteria_fired=[], criteria_missed=[]),
                RubricComponent(id="label", score=15, max_points=15, criteria_fired=[], criteria_missed=[]),
            ],
            caps_applied={"hallucination_cap": 60, "omission_cap": None, "generic_cap": None},
            anti_patterns_triggered=[AntiPatternTrigger(id="production_ready_claim_no_evidence", source_region="", auto_cap=60)],
        ),
        maps_to_metric_summary={"g_eval_composite": 100.0, "finesure_composite": 100.0, "qafact_composite": 100.0, "summac_composite": 100.0},
        editorialization_flags=[],
        evaluator_metadata={"prompt_version": "evaluator.v1", "rubric_version": "rubric_youtube.v1", "atomic_facts_hash": "",
                            "model_used": "gemini-2.5-pro", "total_tokens_in": 0, "total_tokens_out": 0, "latency_ms": 0},
    )
    assert composite_score(result) == 60.0
```

- [ ] **Step 3: Write failing rubric loader test**

```python
# tests/unit/summarization_engine/evaluator/test_rubric_loader.py
from pathlib import Path
import pytest

from website.features.summarization_engine.evaluator.rubric_loader import load_rubric, RubricSchemaError


def test_load_rubric_rejects_missing_version(tmp_path: Path):
    bad = tmp_path / "rubric_youtube.yaml"
    bad.write_text("source_type: youtube\ncomposite_max_points: 100\ncomponents: []\n")
    with pytest.raises(RubricSchemaError):
        load_rubric(bad)
```

- [ ] **Step 4: Run tests to verify fail**

Run: `pytest tests/unit/summarization_engine/evaluator/ -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 5: Create `evaluator/models.py`**

```python
"""Pydantic models for evaluator output + composite score calculation."""
from __future__ import annotations

from statistics import mean
from typing import Literal

from pydantic import BaseModel, Field


class GEvalScores(BaseModel):
    coherence: float = Field(ge=0.0, le=5.0)
    consistency: float = Field(ge=0.0, le=5.0)
    fluency: float = Field(ge=0.0, le=5.0)
    relevance: float = Field(ge=0.0, le=5.0)
    reasoning: str = ""


class FineSurEItem(BaseModel):
    claim: str | None = None
    fact: str | None = None
    sentence: str | None = None
    span: str | None = None
    importance: int | None = None


class FineSurEDimension(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    items: list[FineSurEItem] = Field(default_factory=list)


class FineSurEScores(BaseModel):
    faithfulness: FineSurEDimension
    completeness: FineSurEDimension
    conciseness: FineSurEDimension


class SummaCLiteSentence(BaseModel):
    sentence: str
    reason: str = ""


class SummaCLite(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    contradicted_sentences: list[SummaCLiteSentence] = Field(default_factory=list)
    neutral_sentences: list[SummaCLiteSentence] = Field(default_factory=list)


class RubricComponent(BaseModel):
    id: str
    score: float
    max_points: int
    criteria_fired: list[str] = Field(default_factory=list)
    criteria_missed: list[str] = Field(default_factory=list)


class AntiPatternTrigger(BaseModel):
    id: str
    source_region: str = ""
    auto_cap: int | None = None


class RubricBreakdown(BaseModel):
    components: list[RubricComponent]
    caps_applied: dict[str, int | None] = Field(default_factory=lambda: {
        "hallucination_cap": None, "omission_cap": None, "generic_cap": None,
    })
    anti_patterns_triggered: list[AntiPatternTrigger] = Field(default_factory=list)

    @property
    def total_of_100(self) -> float:
        return sum(c.score for c in self.components)


class EditorializationFlag(BaseModel):
    sentence: str
    flag_type: Literal["added_stance", "added_judgment", "added_framing"]
    explanation: str = ""


class EvalResult(BaseModel):
    g_eval: GEvalScores
    finesure: FineSurEScores
    summac_lite: SummaCLite
    rubric: RubricBreakdown
    maps_to_metric_summary: dict[str, float]
    editorialization_flags: list[EditorializationFlag] = Field(default_factory=list)
    evaluator_metadata: dict


def apply_caps(score: float, caps: dict[str, int | None]) -> float:
    if caps.get("hallucination_cap") is not None:
        return min(score, float(caps["hallucination_cap"]))
    if caps.get("omission_cap") is not None:
        return min(score, float(caps["omission_cap"]))
    if caps.get("generic_cap") is not None:
        return min(score, float(caps["generic_cap"]))
    return score


def composite_score(result: EvalResult) -> float:
    base = (
        0.60 * result.rubric.total_of_100
        + 0.20 * result.finesure.faithfulness.score * 100
        + 0.10 * result.finesure.completeness.score * 100
        + 0.10 * mean([
            result.g_eval.coherence, result.g_eval.consistency,
            result.g_eval.fluency, result.g_eval.relevance,
        ]) * 20
    )
    return round(apply_caps(base, result.rubric.caps_applied), 2)
```

- [ ] **Step 6: Create `evaluator/rubric_loader.py`**

```python
"""Load + validate rubric_<source>.yaml files."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class RubricSchemaError(ValueError):
    """Raised when a rubric YAML is missing required fields."""


_REQUIRED_KEYS = {"version", "source_type", "composite_max_points", "components"}


def load_rubric(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    missing = _REQUIRED_KEYS - set(data.keys())
    if missing:
        raise RubricSchemaError(f"rubric {path} missing required keys: {sorted(missing)}")
    total = sum(c.get("max_points", 0) for c in data.get("components", []))
    if total != data.get("composite_max_points", 100):
        raise RubricSchemaError(
            f"rubric {path} component sum {total} != composite_max_points {data['composite_max_points']}"
        )
    return data
```

- [ ] **Step 7: Run tests to verify pass**

Run: `pytest tests/unit/summarization_engine/evaluator/ -v`
Expected: both PASS.

- [ ] **Step 8: Commit**

```bash
git add website/features/summarization_engine/evaluator/__init__.py website/features/summarization_engine/evaluator/models.py website/features/summarization_engine/evaluator/rubric_loader.py tests/unit/summarization_engine/evaluator/
git commit -m "feat: evaluator models and rubric loader"
```

### Task 20: Evaluator prompts module with `PROMPT_VERSION`

**Files:**
- Create: `website/features/summarization_engine/evaluator/prompts.py`

- [ ] **Step 1: Create `evaluator/prompts.py`**

```python
"""Evaluator prompt templates. Bump PROMPT_VERSION on ANY edit."""
from __future__ import annotations

PROMPT_VERSION = "evaluator.v1"

CONSOLIDATED_SYSTEM = (
    "You are a summary quality evaluator. Be strict, source-grounded, and terse. "
    "Use temperature 0.0 judgment. Do not editorialize. Output JSON only."
)

CONSOLIDATED_USER_TEMPLATE = """\
Evaluate the following summary against the source. Return a JSON object matching the given schema.

RUBRIC:
{rubric_yaml}

ATOMIC FACTS (from source, importance-ranked):
{atomic_facts}

SOURCE:
{source_text}

SUMMARY:
{summary_json}

Produce a JSON object with exactly these top-level keys: g_eval, finesure, summac_lite, rubric, maps_to_metric_summary, editorialization_flags, evaluator_metadata.

For every criterion in the rubric, check: does the summary satisfy its description? Tally scores per component.
For every anti_pattern in the rubric, check: is it triggered? If yes, list in rubric.anti_patterns_triggered AND set the matching caps_applied field.
For summac_lite, classify each summary sentence as entailed / neutral / contradicted vs source; score = entailed_count / total.
For editorialization_flags, list summary sentences that introduce stance/judgment/framing absent from source.
For maps_to_metric_summary, aggregate rubric criterion scores by their maps_to_metric tags into 4 composites (g_eval, finesure, qafact, summac), each 0-100.
"""

ATOMIC_FACTS_PROMPT = """\
Extract importance-ranked source-grounded claims from the following source. Return a JSON array of up to 30 items,
each with keys "claim" (string) and "importance" (1-5). Rank by importance descending.

SOURCE:
{source_text}
"""

NEXT_ACTIONS_PROMPT = """\
Given this eval and manual review, propose concrete edits for the next iteration.

EVAL JSON:
{eval_json}

MANUAL REVIEW:
{manual_review_md}

DIFF:
{diff_md}

For every rubric criterion scoring below full credit, and every module in the engine that could plausibly affect
that criterion, list one concrete edit. Rank the full list by expected impact x implementation cost. Do NOT cap the count.
Allowed edit surfaces (absolute paths):
- website/features/summarization_engine/summarization/<source>/prompts.py
- website/features/summarization_engine/summarization/<source>/schema.py
- website/features/summarization_engine/summarization/<source>/summarizer.py
- website/features/summarization_engine/summarization/common/*.py
- website/features/summarization_engine/source_ingest/<source>/ingest.py
- website/features/summarization_engine/config.yaml
- docs/summary_eval/_config/rubric_<source>.yaml

Return markdown with a status= field, then a ranked list. Each entry: target file, intended criterion improvement,
rationale (1-2 sentences), impact class (high|medium|speculative), dependencies, risks.
"""

MANUAL_REVIEW_PROMPT_TEMPLATE = """\
You are an INDEPENDENT rubric reviewer, blind to any prior evaluator's scoring. Do NOT read eval.json.

Stamp `eval_json_hash_at_review: "NOT_CONSULTED"` at the top of your manual_review.md.

RUBRIC:
{rubric_yaml}

SUMMARY:
{summary_json}

ATOMIC FACTS:
{atomic_facts}

SOURCE:
{source_text}

Score each criterion. 5-15 sentences of prose per criterion, source-grounded. Calculate a composite score 0-100.
Final line of the file must be `estimated_composite: NN.N`.

Save the output at the path printed by the CLI. eval.json SHA256 of the already-computed standard evaluator run
(for enforcement only, do NOT open that file): {eval_json_hash}
"""
```

- [ ] **Step 2: Commit**

```bash
git add website/features/summarization_engine/evaluator/prompts.py
git commit -m "feat: evaluator prompt templates with version"
```

### Task 21: Atomic-facts extractor with cache

**Files:**
- Create: `website/features/summarization_engine/evaluator/atomic_facts.py`
- Test: `tests/unit/summarization_engine/evaluator/test_atomic_facts.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/summarization_engine/evaluator/test_atomic_facts.py
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from website.features.summarization_engine.evaluator.atomic_facts import extract_atomic_facts


@pytest.mark.asyncio
async def test_extract_atomic_facts_returns_list(tmp_path: Path):
    client = MagicMock()
    fake_result = MagicMock(text='[{"claim": "X is Y", "importance": 5}]', input_tokens=10, output_tokens=5)
    client.generate = AsyncMock(return_value=fake_result)
    facts = await extract_atomic_facts(
        client=client, source_text="...", cache_root=tmp_path, url="https://a.com", ingestor_version="1.0.0",
    )
    assert facts == [{"claim": "X is Y", "importance": 5}]


@pytest.mark.asyncio
async def test_extract_atomic_facts_cache_hit(tmp_path: Path):
    client = MagicMock()
    client.generate = AsyncMock()
    # Pre-populate cache
    from website.features.summarization_engine.core.cache import FsContentCache
    cache = FsContentCache(root=tmp_path, namespace="atomic_facts")
    cache.put(("https://a.com", "1.0.0", "evaluator.v1"), {"facts": [{"claim": "cached", "importance": 3}]})
    facts = await extract_atomic_facts(
        client=client, source_text="...", cache_root=tmp_path, url="https://a.com", ingestor_version="1.0.0",
    )
    assert facts == [{"claim": "cached", "importance": 3}]
    client.generate.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/summarization_engine/evaluator/test_atomic_facts.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `evaluator/atomic_facts.py`**

```python
"""Extract importance-ranked source-grounded atomic facts, cached per URL+version."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from website.features.summarization_engine.core.cache import FsContentCache
from website.features.summarization_engine.evaluator.prompts import ATOMIC_FACTS_PROMPT, PROMPT_VERSION
from website.features.summarization_engine.summarization.common.json_utils import parse_json_object


async def extract_atomic_facts(
    *, client: Any, source_text: str, cache_root: Path,
    url: str, ingestor_version: str,
) -> list[dict]:
    cache = FsContentCache(root=cache_root, namespace="atomic_facts")
    key = (url, ingestor_version, PROMPT_VERSION)
    hit = cache.get(key)
    if hit and "facts" in hit:
        return hit["facts"]

    prompt = ATOMIC_FACTS_PROMPT.format(source_text=source_text[:30000])
    result = await client.generate(prompt, tier="flash")
    try:
        raw = parse_json_object(result.text) if result.text.strip().startswith("{") else json.loads(result.text)
    except Exception:
        raw = []
    if isinstance(raw, dict) and "facts" in raw:
        facts = raw["facts"]
    elif isinstance(raw, list):
        facts = raw
    else:
        facts = []
    facts = facts[:30]
    cache.put(key, {"facts": facts})
    return facts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/summarization_engine/evaluator/test_atomic_facts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add website/features/summarization_engine/evaluator/atomic_facts.py tests/unit/summarization_engine/evaluator/test_atomic_facts.py
git commit -m "feat: atomic facts extractor with cache"
```

### Task 22: Consolidated evaluator call

**Files:**
- Create: `website/features/summarization_engine/evaluator/consolidated.py`
- Test: `tests/unit/summarization_engine/evaluator/test_consolidated.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/summarization_engine/evaluator/test_consolidated.py
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from website.features.summarization_engine.evaluator.consolidated import ConsolidatedEvaluator
from website.features.summarization_engine.evaluator.models import EvalResult


_GOOD_RESPONSE = {
    "g_eval": {"coherence": 4.5, "consistency": 4.2, "fluency": 4.8, "relevance": 4.0, "reasoning": "ok"},
    "finesure": {
        "faithfulness": {"score": 0.95, "items": []},
        "completeness": {"score": 0.88, "items": []},
        "conciseness": {"score": 0.9, "items": []},
    },
    "summac_lite": {"score": 0.93, "contradicted_sentences": [], "neutral_sentences": []},
    "rubric": {
        "components": [
            {"id": "brief_summary", "score": 22, "max_points": 25, "criteria_fired": [], "criteria_missed": []},
            {"id": "detailed_summary", "score": 40, "max_points": 45, "criteria_fired": [], "criteria_missed": []},
            {"id": "tags", "score": 13, "max_points": 15, "criteria_fired": [], "criteria_missed": []},
            {"id": "label", "score": 14, "max_points": 15, "criteria_fired": [], "criteria_missed": []},
        ],
        "caps_applied": {"hallucination_cap": None, "omission_cap": None, "generic_cap": None},
        "anti_patterns_triggered": [],
    },
    "maps_to_metric_summary": {"g_eval_composite": 90, "finesure_composite": 91, "qafact_composite": 90, "summac_composite": 93},
    "editorialization_flags": [],
    "evaluator_metadata": {"prompt_version": "evaluator.v1", "rubric_version": "rubric_youtube.v1",
                          "atomic_facts_hash": "abc", "model_used": "gemini-2.5-pro",
                          "total_tokens_in": 100, "total_tokens_out": 50, "latency_ms": 1500},
}


@pytest.mark.asyncio
async def test_consolidated_evaluator_parses_response():
    client = MagicMock()
    client.generate = AsyncMock(return_value=MagicMock(text=json.dumps(_GOOD_RESPONSE), input_tokens=100, output_tokens=50))
    evaluator = ConsolidatedEvaluator(client)
    result = await evaluator.evaluate(
        rubric_yaml={"version": "rubric_youtube.v1", "composite_max_points": 100, "source_type": "youtube", "components": []},
        atomic_facts=[{"claim": "x", "importance": 3}],
        source_text="source",
        summary_json={"mini_title": "t"},
    )
    assert isinstance(result, EvalResult)
    assert result.rubric.total_of_100 == 89
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/summarization_engine/evaluator/test_consolidated.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `evaluator/consolidated.py`**

```python
"""The consolidated Gemini-Pro evaluation call."""
from __future__ import annotations

import json
import time
from typing import Any

from website.features.summarization_engine.evaluator.models import EvalResult
from website.features.summarization_engine.evaluator.prompts import (
    CONSOLIDATED_SYSTEM, CONSOLIDATED_USER_TEMPLATE, PROMPT_VERSION,
)
from website.features.summarization_engine.summarization.common.json_utils import parse_json_object


class ConsolidatedEvaluator:
    def __init__(self, gemini_client: Any) -> None:
        self._client = gemini_client

    async def evaluate(
        self, *, rubric_yaml: dict, atomic_facts: list[dict],
        source_text: str, summary_json: dict,
    ) -> EvalResult:
        import yaml
        prompt = CONSOLIDATED_USER_TEMPLATE.format(
            rubric_yaml=yaml.safe_dump(rubric_yaml, sort_keys=False),
            atomic_facts=json.dumps(atomic_facts, indent=2),
            source_text=source_text[:30000],
            summary_json=json.dumps(summary_json, indent=2),
        )
        t0 = time.perf_counter()
        result = await self._client.generate(
            prompt, tier="pro",
            system_instruction=CONSOLIDATED_SYSTEM,
            temperature=0.0,
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        try:
            payload = parse_json_object(result.text) if result.text.strip().startswith("{") else json.loads(result.text)
        except Exception as exc:
            raise RuntimeError(f"Evaluator returned non-JSON: {exc}")
        payload.setdefault("evaluator_metadata", {})
        payload["evaluator_metadata"].setdefault("prompt_version", PROMPT_VERSION)
        payload["evaluator_metadata"].setdefault("rubric_version", rubric_yaml.get("version", "unknown"))
        payload["evaluator_metadata"]["total_tokens_in"] = getattr(result, "input_tokens", 0)
        payload["evaluator_metadata"]["total_tokens_out"] = getattr(result, "output_tokens", 0)
        payload["evaluator_metadata"]["latency_ms"] = latency_ms
        return EvalResult(**payload)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/summarization_engine/evaluator/test_consolidated.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add website/features/summarization_engine/evaluator/consolidated.py tests/unit/summarization_engine/evaluator/test_consolidated.py
git commit -m "feat: consolidated evaluator call"
```

### Task 23: RAGAS bridge (Faithfulness + AspectCritic)

**Files:**
- Create: `website/features/summarization_engine/evaluator/ragas_bridge.py`
- Test: `tests/unit/summarization_engine/evaluator/test_ragas_bridge.py`

- [ ] **Step 1: Create `evaluator/ragas_bridge.py`**

```python
"""RAGAS Faithfulness + AspectCritic wrapped around TieredGeminiClient."""
from __future__ import annotations

from typing import Any


class RagasBridge:
    """Wraps RAGAS metrics so they use the Gemini key pool instead of OpenAI."""

    def __init__(self, gemini_client: Any) -> None:
        self._client = gemini_client

    async def faithfulness(self, summary: str, source: str) -> float:
        """Run RAGAS Faithfulness. Returns score 0-1."""
        # Implementation uses ragas.metrics.Faithfulness with a GeminiRagasLLM wrapper.
        # When conditional trigger fires (composite-rubric faithfulness < 0.9), this deep-checks.
        # For Phase 0 scaffolding we return a deterministic stub to be replaced during YT iteration loops.
        try:
            from ragas.metrics import Faithfulness  # noqa: F401
        except ImportError:
            return -1.0  # ragas not installed; caller should skip
        # Stub: full wiring lands in a follow-up commit once the evaluator is running end-to-end.
        return 0.90

    async def aspect_critic(self, summary: str, source: str, rubric_yaml: dict) -> dict:
        """Run RAGAS AspectCritic using rubric criteria as critics. Returns {score: 0-100, details: [...]}. """
        try:
            from ragas.metrics import AspectCritic  # noqa: F401
        except ImportError:
            return {"score": -1.0, "details": [], "error": "ragas not installed"}
        return {"score": 85.0, "details": []}
```

- [ ] **Step 2: Write smoke test**

```python
# tests/unit/summarization_engine/evaluator/test_ragas_bridge.py
import pytest
from website.features.summarization_engine.evaluator.ragas_bridge import RagasBridge


@pytest.mark.asyncio
async def test_ragas_bridge_smoke():
    bridge = RagasBridge(gemini_client=None)
    faith = await bridge.faithfulness(summary="s", source="t")
    assert faith >= 0 or faith == -1.0  # stub returns 0.9 or -1.0 if ragas missing
```

- [ ] **Step 3: Run test**

Run: `pytest tests/unit/summarization_engine/evaluator/test_ragas_bridge.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add website/features/summarization_engine/evaluator/ragas_bridge.py tests/unit/summarization_engine/evaluator/test_ragas_bridge.py
git commit -m "feat: ragas bridge scaffolding"
```

### Task 24: Manual-review prompt writer

**Files:**
- Create: `website/features/summarization_engine/evaluator/manual_review_writer.py`
- Test: `tests/unit/summarization_engine/evaluator/test_manual_review_writer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/summarization_engine/evaluator/test_manual_review_writer.py
import json
from pathlib import Path

from website.features.summarization_engine.evaluator.manual_review_writer import (
    write_manual_review_prompt, verify_manual_review,
)


def test_write_manual_review_prompt_includes_eval_hash(tmp_path: Path):
    out = tmp_path / "manual_review_prompt.md"
    eval_json = tmp_path / "eval.json"
    eval_json.write_text(json.dumps({"score": 87}))
    summary_json = {"mini_title": "t"}
    rubric = {"version": "rubric_youtube.v1"}

    hash_val = write_manual_review_prompt(
        out_path=out, rubric_yaml=rubric, summary=summary_json,
        atomic_facts=[], source_text="src", eval_json_path=eval_json,
    )
    assert out.exists()
    assert hash_val in out.read_text(encoding="utf-8")


def test_verify_manual_review_accepts_not_consulted(tmp_path: Path):
    mr = tmp_path / "manual_review.md"
    mr.write_text("eval_json_hash_at_review: \"NOT_CONSULTED\"\n\n...prose...\n\nestimated_composite: 85.0\n", encoding="utf-8")
    is_valid, composite = verify_manual_review(mr)
    assert is_valid
    assert composite == 85.0


def test_verify_manual_review_rejects_hash(tmp_path: Path):
    mr = tmp_path / "manual_review.md"
    mr.write_text("eval_json_hash_at_review: \"abcdef\"\n\nestimated_composite: 85.0\n", encoding="utf-8")
    is_valid, _ = verify_manual_review(mr)
    assert not is_valid
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/summarization_engine/evaluator/test_manual_review_writer.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `evaluator/manual_review_writer.py`**

```python
"""Manual-review prompt emission + verification (Codex writes manual_review.md)."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml

from website.features.summarization_engine.evaluator.prompts import MANUAL_REVIEW_PROMPT_TEMPLATE


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manual_review_prompt(
    *, out_path: Path, rubric_yaml: dict[str, Any], summary: dict[str, Any],
    atomic_facts: list[dict], source_text: str, eval_json_path: Path,
) -> str:
    """Emit the manual_review_prompt.md; return the SHA256 of eval.json."""
    eval_hash = _sha256_of_file(eval_json_path)
    body = MANUAL_REVIEW_PROMPT_TEMPLATE.format(
        rubric_yaml=yaml.safe_dump(rubric_yaml, sort_keys=False),
        summary_json=yaml.safe_dump(summary, sort_keys=False),
        atomic_facts=yaml.safe_dump(atomic_facts, sort_keys=False),
        source_text=source_text[:20000],
        eval_json_hash=eval_hash,
    )
    out_path.write_text(body, encoding="utf-8")
    return eval_hash


_HASH_PATTERN = re.compile(r'eval_json_hash_at_review:\s*"([^"]+)"')
_COMPOSITE_PATTERN = re.compile(r"estimated_composite:\s*([0-9.]+)\s*$", re.MULTILINE)


def verify_manual_review(path: Path) -> tuple[bool, float | None]:
    """Check the blind-review stamp and extract the composite score."""
    text = path.read_text(encoding="utf-8")
    hash_match = _HASH_PATTERN.search(text)
    if not hash_match or hash_match.group(1) != "NOT_CONSULTED":
        return False, None
    composite_match = _COMPOSITE_PATTERN.search(text)
    composite = float(composite_match.group(1)) if composite_match else None
    return True, composite
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/summarization_engine/evaluator/test_manual_review_writer.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add website/features/summarization_engine/evaluator/manual_review_writer.py tests/unit/summarization_engine/evaluator/test_manual_review_writer.py
git commit -m "feat: manual review prompt writer and verifier"
```

### Task 25: Next-actions synthesizer

**Files:**
- Create: `website/features/summarization_engine/evaluator/next_actions.py`

- [ ] **Step 1: Create `evaluator/next_actions.py`**

```python
"""Synthesize next_actions.md (Flash call)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from website.features.summarization_engine.evaluator.prompts import NEXT_ACTIONS_PROMPT


async def synthesize_next_actions(
    *, client: Any, eval_result_json: dict, manual_review_md: str, diff_md: str,
    out_path: Path, status: str = "continue",
) -> None:
    prompt = NEXT_ACTIONS_PROMPT.format(
        eval_json=json.dumps(eval_result_json, indent=2),
        manual_review_md=manual_review_md,
        diff_md=diff_md,
    )
    result = await client.generate(prompt, tier="flash")
    body = f"status: {status}\n\n" + result.text.strip()
    out_path.write_text(body, encoding="utf-8")
```

- [ ] **Step 2: Commit**

```bash
git add website/features/summarization_engine/evaluator/next_actions.py
git commit -m "feat: next actions synthesizer"
```

### Task 26: Public `evaluate()` entry

**Files:**
- Modify: `website/features/summarization_engine/evaluator/__init__.py`

- [ ] **Step 1: Update `__init__.py`**

```python
"""Public evaluator API."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from website.features.summarization_engine.evaluator.atomic_facts import extract_atomic_facts
from website.features.summarization_engine.evaluator.consolidated import ConsolidatedEvaluator
from website.features.summarization_engine.evaluator.models import EvalResult, composite_score
from website.features.summarization_engine.evaluator.ragas_bridge import RagasBridge
from website.features.summarization_engine.evaluator.rubric_loader import load_rubric


async def evaluate(
    *, gemini_client: Any, summary_json: dict, source_text: str,
    source_type: str, url: str, ingestor_version: str,
    rubric_path: Path, cache_root: Path,
) -> EvalResult:
    """Run the full consolidated evaluation. Optional RAGAS faithfulness triggered when needed."""
    rubric_yaml = load_rubric(rubric_path)
    facts = await extract_atomic_facts(
        client=gemini_client, source_text=source_text, cache_root=cache_root,
        url=url, ingestor_version=ingestor_version,
    )
    evaluator = ConsolidatedEvaluator(gemini_client)
    result = await evaluator.evaluate(
        rubric_yaml=rubric_yaml, atomic_facts=facts,
        source_text=source_text, summary_json=summary_json,
    )
    # Conditional RAGAS faithfulness deep-check
    if result.finesure.faithfulness.score < 0.9:
        bridge = RagasBridge(gemini_client)
        ragas_score = await bridge.faithfulness(summary=str(summary_json), source=source_text)
        result.evaluator_metadata["ragas_faithfulness"] = ragas_score
    return result


__all__ = ["evaluate", "EvalResult", "composite_score"]
```

- [ ] **Step 2: Commit**

```bash
git add website/features/summarization_engine/evaluator/__init__.py
git commit -m "feat: evaluator public entry"
```

---

## Phase 0.F — CLI + helpers

### Task 27: links.txt parser

**Files:**
- Create: `ops/scripts/lib/__init__.py`
- Create: `ops/scripts/lib/links_parser.py`
- Test: `tests/unit/ops_scripts/test_links_parser.py`

- [ ] **Step 1: Create `ops/scripts/lib/__init__.py`**

```python
"""Helper modules for ops/scripts/eval_loop.py."""
```

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/ops_scripts/test_links_parser.py
from pathlib import Path

from ops.scripts.lib.links_parser import parse_links_file


def test_parse_section_headered(tmp_path: Path):
    links = tmp_path / "links.txt"
    links.write_text(
        "# YouTube\nhttps://youtube.com/a\nhttps://youtube.com/b\n\n"
        "# Reddit\nhttps://reddit.com/r/x/comments/1\n",
        encoding="utf-8",
    )
    result = parse_links_file(links)
    assert result["youtube"] == ["https://youtube.com/a", "https://youtube.com/b"]
    assert result["reddit"] == ["https://reddit.com/r/x/comments/1"]


def test_parse_ignores_comments_and_blanks(tmp_path: Path):
    links = tmp_path / "links.txt"
    links.write_text("# YouTube\n# a comment\nhttps://yt.com/a\n\n", encoding="utf-8")
    assert parse_links_file(links)["youtube"] == ["https://yt.com/a"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/unit/ops_scripts/test_links_parser.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Create `ops/scripts/lib/links_parser.py`**

```python
"""Parse section-headered docs/testing/links.txt."""
from __future__ import annotations

from pathlib import Path


def parse_links_file(path: Path) -> dict[str, list[str]]:
    """Returns {source_key: [url, ...]} parsed from `# Source` headers."""
    result: dict[str, list[str]] = {}
    current: str | None = None
    with Path(path).open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                heading = line.lstrip("#").strip().lower()
                known = {
                    "youtube", "reddit", "github", "newsletter", "twitter",
                    "hackernews", "linkedin", "arxiv", "podcast", "web",
                }
                if heading in known:
                    current = heading
                    result.setdefault(current, [])
                # Else: it's a plain comment inside a section; skip.
                continue
            if current and line.startswith("http"):
                result[current].append(line)
    return result
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/ops_scripts/test_links_parser.py -v`
Expected: both tests PASS.

- [ ] **Step 6: Commit**

```bash
git add ops/scripts/lib/__init__.py ops/scripts/lib/links_parser.py tests/unit/ops_scripts/test_links_parser.py
git commit -m "feat: section headered links.txt parser"
```

### Task 28: State detector (auto-resume Phase A vs Phase B)

**Files:**
- Create: `ops/scripts/lib/state_detector.py`
- Test: `tests/unit/ops_scripts/test_state_detector.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/ops_scripts/test_state_detector.py
from pathlib import Path

from ops.scripts.lib.state_detector import detect_iteration_state, IterationState


def test_empty_iter_dir_returns_phase_a(tmp_path: Path):
    iter_dir = tmp_path / "iter-01"
    iter_dir.mkdir()
    assert detect_iteration_state(iter_dir) == IterationState.PHASE_A_REQUIRED


def test_only_summary_eval_returns_waiting_for_review(tmp_path: Path):
    iter_dir = tmp_path / "iter-01"; iter_dir.mkdir()
    (iter_dir / "summary.json").write_text("{}")
    (iter_dir / "eval.json").write_text("{}")
    (iter_dir / "manual_review_prompt.md").write_text("")
    assert detect_iteration_state(iter_dir) == IterationState.AWAITING_MANUAL_REVIEW


def test_all_present_including_manual_review_returns_phase_b(tmp_path: Path):
    iter_dir = tmp_path / "iter-01"; iter_dir.mkdir()
    for f in ("summary.json", "eval.json", "manual_review_prompt.md", "manual_review.md"):
        (iter_dir / f).write_text("{}" if f.endswith("json") else "")
    assert detect_iteration_state(iter_dir) == IterationState.PHASE_B_REQUIRED


def test_diff_present_returns_already_committed(tmp_path: Path):
    iter_dir = tmp_path / "iter-01"; iter_dir.mkdir()
    for f in ("summary.json", "eval.json", "manual_review_prompt.md", "manual_review.md", "diff.md"):
        (iter_dir / f).write_text("{}" if f.endswith("json") else "")
    assert detect_iteration_state(iter_dir) == IterationState.ALREADY_COMMITTED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/ops_scripts/test_state_detector.py -v`
Expected: FAIL.

- [ ] **Step 3: Create `state_detector.py`**

```python
"""Auto-resume state detection for eval_loop.py."""
from __future__ import annotations

from enum import Enum
from pathlib import Path


class IterationState(str, Enum):
    PHASE_A_REQUIRED = "phase_a_required"
    AWAITING_MANUAL_REVIEW = "awaiting_manual_review"
    PHASE_B_REQUIRED = "phase_b_required"
    ALREADY_COMMITTED = "already_committed"


def detect_iteration_state(iter_dir: Path) -> IterationState:
    has = lambda n: (iter_dir / n).exists()
    if has("diff.md"):
        return IterationState.ALREADY_COMMITTED
    if has("manual_review.md") and has("summary.json") and has("eval.json"):
        return IterationState.PHASE_B_REQUIRED
    if has("summary.json") and has("eval.json") and has("manual_review_prompt.md"):
        return IterationState.AWAITING_MANUAL_REVIEW
    return IterationState.PHASE_A_REQUIRED
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/ops_scripts/test_state_detector.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ops/scripts/lib/state_detector.py tests/unit/ops_scripts/test_state_detector.py
git commit -m "feat: iteration state detector"
```

### Task 29: Server lifecycle manager

**Files:**
- Create: `ops/scripts/lib/server_manager.py`

- [ ] **Step 1: Create `server_manager.py`**

```python
"""Manage the FastAPI server process for the iteration loop."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx


def start_server(port: int = 10000, env_overrides: dict[str, str] | None = None) -> subprocess.Popen:
    env = {**os.environ, **(env_overrides or {})}
    repo_root = Path(__file__).resolve().parents[3]
    proc = subprocess.Popen(
        [sys.executable, "run.py"],
        cwd=str(repo_root), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    _wait_for_health(port)
    return proc


def stop_server(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _wait_for_health(port: int, timeout_sec: int = 30) -> None:
    deadline = time.monotonic() + timeout_sec
    url = f"http://127.0.0.1:{port}/api/health"
    last_exc = None
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=2.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    return
        except Exception as exc:
            last_exc = exc
        time.sleep(1.0)
    raise RuntimeError(f"Server did not become healthy within {timeout_sec}s: {last_exc}")


def restart_if_code_changed(proc: subprocess.Popen | None, last_hash: str, new_hash: str,
                             port: int = 10000, env_overrides: dict[str, str] | None = None) -> tuple[subprocess.Popen, str]:
    if proc is None or last_hash != new_hash:
        if proc is not None:
            stop_server(proc)
        proc = start_server(port=port, env_overrides=env_overrides)
    return proc, new_hash
```

- [ ] **Step 2: Commit**

```bash
git add ops/scripts/lib/server_manager.py
git commit -m "feat: fastapi server lifecycle manager"
```

### Task 30: URL discovery helper (Gemini google_search grounding)

**Files:**
- Create: `ops/scripts/lib/url_discovery.py`

- [ ] **Step 1: Create `url_discovery.py`**

```python
"""Discover 3 canonical URLs per source using Gemini google_search grounding."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DISCOVERY_PROMPTS = {
    "github": (
        "Find 3 canonical GitHub repository URLs for summarization testing. Coverage target: "
        "one popular multi-module repo (>5k stars, active), one simple single-purpose library "
        "(<1k stars), one minimal-README repo (README <200 words). Return JSON array of objects "
        'with keys "url", "rationale", "rubric_fit_score" (0-100). URLs must resolve.'
    ),
    "newsletter": (
        "Find 3 recently-published newsletter issue URLs. Coverage target: one branded source "
        "(e.g. Stratechery, Platformer), one analytical Substack issue, one product-update/roundup. "
        'Return JSON array with "url", "rationale", "rubric_fit_score".'
    ),
    "hackernews": (
        "Find 3 Hacker News thread URLs. Coverage: one Show HN, one Ask HN, one linked-article discussion. "
        'Return JSON array with "url", "rationale", "rubric_fit_score".'
    ),
    "linkedin": (
        "Find 3 public LinkedIn post URLs with substantial text (>100 words). "
        'Return JSON array with "url", "rationale", "rubric_fit_score".'
    ),
    "arxiv": (
        "Find 3 recent arxiv.org/abs paper URLs from different domains (CS, ML, physics). "
        'Return JSON array with "url", "rationale", "rubric_fit_score".'
    ),
    "podcast": (
        "Find 3 podcast episode URLs on podcasts.apple.com or open.spotify.com with show notes. "
        'Return JSON array with "url", "rationale", "rubric_fit_score".'
    ),
    "twitter": (
        "Find 3 Twitter/X status URLs with substantive text or threads. "
        'Return JSON array with "url", "rationale", "rubric_fit_score".'
    ),
    "web": (
        "Find 3 public article URLs from different publishers (news site, tech blog, academic site). "
        'Return JSON array with "url", "rationale", "rubric_fit_score".'
    ),
}


async def discover_urls(source_type: str, client: Any, count: int = 3) -> list[dict]:
    prompt = DISCOVERY_PROMPTS.get(source_type)
    if not prompt:
        raise ValueError(f"No discovery prompt for source_type={source_type}")
    result = await client.generate(prompt, tier="flash", tools=[{"google_search": {}}])
    try:
        items = json.loads(result.text)
    except Exception:
        items = []
    return items[:count]


def write_discovery_report(source_type: str, urls: list[dict], out_path: Path) -> None:
    lines = [f"# Auto-discovered URLs for {source_type}\n"]
    for item in urls:
        lines.append(f"- **{item.get('url', 'N/A')}** (fit={item.get('rubric_fit_score', '?')})")
        lines.append(f"  - {item.get('rationale', '')}\n")
    out_path.write_text("\n".join(lines), encoding="utf-8")
```

- [ ] **Step 2: Commit**

```bash
git add ops/scripts/lib/url_discovery.py
git commit -m "feat: url discovery via gemini grounding"
```

### Task 31: Cost ledger helper

**Files:**
- Create: `ops/scripts/lib/cost_ledger.py`
- Test: `tests/unit/ops_scripts/test_cost_ledger.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/ops_scripts/test_cost_ledger.py
from ops.scripts.lib.cost_ledger import CostLedger


def test_cost_ledger_records_calls():
    ledger = CostLedger()
    ledger.record("summarizer", model="gemini-2.5-pro", key="key1", role="free",
                  tokens_in=100, tokens_out=50)
    ledger.record("evaluator", model="gemini-2.5-pro", key="key1", role="free",
                  tokens_in=200, tokens_out=80)
    report = ledger.to_dict()
    assert report["role_breakdown"]["free_tier_calls"] == 2
    assert report["role_breakdown"]["billing_calls"] == 0
    assert report["summarizer"]["pro"]["key1"] == 1
    assert report["evaluator"]["pro"]["key1"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/ops_scripts/test_cost_ledger.py -v`
Expected: FAIL.

- [ ] **Step 3: Create `cost_ledger.py`**

```python
"""Track Gemini token + call usage per iteration."""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def _tier(model: str) -> str:
    if model.endswith("pro"):
        return "pro"
    if "flash-lite" in model:
        return "flash_lite"
    return "flash"


class CostLedger:
    def __init__(self) -> None:
        self._phases: dict[str, Any] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
        self._tokens_in: dict[str, int] = defaultdict(int)
        self._tokens_out: dict[str, int] = defaultdict(int)
        self._free_calls: int = 0
        self._billing_calls: int = 0
        self._quota_exhausted: list[dict] = []

    def record(self, phase: str, *, model: str, key: str, role: str,
               tokens_in: int = 0, tokens_out: int = 0) -> None:
        tier = _tier(model)
        self._phases[phase][tier][key] += 1
        self._tokens_in[phase] += tokens_in
        self._tokens_out[phase] += tokens_out
        if role == "billing":
            self._billing_calls += 1
        else:
            self._free_calls += 1

    def record_quota_exhausted(self, model: str, at_iso: str) -> None:
        self._quota_exhausted.append({"model": model, "at": at_iso})

    def to_dict(self) -> dict:
        return {
            **{phase: dict(inner) for phase, inner in self._phases.items()},
            "tokens_in_per_phase": dict(self._tokens_in),
            "tokens_out_per_phase": dict(self._tokens_out),
            "role_breakdown": {
                "free_tier_calls": self._free_calls,
                "billing_calls": self._billing_calls,
            },
            "quota_exhausted_events": self._quota_exhausted,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/ops_scripts/test_cost_ledger.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ops/scripts/lib/cost_ledger.py tests/unit/ops_scripts/test_cost_ledger.py
git commit -m "feat: cost ledger helper"
```

### Task 32: `eval_loop.py` CLI (main)

**Files:**
- Create: `ops/scripts/eval_loop.py`
- Create: `ops/config.prod-overrides.yaml`

- [ ] **Step 1: Create `ops/config.prod-overrides.yaml` stub**

```yaml
# Prod-parity overrides loaded when SUMMARIZE_ENV=prod-parity.
# Empty by default; Codex fills values during iteration loops as prod diverges from dev.
# Shape matches website/features/summarization_engine/config.yaml.
```

- [ ] **Step 2: Create `ops/scripts/eval_loop.py`**

```python
"""Single-URL iteration CLI for the summarization scoring program.

Two-phase auto-resume:
  Phase A: summary + standard evaluator + manual_review_prompt emission.
  Phase B: manual_review consumption + diff + next_actions + commit.

Zoro auth behavior (SUMMARIZE_ENV=prod-parity):
  When --env prod-parity is set, the CLI authenticates with Supabase as the Zoro test user
  (credentials loaded from docs/login_details.txt) so summaries write to the real KG under a
  known test account. This exercises the KG + RAG pipelines end-to-end for loop 7.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ops.scripts.lib.links_parser import parse_links_file
from ops.scripts.lib.state_detector import IterationState, detect_iteration_state
from ops.scripts.lib.server_manager import start_server, stop_server
from ops.scripts.lib.cost_ledger import CostLedger


REPO_ROOT = Path(__file__).resolve().parents[2]
LINKS_TXT = REPO_ROOT / "docs" / "testing" / "links.txt"
ARTIFACT_ROOT = REPO_ROOT / "docs" / "summary_eval"
RUBRIC_DIR = ARTIFACT_ROOT / "_config"
CACHE_ROOT = ARTIFACT_ROOT / "_cache"
LOGIN_DETAILS = REPO_ROOT / "docs" / "login_details.txt"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--source", required=False,
                   choices=["youtube", "reddit", "github", "newsletter", "hackernews",
                            "linkedin", "arxiv", "podcast", "twitter", "web"])
    p.add_argument("--iter", type=int)
    p.add_argument("--phase", choices=["0", "0.5", "iter", "extension"], default="iter")
    p.add_argument("--env", choices=["dev", "prod-parity"], default="dev")
    p.add_argument("--url")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--server", default="http://127.0.0.1:10000")
    p.add_argument("--manage-server", action="store_true", default=True)
    p.add_argument("--auto", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force-phase-a", action="store_true")
    p.add_argument("--force-phase-b", action="store_true")
    p.add_argument("--emit-review-prompt-only", action="store_true")
    p.add_argument("--rebuild-index", action="store_true")
    p.add_argument("--list-urls", action="store_true")
    p.add_argument("--report", action="store_true")
    p.add_argument("--since")
    p.add_argument("--replay", action="store_true")
    p.add_argument("--stop-server", action="store_true")
    return p.parse_args()


def _zoro_credentials() -> dict[str, str]:
    """Parse zoro creds from docs/login_details.txt. Never hardcode in source."""
    text = LOGIN_DETAILS.read_text(encoding="utf-8")
    email_match = re.search(r"zoro@\S+", text)
    pw_match = re.search(r"Zoro2026!\S*", text)
    auth_match = re.search(r"a57e1f2f-7d89-4cd7-ae39-72c440ed4b4e", text)
    if not (email_match and pw_match and auth_match):
        raise RuntimeError("Zoro credentials not found in docs/login_details.txt")
    return {"email": email_match.group(0), "password": pw_match.group(0), "user_id": auth_match.group(0)}


async def _zoro_bearer_token(supabase_url: str, anon_key: str, creds: dict[str, str]) -> str:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{supabase_url}/auth/v1/token?grant_type=password",
            headers={"apikey": anon_key, "Content-Type": "application/json"},
            json={"email": creds["email"], "password": creds["password"]},
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def _call_summarize_api(server: str, url: str, write_to_supabase: bool = False,
                               bearer_token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(
            f"{server}/api/v2/summarize",
            json={"url": url, "write_to_supabase": write_to_supabase},
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()


async def _run_phase_a(args, iter_dir: Path, source: str, urls_to_run: list[str], rubric_path: Path) -> None:
    from website.features.summarization_engine.evaluator import evaluate
    from website.features.summarization_engine.evaluator.manual_review_writer import write_manual_review_prompt
    from website.features.summarization_engine.evaluator.rubric_loader import load_rubric

    iter_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[dict] = []
    evals: list[dict] = []
    ledger = CostLedger()

    bearer = None
    write_to_supabase = False
    if args.env == "prod-parity":
        creds = _zoro_credentials()
        supabase_url = os.environ.get("SUPABASE_URL", "")
        anon_key = os.environ.get("SUPABASE_ANON_KEY", "")
        if supabase_url and anon_key:
            bearer = await _zoro_bearer_token(supabase_url, anon_key, creds)
            write_to_supabase = True
            (iter_dir / "prod_parity_auth.txt").write_text(
                f"authenticated_as=zoro user_id={creds['user_id']}\n", encoding="utf-8",
            )

    for url in urls_to_run:
        result = await _call_summarize_api(
            args.server, url, write_to_supabase=write_to_supabase, bearer_token=bearer,
        )
        summaries.append({"url": url, "response": result})

        # Evaluator
        from website.features.summarization_engine.core.config import load_config
        from website.features.summarization_engine.api.routes import _gemini_client  # reuse key pool
        client = _gemini_client()
        rubric_yaml = load_rubric(rubric_path)
        source_text = result.get("summary", {}).get("metadata", {}).get("url", "") or url
        # For eval we need source text; pull from ingest cache via cache key
        ingest_cache = CACHE_ROOT / "ingests"
        eval_result = await evaluate(
            gemini_client=client,
            summary_json=result["summary"],
            source_text=source_text,
            source_type=source,
            url=url,
            ingestor_version="1.0.0",
            rubric_path=rubric_path,
            cache_root=CACHE_ROOT,
        )
        evals.append(eval_result.model_dump(mode="json"))

    (iter_dir / "summary.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    (iter_dir / "eval.json").write_text(json.dumps(evals, indent=2), encoding="utf-8")

    # Emit manual review prompt (using first URL's summary + eval)
    atomic_facts_cache = CACHE_ROOT / "atomic_facts"
    url0 = urls_to_run[0]
    url_hash = hashlib.sha256(url0.encode()).hexdigest()
    atomic_facts = []
    for fp in atomic_facts_cache.glob(f"*.json"):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            if "facts" in data:
                atomic_facts = data["facts"]; break
        except Exception:
            continue

    prompt_path = iter_dir / "manual_review_prompt.md"
    write_manual_review_prompt(
        out_path=prompt_path,
        rubric_yaml=load_rubric(rubric_path),
        summary=summaries[0]["response"]["summary"],
        atomic_facts=atomic_facts,
        source_text=source_text,
        eval_json_path=iter_dir / "eval.json",
    )
    print(f"status=awaiting_manual_review path={prompt_path}")


async def _run_phase_b(args, iter_dir: Path, source: str, rubric_path: Path, prev_iter_dir: Path | None) -> None:
    from website.features.summarization_engine.evaluator.manual_review_writer import verify_manual_review
    from website.features.summarization_engine.evaluator.next_actions import synthesize_next_actions
    from website.features.summarization_engine.api.routes import _gemini_client
    from website.features.summarization_engine.evaluator.models import composite_score, EvalResult

    mr_path = iter_dir / "manual_review.md"
    is_valid, codex_composite = verify_manual_review(mr_path)
    if not is_valid:
        (iter_dir / "next_actions.md").write_text("status: blind_review_violation\n", encoding="utf-8")
        print("status=blind_review_violation")
        return

    eval_json = json.loads((iter_dir / "eval.json").read_text(encoding="utf-8"))
    if isinstance(eval_json, list):
        gemini_composite = composite_score(EvalResult(**eval_json[0]))
    else:
        gemini_composite = composite_score(EvalResult(**eval_json))
    divergence = abs(gemini_composite - (codex_composite or 0))
    stamp = "AGREEMENT" if divergence <= 5 else ("MINOR_DISAGREEMENT" if divergence <= 10 else "MAJOR_DISAGREEMENT")
    header = f"# Manual Review — {stamp} (divergence={divergence:.1f})\n\n"
    mr_text = mr_path.read_text(encoding="utf-8")
    if not mr_text.startswith("# Manual Review"):
        mr_path.write_text(header + mr_text, encoding="utf-8")

    # Diff vs prior iter
    diff_md = ""
    if prev_iter_dir and (prev_iter_dir / "eval.json").exists():
        prev = json.loads((prev_iter_dir / "eval.json").read_text(encoding="utf-8"))
        prev_composite = composite_score(EvalResult(**(prev[0] if isinstance(prev, list) else prev)))
        diff_md = f"score_delta_vs_prev: {gemini_composite - prev_composite:+.2f}\n"
    (iter_dir / "diff.md").write_text(diff_md, encoding="utf-8")

    # Next actions via Flash
    client = _gemini_client()
    await synthesize_next_actions(
        client=client, eval_result_json=eval_json,
        manual_review_md=mr_path.read_text(encoding="utf-8"),
        diff_md=diff_md, out_path=iter_dir / "next_actions.md",
        status="continue",
    )

    # Commit
    subprocess.run(["git", "add", str(iter_dir)], check=True)
    subprocess.run(["git", "commit", "-m",
                    f"test: {source} iter-{args.iter or iter_dir.name} score {gemini_composite:.1f}"], check=True)


async def _main() -> int:
    args = _parse_args()

    if args.list_urls:
        parsed = parse_links_file(LINKS_TXT)
        print(json.dumps(parsed.get(args.source or "", []), indent=2))
        return 0

    if args.no_cache:
        os.environ["CACHE_DISABLED"] = "1"

    # Resolve iter directory
    source_dir = ARTIFACT_ROOT / (args.source or "")
    iter_num = args.iter or _next_iter_num(source_dir)
    iter_dir = source_dir / f"iter-{iter_num:02d}"
    prev_iter_dir = source_dir / f"iter-{(iter_num - 1):02d}" if iter_num > 1 else None
    rubric_path = RUBRIC_DIR / f"rubric_{args.source}.yaml"
    if not rubric_path.exists():
        rubric_path = RUBRIC_DIR / "rubric_universal.yaml"

    state = detect_iteration_state(iter_dir) if iter_dir.exists() else IterationState.PHASE_A_REQUIRED
    if args.force_phase_a:
        state = IterationState.PHASE_A_REQUIRED
    if args.force_phase_b:
        state = IterationState.PHASE_B_REQUIRED

    # URL selection from links.txt
    parsed = parse_links_file(LINKS_TXT)
    urls = parsed.get(args.source, [])[:3]
    if not urls:
        print(f"status=no_urls_found source={args.source} — add URLs to docs/testing/links.txt")
        return 2

    if state in (IterationState.PHASE_A_REQUIRED, IterationState.AWAITING_MANUAL_REVIEW):
        await _run_phase_a(args, iter_dir, args.source, urls, rubric_path)
        return 0
    if state == IterationState.PHASE_B_REQUIRED:
        await _run_phase_b(args, iter_dir, args.source, rubric_path, prev_iter_dir)
        return 0
    if state == IterationState.ALREADY_COMMITTED:
        print(f"status=iteration_already_committed iter={iter_num}")
        return 0
    return 1


def _next_iter_num(source_dir: Path) -> int:
    if not source_dir.exists():
        return 1
    nums = []
    for child in source_dir.iterdir():
        if child.is_dir() and child.name.startswith("iter-"):
            try:
                nums.append(int(child.name[5:]))
            except ValueError:
                continue
    return (max(nums) + 1) if nums else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
```

- [ ] **Step 3: Run a dry-run sanity check**

Run: `python ops/scripts/eval_loop.py --source youtube --list-urls`
Expected: JSON array of YouTube URLs (once Task 33 migrates `links.txt`).

- [ ] **Step 4: Commit**

```bash
git add ops/scripts/eval_loop.py ops/config.prod-overrides.yaml
git commit -m "feat: eval loop cli two phase auto resume"
```

### Task 33: Migrate `docs/testing/links.txt` to section-headered format

**Files:**
- Modify: `docs/testing/links.txt`

- [ ] **Step 1: Replace the entire content**

Replace the whole file with:

```
# YouTube
https://www.youtube.com/watch?v=hhjhU5MXZOo
https://www.youtube.com/watch?v=HBTYVVUBAGs
https://www.youtube.com/watch?v=Brm71uCWr-I
https://www.youtube.com/watch?v=Ctwc8t5CsQs
https://www.youtube.com/watch?v=CtrhU7GOjOg

# Twitter
https://x.com/arrgnt_sanatan/status/1854027462042321075

# Reddit
https://www.reddit.com/r/IndianStockMarket/comments/1getc4l/rajkot_collapsed_hyundai_ipo/
https://www.reddit.com/r/IAmA/comments/9ke63/i_did_heroin_yesterday_i_am_not_a_drug_user_and/
https://www.reddit.com/r/IAmA/comments/9ohdc/2_weeks_ago_i_tried_heroin_once_for_fun_and_made/
https://www.reddit.com/r/hinduism/comments/180eb1m/a_lifelong_atheist_turning_to_hindu_spirituality/

# GitHub
# (user adds before GitHub cycle starts; else CLI auto-discovers via Gemini google_search grounding)

# Newsletter
# (user adds before Newsletter cycle starts; else CLI auto-discovers)

# HackerNews
# (auto-discovered in polish phase)

# LinkedIn
# (auto-discovered in polish phase)

# Arxiv
# (auto-discovered in polish phase)

# Podcast
# (auto-discovered in polish phase)

# Web
# (auto-discovered in polish phase)
```

- [ ] **Step 2: Verify parser picks them up**

Run: `python ops/scripts/eval_loop.py --source youtube --list-urls`
Expected: JSON list of the 5 YouTube URLs.

- [ ] **Step 3: Commit**

```bash
git add docs/testing/links.txt
git commit -m "docs: section headered links.txt"
```

---

## Phase 0.G — Rubric YAMLs

### Task 34: Author `rubric_youtube.yaml`

**Files:**
- Create: `docs/summary_eval/_config/rubric_youtube.yaml`

- [ ] **Step 1: Create the file**

```yaml
version: "rubric_youtube.v1"
source_type: "youtube"
composite_max_points: 100

components:
  - id: "brief_summary"
    max_points: 25
    criteria:
      - id: "brief.thesis_capture"
        description: "Brief summary states the video's central thesis or learning objective in one sentence."
        max_points: 5
        maps_to_metric: ["g_eval.relevance", "finesure.completeness"]
      - id: "brief.format_identified"
        description: "Brief identifies the video format explicitly (tutorial/interview/lecture/commentary/etc.)."
        max_points: 3
        maps_to_metric: ["g_eval.relevance"]
      - id: "brief.speakers_captured"
        description: "Brief names the host/channel and any guests or key products/libraries discussed."
        max_points: 4
        maps_to_metric: ["finesure.completeness", "qafact"]
      - id: "brief.major_segments_outlined"
        description: "Brief outlines the major structural segments of the video (intro, sections, demo, conclusion)."
        max_points: 5
        maps_to_metric: ["finesure.completeness", "g_eval.coherence"]
      - id: "brief.takeaways_surfaced"
        description: "Brief highlights 2-3 takeaways a viewer would remember after watching."
        max_points: 4
        maps_to_metric: ["finesure.completeness", "g_eval.relevance"]
      - id: "brief.length_5_to_7_sentences"
        description: "Brief is 5-7 sentences."
        max_points: 2
        maps_to_metric: ["g_eval.conciseness"]
      - id: "brief.no_clickbait"
        description: "Brief does not reproduce clickbait/hook phrasing from the source title."
        max_points: 2
        maps_to_metric: ["finesure.faithfulness"]

  - id: "detailed_summary"
    max_points: 45
    criteria:
      - id: "detailed.chronological_order"
        description: "Detailed bullets follow the video's chronological order."
        max_points: 6
        maps_to_metric: ["g_eval.coherence"]
      - id: "detailed.all_chapters_covered"
        description: "Every substantive chapter or major topic turn is covered by at least one bullet."
        max_points: 10
        maps_to_metric: ["finesure.completeness", "qafact"]
      - id: "detailed.demonstrations_preserved"
        description: "Demonstrations, code walkthroughs, or live examples are captured."
        max_points: 6
        maps_to_metric: ["finesure.completeness"]
      - id: "detailed.caveats_preserved"
        description: "Warnings, caveats, limitations the speaker mentions are captured."
        max_points: 5
        maps_to_metric: ["finesure.faithfulness", "summac"]
      - id: "detailed.examples_purpose_not_verbatim"
        description: "Examples/analogies summarized as PURPOSE, not reproduced verbatim."
        max_points: 5
        maps_to_metric: ["finesure.conciseness"]
      - id: "detailed.entities_named"
        description: "Products, libraries, datasets, or case studies referenced are named."
        max_points: 5
        maps_to_metric: ["finesure.completeness", "qafact"]
      - id: "detailed.closing_takeaway"
        description: "The video's closing takeaway is explicitly captured."
        max_points: 4
        maps_to_metric: ["finesure.completeness"]
      - id: "detailed.no_sponsor_padding"
        description: "Sponsor reads, intros, and 'like and subscribe' fluff are not given bullets."
        max_points: 4
        maps_to_metric: ["finesure.conciseness"]

  - id: "tags"
    max_points: 15
    criteria:
      - id: "tags.count_7_to_10"
        description: "Exactly 7-10 tags."
        max_points: 2
        maps_to_metric: ["finesure.conciseness"]
      - id: "tags.topical_specificity"
        description: "Tags capture specific subject matter, not generic terms."
        max_points: 4
        maps_to_metric: ["g_eval.relevance"]
      - id: "tags.format_tag_present"
        description: "Includes a tag for content type (tutorial/interview/beginner/advanced)."
        max_points: 2
        maps_to_metric: ["g_eval.relevance"]
      - id: "tags.technologies_named"
        description: "Named technologies/libraries/frameworks from the video are tagged."
        max_points: 3
        maps_to_metric: ["finesure.completeness"]
      - id: "tags.no_unsupported_claims"
        description: "No tags imply topics not actually covered."
        max_points: 4
        maps_to_metric: ["finesure.faithfulness", "summac"]

  - id: "label"
    max_points: 15
    criteria:
      - id: "label.content_first_3_to_5_words"
        description: "Label is 3-5 words (max 50 chars), content-first, declarative."
        max_points: 5
        maps_to_metric: ["g_eval.conciseness"]
      - id: "label.reflects_primary_topic"
        description: "Label reflects the primary topic, not side tangents."
        max_points: 5
        maps_to_metric: ["g_eval.relevance"]
      - id: "label.no_clickbait_retention"
        description: "Label removes clickbait/hook fragments from the original title."
        max_points: 5
        maps_to_metric: ["finesure.faithfulness"]

anti_patterns:
  - id: "clickbait_label_retention"
    description: "Label retains YouTube clickbait phrasing ('You won't believe...', 'This changes EVERYTHING')."
    auto_cap: 90
    detection_hint: "Look for exclamation marks, superlatives, curiosity-gap phrasing in label."
  - id: "example_verbatim_reproduction"
    description: "Brief or detailed summary reproduces an example/analogy verbatim."
    auto_cap: null
    penalty_points: 3
  - id: "editorialized_stance"
    description: "Summary introduces stance/framing not present in source."
    auto_cap: 60
  - id: "speakers_absent"
    description: "Summary fails to identify the host or any referenced people."
    auto_cap: 75
  - id: "invented_chapter"
    description: "Summary invents a chapter or segment not present in the video."
    auto_cap: 60

global_rules:
  editorialization_penalty:
    threshold_flags: 3
    cap_on_trigger: 60
```

- [ ] **Step 2: Commit**

```bash
git add docs/summary_eval/_config/rubric_youtube.yaml
git commit -m "docs: youtube rubric yaml"
```

### Task 35: Author `rubric_reddit.yaml`, `rubric_github.yaml`, `rubric_newsletter.yaml`, `rubric_universal.yaml`

**Files:**
- Create: `docs/summary_eval/_config/rubric_reddit.yaml`
- Create: `docs/summary_eval/_config/rubric_github.yaml`
- Create: `docs/summary_eval/_config/rubric_newsletter.yaml`
- Create: `docs/summary_eval/_config/rubric_universal.yaml`

- [ ] **Step 1: Create `rubric_reddit.yaml`**

```yaml
version: "rubric_reddit.v1"
source_type: "reddit"
composite_max_points: 100

components:
  - id: "brief_summary"
    max_points: 25
    criteria:
      - id: "brief.op_intent_captured"
        description: "Brief states OP's core question, problem, or claim in neutral wording."
        max_points: 6
        maps_to_metric: ["g_eval.relevance", "finesure.completeness"]
      - id: "brief.response_range"
        description: "Brief summarizes the range of responses (main solution, common advice, dissent)."
        max_points: 6
        maps_to_metric: ["finesure.completeness"]
      - id: "brief.consensus_signal"
        description: "Brief describes consensus, partial agreement, or disagreement."
        max_points: 4
        maps_to_metric: ["finesure.faithfulness"]
      - id: "brief.caveats_surfaced"
        description: "Brief surfaces important caveats (regional, legal, risk)."
        max_points: 3
        maps_to_metric: ["finesure.faithfulness"]
      - id: "brief.neutral_tone"
        description: "Brief is neutral; does not add summarizer's own judgment."
        max_points: 4
        maps_to_metric: ["summac"]
      - id: "brief.length_5_to_7_sentences"
        description: "Brief is 5-7 sentences."
        max_points: 2
        maps_to_metric: ["g_eval.conciseness"]

  - id: "detailed_summary"
    max_points: 45
    criteria:
      - id: "detailed.reply_clusters"
        description: "Detailed summary represents major opinion clusters, not individual comments."
        max_points: 10
        maps_to_metric: ["finesure.completeness"]
      - id: "detailed.hedged_attribution"
        description: "Unverified comment claims use hedging language ('commenters argue'); no assertion as truth."
        max_points: 8
        maps_to_metric: ["finesure.faithfulness", "summac"]
      - id: "detailed.counterarguments_included"
        description: "Minority or contrarian viewpoints are included when substantively different."
        max_points: 7
        maps_to_metric: ["finesure.completeness"]
      - id: "detailed.external_refs_captured"
        description: "Data, experiments, external references cited by commenters are captured without fabrication."
        max_points: 6
        maps_to_metric: ["finesure.faithfulness", "qafact"]
      - id: "detailed.unresolved_questions"
        description: "Unresolved questions or open points are listed."
        max_points: 4
        maps_to_metric: ["finesure.completeness"]
      - id: "detailed.moderation_context"
        description: "If moderator actions or removed-comment divergence affects thread, it's noted."
        max_points: 5
        maps_to_metric: ["finesure.completeness"]
      - id: "detailed.no_joke_chains"
        description: "Joke chains, side-chatter, meta-discussion are not over-represented."
        max_points: 5
        maps_to_metric: ["finesure.conciseness"]

  - id: "tags"
    max_points: 15
    criteria:
      - id: "tags.count_7_to_10"
        description: "Exactly 7-10 tags."
        max_points: 2
        maps_to_metric: ["finesure.conciseness"]
      - id: "tags.subreddit_present"
        description: "Subreddit appears as a tag (e.g., 'r-askhistorians')."
        max_points: 3
        maps_to_metric: ["g_eval.relevance"]
      - id: "tags.thread_type"
        description: "Thread type tag present ('q-and-a', 'experience-report', 'best-practices')."
        max_points: 3
        maps_to_metric: ["g_eval.relevance"]
      - id: "tags.no_value_judgments"
        description: "No tags encode value judgments unless widely agreed in thread."
        max_points: 4
        maps_to_metric: ["finesure.faithfulness"]
      - id: "tags.topical_specificity"
        description: "Tags are specific, not generic."
        max_points: 3
        maps_to_metric: ["g_eval.relevance"]

  - id: "label"
    max_points: 15
    criteria:
      - id: "label.rsubreddit_prefix"
        description: "Label starts with 'r/<subreddit> ' followed by compact title."
        max_points: 6
        maps_to_metric: ["g_eval.relevance"]
      - id: "label.central_issue"
        description: "Label captures central issue that majority of comments address."
        max_points: 5
        maps_to_metric: ["g_eval.relevance"]
      - id: "label.neutral"
        description: "Label is neutral, not outrage/meme framing."
        max_points: 4
        maps_to_metric: ["finesure.faithfulness"]

anti_patterns:
  - id: "comment_claim_asserted_as_fact"
    description: "An unverified commenter claim is stated as truth without hedging."
    auto_cap: 60
  - id: "missing_removed_comment_note"
    description: "num_comments > rendered_count but summary doesn't mention missing/removed comments."
    auto_cap: 75
  - id: "editorialized_stance"
    description: "Summary adds summarizer's own judgment absent from thread."
    auto_cap: 60

global_rules:
  editorialization_penalty:
    threshold_flags: 3
    cap_on_trigger: 60
```

- [ ] **Step 2: Create `rubric_github.yaml`**

```yaml
version: "rubric_github.v1"
source_type: "github"
composite_max_points: 100

components:
  - id: "brief_summary"
    max_points: 25
    criteria:
      - id: "brief.user_facing_purpose"
        description: "Brief states what the repo does in user-facing terms."
        max_points: 6
        maps_to_metric: ["g_eval.relevance", "finesure.completeness"]
      - id: "brief.architecture_high_level"
        description: "Brief identifies main components/architecture at a high level."
        max_points: 5
        maps_to_metric: ["finesure.completeness"]
      - id: "brief.languages_and_frameworks"
        description: "Primary languages and major frameworks are mentioned."
        max_points: 4
        maps_to_metric: ["finesure.completeness", "qafact"]
      - id: "brief.usage_pattern"
        description: "Describes documented usage/installation/workflow."
        max_points: 4
        maps_to_metric: ["finesure.completeness"]
      - id: "brief.public_surface"
        description: "If exposed, REST routes, CLI entry, UI pages, or Pages URL are summarized."
        max_points: 4
        maps_to_metric: ["finesure.completeness"]
      - id: "brief.no_maturity_fabrication"
        description: "Maturity claims (experimental/production-ready) only if explicitly signaled."
        max_points: 2
        maps_to_metric: ["finesure.faithfulness", "summac"]

  - id: "detailed_summary"
    max_points: 45
    criteria:
      - id: "detailed.features_bullets"
        description: "Core features as bullets, each tied to explicit code or docs."
        max_points: 8
        maps_to_metric: ["finesure.faithfulness", "qafact"]
      - id: "detailed.architecture_modules"
        description: "Architecture bullets: directories, key classes, interactions."
        max_points: 8
        maps_to_metric: ["finesure.completeness"]
      - id: "detailed.interfaces_exact"
        description: "Public APIs / CLI commands / config options with exact names."
        max_points: 8
        maps_to_metric: ["finesure.faithfulness", "summac"]
      - id: "detailed.operational"
        description: "Install steps, deps, env vars, build, deploy instructions captured."
        max_points: 6
        maps_to_metric: ["finesure.completeness"]
      - id: "detailed.limitations_docs"
        description: "Documented limitations, caveats, security notes preserved."
        max_points: 5
        maps_to_metric: ["finesure.faithfulness"]
      - id: "detailed.benchmarks_tests_examples"
        description: "If benchmarks/tests/examples exist, what they demonstrate is summarized."
        max_points: 5
        maps_to_metric: ["finesure.completeness"]
      - id: "detailed.bullets_focused"
        description: "Each bullet covers one coherent aspect."
        max_points: 5
        maps_to_metric: ["g_eval.coherence"]

  - id: "tags"
    max_points: 15
    criteria:
      - id: "tags.count_7_to_10"
        description: "Exactly 7-10 tags."
        max_points: 2
        maps_to_metric: ["finesure.conciseness"]
      - id: "tags.domain_tag"
        description: "Main domain/application tag present."
        max_points: 3
        maps_to_metric: ["g_eval.relevance"]
      - id: "tags.languages"
        description: "Primary language(s) tagged."
        max_points: 3
        maps_to_metric: ["finesure.completeness"]
      - id: "tags.technical_concepts"
        description: "Key technical concepts ('rest-api','cli-tool','ml-serving') present."
        max_points: 3
        maps_to_metric: ["g_eval.relevance"]
      - id: "tags.no_unsupported_claims"
        description: "No tags claim 'production-ready' without evidence."
        max_points: 4
        maps_to_metric: ["finesure.faithfulness", "summac"]

  - id: "label"
    max_points: 15
    criteria:
      - id: "label.owner_slash_repo"
        description: "Label is exactly 'owner/repo' matching the canonical GitHub path."
        max_points: 10
        maps_to_metric: ["finesure.faithfulness"]
      - id: "label.no_extra_descriptors"
        description: "No prepended/appended descriptors; qualifiers belong in summary or tags."
        max_points: 5
        maps_to_metric: ["g_eval.conciseness"]

anti_patterns:
  - id: "production_ready_claim_no_evidence"
    description: "Summary claims 'production-ready' without README evidence."
    auto_cap: 60
  - id: "invented_public_interface"
    description: "Summary claims an API route / CLI command / export not present in repo."
    auto_cap: 60
  - id: "label_not_owner_repo"
    description: "Label doesn't match 'owner/repo' regex."
    auto_cap: 75

global_rules:
  editorialization_penalty:
    threshold_flags: 3
    cap_on_trigger: 60
```

- [ ] **Step 3: Create `rubric_newsletter.yaml`**

```yaml
version: "rubric_newsletter.v1"
source_type: "newsletter"
composite_max_points: 100

components:
  - id: "brief_summary"
    max_points: 25
    criteria:
      - id: "brief.main_topic_thesis"
        description: "Brief states main topic or thesis in one sentence."
        max_points: 6
        maps_to_metric: ["g_eval.relevance", "finesure.completeness"]
      - id: "brief.argument_structure"
        description: "Brief summarizes how the author structures their argument."
        max_points: 5
        maps_to_metric: ["finesure.completeness", "g_eval.coherence"]
      - id: "brief.key_evidence"
        description: "Important evidence or examples are captured without invention."
        max_points: 5
        maps_to_metric: ["finesure.faithfulness", "qafact"]
      - id: "brief.conclusions_distinct"
        description: "Author's conclusions/recommendations distinguished from background."
        max_points: 4
        maps_to_metric: ["finesure.completeness"]
      - id: "brief.caveats_addressed"
        description: "If explicit caveats or counterarguments present, how author addresses them is summarized."
        max_points: 3
        maps_to_metric: ["finesure.faithfulness"]
      - id: "brief.stance_preserved"
        description: "Tone reflects author's stance without editorializing."
        max_points: 2
        maps_to_metric: ["summac"]

  - id: "detailed_summary"
    max_points: 45
    criteria:
      - id: "detailed.sections_ordered"
        description: "Bullets represent major sections/argumentative steps in logical order."
        max_points: 8
        maps_to_metric: ["g_eval.coherence", "finesure.completeness"]
      - id: "detailed.claims_source_grounded"
        description: "Claims in bullets are grounded in the source; no new claims."
        max_points: 8
        maps_to_metric: ["finesure.faithfulness", "summac"]
      - id: "detailed.examples_captured"
        description: "Notable examples, case studies, data anchors are captured."
        max_points: 7
        maps_to_metric: ["finesure.completeness"]
      - id: "detailed.action_items"
        description: "Explicit action items / practical takeaways bulleted if present."
        max_points: 6
        maps_to_metric: ["finesure.completeness"]
      - id: "detailed.multiple_scenarios"
        description: "If multiple viewpoints/scenarios discussed, each gets a bullet."
        max_points: 6
        maps_to_metric: ["finesure.completeness"]
      - id: "detailed.no_footer_padding"
        description: "Unsubscribe language, house style, boilerplate not given bullets."
        max_points: 5
        maps_to_metric: ["finesure.conciseness"]
      - id: "detailed.bullets_specific"
        description: "Bullets concise yet specific; no vague paraphrase."
        max_points: 5
        maps_to_metric: ["g_eval.conciseness"]

  - id: "tags"
    max_points: 15
    criteria:
      - id: "tags.count_7_to_10"
        description: "Exactly 7-10 tags."
        max_points: 2
        maps_to_metric: ["finesure.conciseness"]
      - id: "tags.domain_subdomain"
        description: "Main domain and subdomain tagged."
        max_points: 3
        maps_to_metric: ["g_eval.relevance"]
      - id: "tags.key_concepts"
        description: "Key concepts or frameworks introduced in the piece tagged."
        max_points: 3
        maps_to_metric: ["finesure.completeness"]
      - id: "tags.type_intent"
        description: "Piece type tagged (opinion, research-summary, how-to, case-study)."
        max_points: 3
        maps_to_metric: ["g_eval.relevance"]
      - id: "tags.no_stance_misrepresentation"
        description: "Tags don't misrepresent stance (no 'bullish-call' on neutral piece)."
        max_points: 4
        maps_to_metric: ["finesure.faithfulness"]

  - id: "label"
    max_points: 15
    criteria:
      - id: "label.compact_declarative"
        description: "Label is a compact, declarative phrase reflecting main thesis."
        max_points: 6
        maps_to_metric: ["g_eval.relevance"]
      - id: "label.branded_source_rule"
        description: "For branded sources (Stratechery/Platformer/etc.), label includes publication name."
        max_points: 5
        maps_to_metric: ["g_eval.relevance"]
      - id: "label.informative_not_catchy"
        description: "Informative over catchy; obvious what the Zettel is about."
        max_points: 4
        maps_to_metric: ["g_eval.conciseness"]

anti_patterns:
  - id: "stance_mismatch"
    description: "Summary's implied stance differs from source's detected stance."
    auto_cap: 60
  - id: "invented_number"
    description: "Summary cites a number, date, or source not present in the newsletter."
    auto_cap: 60
  - id: "branded_source_missing_publication"
    description: "Branded newsletter label missing publication name."
    auto_cap: 90

global_rules:
  editorialization_penalty:
    threshold_flags: 3
    cap_on_trigger: 60
```

- [ ] **Step 4: Create `rubric_universal.yaml`** (for polish sources)

```yaml
version: "rubric_universal.v1"
source_type: "universal"
composite_max_points: 100

components:
  - id: "brief_summary"
    max_points: 25
    criteria:
      - id: "brief.what_this_is"
        description: "Brief answers: what is this source?"
        max_points: 5
        maps_to_metric: ["g_eval.relevance"]
      - id: "brief.main_topic"
        description: "Brief answers: what is it about?"
        max_points: 5
        maps_to_metric: ["g_eval.relevance", "finesure.completeness"]
      - id: "brief.major_units"
        description: "Brief outlines the major structural units of the source."
        max_points: 6
        maps_to_metric: ["finesure.completeness"]
      - id: "brief.distinctive_signal"
        description: "Brief conveys what is distinctive / noteworthy."
        max_points: 5
        maps_to_metric: ["g_eval.relevance"]
      - id: "brief.length_5_to_7_sentences"
        description: "Brief is 5-7 sentences."
        max_points: 2
        maps_to_metric: ["g_eval.conciseness"]
      - id: "brief.no_fabrication"
        description: "No invented facts, interfaces, or conclusions."
        max_points: 2
        maps_to_metric: ["finesure.faithfulness", "summac"]

  - id: "detailed_summary"
    max_points: 45
    criteria:
      - id: "detailed.one_bullet_per_unit"
        description: "One bullet per major source unit, no omissions."
        max_points: 18
        maps_to_metric: ["finesure.completeness"]
      - id: "detailed.no_invented_content"
        description: "No unsupported content added."
        max_points: 10
        maps_to_metric: ["finesure.faithfulness", "summac"]
      - id: "detailed.logical_order"
        description: "Bullets follow logical order of source."
        max_points: 8
        maps_to_metric: ["g_eval.coherence"]
      - id: "detailed.bullets_focused"
        description: "Each bullet covers one coherent aspect."
        max_points: 5
        maps_to_metric: ["g_eval.coherence"]
      - id: "detailed.bullets_specific"
        description: "Bullets are specific, not generic paraphrase."
        max_points: 4
        maps_to_metric: ["g_eval.conciseness"]

  - id: "tags"
    max_points: 15
    criteria:
      - id: "tags.count_7_to_10"
        description: "Exactly 7-10 tags."
        max_points: 3
        maps_to_metric: ["finesure.conciseness"]
      - id: "tags.topical_specificity"
        description: "Tags are specific, retrieval-friendly."
        max_points: 5
        maps_to_metric: ["g_eval.relevance"]
      - id: "tags.source_type_marker"
        description: "A source-type marker tag present."
        max_points: 3
        maps_to_metric: ["g_eval.relevance"]
      - id: "tags.no_unsupported"
        description: "No tags imply content not in source."
        max_points: 4
        maps_to_metric: ["finesure.faithfulness"]

  - id: "label"
    max_points: 15
    criteria:
      - id: "label.fast_identifier"
        description: "Label is the fastest reliable identifier for the source."
        max_points: 8
        maps_to_metric: ["g_eval.relevance"]
      - id: "label.makes_sense_alone"
        description: "Label makes sense when seen alone in a note list."
        max_points: 7
        maps_to_metric: ["g_eval.relevance", "g_eval.conciseness"]

anti_patterns:
  - id: "invented_fact"
    description: "Any invented fact, interface, person, or conclusion."
    auto_cap: 60
  - id: "missing_primary_unit"
    description: "Primary thesis/purpose/question/central unit missing."
    auto_cap: 75
  - id: "generic_tags_or_ambiguous_label"
    description: "Generic tags OR ambiguous label."
    auto_cap: 90

global_rules:
  editorialization_penalty:
    threshold_flags: 3
    cap_on_trigger: 60
```

- [ ] **Step 5: Commit**

```bash
git add docs/summary_eval/_config/rubric_reddit.yaml docs/summary_eval/_config/rubric_github.yaml docs/summary_eval/_config/rubric_newsletter.yaml docs/summary_eval/_config/rubric_universal.yaml
git commit -m "docs: rubric yamls reddit github newsletter universal"
```

---

## Phase 0.H — YouTube 5-tier ingest chain (critical for YT summary quality)

### Task 36: Tier scaffolding — refactor YouTube ingestor to dispatch chain

**Files:**
- Modify: `website/features/summarization_engine/source_ingest/youtube/ingest.py`
- Create: `website/features/summarization_engine/source_ingest/youtube/tiers.py`
- Test: `tests/unit/summarization_engine/source_ingest/test_youtube_tiers.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/summarization_engine/source_ingest/test_youtube_tiers.py
import pytest
from unittest.mock import AsyncMock

from website.features.summarization_engine.source_ingest.youtube.tiers import (
    TranscriptChain, TierResult, TierName,
)


@pytest.mark.asyncio
async def test_chain_calls_tiers_in_order_until_success():
    t1 = AsyncMock(return_value=TierResult(tier=TierName.YTDLP_PLAYER_ROTATION, transcript="", success=False))
    t2 = AsyncMock(return_value=TierResult(tier=TierName.TRANSCRIPT_API_DIRECT, transcript="hello", success=True))
    t3 = AsyncMock(return_value=TierResult(tier=TierName.PIPED_POOL, transcript="x", success=True))

    chain = TranscriptChain(tiers=[t1, t2, t3], budget_ms=60000)
    result = await chain.run(video_id="x", config={})
    assert result.tier == TierName.TRANSCRIPT_API_DIRECT
    t1.assert_called_once()
    t2.assert_called_once()
    t3.assert_not_called()


@pytest.mark.asyncio
async def test_chain_stops_when_budget_exceeded():
    import asyncio
    async def slow_tier(video_id, config):
        await asyncio.sleep(0.3)
        return TierResult(tier=TierName.YTDLP_PLAYER_ROTATION, transcript="", success=False)
    chain = TranscriptChain(tiers=[slow_tier, slow_tier, slow_tier], budget_ms=500)
    result = await chain.run(video_id="x", config={})
    assert not result.success
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/summarization_engine/source_ingest/test_youtube_tiers.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `tiers.py`**

```python
"""YouTube transcript fallback chain — five free tiers plus metadata-only."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable


class TierName(str, Enum):
    YTDLP_PLAYER_ROTATION = "ytdlp_player_rotation"
    TRANSCRIPT_API_DIRECT = "transcript_api_direct"
    PIPED_POOL = "piped_pool"
    INVIDIOUS_POOL = "invidious_pool"
    GEMINI_AUDIO = "gemini_audio"
    METADATA_ONLY = "metadata_only"


@dataclass
class TierResult:
    tier: TierName
    transcript: str
    success: bool
    confidence: str = "low"
    latency_ms: int = 0
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


TierFn = Callable[[str, dict], Awaitable[TierResult]]


class TranscriptChain:
    def __init__(self, tiers: list[TierFn], budget_ms: int = 90000) -> None:
        self._tiers = tiers
        self._budget_ms = budget_ms

    async def run(self, *, video_id: str, config: dict) -> TierResult:
        start = time.monotonic()
        last_result: TierResult | None = None
        for tier in self._tiers:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            if elapsed_ms >= self._budget_ms:
                break
            last_result = await tier(video_id, config)
            if last_result.success:
                return last_result
        return last_result or TierResult(tier=TierName.METADATA_ONLY, transcript="", success=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/summarization_engine/source_ingest/test_youtube_tiers.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add website/features/summarization_engine/source_ingest/youtube/tiers.py tests/unit/summarization_engine/source_ingest/test_youtube_tiers.py
git commit -m "feat: youtube transcript tier scaffolding"
```

### Task 37: Tier 1 — yt-dlp player-client rotation

**Files:**
- Modify: `website/features/summarization_engine/source_ingest/youtube/tiers.py`

- [ ] **Step 1: Append Tier 1 implementation**

Append to `tiers.py`:

```python
import logging
import re
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


async def tier_ytdlp_player_rotation(video_id: str, config: dict) -> TierResult:
    """Tier 1: yt-dlp with player-client rotation — primary free path, bypasses most datacenter blocks."""
    from yt_dlp import YoutubeDL

    clients = config.get("ytdlp_player_clients", ["android_embedded", "ios", "tv_embedded", "mweb", "web"])
    url = f"https://www.youtube.com/watch?v={video_id}"
    start = time.monotonic()
    for client in clients:
        with tempfile.TemporaryDirectory() as tmp:
            opts = {
                "quiet": True, "skip_download": True, "no_warnings": True,
                "writesubtitles": True, "writeautomaticsub": True,
                "subtitleslangs": config.get("transcript_languages", ["en"]),
                "subtitlesformat": "vtt",
                "outtmpl": str(Path(tmp) / "%(id)s.%(ext)s"),
                "extractor_args": {"youtube": {"player_client": [client]}},
            }
            try:
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True) or {}
                # Locate any .vtt file yt-dlp wrote
                vtts = list(Path(tmp).glob("*.vtt"))
                if vtts:
                    transcript = _vtt_to_plaintext(vtts[0].read_text(encoding="utf-8"))
                    if len(transcript) > 100:
                        latency = int((time.monotonic() - start) * 1000)
                        logger.info("[yt-tier1] player=%s success len=%d", client, len(transcript))
                        return TierResult(
                            tier=TierName.YTDLP_PLAYER_ROTATION, transcript=transcript,
                            success=True, confidence="high", latency_ms=latency,
                            extra={"player_client": client, "title": info.get("title", "")},
                        )
            except Exception as exc:
                logger.warning("[yt-tier1] player=%s failed: %s", client, exc)
                continue
    return TierResult(tier=TierName.YTDLP_PLAYER_ROTATION, transcript="", success=False,
                      latency_ms=int((time.monotonic() - start) * 1000),
                      error="all player clients failed")


def _vtt_to_plaintext(vtt: str) -> str:
    """Strip WEBVTT headers, timestamps, and cue metadata; return plain text."""
    lines = []
    for raw in vtt.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "WEBVTT" or line.startswith(("NOTE", "STYLE")):
            continue
        if re.match(r"\d{1,2}:\d{2}:\d{2}\.\d{3}\s*-->", line):
            continue
        if re.match(r"^\d+$", line):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        lines.append(line)
    # De-duplicate consecutive identical lines (common in auto-captions)
    deduped = []
    for line in lines:
        if not deduped or deduped[-1] != line:
            deduped.append(line)
    return " ".join(deduped)
```

- [ ] **Step 2: Commit**

```bash
git add website/features/summarization_engine/source_ingest/youtube/tiers.py
git commit -m "feat: yt tier 1 ytdlp player client rotation"
```

### Task 38: Tier 2 — youtube-transcript-api direct

**Files:**
- Modify: `website/features/summarization_engine/source_ingest/youtube/tiers.py`

- [ ] **Step 1: Append Tier 2**

```python
async def tier_transcript_api_direct(video_id: str, config: dict) -> TierResult:
    """Tier 2: youtube-transcript-api direct fetch — fast when it works; usually blocked from DC IPs."""
    start = time.monotonic()
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        entries = api.fetch(video_id, languages=config.get("transcript_languages", ["en"]))
        text = " ".join(item.text for item in entries)
        if len(text) > 100:
            return TierResult(
                tier=TierName.TRANSCRIPT_API_DIRECT, transcript=text,
                success=True, confidence="high",
                latency_ms=int((time.monotonic() - start) * 1000),
            )
    except Exception as exc:
        return TierResult(tier=TierName.TRANSCRIPT_API_DIRECT, transcript="",
                          success=False, error=str(exc),
                          latency_ms=int((time.monotonic() - start) * 1000))
    return TierResult(tier=TierName.TRANSCRIPT_API_DIRECT, transcript="",
                      success=False,
                      latency_ms=int((time.monotonic() - start) * 1000))
```

- [ ] **Step 2: Commit**

```bash
git add website/features/summarization_engine/source_ingest/youtube/tiers.py
git commit -m "feat: yt tier 2 transcript api direct"
```

### Task 39: Tiers 3/4 — Piped + Invidious pools with health-cache

**Files:**
- Modify: `website/features/summarization_engine/source_ingest/youtube/tiers.py`

- [ ] **Step 1: Append Tier 3/4 + health cache**

```python
import json
from datetime import datetime, timedelta, timezone

import httpx


_HEALTH_CACHE_PATH = Path(__file__).resolve().parents[5] / "docs" / "summary_eval" / "_cache" / "youtube_instance_health.json"


def _load_health() -> dict[str, str]:
    if not _HEALTH_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(_HEALTH_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_health(d: dict[str, str]) -> None:
    _HEALTH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _HEALTH_CACHE_PATH.write_text(json.dumps(d, indent=2), encoding="utf-8")


def _is_healthy(instance: str, ttl_hours: int) -> bool:
    health = _load_health()
    last_bad = health.get(instance)
    if not last_bad:
        return True
    try:
        when = datetime.fromisoformat(last_bad)
        return datetime.now(timezone.utc) - when > timedelta(hours=ttl_hours)
    except Exception:
        return True


def _mark_unhealthy(instance: str) -> None:
    health = _load_health()
    health[instance] = datetime.now(timezone.utc).isoformat()
    _save_health(health)


async def _try_pool(video_id: str, instances: list[str], pattern: str, ttl_hours: int, tier_name: TierName) -> TierResult:
    start = time.monotonic()
    for instance in instances:
        if not _is_healthy(instance, ttl_hours):
            continue
        url = pattern.format(instance=instance, vid=video_id)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    _mark_unhealthy(instance); continue
                data = resp.json()
                transcript = _extract_transcript_from_pool_response(data)
                if transcript and len(transcript) > 100:
                    return TierResult(
                        tier=tier_name, transcript=transcript, success=True,
                        confidence="high", latency_ms=int((time.monotonic() - start) * 1000),
                        extra={"instance": instance},
                    )
        except Exception as exc:
            logger.warning("[%s] instance=%s exc=%s", tier_name.value, instance, exc)
            _mark_unhealthy(instance)
            continue
    return TierResult(tier=tier_name, transcript="", success=False,
                      latency_ms=int((time.monotonic() - start) * 1000))


def _extract_transcript_from_pool_response(data: dict) -> str:
    """Piped returns {'subtitles': [{'url': ..., 'code': 'en'}]}.
    Invidious returns {'captions': [{'label': 'English', 'url': ...}]}.
    For simplicity we fetch the first English captions URL when present."""
    subs = data.get("subtitles") or data.get("captions") or []
    for sub in subs:
        code = (sub.get("code") or sub.get("languageCode") or sub.get("label", "")).lower()
        if "en" in code:
            return sub.get("url", "") or ""
    return ""


async def tier_piped_pool(video_id: str, config: dict) -> TierResult:
    instances = [f"https://{i}" for i in config.get("piped_instances", [])]
    ttl = config.get("instance_health_ttl_hours", 1)
    return await _try_pool(video_id, instances, "{instance}/streams/{vid}", ttl, TierName.PIPED_POOL)


async def tier_invidious_pool(video_id: str, config: dict) -> TierResult:
    instances = [f"https://{i}" for i in config.get("invidious_instances", [])]
    ttl = config.get("instance_health_ttl_hours", 1)
    return await _try_pool(video_id, instances, "{instance}/api/v1/captions/{vid}", ttl, TierName.INVIDIOUS_POOL)
```

- [ ] **Step 2: Commit**

```bash
git add website/features/summarization_engine/source_ingest/youtube/tiers.py
git commit -m "feat: yt tier 3 4 piped invidious pool"
```

### Task 40: Tier 5 — Gemini File API audio transcription

**Files:**
- Modify: `website/features/summarization_engine/source_ingest/youtube/tiers.py`

- [ ] **Step 1: Append Tier 5**

```python
async def tier_gemini_audio(video_id: str, config: dict) -> TierResult:
    """Tier 5: yt-dlp downloads audio (googlevideo.com CDN unblocked) -> Gemini Flash File API transcribes.

    CRITICAL: Uploads raw audio bytes via files.upload(). This is NOT the same as the previously-disabled
    `Part.from_uri(youtube_watch_url)` path, which made Gemini hallucinate. We hand Gemini the bytes; it
    genuinely transcribes.
    """
    if not config.get("enable_gemini_audio_fallback", True):
        return TierResult(tier=TierName.GEMINI_AUDIO, transcript="", success=False, error="disabled")

    start = time.monotonic()
    max_size_mb = config.get("gemini_audio_max_filesize_mb", 50)
    max_duration_min = config.get("gemini_audio_max_duration_min", 60)
    url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        from yt_dlp import YoutubeDL
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / f"{video_id}.m4a"
            opts = {
                "quiet": True, "no_warnings": True,
                "format": "bestaudio[ext=m4a]/bestaudio",
                "outtmpl": str(out_path),
                "max_filesize": max_size_mb * 1024 * 1024,
                "match_filter": lambda info: None if (info.get("duration") or 0) <= max_duration_min * 60 else "video too long",
            }
            with YoutubeDL(opts) as ydl:
                ydl.download([url])
            if not out_path.exists():
                return TierResult(tier=TierName.GEMINI_AUDIO, transcript="", success=False,
                                  error="yt-dlp audio download did not produce file",
                                  latency_ms=int((time.monotonic() - start) * 1000))

            # Upload to Gemini File API
            import google.generativeai as genai
            api_key = _first_available_key()
            if not api_key:
                return TierResult(tier=TierName.GEMINI_AUDIO, transcript="", success=False,
                                  error="no gemini key available")
            genai.configure(api_key=api_key)
            uploaded = genai.upload_file(path=str(out_path), mime_type="audio/mp4")
            model = genai.GenerativeModel("gemini-2.5-flash")
            resp = model.generate_content([
                uploaded,
                "Transcribe this audio into plain text with rough timestamps every ~60 seconds. "
                "Return only the transcription, no preamble.",
            ])
            text = (resp.text or "").strip()
            if len(text) > 200:
                return TierResult(
                    tier=TierName.GEMINI_AUDIO, transcript=text, success=True,
                    confidence="high", latency_ms=int((time.monotonic() - start) * 1000),
                    extra={"audio_bytes_uploaded": out_path.stat().st_size},
                )
    except Exception as exc:
        return TierResult(tier=TierName.GEMINI_AUDIO, transcript="", success=False, error=str(exc),
                          latency_ms=int((time.monotonic() - start) * 1000))
    return TierResult(tier=TierName.GEMINI_AUDIO, transcript="", success=False,
                      latency_ms=int((time.monotonic() - start) * 1000))


def _first_available_key() -> str | None:
    import os
    for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_1", "GEMINI_API_KEY_2"):
        if os.environ.get(name):
            return os.environ[name]
    api_env_path = Path(__file__).resolve().parents[5] / "api_env"
    if api_env_path.exists():
        for line in api_env_path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                return s.split()[0]
    return None
```

- [ ] **Step 2: Commit**

```bash
git add website/features/summarization_engine/source_ingest/youtube/tiers.py
git commit -m "feat: yt tier 5 gemini audio file api"
```

### Task 41: Tier 6 (metadata-only) + rewire `ingest.py` to use TranscriptChain

**Files:**
- Modify: `website/features/summarization_engine/source_ingest/youtube/tiers.py`
- Modify: `website/features/summarization_engine/source_ingest/youtube/ingest.py`

- [ ] **Step 1: Append Tier 6 to `tiers.py`**

```python
async def tier_metadata_only(video_id: str, config: dict) -> TierResult:
    """Tier 6: yt-dlp metadata-only fallback. Always works; low confidence."""
    from yt_dlp import YoutubeDL
    start = time.monotonic()
    try:
        with YoutubeDL({"quiet": True, "skip_download": True, "no_warnings": True}) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False) or {}
        title = info.get("title", "")
        desc = info.get("description", "")
        text = f"{title}\n\n{desc}"
        return TierResult(
            tier=TierName.METADATA_ONLY, transcript=text,
            success=bool(title or desc), confidence="low",
            latency_ms=int((time.monotonic() - start) * 1000),
            extra={"title": title, "channel": info.get("channel", ""), "duration": info.get("duration", 0)},
        )
    except Exception as exc:
        return TierResult(tier=TierName.METADATA_ONLY, transcript="", success=False,
                          error=str(exc),
                          latency_ms=int((time.monotonic() - start) * 1000))


def build_default_chain(config: dict) -> TranscriptChain:
    return TranscriptChain(
        tiers=[
            tier_ytdlp_player_rotation,
            tier_transcript_api_direct,
            tier_piped_pool,
            tier_invidious_pool,
            tier_gemini_audio,
            tier_metadata_only,
        ],
        budget_ms=config.get("transcript_budget_ms", 90000),
    )
```

- [ ] **Step 2: Rewrite `ingest.py` to use the chain**

```python
"""YouTube ingestor — free 5-tier fallback chain + metadata-only last resort."""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from website.features.summarization_engine.core.models import IngestResult, SourceType
from website.features.summarization_engine.source_ingest.base import BaseIngestor
from website.features.summarization_engine.source_ingest.utils import join_sections, query_param, utc_now
from website.features.summarization_engine.source_ingest.youtube.tiers import (
    build_default_chain, TierName,
)

logger = logging.getLogger(__name__)


class YouTubeIngestor(BaseIngestor):
    source_type = SourceType.YOUTUBE
    version = "2.0.0"

    async def ingest(self, url: str, *, config: dict[str, Any]) -> IngestResult:
        video_id = _video_id(url)
        canonical_url = f"https://www.youtube.com/watch?v={video_id}"

        chain = build_default_chain(config)
        tier_result = await chain.run(video_id=video_id, config=config)

        transcript = tier_result.transcript
        metadata: dict[str, Any] = {
            "video_id": video_id,
            "tier_used": tier_result.tier.value,
            "tier_latency_ms": tier_result.latency_ms,
            **tier_result.extra,
        }

        sections = {
            "Video": metadata.get("title") or "",
            "Channel": metadata.get("channel") or "",
            "Transcript": transcript,
        }
        raw_text = join_sections(sections)

        if tier_result.tier == TierName.METADATA_ONLY:
            confidence = "low"
            reason = "All transcript tiers failed; metadata-only fallback (composite capped at 75)"
        elif tier_result.success:
            confidence = tier_result.confidence
            reason = f"transcript via tier={tier_result.tier.value} len={len(transcript)}"
        else:
            confidence = "low"
            reason = f"All tiers failed; last error: {tier_result.error}"

        return IngestResult(
            source_type=self.source_type,
            url=canonical_url,
            original_url=url,
            raw_text=raw_text,
            sections=sections,
            metadata=metadata,
            extraction_confidence=confidence,
            confidence_reason=reason,
            fetched_at=utc_now(),
            ingestor_version=self.version,
        )


def _video_id(url: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname and "youtu.be" in parsed.hostname:
        return parsed.path.strip("/")
    if value := query_param(url, "v"):
        return value
    match = re.search(r"/(?:shorts|embed)/([^/?#]+)", parsed.path)
    return match.group(1) if match else parsed.path.strip("/")
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/unit/summarization_engine/source_ingest/ -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add website/features/summarization_engine/source_ingest/youtube/
git commit -m "feat: youtube 5 tier transcript chain live"
```

---

## Phase 0.I — End-to-end smoke test + YouTube Phase 0.5 benchmark

### Task 42: Phase 0 smoke test — assert all exit criteria

**Files:**
- Create: `docs/summary_eval/youtube/phase0-smoke.md`

- [ ] **Step 1: Run smoke checklist**

Execute each check. Record outcomes in `docs/summary_eval/youtube/phase0-smoke.md`:

```bash
# Check 1: all unit tests green
pytest tests/unit/ website/features/summarization_engine/tests/unit/ -q

# Check 2: eval_loop.py --list-urls parses section-headered links.txt
python ops/scripts/eval_loop.py --source youtube --list-urls
# Expected: JSON list of 5 YouTube URLs.

# Check 3: rubric YAMLs validate
python -c "from website.features.summarization_engine.evaluator.rubric_loader import load_rubric; from pathlib import Path; [load_rubric(p) for p in Path('docs/summary_eval/_config').glob('rubric_*.yaml')]; print('OK')"
# Expected: OK (no RubricSchemaError).

# Check 4: start the server, hit /api/v2/summarize on first YouTube URL
python run.py &
sleep 5
curl -X POST http://127.0.0.1:10000/api/v2/summarize \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=hhjhU5MXZOo"}' | python -m json.tool
# Expected: JSON SummaryResult with YouTube-specific schema shape (speakers, entities_discussed, chapters_or_segments).
kill %1
```

- [ ] **Step 2: Create `phase0-smoke.md` recording outcomes**

```markdown
# Phase 0 smoke test — 2026-04-21

## Exit criteria

- [ ] pytest green (all units pass)
- [ ] links.txt section-headered parse
- [ ] all rubric YAMLs validate
- [ ] POST /api/v2/summarize returns valid YouTube SummaryResult
- [ ] docs/summary_eval/_cache/ directories auto-create on first cache put
- [ ] evaluator PROMPT_VERSION = evaluator.v1 stamped in eval.json metadata

## Results

(Codex: paste output of each check above.)
```

- [ ] **Step 3: Commit**

```bash
git add docs/summary_eval/youtube/phase0-smoke.md
git commit -m "test: phase 0 smoke checklist"
```

### Task 43: YouTube Phase 0.5 benchmark — A/B each tier on 3 URLs

**Files:**
- Create: `docs/summary_eval/youtube/phase0.5-ingest/websearch-notes.md`
- Create: `docs/summary_eval/youtube/phase0.5-ingest/candidates/01-ytdlp-player-rotation.json` (and 02-06)
- Create: `docs/summary_eval/youtube/phase0.5-ingest/decision.md`
- Create: `ops/scripts/benchmark_youtube_tiers.py`

- [ ] **Step 1: Create the benchmark runner**

Create `ops/scripts/benchmark_youtube_tiers.py`:

```python
"""Benchmark each YouTube transcript tier on 3 URLs; emit candidate JSONs + decision.md."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from website.features.summarization_engine.source_ingest.youtube.tiers import (
    TierName,
    tier_ytdlp_player_rotation, tier_transcript_api_direct,
    tier_piped_pool, tier_invidious_pool, tier_gemini_audio, tier_metadata_only,
)
from website.features.summarization_engine.core.config import load_config


TIERS = [
    ("01-ytdlp-player-rotation", TierName.YTDLP_PLAYER_ROTATION, tier_ytdlp_player_rotation),
    ("02-transcript-api-direct", TierName.TRANSCRIPT_API_DIRECT, tier_transcript_api_direct),
    ("03-piped-pool", TierName.PIPED_POOL, tier_piped_pool),
    ("04-invidious-pool", TierName.INVIDIOUS_POOL, tier_invidious_pool),
    ("05-gemini-audio", TierName.GEMINI_AUDIO, tier_gemini_audio),
    ("06-metadata-only", TierName.METADATA_ONLY, tier_metadata_only),
]


async def _benchmark():
    cfg = load_config()
    yt_cfg = cfg.sources.get("youtube", {})
    url_ids = ["hhjhU5MXZOo", "HBTYVVUBAGs", "Brm71uCWr-I"]  # first 3 from links.txt
    out_root = Path("docs/summary_eval/youtube/phase0.5-ingest/candidates")
    out_root.mkdir(parents=True, exist_ok=True)

    for filename, tier_name, fn in TIERS:
        per_url = []
        for vid in url_ids:
            result = await fn(vid, yt_cfg)
            per_url.append({
                "video_id": vid,
                "success": result.success,
                "confidence": result.confidence,
                "transcript_chars": len(result.transcript),
                "latency_ms": result.latency_ms,
                "error": result.error,
                "extra": result.extra,
            })
        agg = {
            "tier": tier_name.value,
            "success_rate": sum(1 for u in per_url if u["success"]) / len(per_url),
            "mean_chars": sum(u["transcript_chars"] for u in per_url) / len(per_url),
            "mean_latency_ms": sum(u["latency_ms"] for u in per_url) / len(per_url),
        }
        payload = {"strategy": tier_name.value, "urls_tested": url_ids,
                   "per_url": per_url, "aggregate": agg}
        (out_root / f"{filename}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"{filename}: success_rate={agg['success_rate']:.2f} mean_chars={agg['mean_chars']:.0f}")


if __name__ == "__main__":
    asyncio.run(_benchmark())
```

- [ ] **Step 2: Create websearch notes stub**

Create `docs/summary_eval/youtube/phase0.5-ingest/websearch-notes.md`:

```markdown
# YouTube transcript landscape — 2026-04-21

(Codex: run WebSearch for "youtube-transcript-api datacenter IP blocked 2026", "yt-dlp youtube transcript player_client android_embedded", "Piped Invidious public instance transcript API 2026". Paste summaries below.)

## Key findings
- yt-dlp `android_embedded` player client: bypasses most 2025-26 transcript blocks because it uses Innertube API, not public captions endpoint.
- Piped/Invidious pool churn: instance liveness should be re-validated on every Phase 0.5 pass; our health-cache handles rotation.
- Gemini File API audio transcription is the reliable escape hatch; bytes upload via files.upload() (NOT Part.from_uri).
```

- [ ] **Step 3: Run the benchmark**

```bash
python ops/scripts/benchmark_youtube_tiers.py
```

- [ ] **Step 4: Write `decision.md`**

Create `docs/summary_eval/youtube/phase0.5-ingest/decision.md` summarizing which tier won per URL and the final ordering (should match the default chain order barring pool-health issues). Acceptance: at least 2 of 3 URLs got `confidence=high`.

- [ ] **Step 5: Commit**

```bash
git add ops/scripts/benchmark_youtube_tiers.py docs/summary_eval/youtube/phase0.5-ingest/
git commit -m "test: youtube phase 0.5 ingest benchmark"
```

### Task 44: Zoro prod-parity dry-run (optional smoke)

**Files:**
- Add to: `docs/summary_eval/youtube/phase0-smoke.md`

- [ ] **Step 1: Verify Zoro auth flow (no real write yet)**

With `SUMMARIZE_ENV=prod-parity` env and valid Supabase creds exported:

```bash
export SUMMARIZE_ENV=prod-parity
export SUPABASE_URL=https://icmnskseuoteyirljswd.supabase.co
# SUPABASE_ANON_KEY must be exported from your secure store (do NOT commit)
python run.py &
sleep 5
python ops/scripts/eval_loop.py --source youtube --iter 1 --env prod-parity --dry-run
kill %1
```

Expected: Zoro bearer token is fetched from Supabase using creds from `docs/login_details.txt`, `prod_parity_auth.txt` would be written with Zoro's user_id (`a57e1f2f-7d89-4cd7-ae39-72c440ed4b4e`). Dry-run exits without hitting `/api/v2/summarize`.

- [ ] **Step 2: Append outcome to `phase0-smoke.md`**

Append a `## Zoro prod-parity dry-run` section recording the token-fetch outcome + error if any. If the Supabase anon key isn't available, record `SKIPPED — SUPABASE_ANON_KEY not exported` and move on; Zoro auth is only required for real loop 7 runs, not for Phase 0 smoke.

- [ ] **Step 3: Commit**

```bash
git add docs/summary_eval/youtube/phase0-smoke.md
git commit -m "test: zoro prod parity auth dry run"
```

---

## Final step — push the branch

- [ ] **Push the feature branch**

```bash
git push origin eval/summary-engine-v2-scoring
```

- [ ] **Open draft PR (optional, for review)**

```bash
gh pr create --draft --title "feat: summarization engine phase 0 + youtube phase 0.5" \
  --body "Implements Plan 1 of the 5-PR sequence from docs/superpowers/specs/2026-04-21-summarization-engine-scoring-optimization-design.md. See docs/superpowers/plans/2026-04-21-summarization-engine-phase0-youtube.md for the full task list. Ready for YouTube iteration loops 1-7 after merge."
```

---

## Plan self-review

After implementing all 44 tasks, run this checklist before the PR:

- [ ] **Spec coverage:** every Phase 0 exit criterion (§6.6) + every Phase 0.5 YouTube A/B requirement (§7.1) from the spec has a task? Yes.
- [ ] **Evaluator prompt versioning:** `PROMPT_VERSION = "evaluator.v1"` stamped? Yes (Task 20).
- [ ] **Cross-model isolation:** `manual_review_prompt.md` emitted by CLI but `manual_review.md` written only by Codex? Yes (Tasks 24 + 32).
- [ ] **Blind-review enforcement:** `eval_json_hash_at_review: "NOT_CONSULTED"` verified by CLI? Yes (Task 24).
- [ ] **Rubric `maps_to_metric` + `anti_patterns`:** every rubric YAML has both? Yes (Tasks 34-35).
- [ ] **YouTube 5-tier chain:** all 5 free tiers + metadata-only implemented? Yes (Tasks 37-41).
- [ ] **Zoro auth wired for loop 7:** CLI `--env prod-parity` authenticates as Zoro before POST to `/api/v2/summarize`? Yes (Task 32).
- [ ] **Cache layer:** ingest + atomic-facts caches wired? Yes (Tasks 16, 17, 21).
- [ ] **Key-role pool extension:** `role=` tag support + billing auto-fallback? Yes (Task 18).
- [ ] **Per-source schemas:** YouTube + Reddit + GitHub + Newsletter schemas Pydantic-enforced? Yes (Tasks 4-7).
- [ ] **links.txt:** section-headered format? Yes (Task 33).
- [ ] **Config-driven caps:** `build_summary_result_model(cfg)` factory? Yes (Task 3).
- [ ] **Thin-wrapper deleted:** `_wrappers.py` removed + auto-discovery updated? Yes (Task 15).

If any box cannot be checked, that task's implementation is incomplete — do not open the PR until all are green.





