"""GitHub repository ingestor."""
from __future__ import annotations

import base64
import logging
import os
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)
from typing import Any
from urllib.parse import urlparse

import httpx

from website.features.summarization_engine.core.models import IngestResult, SourceType
from website.features.summarization_engine.source_ingest.github.api_client import (
    GitHubApiClient,
)
from website.features.summarization_engine.source_ingest.github.architecture import (
    extract_architecture_overview,
)
from website.features.summarization_engine.source_ingest.base import BaseIngestor
from website.features.summarization_engine.source_ingest.utils import (
    compact_text,
    join_sections,
    raise_extraction,
    utc_now,
)

# Top-level Markdown files we look for beyond the README. Names are matched
# case-insensitively against the repo's top-level tree.
_EXTRA_TOP_LEVEL_DOCS = (
    "CONTRIBUTING.md",
    "ARCHITECTURE.md",
    "ROADMAP.md",
    "CHANGELOG.md",
    "SECURITY.md",
    "GOVERNANCE.md",
    "CODE_OF_CONDUCT.md",
)

# Filenames to pick up inside a top-level docs/ directory. First match wins
# for each "slot" so a repo with docs/README.md AND docs/index.md only yields
# one of them.
_DOCS_DIR_CANDIDATES = (
    ("docs/README.md", "docs/readme.md"),
    ("docs/index.md", "docs/INDEX.md"),
    ("docs/getting-started.md", "docs/GETTING_STARTED.md"),
    ("docs/overview.md", "docs/OVERVIEW.md"),
)

# Hard cap on the number of additional doc files fetched per repo (beyond the
# README) to avoid blowing the GitHub API rate limit.
_MAX_EXTRA_DOCS = 4
# Per-file character cap for any single doc file appended to the raw text.
_DOC_FILE_CHAR_CAP = 4000


