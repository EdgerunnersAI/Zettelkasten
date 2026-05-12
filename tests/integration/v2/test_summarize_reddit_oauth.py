"""SE-06: Reddit ingestor JSON → HTML fallback correctness.

The summarization-engine ``RedditIngestor`` does NOT use OAuth (the
website surface bypasses Reddit's OAuth-gated APIs in favour of the
public ``.json`` endpoint and an HTML scrape fallback). The OAuth
client_id/secret are reserved for ``ops/scripts/backfill_chunks.py`` per
CLAUDE.md.

This test covers degradation:

  1. JSON happy path → ``extraction_confidence == "high"``, fields parsed.
  2. JSON fetch fails → falls through to HTML scrape; confidence drops to
     ``"medium"``; ``confidence_reason`` reflects the fallback.
  3. ALL fetches fail → returns a ``low``-confidence stub with the URL +
     subreddit derived from the slug, never raising (so the orchestrator
     can catch it via the < 50-char guard or surface as a thin extraction).

We mock at the network boundary (``fetch_json`` / ``fetch_text``) so no
real Reddit traffic is hit and no real OAuth credentials are needed
(CLAUDE.md guard).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from website.features.summarization_engine.source_ingest.reddit.ingest import (
    RedditIngestor,
    _title_from_url_slug,
    _extract_subreddit,
)


_URL = "https://www.reddit.com/r/MachineLearning/comments/abc123/some_post_title_here/"


def _json_payload(*, num_comments=3, rendered=3):
    """Build a minimal Reddit ``.json`` envelope shape."""
    post = {
        "id": "abc123",
        "title": "Some Post Title Here",
        "selftext": "This is the body of the post, with substantial content.",
        "url": _URL,
        "subreddit": "MachineLearning",
        "author": "u_test",
        "score": 42,
        "num_comments": num_comments,
        "permalink": "/r/MachineLearning/comments/abc123/some_post_title_here/",
    }
    children = [
        {
            "kind": "t1",
            "data": {
                "author": f"u_commenter_{i}",
                "body": f"Comment body {i} with enough content to show up.",
            },
        }
        for i in range(rendered)
    ]
    return [
        {"data": {"children": [{"data": post}]}},
        {"data": {"children": children}},
    ]


@pytest.mark.asyncio
async def test_reddit_json_happy_path() -> None:
    payload = _json_payload(num_comments=3, rendered=3)

    with patch(
        "website.features.summarization_engine.source_ingest.reddit.ingest.fetch_json",
        new=AsyncMock(return_value=(payload, _URL + ".json")),
    ):
        # Disable pullpush — not relevant for happy path.
        ingestor = RedditIngestor()
        result = await ingestor.ingest(_URL, config={"pullpush_enabled": False})

    assert result.extraction_confidence == "high"
    assert result.metadata["title"] == "Some Post Title Here"
    assert result.metadata["subreddit"] == "MachineLearning"
    assert result.metadata["num_comments"] == 3
    assert result.metadata["rendered_comment_count"] == 3
    assert result.metadata["comment_divergence_pct"] == 0.0
    assert "Some Post Title Here" in result.raw_text
    assert "Comment body 0" in result.raw_text


@pytest.mark.asyncio
async def test_reddit_json_failure_falls_back_to_html() -> None:
    """JSON endpoint dies → HTML scrape kicks in. Confidence == 'medium'."""
    html = """<html>
        <head><title>Some Post Title Here : MachineLearning</title></head>
        <body><div>Article body text repeated to clear extractor heuristics.</div></body>
        </html>"""

    with patch(
        "website.features.summarization_engine.source_ingest.reddit.ingest.fetch_json",
        new=AsyncMock(side_effect=RuntimeError("403 blocked")),
    ), patch(
        "website.features.summarization_engine.source_ingest.reddit.ingest.fetch_text",
        new=AsyncMock(return_value=(html, _URL)),
    ):
        ingestor = RedditIngestor()
        result = await ingestor.ingest(_URL, config={})

    assert result.extraction_confidence == "medium"
    assert "JSON blocked" in result.confidence_reason
    assert result.metadata["subreddit"] == "MachineLearning"


@pytest.mark.asyncio
async def test_reddit_total_block_returns_low_confidence_stub() -> None:
    """Both JSON and HTML fail (Reddit anti-bot wall). The ingestor returns
    a low-confidence stub with the URL + subreddit derived from the slug,
    so the orchestrator can decide whether to reject it via the < 50-char
    guard."""
    with patch(
        "website.features.summarization_engine.source_ingest.reddit.ingest.fetch_json",
        new=AsyncMock(side_effect=RuntimeError("403")),
    ), patch(
        "website.features.summarization_engine.source_ingest.reddit.ingest.fetch_text",
        new=AsyncMock(side_effect=RuntimeError("403")),
    ):
        ingestor = RedditIngestor()
        result = await ingestor.ingest(_URL, config={})

    assert result.extraction_confidence == "low"
    assert "blocked all fetch attempts" in result.confidence_reason
    assert result.metadata["subreddit"] == "MachineLearning"
    # Title derived from URL slug since server-side title was unavailable.
    assert result.metadata["title"] != ""


# --- helpers (unit) -------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        (_URL, "MachineLearning"),
        ("https://reddit.com/r/python/comments/x/y/", "python"),
        ("https://www.reddit.com/", None),
    ],
)
def test_extract_subreddit(url, expected) -> None:
    assert _extract_subreddit(url) == expected


@pytest.mark.parametrize(
    "url,expected_substring",
    [
        (_URL, "Some Post Title Here"),
        (
            "https://reddit.com/r/python/comments/x/eli5_what_is_a_metaclass/",
            "ELI5 What Is",
        ),
        ("https://www.reddit.com/", ""),  # no slug
    ],
)
def test_title_from_url_slug_handles_acronyms_and_truncation(
    url, expected_substring
) -> None:
    out = _title_from_url_slug(url)
    if expected_substring:
        assert expected_substring in out, (
            f"slug-derived title missing expected: got={out!r}"
        )
    else:
        assert out == ""


def test_title_from_url_slug_strips_trailing_stopwords() -> None:
    url = (
        "https://reddit.com/r/x/comments/y/"
        "the_truth_about_neural_nets_is/"
    )
    out = _title_from_url_slug(url)
    assert not out.lower().endswith(" is")
    assert not out.lower().endswith(" the")


# --- env-var protection: no real Reddit creds may be referenced -----------


def test_no_reddit_oauth_credentials_referenced(monkeypatch) -> None:
    """The summarization-engine RedditIngestor must NOT read REDDIT_CLIENT_*
    env vars (those are owned by ops/scripts/backfill_chunks.py). Sanity
    check: even if they're set we don't change behaviour."""
    monkeypatch.setenv("REDDIT_CLIENT_ID", "should-be-ignored")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "should-be-ignored")

    import inspect
    from website.features.summarization_engine.source_ingest.reddit import (
        ingest as reddit_ingest_mod,
    )
    src = inspect.getsource(reddit_ingest_mod)
    assert "REDDIT_CLIENT_ID" not in src, (
        "RedditIngestor leaked OAuth env-var read; ops/scripts owns those."
    )
    assert "REDDIT_CLIENT_SECRET" not in src
