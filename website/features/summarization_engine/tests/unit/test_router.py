"""URL router tests: detect SourceType from URL."""
import pytest

from website.features.summarization_engine.core.models import SourceType
from website.features.summarization_engine.core.router import (
    detect_route_decision,
    detect_source_type,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/foo/bar", SourceType.GITHUB),
        ("https://www.github.com/foo/bar", SourceType.GITHUB),
        ("https://github.com/foo/bar/tree/main", SourceType.GITHUB),
        ("https://news.ycombinator.com/item?id=123", SourceType.HACKERNEWS),
        ("https://arxiv.org/abs/2310.11511", SourceType.ARXIV),
        ("https://arxiv.org/pdf/2310.11511", SourceType.ARXIV),
        ("https://ar5iv.labs.arxiv.org/html/2310.11511", SourceType.ARXIV),
        ("https://www.reddit.com/r/Python/comments/abc/test/", SourceType.REDDIT),
        ("https://old.reddit.com/r/Python/comments/abc/test/", SourceType.REDDIT),
        ("https://redd.it/abc123", SourceType.REDDIT),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", SourceType.YOUTUBE),
        ("https://youtu.be/dQw4w9WgXcQ", SourceType.YOUTUBE),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", SourceType.YOUTUBE),
        ("https://www.linkedin.com/posts/satya_activity-1234", SourceType.LINKEDIN),
        ("https://stratechery.substack.com/p/some-post", SourceType.NEWSLETTER),
        ("https://medium.com/@author/some-post-abc123", SourceType.NEWSLETTER),
        ("https://author.substack.com/p/post", SourceType.NEWSLETTER),
        ("https://newsletter.pragmaticengineer.com/p/the-product-minded-engineer", SourceType.NEWSLETTER),
        ("https://podcasts.apple.com/us/podcast/foo/id123?i=456", SourceType.PODCAST),
        ("https://open.spotify.com/episode/abc123", SourceType.PODCAST),
        ("https://overcast.fm/+XYZ", SourceType.PODCAST),
        ("https://twitter.com/user/status/1234567890", SourceType.TWITTER),
        ("https://x.com/user/status/1234567890", SourceType.TWITTER),
        ("https://example.com/article", SourceType.WEB),
        ("https://unknown-site.org/page", SourceType.WEB),
    ],
)
def test_detect_source_type(url, expected):
    assert detect_source_type(url) == expected


def test_detect_source_type_empty_returns_web():
    assert detect_source_type("") == SourceType.WEB


def test_detect_source_type_malformed_returns_web():
    assert detect_source_type("not-a-url") == SourceType.WEB


@pytest.mark.parametrize(
    ("url", "subtype", "supported"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "video", True),
        ("https://youtu.be/dQw4w9WgXcQ", "video", True),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "video", True),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "video", True),
        ("https://www.youtube.com/@somechannel", "channel", False),
        ("https://www.youtube.com/playlist?list=PL123", "playlist", False),
        ("https://github.com/foo/bar", "repo", True),
        ("https://github.com/foo/bar/issues/10", "issue", True),
        ("https://github.com/foo/bar/pull/11", "pull_request", True),
        ("https://github.com/foo/bar/commit/abc123", "commit", True),
        ("https://github.com/foo/bar/releases/tag/v1.0.0", "release", True),
        ("https://github.com/foo/bar/blob/main/README.md", "blob", True),
        ("https://github.com/foo/bar/tree/main/src", "tree", True),
        ("https://www.linkedin.com/posts/satya_activity-1234", "post", True),
        ("https://www.linkedin.com/login", "authwall", False),
        ("https://arxiv.org/abs/2310.11511", "abstract", True),
        ("https://arxiv.org/pdf/2310.11511", "pdf", True),
        ("https://ar5iv.labs.arxiv.org/html/2310.11511", "html", True),
        ("https://open.spotify.com/episode/abc123", "episode", True),
        ("https://twitter.com/user/status/1234567890", "status", True),
    ],
)
def test_detect_route_decision_subtypes(url, subtype, supported):
    decision = detect_route_decision(url)
    assert decision.subtype == subtype
    assert decision.supported is supported


def test_detect_route_decision_marks_bad_youtube_shape_without_changing_family():
    decision = detect_route_decision("https://www.youtube.com/@somechannel")

    assert decision.source_type == SourceType.YOUTUBE
    assert decision.supported is False
    assert decision.reason == "unsupported_youtube_channel"