class GitHubIngestor(BaseIngestor):
    source_type = SourceType.GITHUB

    async def ingest(self, url: str, *, config: dict[str, Any]) -> IngestResult:
        route = _parse_github_route(url)
        owner, repo = route["owner"], route["repo"]
        headers = {"Accept": "application/vnd.github+json"}
        token = _github_token(config)
        if token:
            headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient(
            timeout=20.0,
            headers=headers,
            follow_redirects=True,
        ) as client:
            repo_resp = await client.get(f"https://api.github.com/repos/{owner}/{repo}")
            if repo_resp.status_code == 404:
                raise_extraction("GitHub repository not found", self.source_type, "404")
            repo_resp.raise_for_status()
            repo_data = repo_resp.json()

            if route["subtype"] != "repo":
                object_result = await _ingest_github_object(
                    client,
                    url=url,
                    owner=owner,
                    repo=repo,
                    repo_data=repo_data,
                    route=route,
                )
                if object_result is not None:
                    return object_result

            readme = await _optional_readme(client, owner, repo)
            languages = await _optional_json(client, f"https://api.github.com/repos/{owner}/{repo}/languages", {})
            issues = []
            if config.get("fetch_issues", True):
                issues = await _optional_json(
                    client,
                    f"https://api.github.com/repos/{owner}/{repo}/issues",
                    [],
                    params={"state": "open", "per_page": int(config.get("max_issues", 20))},
                )
            commits = []
            if config.get("fetch_commits", True):
                commits = await _optional_json(
                    client,
                    f"https://api.github.com/repos/{owner}/{repo}/commits",
                    [],
                    params={"per_page": int(config.get("max_commits", 10))},
                )

            extra_docs: list[tuple[str, str]] = []
            if config.get("fetch_docs", True):
                default_branch = repo_data.get("default_branch") or "main"
                extra_docs = await _fetch_extra_docs(
                    client,
                    owner,
                    repo,
                    default_branch,
                    max_files=int(config.get("max_docs", _MAX_EXTRA_DOCS)),
                    char_cap=int(config.get("doc_char_cap", _DOC_FILE_CHAR_CAP)),
                )

        docs_section = ""
        if extra_docs:
            docs_section = "\n\n".join(
                f"### {name}\n{body}" for name, body in extra_docs
            )

        sections = {
            "Repository": (
                f"{repo_data.get('full_name', f'{owner}/{repo}')}\n"
                f"{repo_data.get('description') or ''}\n"
                f"Language: {repo_data.get('language') or 'unknown'}\n"
                f"Topics: {', '.join(repo_data.get('topics') or [])}"
            ),
            "README": readme,
            "Docs": docs_section,
            "Languages": ", ".join(f"{k}: {v}" for k, v in languages.items()) if isinstance(languages, dict) else "",
            "Issues": "\n".join(_issue_line(issue) for issue in issues[: int(config.get("max_issues", 20))]),
            "Commits": "\n".join(_commit_line(commit) for commit in commits[: int(config.get("max_commits", 10))]),
        }
        metadata = {
            "owner": owner,
            "repo": repo,
            "github_subtype": "repo",
            "full_name": repo_data.get("full_name"),
            "stars": repo_data.get("stargazers_count", 0),
            "forks": repo_data.get("forks_count", 0),
            "language": repo_data.get("language"),
            "topics": repo_data.get("topics") or [],
            "license": (repo_data.get("license") or {}).get("spdx_id"),
            "updated_at": repo_data.get("updated_at"),
            "extra_doc_files": [name for name, _ in extra_docs],
        }

        owner_repo = f"{owner}/{repo}"
        api_client = GitHubApiClient(
            token=token or "",
            base_url=config.get("api_base_url", "https://api.github.com"),
            timeout_sec=int(config.get("api_timeout_sec", 15)),
        )
        signals = await api_client.fetch_all_signals(
            owner_repo,
            {
                "fetch_pages": config.get("fetch_pages", False),
                "fetch_workflows": config.get("fetch_workflows", False),
                "fetch_releases": config.get("fetch_releases", False),
                "max_releases": config.get("max_releases", 5),
                "fetch_languages": config.get("fetch_languages", False),
                "fetch_root_dir_listing": config.get("fetch_root_dir_listing", False),
            },
        )

        metadata.update(
            {
                "pages_url": signals.pages_url,
                "has_workflows": signals.has_workflows,
                "workflow_count": signals.workflow_count,
                "releases": signals.releases,
                "languages": signals.languages,
                **{k: v for k, v in signals.root_dir_flags.items()},
            }
        )

        signal_lines = [
            f"Pages URL: {signals.pages_url or 'none'}",
            f"GitHub Actions workflows: {signals.workflow_count}",
            "Recent releases: "
            + (", ".join(r.get("tag_name", "?") for r in signals.releases) or "none"),
            "Language composition: "
            + (
                ", ".join(f"{lang}={pct:.1f}%" for lang, pct in signals.languages[:5])
                or "none"
            ),
            "Root dirs: "
            + ", ".join(
                key.replace("has_", "")
                for key, value in signals.root_dir_flags.items()
                if value
            ),
        ]
        sections["Repository signals"] = "\n".join(signal_lines)

        if config.get("architecture_overview_enabled", False):
            try:
                from website.features.summarization_engine.api.routes import _gemini_client

                client = _gemini_client()
                cache_root = (
                    Path(__file__).resolve().parents[5]
                    / "docs"
                    / "summary_eval"
                    / "_cache"
                )
                arch_overview = await extract_architecture_overview(
                    client=client,
                    readme_text=readme or "",
                    top_level_dirs=[
                        key.replace("has_", "")
                        for key, value in signals.root_dir_flags.items()
                        if value
                    ],
                    max_chars=int(config.get("architecture_overview_max_chars", 500)),
                    cache_root=cache_root,
                    slug=owner_repo,
                )
                sections["Architecture overview"] = arch_overview
                metadata["architecture_overview"] = arch_overview
            except Exception as exc:
                logger.warning(
                    "[gh-ingest] architecture overview failed for %s: %s",
                    owner_repo,
                    exc,
                )

        raw_text = join_sections(sections)

        has_content = bool(readme) or bool(extra_docs)
        confidence: str = "high" if has_content else "medium"
        if readme and extra_docs:
            reason = f"repo metadata, README, and {len(extra_docs)} extra doc(s) fetched"
        elif readme:
            reason = "repo metadata and README fetched"
        elif extra_docs:
            reason = f"repo metadata and {len(extra_docs)} extra doc(s) fetched (no README)"
        else:
            reason = "repo metadata fetched without README"

        return IngestResult(
            source_type=self.source_type,
            url=f"https://github.com/{owner}/{repo}",
            original_url=url,
            raw_text=raw_text,
            sections=sections,
            metadata=metadata,
            extraction_confidence=confidence,  # type: ignore[arg-type]
            confidence_reason=reason,
            fetched_at=utc_now(),
        )


def _parse_repo(url: str) -> tuple[str, str]:
    route = _parse_github_route(url)
    return route["owner"], route["repo"]


