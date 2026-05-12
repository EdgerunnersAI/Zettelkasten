"""SE-02: per-adapter contract tests for the 10 source ingestors.

For each ``SourceType`` member we (a) confirm the registry resolves to a
concrete ``BaseIngestor`` subclass, (b) load a recorded HTTP fixture from
``tests/fixtures/source_ingest/<source>/{happy,thin}.json`` if available,
and (c) drive the ingestor with the cassette to assert the
``IngestResult`` contract:

  * ``source_type`` matches the SourceType under test.
  * ``raw_text`` is non-empty (happy) / below threshold (thin).
  * ``metadata`` carries the source-canonical fields (title where the
    upstream service exposes one).
  * ``extraction_confidence`` lands in the documented bucket per scenario.

Per the WAVE-C re-dispatch brief: cassettes that would require OAuth
tokens we do not possess (Reddit OAuth-app, full GitHub API auth,
LinkedIn auth, podcast-platform-specific shapes) are SKIPPED with an
explanation rather than faked. A passing test backed by an invented
cassette would prove the wrong thing.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from website.features.summarization_engine.core.models import SourceType
from website.features.summarization_engine.source_ingest import (
    get_ingestor,
    list_ingestors,
)
from tests.v2.fixtures.wave_c import (
    SOURCE_INGEST_NAMES,
    SourceFixturePathResolver,
)

_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "source_ingest"


# Sources we can build realistic cassettes for from public-API shapes:
#   * web: arbitrary HTML.
#   * hackernews: Algolia ``/items/<id>`` JSON shape (public, documented).
#   * arxiv: Atom XML shape (public).
# Sources we SKIP (cassette would be invented; risk of wrong-pass):
#   * youtube: yt-dlp pulls a binary stream, not a single HTTP doc.
#   * github: requires OAuth-able api_client + multi-call orchestration.
#   * linkedin: requires authenticated session — no public extract.
#   * newsletter: per-site extractor matrix, multi-step.
#   * podcast: per-platform parser (Apple/Spotify/Overcast/Snipd) — no
#     single canonical doc.
#   * reddit: covered separately by SE-06 with focused JSON+HTML stubs.
#   * twitter: requires Nitter mirror pool — multi-host probe.
_REALISTIC_CASSETTE_SOURCES = {"web", "hackernews", "arxiv"}
_SKIP_CASSETTE_SOURCES = {
    "youtube": "ingestor uses yt-dlp + transcript API; no single-doc cassette",
    "github": "ingestor requires GitHub API auth + multi-call api_client",
    "linkedin": "no public unauthenticated path; cassette would be misleading",
    "newsletter": "per-site extractor matrix; multi-step probe + extract",
    "podcast": "per-platform parser tree (Apple/Spotify/Overcast/Snipd); no single doc",
    "reddit": "covered by tests/integration/v2/test_summarize_reddit_oauth.py",
    "twitter": "requires Nitter mirror pool probe; multi-host fallback chain",
}


# --- registry completeness gate ------------------------------------------


def test_registry_contains_all_ten_sources() -> None:
    """Auto-discovery must find every SourceType member; pkgutil drift
    (an ``__init__.py`` rename, a missed ``register_ingestor`` call) would
    silently break a source. Anchored against the canonical SOURCE_INGEST_NAMES."""
    registered = list_ingestors()
    registered_names = {st.value for st in registered.keys()}
    expected = set(SOURCE_INGEST_NAMES)
    assert registered_names == expected, (
        f"registry mismatch: missing={expected - registered_names}, "
        f"extra={registered_names - expected}"
    )


@pytest.mark.parametrize("source_name", SOURCE_INGEST_NAMES)
def test_each_source_resolves_to_a_base_ingestor(source_name: str) -> None:
    from website.features.summarization_engine.source_ingest.base import (
        BaseIngestor,
    )

    cls = get_ingestor(SourceType(source_name))
    assert issubclass(cls, BaseIngestor)
    instance = cls()
    assert hasattr(instance, "ingest")
    assert callable(instance.ingest)


# --- per-adapter contracts (cassette-backed) ------------------------------


@pytest.mark.parametrize("source_name", SOURCE_INGEST_NAMES)
def test_per_source_cassette_directory_exists(source_name: str) -> None:
    """Phase 1a requirement: every source dir is present (with .gitkeep
    or with a real cassette). Mismatched layout breaks ``recorded_source_fixtures``."""
    src_dir = _FIXTURE_ROOT / source_name
    assert src_dir.exists() and src_dir.is_dir(), (
        f"source_ingest fixture dir missing: {src_dir}"
    )


# --- web: HTML happy path ------------------------------------------------


_WEB_HAPPY_HTML = """<html>
<head><title>Test Article: Lessons in Engineering</title>
<meta name="description" content="A real article about engineering lessons.">
</head>
<body><article>
<h1>Test Article</h1>
<p>This article body has substantial content covering several topics in depth. It includes multiple paragraphs of meaningful prose to satisfy the trafilatura main-content extractor.</p>
<p>The second paragraph elaborates further on the topic, ensuring we comfortably exceed the 300 character floor that the WebIngestor uses to mark extraction confidence as high. We are talking through engineering lessons, with examples drawn from production incidents and post-mortems published by industry teams.</p>
<p>A third paragraph for good measure, with enough words to satisfy the high-confidence threshold and provide downstream summarisers something coherent to chew on. Bullet-point density: medium. Specificity: high. Sentence count: at least eight across the three paragraphs.</p>
</article></body></html>"""


@pytest.mark.asyncio
async def test_web_adapter_happy_contract() -> None:
    """Web ingestor: extract HTML article body, confidence = high when
    extracted text exceeds the configured ``min_text_length`` floor (300)."""
    from website.features.summarization_engine.source_ingest.web.ingest import (
        WebIngestor,
    )

    with patch(
        "website.features.summarization_engine.source_ingest.web.ingest.fetch_text",
        new=AsyncMock(return_value=(_WEB_HAPPY_HTML, "https://example.com/x")),
    ):
        ingestor = WebIngestor()
        result = await ingestor.ingest("https://example.com/x", config={})

    assert result.source_type == SourceType.WEB
    assert result.url == "https://example.com/x"
    # Body extracted (via trafilatura or BS4 fallback) — assert on
    # content actually present in <article>, not on the meta-description.
    assert "substantial content" in result.raw_text.lower()
    assert result.extraction_confidence == "high"
    assert "Test Article" in result.metadata.get("title", "")
    # Meta description IS captured in metadata for downstream summarisers.
    assert (
        "engineering lessons"
        in (result.metadata.get("description") or "").lower()
    )


@pytest.mark.asyncio
async def test_web_adapter_thin_extraction_low_confidence() -> None:
    """Below-threshold body → confidence drops to 'low'."""
    from website.features.summarization_engine.source_ingest.web.ingest import (
        WebIngestor,
    )
    thin_html = "<html><body><p>tiny.</p></body></html>"

    with patch(
        "website.features.summarization_engine.source_ingest.web.ingest.fetch_text",
        new=AsyncMock(return_value=(thin_html, "https://example.com/thin")),
    ):
        ingestor = WebIngestor()
        result = await ingestor.ingest(
            "https://example.com/thin", config={"min_text_length": 300}
        )

    assert result.extraction_confidence == "low"
    assert "below threshold" in result.confidence_reason


# --- hackernews: Algolia API JSON shape ----------------------------------


_HN_HAPPY_PAYLOAD = {
    "id": 12345678,
    "title": "Show HN: A test post for the contract harness",
    "url": "https://example.com/blog",
    "text": "Body text of a Show HN entry.",
    "points": 100,
    "author": "test_user",
    "children": [
        {
            "author": "commenter_a",
            "text": "Top-level comment with substantive content.",
            "children": [
                {
                    "author": "commenter_b",
                    "text": "Nested reply elaborates on the parent.",
                    "children": [],
                }
            ],
        },
        {"author": "commenter_c", "text": "Sibling comment.", "children": []},
    ],
}


@pytest.mark.asyncio
async def test_hackernews_adapter_happy_contract() -> None:
    """Hacker News: Algolia /items/<id> JSON; confidence == high when
    title is present; comments are flattened breadth-first."""
    from website.features.summarization_engine.source_ingest.hackernews.ingest import (
        HackerNewsIngestor,
    )
    url = "https://news.ycombinator.com/item?id=12345678"

    with patch(
        "website.features.summarization_engine.source_ingest.hackernews.ingest.fetch_json",
        new=AsyncMock(return_value=(_HN_HAPPY_PAYLOAD, url)),
    ):
        ingestor = HackerNewsIngestor()
        result = await ingestor.ingest(url, config={"max_comments": 10})

    assert result.source_type == SourceType.HACKERNEWS
    assert result.metadata["item_id"] == "12345678"
    assert result.metadata["points"] == 100
    assert result.metadata["author"] == "test_user"
    assert result.extraction_confidence == "high"
    # Comments are flattened in BFS order: a, c (siblings) then b (nested).
    assert "commenter_a" in result.raw_text
    assert "commenter_b" in result.raw_text
    assert "commenter_c" in result.raw_text


@pytest.mark.asyncio
async def test_hackernews_adapter_no_title_drops_to_medium() -> None:
    """Untitled item (rare): confidence drops to 'medium'."""
    from website.features.summarization_engine.source_ingest.hackernews.ingest import (
        HackerNewsIngestor,
    )
    payload = dict(_HN_HAPPY_PAYLOAD, title=None)
    url = "https://news.ycombinator.com/item?id=99"

    with patch(
        "website.features.summarization_engine.source_ingest.hackernews.ingest.fetch_json",
        new=AsyncMock(return_value=(payload, url)),
    ):
        ingestor = HackerNewsIngestor()
        result = await ingestor.ingest(url, config={})

    assert result.extraction_confidence == "medium"


# --- arxiv: Atom XML feed shape -----------------------------------------


_ARXIV_HAPPY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>A Test Paper on Knowledge Graphs</title>
    <summary>This is the abstract of a test paper. It discusses
    knowledge-graph construction at scale, with substantive findings
    about node-deduplication and edge-density tradeoffs.</summary>
    <published>2024-04-01T00:00:00Z</published>
    <author><name>Alice Researcher</name></author>
    <author><name>Bob Coauthor</name></author>
    <link rel="alternate" type="text/html" href="https://arxiv.org/abs/2404.00001"/>
    <link rel="related" type="application/pdf" href="https://arxiv.org/pdf/2404.00001"/>
  </entry>
</feed>"""


