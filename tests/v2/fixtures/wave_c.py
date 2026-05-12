"""WAVE-C Phase 1a fixture helpers.

Heavy-lift implementation behind the conftest fixtures so the conftest stays
small and additive (avoids merge conflicts with WAVE-A/B).

Surface:

* ``StubGeminiPool``        — deterministic stand-in for
  ``website.features.api_key_switching.GeminiKeyPool``. Records every call,
  serves predefined embedding vectors / generate-content stubs, supports
  forced 429 injection and (key_index, model) cooldown injection.
* ``SourceFixturePathResolver`` — locates recorded HTTP cassettes under
  ``tests/fixtures/source_ingest/<source>/<scenario>.json``.
* ``GraphJsonValidator``    — JSON-schema-style validator for
  ``website/features/knowledge_graph/content/graph.json``.
* ``build_random_digraph`` — seeded NetworkX Erdős-Rényi factory.

Anti-pattern guard: nothing in this module touches Supabase, auth, billing,
SQL function bodies, or any protected infra knob. The stub pool is a chaos
testing surface only — production code paths use the real ``GeminiKeyPool``.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

# 10 source-ingest plug-ins per WAVE-C discovery doc (registry-completeness gate).
SOURCE_INGEST_NAMES: tuple[str, ...] = (
    "arxiv",
    "github",
    "hackernews",
    "linkedin",
    "newsletter",
    "podcast",
    "reddit",
    "twitter",
    "web",
    "youtube",
)

# Default Gemini embedding dim for text-embedding-004 = 768. We default the
# stub to 1536 matching the spec ask, but tests can override per-call.
_DEFAULT_EMBED_DIM = 1536

_FIXTURES_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "source_ingest"


# ---------------------------------------------------------------------------
# Mock Gemini pool
# ---------------------------------------------------------------------------


class _Stub429Error(Exception):
    """Stand-in for the google-genai 429 / RESOURCE_EXHAUSTED exception.

    The real GeminiKeyPool detects rate limits via ``_is_rate_limited(exc)``
    which sniffs the string ``"429"`` / ``"RESOURCE_EXHAUSTED"`` in str(exc);
    we mirror that surface so any caller that bubbles the exception up sees
    the same shape. Tests assert on the `is_429` flag.
    """

    is_429 = True

    def __init__(self, message: str = "429 RESOURCE_EXHAUSTED") -> None:
        super().__init__(message)


@dataclass
class StubCall:
    """Single recorded interaction with the stub pool."""

    method: str  # "generate_content" | "embed_content" | "embed_content_safe"
    key_index: int
    model: str
    content_hash: str
    label: str = ""
    raised_429: bool = False


@dataclass
class StubGeminiPool:
    """Deterministic stand-in for ``GeminiKeyPool``.

    Behaviour matrix:

    * ``embed_content`` / ``embed_content_safe`` — return a deterministic
      vector derived from the SHA-256 of the input content (so the same
      input always returns the same vector across test runs). Vector length
      = ``embedding_dim``. ``embed_content_safe`` swallows exceptions and
      returns ``None`` on failure (mirrors the real pool).
    * ``generate_content`` (async) — returns a tuple
      ``(response_obj, model_used, key_index)`` where ``response_obj`` is a
      simple dataclass with a ``text`` attribute. Default response text is
      the JSON-serialised stub, but tests can override per-call.
    * ``next_attempt(model)`` — returns the first non-cooled-down
      ``Attempt`` from the deterministic chain.
    * ``force_429_after`` — if set to N, the (N+1)-th call to any generate
      method raises ``_Stub429Error`` exactly once before reverting.
    * ``inject_cooldown(key_index, model, until_seconds)`` — forces the
      slot into cooldown until monotonic ``until_seconds``. ``next_attempt``
      / ``embed_content`` skip cooled-down slots.
    """

    embedding_dim: int = _DEFAULT_EMBED_DIM
    keys: list[str] = field(default_factory=lambda: ["stub-key-0", "stub-key-1"])
    models: list[str] = field(
        default_factory=lambda: ["gemini-2.5-flash", "gemini-2.5-flash-lite"]
    )
    force_429_after: int | None = None
    generate_response_text: str = '{"summary": "stub"}'

    calls: list[StubCall] = field(default_factory=list)
    cooldowns: dict[tuple[int, str], float] = field(default_factory=dict)

    _gen_calls_seen: int = 0

    # ----- helpers ----------------------------------------------------

    @staticmethod
    def _hash(content: Any) -> str:
        try:
            blob = (
                content
                if isinstance(content, (bytes, bytearray))
                else str(content).encode("utf-8")
            )
        except Exception:
            blob = repr(content).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:16]

    def _vector(self, content_hash: str) -> list[float]:
        # Deterministic vector: stretch the 16-hex-char hash across embedding_dim
        # by repeating + mod-scaling to [-1.0, 1.0). Not L2-normalised — callers
        # that need a unit vector should normalise. (kg_features.embeddings has
        # _normalize_embedding for this.)
        seed_int = int(content_hash, 16)
        out = []
        for i in range(self.embedding_dim):
            v = ((seed_int >> (i % 60)) & 0xFFFF) / 0xFFFF * 2.0 - 1.0
            out.append(v)
        return out

    def _next_free_slot(self, model: str) -> tuple[int, str] | None:
        # purge expired
        now = time.monotonic()
        self.cooldowns = {
            slot: exp for slot, exp in self.cooldowns.items() if exp > now
        }
        for ki in range(len(self.keys)):
            if (ki, model) not in self.cooldowns:
                return (ki, model)
        # All keys cooled for this model; downgrade to next model
        for fallback in self.models:
            if fallback == model:
                continue
            for ki in range(len(self.keys)):
                if (ki, fallback) not in self.cooldowns:
                    return (ki, fallback)
        return None

    # ----- public API mirrors --------------------------------------

    def inject_cooldown(
        self,
        *,
        key_index: int,
        model: str,
        cooldown_seconds: float = 60.0,
    ) -> None:
        self.cooldowns[(key_index, model)] = time.monotonic() + cooldown_seconds

    def next_attempt(self, model: str):
        slot = self._next_free_slot(model)
        if slot is None:
            raise RuntimeError(
                "All configured Gemini key/model slots are on cooldown"
            )
        ki, m = slot
        # Lazy import to avoid the dataclass / pool import-time cost in tests
        from website.features.api_key_switching.key_pool import Attempt

        return Attempt(key=self.keys[ki], role="free", model=m)

    async def generate_content(
        self,
        contents,
        *,
        config: dict | None = None,
        starting_model: str | None = None,
        label: str = "",
        telemetry_sink: list | None = None,
    ):
        self._gen_calls_seen += 1
        model = starting_model or self.models[0]
        slot = self._next_free_slot(model)
        if slot is None:
            raise RuntimeError(
                "All configured Gemini key/model slots are on cooldown"
            )
        ki, used_model = slot
        chash = self._hash(contents)
        if (
            self.force_429_after is not None
            and self._gen_calls_seen == self.force_429_after + 1
        ):
            self.calls.append(
                StubCall(
                    method="generate_content",
                    key_index=ki,
                    model=used_model,
                    content_hash=chash,
                    label=label,
                    raised_429=True,
                )
            )
            raise _Stub429Error()
        self.calls.append(
            StubCall(
                method="generate_content",
                key_index=ki,
                model=used_model,
                content_hash=chash,
                label=label,
            )
        )

        @dataclass
        class _StubResponse:
            text: str

        if telemetry_sink is not None:
            telemetry_sink.append(
                {
                    "label": label,
                    "model_used": used_model,
                    "starting_model": starting_model or used_model,
                    "key_index": ki,
                    "fallback_reason": None,
                    "failed_attempts": [],
                }
            )
        return _StubResponse(text=self.generate_response_text), used_model, ki

    async def generate_structured(
        self,
        *,
        prompt: str,
        response_schema: dict,
        model_preference: str = "flash-lite",
        label: str = "",
    ):
        starting_model = {
            "flash-lite": "gemini-2.5-flash-lite",
            "flash": "gemini-2.5-flash",
            "pro": "gemini-2.5-pro",
        }.get(model_preference, "gemini-2.5-flash-lite")
        resp, _m, _ki = await self.generate_content(
            contents=prompt,
            config={"response_schema": response_schema},
            starting_model=starting_model,
            label=label or "generate_structured",
        )
        try:
            return json.loads(resp.text)
        except json.JSONDecodeError:
            return resp.text

    def embed_content(self, contents, *, config: dict | None = None):
        slot = self._next_free_slot("text-embedding-004")
        if slot is None:
            raise RuntimeError(
                "All configured Gemini embedding key slots are on cooldown"
            )
        ki, model = slot
        chash = self._hash(contents)
        if (
            self.force_429_after is not None
            and len(self.calls) == self.force_429_after
        ):
            self.calls.append(
                StubCall(
                    method="embed_content",
                    key_index=ki,
                    model=model,
                    content_hash=chash,
                    raised_429=True,
                )
            )
            raise _Stub429Error()
        self.calls.append(
            StubCall(
                method="embed_content",
                key_index=ki,
                model=model,
                content_hash=chash,
            )
        )

        @dataclass
        class _EmbResponse:
            embeddings: list[Any]

        @dataclass
        class _EmbeddingObj:
            values: list[float]

        return _EmbResponse(embeddings=[_EmbeddingObj(values=self._vector(chash))])

    def embed_content_safe(self, contents, *, config: dict | None = None):
        try:
            return self.embed_content(contents, config=config)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Source-ingest fixture file resolution
# ---------------------------------------------------------------------------


class SourceFixturePathResolver:
    """Resolve recorded HTTP fixture files for the 10 source ingestors.

    Layout (Phase 1a creates the directories with .gitkeep; subsequent
    sub-agents fill in actual JSON cassettes per source):

        tests/fixtures/source_ingest/<source>/<scenario>.json

    Where ``<scenario>`` is typically ``"happy"`` or ``"thin"``.
    """

    root: Path = _FIXTURES_ROOT

    @classmethod
    def path_for(cls, *, source: str, scenario: str = "happy") -> Path:
        if source not in SOURCE_INGEST_NAMES:
            raise ValueError(
                f"Unknown source {source!r}; valid sources: "
                f"{sorted(SOURCE_INGEST_NAMES)}"
            )
        return cls.root / source / f"{scenario}.json"

    @classmethod
    def load(cls, *, source: str, scenario: str = "happy") -> dict:
        path = cls.path_for(source=source, scenario=scenario)
        if not path.exists():
            # Phase 1a: directories exist but cassettes are not yet recorded.
            # Surface a clear FileNotFoundError so downstream sub-agents know
            # which file they need to provide.
            raise FileNotFoundError(
                f"No recorded fixture at {path}. Phase 1b sub-agents must "
                f"record fixtures per source before tests run."
            )
        return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# graph.json schema validation
# ---------------------------------------------------------------------------


class GraphJsonValidator:
    """Minimal JSON-schema-style validator for ``content/graph.json``.

    The actual file uses the shape::

        {
            "nodes": [{"id": str, "title": str, "tags": [str], ...}, ...],
            "links": [{"source": str, "target": str, ...}, ...],
            "version": int  # optional
        }

    We avoid a hard dep on jsonschema-the-library — the schema is small and
    stable, and a focused validator gives sharper error messages.
    """

    @staticmethod
    def validate(payload: dict) -> None:
        if not isinstance(payload, dict):
            raise ValueError(
                f"graph.json root must be a dict; got {type(payload).__name__}"
            )
        for required in ("nodes", "links"):
            if required not in payload:
                raise ValueError(f"graph.json missing required key {required!r}")
            if not isinstance(payload[required], list):
                raise ValueError(
                    f"graph.json key {required!r} must be a list; "
                    f"got {type(payload[required]).__name__}"
                )
        node_ids = set()
        for i, node in enumerate(payload["nodes"]):
            if not isinstance(node, dict):
                raise ValueError(f"graph.json node[{i}] must be a dict")
            if "id" not in node or not isinstance(node["id"], str):
                raise ValueError(f"graph.json node[{i}] missing string id")
            if node["id"] in node_ids:
                raise ValueError(
                    f"graph.json duplicate node id {node['id']!r} at index {i}"
                )
            node_ids.add(node["id"])
        for i, link in enumerate(payload["links"]):
            if not isinstance(link, dict):
                raise ValueError(f"graph.json link[{i}] must be a dict")
            for key in ("source", "target"):
                if key not in link or not isinstance(link[key], str):
                    raise ValueError(
                        f"graph.json link[{i}] missing string {key!r}"
                    )
                if link[key] not in node_ids:
                    raise ValueError(
                        f"graph.json link[{i}] {key}={link[key]!r} "
                        f"references unknown node"
                    )


# ---------------------------------------------------------------------------
# NetworkX deterministic graph factory
# ---------------------------------------------------------------------------


def build_random_digraph(
    *,
    n: int = 100,
    p: float = 0.05,
    seed: int = 42,
    weighted: bool = True,
):
    """Seeded Erdős-Rényi directed graph for analytics tests.

    Returns ``networkx.DiGraph`` with N nodes and edges drawn at probability
    ``p``. When ``weighted=True`` each edge gets a deterministic weight
    derived from a fresh ``random.Random(seed)`` so test assertions are
    reproducible across runs and Python versions.
    """
    import random as _random

    import networkx as nx

    if n < 1:
        raise ValueError("n must be >= 1")
    if not (0.0 <= p <= 1.0):
        raise ValueError("p must be in [0, 1]")

    g = nx.gnp_random_graph(n=n, p=p, seed=seed, directed=True)
    g = nx.DiGraph(g)
    if weighted:
        rng = _random.Random(seed)
        for u, v in g.edges():
            g[u][v]["weight"] = round(rng.random(), 6)
    return g


__all__ = [
    "SOURCE_INGEST_NAMES",
    "StubGeminiPool",
    "SourceFixturePathResolver",
    "GraphJsonValidator",
    "build_random_digraph",
]