def _parse_github_route(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or not re.match(r"^[A-Za-z0-9_.-]+$", parts[0]):
        raise_extraction("Invalid GitHub repository URL", SourceType.GITHUB, "bad_url")
    subtype = "repo"
    identifier = ""
    if len(parts) >= 4 and parts[2] in {"issues", "issue"}:
        subtype = "issue"
        identifier = parts[3]
    elif len(parts) >= 4 and parts[2] in {"pull", "pulls"}:
        subtype = "pull_request"
        identifier = parts[3]
    elif len(parts) >= 4 and parts[2] == "commit":
        subtype = "commit"
        identifier = parts[3]
    elif len(parts) >= 3 and parts[2] == "releases":
        subtype = "release"
        identifier = "/".join(parts[3:])
    elif len(parts) >= 4 and parts[2] in {"blob", "tree"}:
        subtype = parts[2]
        identifier = "/".join(parts[3:])
    return {
        "owner": parts[0],
        "repo": parts[1].removesuffix(".git"),
        "subtype": subtype,
        "identifier": identifier,
    }


async def _ingest_github_object(
    client: httpx.AsyncClient,
    *,
    url: str,
    owner: str,
    repo: str,
    repo_data: dict[str, Any],
    route: dict[str, str],
) -> IngestResult | None:
    subtype = route["subtype"]
    ident = route["identifier"]
    api_base = f"https://api.github.com/repos/{owner}/{repo}"
    payload: dict[str, Any] | None = None
    if subtype == "issue":
        payload = await _optional_json(client, f"{api_base}/issues/{ident}", {})
    elif subtype == "pull_request":
        payload = await _optional_json(client, f"{api_base}/pulls/{ident}", {})
    elif subtype == "commit":
        payload = await _optional_json(client, f"{api_base}/commits/{ident}", {})
    elif subtype == "release":
        tag = ident.removeprefix("tag/")
        payload = await _optional_json(client, f"{api_base}/releases/tags/{tag}", {})
    elif subtype in {"blob", "tree"}:
        return _degraded_file_context_result(
            url=url,
            owner=owner,
            repo=repo,
            repo_data=repo_data,
            route=route,
        )
    if not payload:
        return None

    title = (
        payload.get("title")
        or payload.get("name")
        or payload.get("commit", {}).get("message")
        or f"{subtype} {ident}"
    )
    body = payload.get("body") or payload.get("commit", {}).get("message") or ""
    sections = {
        "Repository": f"{repo_data.get('full_name', f'{owner}/{repo}')}\n{repo_data.get('description') or ''}",
        subtype.replace("_", " ").title(): (
            f"{title}\nState: {payload.get('state') or payload.get('mergeable_state') or 'unknown'}\n"
            f"Author: {((payload.get('user') or {}).get('login')) or ((payload.get('author') or {}).get('login')) or 'unknown'}\n"
            f"Comments: {payload.get('comments', 0)}\n{body}"
        ),
    }
    return IngestResult(
        source_type=SourceType.GITHUB,
        url=url,
        original_url=url,
        raw_text=join_sections(sections),
        sections=sections,
        metadata={
            "owner": owner,
            "repo": repo,
            "full_name": repo_data.get("full_name"),
            "github_subtype": subtype,
            "github_object_id": ident,
            "title": compact_text(str(title), max_chars=160),
            "state": payload.get("state"),
        },
        extraction_confidence="high" if body or title else "medium",
        confidence_reason=f"GitHub {subtype} object fetched",
        fetched_at=utc_now(),
    )


def _degraded_file_context_result(
    *,
    url: str,
    owner: str,
    repo: str,
    repo_data: dict[str, Any],
    route: dict[str, str],
) -> IngestResult:
    subtype = route["subtype"]
    ident = route["identifier"]
    sections = {
        "Repository": f"{repo_data.get('full_name', f'{owner}/{repo}')}\n{repo_data.get('description') or ''}",
        "File Context": (
            f"This GitHub URL points to a {subtype} path: {ident}. "
            "The engine treats this as degraded file-context metadata unless a dedicated file fetch succeeds."
        ),
    }
    return IngestResult(
        source_type=SourceType.GITHUB,
        url=url,
        original_url=url,
        raw_text=join_sections(sections),
        sections=sections,
        metadata={
            "owner": owner,
            "repo": repo,
            "full_name": repo_data.get("full_name"),
            "github_subtype": subtype,
            "github_object_id": ident,
            "degraded_file_context": True,
        },
        extraction_confidence="medium",
        confidence_reason=f"GitHub {subtype} URL treated as degraded file context",
        fetched_at=utc_now(),
    )


def _github_token(config: dict[str, Any]) -> str:
    token = os.environ.get(config.get("github_token_env", "GITHUB_TOKEN"), "").strip()
    if token:
        return token
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


async def _optional_json(client: httpx.AsyncClient, url: str, default: Any, **kwargs: Any) -> Any:
    try:
        response = await client.get(url, **kwargs)
        if response.status_code >= 400:
            return default
        return response.json()
    except Exception:
        return default


async def _optional_readme(client: httpx.AsyncClient, owner: str, repo: str) -> str:
    data = await _optional_json(client, f"https://api.github.com/repos/{owner}/{repo}/readme", {})
    encoded = data.get("content") if isinstance(data, dict) else None
    if not encoded:
        return ""
    try:
        return base64.b64decode(encoded).decode("utf-8", errors="replace")
    except Exception:
        return ""


async def _fetch_extra_docs(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    default_branch: str,
    *,
    max_files: int,
    char_cap: int,
) -> list[tuple[str, str]]:
    """Fetch up to ``max_files`` additional Markdown docs from the repo.

    Preference order:
      1. Top-level governance/architecture docs (CONTRIBUTING, ARCHITECTURE,
         ROADMAP, CHANGELOG, SECURITY, GOVERNANCE, CODE_OF_CONDUCT).
      2. Curated docs/ entries (README, index, getting-started, overview).

    Filesystem access is through the Contents API; we bail silently if the
    repo has no Markdown, the rate limit kicks in, or the repo is archived.
    """
    if max_files <= 0:
        return []

    tree_listing = await _optional_json(
        client,
        f"https://api.github.com/repos/{owner}/{repo}/contents",
        [],
    )
    if not isinstance(tree_listing, list):
        return []

    top_level_names = {
        entry.get("name", ""): entry.get("name", "")
        for entry in tree_listing
        if isinstance(entry, dict)
    }
    lower_to_actual = {name.lower(): name for name in top_level_names if name}

    picked: list[tuple[str, str]] = []

    # Pass 1 — governance/architecture docs at the repo root.
    for candidate in _EXTRA_TOP_LEVEL_DOCS:
        if len(picked) >= max_files:
            break
        actual = lower_to_actual.get(candidate.lower())
        if not actual:
            continue
        body = await _fetch_file_contents(client, owner, repo, actual, default_branch)
        if not body:
            continue
        picked.append((actual, _truncate(body, char_cap)))

    # Early return: skip docs/ scan if we already hit the cap.
    if len(picked) >= max_files:
        return picked

    # Pass 2 — walk docs/ if present.
    has_docs_dir = any(
        entry.get("name") == "docs" and entry.get("type") == "dir"
        for entry in tree_listing
        if isinstance(entry, dict)
    )
    if not has_docs_dir:
        return picked

    for group in _DOCS_DIR_CANDIDATES:
        if len(picked) >= max_files:
            break
        for path in group:
            body = await _fetch_file_contents(
                client, owner, repo, path, default_branch
            )
            if body:
                picked.append((path, _truncate(body, char_cap)))
                break  # one per "slot"

    return picked


async def _fetch_file_contents(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    path: str,
    ref: str,
) -> str:
    """Fetch one file from the Contents API, decoding base64.

    Returns empty string on any non-200 response, missing content field, or
    decode failure — the caller treats empty as "skip".
    """
    data = await _optional_json(
        client,
        f"https://api.github.com/repos/{owner}/{repo}/contents/{path}",
        {},
        params={"ref": ref},
    )
    if not isinstance(data, dict):
        return ""
    encoded = data.get("content")
    if not encoded or data.get("encoding") != "base64":
        return ""
    try:
        return base64.b64decode(encoded).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _truncate(text: str, char_cap: int) -> str:
    if len(text) <= char_cap:
        return text.strip()
    return text[: char_cap - 1].rstrip() + "…"


def _issue_line(issue: dict[str, Any]) -> str:
    return compact_text(f"#{issue.get('number')}: {issue.get('title')} {issue.get('body') or ''}", max_chars=400)


def _commit_line(commit: dict[str, Any]) -> str:
    message = (commit.get("commit") or {}).get("message") or ""
    return compact_text(f"{commit.get('sha', '')}: {message}", max_chars=240)
