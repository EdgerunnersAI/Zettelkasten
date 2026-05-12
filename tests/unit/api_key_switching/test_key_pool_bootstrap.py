"""KP-01: Bootstrap discipline for the GeminiKeyPool singleton.

Covers the five surfaces called out in
``docs/research/full_modular_test_plans/api_key_switching.md``:

* missing ``api_env`` (no file at any candidate path)
* malformed line (unknown ``role=`` token raises ValueError)
* empty ``api_env`` file (silently treated as no keys → next source wins)
* legacy ``GEMINI_API_KEY`` settings fallback (lowest precedence)
* ``/etc/secrets/api_env`` participates in the candidate path list

These guard the bootstrap path that, if broken, takes down the website,
RAG, and KG simultaneously (per plan rationale).

Anti-pattern guard: never alter ``_GENERATIVE_MODEL_CHAIN`` or
``GUNICORN_*`` knobs; bootstrap tests are observational only.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from website.features import api_key_switching as aks_mod
from website.features.api_key_switching.key_pool import (
    GeminiKeyPool,
    _load_keys_from_file,
    candidate_api_env_paths,
    parse_api_env_line,
)


# ---------------------------------------------------------------------------
# parse_api_env_line — malformed line handling
# ---------------------------------------------------------------------------


def test_parse_api_env_line_empty_raises():
    with pytest.raises(ValueError, match="empty api_env line"):
        parse_api_env_line("   ")


def test_parse_api_env_line_unknown_role_token_raises():
    # Locked invariant: only "free"/"billing" are accepted role values.
    with pytest.raises(ValueError, match="invalid role"):
        parse_api_env_line("AIzaKey role=enterprise")


def test_parse_api_env_line_extra_unknown_token_ignored():
    # Tokens that are not ``role=...`` are silently ignored — keeps the
    # parser forward-compatible with future per-key flags.
    key, role = parse_api_env_line("AIzaKey region=us-east")
    assert key == "AIzaKey"
    assert role == "free"


# ---------------------------------------------------------------------------
# _load_keys_from_file
# ---------------------------------------------------------------------------


def test_load_keys_from_missing_file_returns_empty(tmp_path: Path):
    missing = tmp_path / "nope_api_env"
    assert _load_keys_from_file(str(missing)) == []


def test_load_keys_from_empty_file_returns_empty(tmp_path: Path):
    empty = tmp_path / "api_env"
    empty.write_text("", encoding="utf-8")
    assert _load_keys_from_file(str(empty)) == []


def test_load_keys_skips_comments_and_blank_lines(tmp_path: Path):
    p = tmp_path / "api_env"
    p.write_text(
        "# comment line\n"
        "\n"
        "AIzaKey1\n"
        "   \n"
        "AIzaKey2 role=billing\n",
        encoding="utf-8",
    )
    keys = _load_keys_from_file(str(p))
    # Untagged keys come back as plain strings; explicit role= as tuples.
    assert keys == ["AIzaKey1", ("AIzaKey2", "billing")]


def test_load_keys_malformed_role_raises(tmp_path: Path):
    p = tmp_path / "api_env"
    p.write_text("AIzaKey role=enterprise\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid role"):
        _load_keys_from_file(str(p))


# ---------------------------------------------------------------------------
# candidate_api_env_paths — /etc/secrets/api_env precedence
# ---------------------------------------------------------------------------


def test_candidate_paths_include_etc_secrets():
    paths = candidate_api_env_paths()
    # Production droplet mounts the secret file at /etc/secrets/api_env.
    # The path MUST be present in the candidate list (last tier — only
    # consulted when feature/project-root files are missing).
    assert Path("/etc/secrets/api_env") in paths


def test_candidate_paths_etc_secrets_is_lowest_priority():
    paths = candidate_api_env_paths()
    # /etc/secrets/api_env is the FALLBACK path — repo-local files win.
    # If this invariant flips, an operator who accidentally drops a stale
    # api_env into /etc/secrets while debugging would shadow real keys.
    assert paths[-1] == Path("/etc/secrets/api_env")


def test_candidate_paths_dedup():
    # Duplicate path candidates (e.g. when worktree resolution overlaps the
    # main repo path) must be coalesced; otherwise the same file would be
    # parsed twice and double-count keys.
    paths = candidate_api_env_paths()
    assert len(paths) == len(set(paths))


# ---------------------------------------------------------------------------
# init_key_pool precedence chain
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_pool_singleton():
    """Reset module-level singleton so each test starts clean."""
    aks_mod._pool = None
    yield
    aks_mod._pool = None


def _patch_paths(monkeypatch, paths: list[str]):
    monkeypatch.setattr(aks_mod, "_API_ENV_PATHS", tuple(paths))


def test_init_pool_prefers_api_env_file(tmp_path: Path, monkeypatch):
    p = tmp_path / "api_env"
    p.write_text("AIzaFromFile\n", encoding="utf-8")
    _patch_paths(monkeypatch, [str(p)])
    monkeypatch.setenv("GEMINI_API_KEYS", "AIzaFromCsv")

    pool = aks_mod.init_key_pool()
    assert pool._keys == ["AIzaFromFile"]


def test_init_pool_csv_env_when_file_missing(tmp_path: Path, monkeypatch):
    _patch_paths(monkeypatch, [str(tmp_path / "missing")])
    monkeypatch.setenv("GEMINI_API_KEYS", "AIzaCsv1, AIzaCsv2 ,  ")

    pool = aks_mod.init_key_pool()
    # Empty / whitespace tokens are filtered out.
    assert pool._keys == ["AIzaCsv1", "AIzaCsv2"]


def test_init_pool_falls_back_to_settings_gemini_api_key(monkeypatch, tmp_path: Path):
    _patch_paths(monkeypatch, [str(tmp_path / "missing")])
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)

    class _FakeSettings:
        gemini_api_key = "AIzaLegacy"

    with patch.object(aks_mod, "get_settings", return_value=_FakeSettings()):
        pool = aks_mod.init_key_pool()
    assert pool._keys == ["AIzaLegacy"]


def test_init_pool_raises_when_no_source_provides_keys(monkeypatch, tmp_path: Path):
    _patch_paths(monkeypatch, [str(tmp_path / "missing")])
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)

    class _FakeSettings:
        gemini_api_key = "   "  # whitespace-only — treated as absent

    with patch.object(aks_mod, "get_settings", return_value=_FakeSettings()):
        with pytest.raises(ValueError, match="No Gemini API keys found"):
            aks_mod.init_key_pool()


# ---------------------------------------------------------------------------
# GeminiKeyPool boundary checks
# ---------------------------------------------------------------------------


def test_pool_rejects_empty_key_list():
    with pytest.raises(ValueError, match="at least one API key"):
        GeminiKeyPool([])


def test_pool_rejects_more_than_max_keys():
    with pytest.raises(ValueError, match="maximum of 10"):
        GeminiKeyPool([f"k{i}" for i in range(11)])


def test_pool_rejects_blank_key():
    with pytest.raises(ValueError, match="cannot be empty"):
        GeminiKeyPool(["   "])
