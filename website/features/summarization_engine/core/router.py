"""URL to SourceType detection for the summarization engine.

Also exposes thin delegating wrappers for the YouTube format classifier and
GitHub archetype classifier so the orchestrator / per-source summarizers have
a single import surface for routing decisions (URL -> source, content ->
format/archetype). The wrappers MUST be safe no-ops on empty/None input so
callers do not need to duplicate guard logic at every site.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from website.features.summarization_engine.core.models import SourceType
from website.features.summarization_engine.summarization.github.archetype import (
    RepoArchetype,
    classify_archetype,
)
from website.features.summarization_engine.summarization.youtube.format_classifier import (
    FORMAT_LABELS,
    classify_format,
)

_DOMAIN_RULES: list[tuple[tuple[str, ...], SourceType]] = [
    (("github.com",), SourceType.GITHUB),
    (("news.ycombinator.com",), SourceType.HACKERNEWS),
    (("arxiv.org", "ar5iv.labs.arxiv.org"), SourceType.ARXIV),
    (("reddit.com", "redd.it"), SourceType.REDDIT),
    (("youtube.com", "youtu.be"), SourceType.YOUTUBE),
    (("linkedin.com",), SourceType.LINKEDIN),
    (("twitter.com", "x.com"), SourceType.TWITTER),
    (
        (
            "podcasts.apple.com",
            "open.spotify.com",
            "overcast.fm",
            "pca.st",
            "share.snipd.com",
            "snipd.com",
        ),
        SourceType.PODCAST,
    ),
]

_NEWSLETTER_DOMAINS: tuple[str, ...] = (
    "substack.com",
    "medium.com",
    "beehiiv.com",
    "buttondown.email",
    "mailchimp.com",
    "hackernoon.com",
    "dev.to",
    "stratechery.com",
    "platformer.news",
    "pragmaticengineer.com",
)
_NEWSLETTER_CUSTOM_SUFFIXES: tuple[str, ...] = (".news",)


@dataclass(frozen=True)
class RouteDecision:
    """Structured route contract for source-aware ingestion.

    ``detect_source_type`` stays as the compatibility API. New callers can use
    this richer object to distinguish a supported repo URL from a GitHub issue,
    or a YouTube video URL from a channel/playlist shape that the summarizer
    should not pretend is video content.
    """

    source_type: SourceType
    subtype: str
    supported: bool = True
    reason: str | None = None


def _strip_known_mobile_prefix(host: str) -> str:
    for prefix in ("www.", "m.", "mobile.", "old."):
        if host.startswith(prefix):
            return host[len(prefix) :]
    return host


def _looks_like_newsletter_post(path: str) -> bool:
    normalized = (path or "").rstrip("/")
    return normalized == "/p" or normalized.startswith("/p/")


def detect_source_type(url: str) -> SourceType:
    """Detect a source type, returning WEB for unknown or malformed URLs."""
    return detect_route_decision(url).source_type


def detect_route_decision(url: str) -> RouteDecision:
    """Detect source family plus object subtype/support contract."""
    if not url:
        return RouteDecision(SourceType.WEB, "unknown", False, "empty_url")
    try:
        parsed = urlparse(url)
    except ValueError:
        return RouteDecision(SourceType.WEB, "unknown", False, "malformed_url")

    host = (parsed.hostname or "").lower()
    if not host:
        return RouteDecision(SourceType.WEB, "unknown", False, "missing_host")
    host = _strip_known_mobile_prefix(host)
    path = parsed.path or ""

    if host == "youtu.be" or host.endswith(".youtu.be"):
        subtype = "video" if path.strip("/") else "unknown"
        return RouteDecision(
            SourceType.YOUTUBE,
            subtype,
            bool(path.strip("/")),
            None if path.strip("/") else "missing_youtube_video_id",
        )
    if host == "youtube.com" or host.endswith(".youtube.com"):
        normalized = path.rstrip("/") or "/"
        if parsed.query and query_param_from_parsed(parsed.query, "v"):
            return RouteDecision(SourceType.YOUTUBE, "video")
        if normalized.startswith("/shorts/") or normalized.startswith("/embed/"):
            return RouteDecision(SourceType.YOUTUBE, "video")
        if normalized.startswith("/playlist"):
            return RouteDecision(
                SourceType.YOUTUBE,
                "playlist",
                False,
                "unsupported_youtube_playlist",
            )
        if normalized.startswith("/@") or normalized.startswith("/channel/") or normalized.startswith("/c/"):
            return RouteDecision(
                SourceType.YOUTUBE,
                "channel",
                False,
                "unsupported_youtube_channel",
            )
        return RouteDecision(
            SourceType.YOUTUBE,
            "unknown",
            False,
            "unsupported_youtube_url_shape",
        )

    if host == "github.com" or host.endswith(".github.com"):
        parts = [p for p in path.split("/") if p]
        subtype = "repo"
        if len(parts) >= 4 and parts[2] in {"issues", "issue"}:
            subtype = "issue"
        elif len(parts) >= 4 and parts[2] in {"pull", "pulls"}:
            subtype = "pull_request"
        elif len(parts) >= 4 and parts[2] == "commit":
            subtype = "commit"
        elif len(parts) >= 3 and parts[2] == "releases":
            subtype = "release"
        elif len(parts) >= 4 and parts[2] == "blob":
            subtype = "blob"
        elif len(parts) >= 4 and parts[2] == "tree":
            subtype = "tree"
        supported = len(parts) >= 2
        return RouteDecision(
            SourceType.GITHUB,
            subtype if supported else "unknown",
            supported,
            None if supported else "missing_github_owner_repo",
        )

    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        normalized = path.rstrip("/") or "/"
        if normalized.startswith("/login") or normalized.startswith("/checkpoint"):
            return RouteDecision(
                SourceType.LINKEDIN,
                "authwall",
                False,
                "unsupported_linkedin_authwall",
            )
        if normalized.startswith("/posts/"):
            return RouteDecision(SourceType.LINKEDIN, "post")
        if normalized.startswith("/pulse/"):
            return RouteDecision(SourceType.LINKEDIN, "article")
        if normalized.startswith("/company/"):
            return RouteDecision(SourceType.LINKEDIN, "company")
        if normalized.startswith("/in/"):
            return RouteDecision(SourceType.LINKEDIN, "profile")
        return RouteDecision(SourceType.LINKEDIN, "public_page")

    if host == "ar5iv.labs.arxiv.org" or host.endswith(".ar5iv.labs.arxiv.org"):
        return RouteDecision(SourceType.ARXIV, "html")
    if host == "arxiv.org" or host.endswith(".arxiv.org"):
        if path.startswith("/pdf/"):
            return RouteDecision(SourceType.ARXIV, "pdf")
        if path.startswith("/html/"):
            return RouteDecision(SourceType.ARXIV, "html")
        return RouteDecision(SourceType.ARXIV, "abstract")

    if host in {"twitter.com", "x.com"} or host.endswith(".twitter.com") or host.endswith(".x.com"):
        return RouteDecision(
            SourceType.TWITTER,
            "status" if "/status/" in path else "public_page",
            "/status/" in path,
            None if "/status/" in path else "unsupported_twitter_url_shape",
        )

    for domains, source_type in _DOMAIN_RULES:
        for domain in domains:
            if host == domain or host.endswith("." + domain):
                subtype = "episode" if source_type == SourceType.PODCAST else source_type.value
                return RouteDecision(source_type, subtype)

    for domain in _NEWSLETTER_DOMAINS:
        if host == domain or host.endswith("." + domain):
            subtype = "post" if _looks_like_newsletter_post(parsed.path) else "publication_page"
            return RouteDecision(SourceType.NEWSLETTER, subtype)

    if _looks_like_newsletter_post(parsed.path):
        for suffix in _NEWSLETTER_CUSTOM_SUFFIXES:
            if host.endswith(suffix):
                return RouteDecision(SourceType.NEWSLETTER, "post")

    return RouteDecision(SourceType.WEB, "page")


def query_param_from_parsed(query: str, key: str) -> str | None:
    """Small local query parser to keep router independent from ingest utils."""
    from urllib.parse import parse_qs

    values = parse_qs(query or "").get(key)
    if not values:
        return None
    return values[0] or None


# Default fallbacks used when input is empty/None. Kept module-private so
# callers cannot drift from the contract.
_YOUTUBE_DEFAULT_FORMAT = "commentary"
_YOUTUBE_DEFAULT_CONFIDENCE = 0.0
_GITHUB_DEFAULT_ARCHETYPE = RepoArchetype.LIBRARY_THIN.value


def classify_youtube_format(transcript: str | None) -> tuple[str, float]:
    """Return ``(format_label, confidence)`` for a YouTube transcript.

    Thin wrapper around :func:`...youtube.format_classifier.classify_format`.
    The underlying classifier scores against title / description / chapter
    titles / speakers; when only a raw transcript is available we feed it as
    the description signal so lexical cues (`tutorial`, `interview`, etc.)
    still fire. On empty/None input returns the default label with confidence
    ``0.0`` so callers can branch on confidence safely.
    """
    if not transcript or not str(transcript).strip():
        return _YOUTUBE_DEFAULT_FORMAT, _YOUTUBE_DEFAULT_CONFIDENCE
    label, confidence = classify_format(
        title="",
        description=str(transcript),
        chapter_titles=[],
        speakers=[],
    )
    if label not in FORMAT_LABELS:
        return _YOUTUBE_DEFAULT_FORMAT, _YOUTUBE_DEFAULT_CONFIDENCE
    return label, float(confidence)


def classify_github_archetype(repo_metadata: dict[str, Any] | None) -> str:
    """Return the archetype string for a GitHub repo metadata dict.

    Thin wrapper around :func:`...github.archetype.classify_archetype`. The
    underlying classifier needs ``raw_text`` plus optional metadata; the
    metadata dict accepted here may carry ``raw_text`` / ``readme`` /
    ``description`` plus structural metadata (``topics``, ``language``,
    ``has_*`` flags). On empty/None input returns ``"library"`` (mapped to
    :data:`RepoArchetype.LIBRARY_THIN`) so the caller always has a usable
    label.
    """
    if not repo_metadata or not isinstance(repo_metadata, dict):
        return _GITHUB_DEFAULT_ARCHETYPE
    raw_text = (
        repo_metadata.get("raw_text")
        or repo_metadata.get("readme")
        or repo_metadata.get("description")
        or ""
    )
    if not isinstance(raw_text, str) or not raw_text.strip():
        return _GITHUB_DEFAULT_ARCHETYPE
    structural = {
        k: v for k, v in repo_metadata.items()
        if k not in {"raw_text", "readme", "description"}
    }
    verdict = classify_archetype(raw_text=raw_text, metadata=structural)
    return verdict.archetype.value