@pytest.mark.asyncio
async def test_arxiv_adapter_happy_contract() -> None:
    """arXiv: Atom XML metadata; confidence high when title + summary present."""
    from website.features.summarization_engine.source_ingest.arxiv.ingest import (
        ArxivIngestor,
    )
    url = "https://arxiv.org/abs/2404.00001"

    with patch(
        "website.features.summarization_engine.source_ingest.arxiv.ingest.fetch_text",
        new=AsyncMock(return_value=(_ARXIV_HAPPY_XML, url)),
    ):
        ingestor = ArxivIngestor()
        result = await ingestor.ingest(url, config={})

    assert result.source_type == SourceType.ARXIV
    assert result.metadata["paper_id"] == "2404.00001"
    assert result.metadata["title"] == "A Test Paper on Knowledge Graphs"
    assert "Alice Researcher" in result.metadata["authors"]
    assert "Bob Coauthor" in result.metadata["authors"]
    assert result.extraction_confidence == "high"
    assert "knowledge-graph construction" in result.raw_text.lower()


@pytest.mark.asyncio
async def test_arxiv_adapter_empty_feed_low_confidence() -> None:
    """No entries → low confidence."""
    from website.features.summarization_engine.source_ingest.arxiv.ingest import (
        ArxivIngestor,
    )
    empty_xml = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"/>'
    url = "https://arxiv.org/abs/missing"

    with patch(
        "website.features.summarization_engine.source_ingest.arxiv.ingest.fetch_text",
        new=AsyncMock(return_value=(empty_xml, url)),
    ):
        ingestor = ArxivIngestor()
        result = await ingestor.ingest(url, config={})

    assert result.extraction_confidence == "low"


# --- skipped sources: explicit explanation each ---------------------------


@pytest.mark.parametrize(
    "source,reason",
    list(_SKIP_CASSETTE_SOURCES.items()),
)
def test_cassette_skipped_with_reason(source: str, reason: str) -> None:
    """Sources whose cassette would require fabricated OAuth/binary/multi-step
    fixtures are documented as skipped here. Each represents a contract that
    a future operator-approved investment can record properly."""
    pytest.skip(f"SE-02 cassette intentionally skipped for {source}: {reason}")


# --- recorded_source_fixtures resolver smoke -----------------------------


def test_resolver_unknown_source_raises() -> None:
    with pytest.raises(ValueError, match="Unknown source"):
        SourceFixturePathResolver.path_for(source="not-a-source")


def test_resolver_missing_cassette_raises_clear_error() -> None:
    """When a cassette is referenced but not yet recorded, the resolver
    must emit a FileNotFoundError that names the missing path so the
    sub-agent knows what to provide."""
    with pytest.raises(FileNotFoundError, match="No recorded fixture at"):
        SourceFixturePathResolver.load(source="github", scenario="happy")
