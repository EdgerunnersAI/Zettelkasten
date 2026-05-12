"""Centralized Gemini API key pool with multi-key rotation and model fallback."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import time

import httpx
from google import genai
from google.genai.errors import ClientError

logger = logging.getLogger(__name__)

_MAX_KEYS = 10
_GENERATIVE_MODEL_CHAIN = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]
_EMBEDDING_MODEL = "gemini-embedding-001"


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return max(minimum, default)


def _bool_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _max_retries() -> int:
    return _int_env("GEMINI_MAX_RETRIES", 3)


def _rate_limit_cooldown_secs() -> int:
    """Legacy flat-cooldown env. Kept for callers/tests that still pass an int.
    Production path now uses _cooldown_for_attempt() which scales with attempt
    count and respects Retry-After. Default 10s acts as the base cooldown
    when neither attempt count nor Retry-After is available."""
    return _int_env("GEMINI_RATE_LIMIT_COOLDOWN_SECS", 10)


def _cooldown_base_s() -> float:
    return float(_int_env("GEMINI_COOLDOWN_BASE_S", 1))


def _cooldown_max_s() -> float:
    return float(_int_env("GEMINI_COOLDOWN_MAX_S", 30))


def _retry_after_from_exc(exc: Exception) -> float | None:
    """Try to extract a Retry-After hint from a Gemini SDK error.
    google-genai's ClientError exposes the upstream HTTP response on either
    .response (httpx.Response) or .response_data (parsed JSON). When Google
    sends 429, the JSON body usually contains a `retryDelay` field like
    "23s" inside details[].retryInfo. We honor it as the floor so we don't
    bounce 429 instantly on a per-minute limit. Returns seconds, or None."""
    # 1. httpx Response with Retry-After header
    resp = getattr(exc, "response", None)
    if resp is not None:
        ra = None
        try:
            ra = resp.headers.get("Retry-After") if hasattr(resp, "headers") else None
        except Exception:  # noqa: BLE001
            ra = None
        if ra:
            try:
                return float(ra)
            except (TypeError, ValueError):
                pass
    # 2. Gemini structured error body — look for retryDelay in details
    body = getattr(exc, "response_data", None) or getattr(exc, "details", None)
    if isinstance(body, dict):
        for d in (body.get("error", {}).get("details") or []):
            delay = (d or {}).get("retryDelay")
            if isinstance(delay, str) and delay.endswith("s"):
                try:
                    return float(delay[:-1])
                except ValueError:
                    pass
    # 3. Fallback: scrape the str() form for a "retryDelay: \"NNs\"" hint
    text = str(exc)
    import re
    m = re.search(r'retryDelay[\'"\s:]*\s*[\'"]?(\d+(?:\.\d+)?)s', text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _cooldown_for_attempt(
    attempt: int, exc: Exception | None = None
) -> float:
    """Exponential backoff with jitter, clamped to [base, max].
    Honors Retry-After / retryDelay from the upstream error if present.
    `attempt` is 1-indexed (1 = first retry).
    """
    base = _cooldown_base_s()
    cap = _cooldown_max_s()
    backoff = min(cap, base * (2 ** max(0, attempt - 1)))
    # 25% jitter so concurrent workers don't synchronize on the same wakeup.
    import random
    jittered = backoff * (1.0 + random.uniform(-0.25, 0.25))
    if exc is not None:
        hint = _retry_after_from_exc(exc)
        if hint is not None:
            # Use the upstream hint as a floor — never sleep less than it
            # asked, but also don't exceed our cap (safety against rogue
            # 600s hints during a quota outage).
            jittered = max(jittered, min(cap, hint))
    return max(base, jittered)


def _fail_fast_on_all_cooldowns() -> bool:
    return _bool_env("GEMINI_FAIL_FAST_ON_ALL_COOLDOWNS")


def key_role_filter() -> str | None:
    value = os.environ.get("GEMINI_KEY_ROLE_FILTER", "").strip().lower()
    if value in {"free", "billing"}:
        return value
    return None


# This is a decision because: iter-06 spec §11 requires deferring the
# paid/billing key until both free keys are on cooldown, but operators may
# load keys from a plain api_env file (no `role=` tokens). We expose
# RAG_BILLING_KEY_INDEX as an opt-in env override so a specific index is
# tagged billing-tier at pool init without rewriting the secret file.
# Explicit `role=billing` in api_env still wins (we only promote keys whose
# parsed role is "free"); without the env var, behavior is unchanged.
def _billing_key_index_override() -> int | None:
    raw = os.environ.get("RAG_BILLING_KEY_INDEX", "").strip()
    if not raw:
        return None
    try:
        idx = int(raw)
    except ValueError:
        return None
    if idx < 0:
        return None
    return idx


def parse_api_env_line(line: str) -> tuple[str, str]:
    """Parse one api_env line into (key, role)."""
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


def normalize_api_keys(
    api_keys: list[str] | list[tuple[str, str]],
) -> list[tuple[str, str]]:
    normalized: list[tuple[str, str]] = []
    for entry in api_keys:
        if isinstance(entry, tuple):
            key, role = entry
        else:
            key, role = entry, "free"

        key = key.strip()
        role = role.strip().lower()
        if not key:
            raise ValueError("Gemini API keys cannot be empty")
        if role not in {"free", "billing"}:
            raise ValueError(f"invalid Gemini key role '{role}'")
        normalized.append((key, role))

    normalized.sort(key=lambda item: item[1] != "free")
    return normalized


def filter_api_keys_by_role(
    api_keys: list[str] | list[tuple[str, str]],
    role_filter: str | None = None,
) -> list[tuple[str, str]]:
    normalized = normalize_api_keys(api_keys)
    active_filter = role_filter or key_role_filter()
    if active_filter in {"free", "billing"}:
        return [item for item in normalized if item[1] == active_filter]
    return normalized


def _is_rate_limited(exc: Exception) -> bool:
    if isinstance(exc, ClientError) and getattr(exc, "code", None) == 429:
        return True
    return "429" in str(exc) and "RESOURCE_EXHAUSTED" in str(exc)


def _is_retryable(exc: Exception) -> bool:
    if _is_rate_limited(exc):
        return True
    if isinstance(exc, ClientError) and getattr(exc, "code", None) in (503, 504):
        return True
    exc_str = str(exc)
    if "DEADLINE_EXCEEDED" in exc_str or "504" in exc_str:
        return True
    if "UNAVAILABLE" in exc_str or "503" in exc_str:
        return True
    return False


def _send_slack_alert(message: str) -> None:
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        return
    try:
        httpx.post(webhook_url, json={"text": message}, timeout=5.0)
    except Exception:
        logger.debug("Slack alert failed (non-critical)", exc_info=True)


def _load_keys_from_file(path: str) -> list[str | tuple[str, str]]:
    """Read API keys from an api_env file."""
    try:
        with open(path, encoding="utf-8") as f:
            keys: list[str | tuple[str, str]] = []
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                key, role = parse_api_env_line(stripped)
                if "role=" in stripped:
                    keys.append((key, role))
                else:
                    keys.append(key)
            return keys
    except FileNotFoundError:
        return []


def candidate_api_env_paths(anchor: Path | None = None) -> list[Path]:
    """Return likely api_env locations for both normal repos and git worktrees."""
    current = (anchor or Path(__file__)).resolve()
    feature_dir = current.parent
    project_root = feature_dir.parent.parent.parent

    candidates = [
        feature_dir / "api_env",
        project_root / "api_env",
    ]

    main_checkout_root: Path | None = None
    for parent in current.parents:
        if parent.name == ".worktrees":
            main_checkout_root = parent.parent
            break

    if main_checkout_root is not None:
        candidates.extend(
            [
                main_checkout_root / "website" / "features" / "api_key_switching" / "api_env",
                main_checkout_root / "api_env",
            ]
        )

    candidates.append(Path("/etc/secrets/api_env"))

    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        ordered.append(path)
    return ordered


@dataclass(frozen=True)
class Attempt:
    key: str
    role: str
    model: str


class GeminiKeyPool:
    """Pool of Gemini API clients with automatic key/model rotation."""

    def __init__(self, api_keys: list[str] | list[tuple[str, str]]) -> None:
        if not api_keys:
            raise ValueError("GeminiKeyPool requires at least one API key")
        if len(api_keys) > _MAX_KEYS:
            raise ValueError(f"GeminiKeyPool supports a maximum of {_MAX_KEYS} keys")

        normalized = normalize_api_keys(api_keys)
        self._keys = [key for key, _role in normalized]
        self._key_roles = [role for _key, role in normalized]

        # Apply RAG_BILLING_KEY_INDEX override only when the caller hasn't
        # already tagged any key as billing in api_env — explicit caller
        # intent always wins over the env var.
        billing_override = _billing_key_index_override()
        any_explicit_billing = any(role == "billing" for role in self._key_roles)
        if (
            billing_override is not None
            and not any_explicit_billing
            and billing_override < len(self._key_roles)
        ):
            self._key_roles[billing_override] = "billing"
        self._clients: dict[int, genai.Client] = {}
        self._cooldowns: dict[tuple[int, str], float] = {}
        self._next_gen_key = 0
        self._next_emb_key = 0

    def _get_client(self, key_index: int) -> genai.Client:
        if key_index not in self._clients:
            # iter-03 §B (2026-04-29): bound the underlying httpx connection
            # pool. Without limits the pool grows to ~30 MB of buffered
            # response state per client per worker, and with up to 10 keys
            # cached in self._clients that's ~300 MB of pure connection
            # residual. limits below were verified against google-genai 1.65
            # (HttpOptions.client_args / async_client_args spread directly
            # into httpx.Client kwargs at _api_client.py:736/740). Safe
            # values per SDK research: max_connections >= 4 leaves headroom
            # for fan-out retries (key-pool tries next key on rate limit).
            import httpx
            from google.genai.types import HttpOptions

            _pool_limits = httpx.Limits(
                max_keepalive_connections=5,
                max_connections=10,
                keepalive_expiry=30.0,
            )
            self._clients[key_index] = genai.Client(
                api_key=self._keys[key_index],
                # 180s upper bound. The previous 60s value caused gemini-2.5-pro
                # to abort on long high-quality synthesis queries (multi-hop,
                # thematic, two-fact extraction) — the user-facing symptom was
                # the chat bubble showing the SDK's stringified timeout
                # ("network error") and a Retry button. 180s covers the slowest
                # observed legitimate response while still bounding runaway.
                http_options=HttpOptions(
                    timeout=180_000,
                    client_args={"limits": _pool_limits},
                    async_client_args={"limits": _pool_limits},
                ),
            )
        return self._clients[key_index]

    def _mark_cooldown(
        self,
        key_index: int,
        model: str,
        attempt: int = 1,
        exc: Exception | None = None,
    ) -> float:
        """Mark (key, model) on cooldown using exp-backoff + Retry-After hint.
        Returns the cooldown seconds applied so the caller can log/telemetry."""
        cooldown_s = _cooldown_for_attempt(attempt, exc=exc)
        self._cooldowns[(key_index, model)] = time.monotonic() + cooldown_s
        return cooldown_s

    def _purge_expired(self) -> None:
        now = time.monotonic()
        self._cooldowns = {
            slot: exp for slot, exp in self._cooldowns.items() if exp > now
        }

    def count_active_keys(self) -> int:
        """Keys with zero current cooldowns — WM-11 canary signal.

        Conservative: a key with ANY model-slot in cooldown is counted as
        unavailable. Heartbeat publishes this in the healthchecks.io ping body
        so Slack alerts can surface "alive but keys exhausted" degraded state.
        """
        self._purge_expired()
        cooled_keys = {ki for (ki, _model) in self._cooldowns.keys()}
        return len(self._keys) - len(cooled_keys)

    def _role_for_key(self, key_index: int) -> str:
        return self._key_roles[key_index]

    def _ordered_key_indices(self, start_index: int) -> list[int]:
        n = len(self._keys)
        rotated = [(start_index + i) % n for i in range(n)]
        free = [index for index in rotated if self._role_for_key(index) == "free"]
        billing = [index for index in rotated if self._role_for_key(index) == "billing"]
        return free + billing

    def next_attempt(self, model: str) -> Attempt:
        chain = self._build_attempt_chain(starting_model=model)
        if not chain:
            raise RuntimeError("All configured Gemini key/model slots are on cooldown")
        key_index, attempt_model = chain[0]
        return Attempt(
            key=self._keys[key_index],
            role=self._role_for_key(key_index),
            model=attempt_model,
        )

    def _log_quota_exhausted(
        self,
        *,
        model: str,
        current_key_role: str,
        next_key_role: str | None,
    ) -> None:
        if current_key_role == "free" and next_key_role == "billing":
            logger.warning("quota_exhausted_event model=%s escalating_to=billing", model)

    def _build_attempt_chain(
        self,
        starting_model: str | None = None,
    ) -> list[tuple[int, str]]:
        self._purge_expired()
        models = (
            [starting_model] + [m for m in _GENERATIVE_MODEL_CHAIN if m != starting_model]
            if starting_model
            else list(_GENERATIVE_MODEL_CHAIN)
        )
        key_indices = self._ordered_key_indices(self._next_gen_key)
        full_chain = [(ki, model) for model in models for ki in key_indices]
        filtered = [slot for slot in full_chain if slot not in self._cooldowns]

        if not filtered:
            if _fail_fast_on_all_cooldowns():
                logger.warning("All key/model slots on cooldown; fail-fast enabled")
                return []
            logger.warning("All key/model slots on cooldown; retrying full chain")
            return full_chain

        return filtered

    def _build_embedding_chain(self) -> list[tuple[int, str]]:
        self._purge_expired()
        key_indices = self._ordered_key_indices(self._next_emb_key)
        full_chain = [(ki, _EMBEDDING_MODEL) for ki in key_indices]
        filtered = [slot for slot in full_chain if slot not in self._cooldowns]

        if not filtered:
            if _fail_fast_on_all_cooldowns():
                logger.warning("All embedding key slots on cooldown; fail-fast enabled")
                return []
            logger.warning("All embedding key slots on cooldown; retrying full chain")
            return full_chain

        return filtered

    async def generate_content(
        self,
        contents,
        *,
        config: dict | None = None,
        starting_model: str | None = None,
        label: str = "",
        telemetry_sink: list | None = None,
    ):
        """Call Gemini with key+model rotation.

        ``telemetry_sink`` is an optional mutable list that callers pass in when
        they want per-call attempt trace. On success, one dict is appended:
        ``{"label": ..., "model_used": m, "starting_model": s,
           "fallback_reason": r, "failed_attempts": [...]}``. ``fallback_reason``
        is ``None`` when the first-tier model succeeded; otherwise a short
        string like ``"gemini-2.5-flash-timeout"`` or ``"gemini-2.5-pro-rate-limited"``
        synthesized from the first failed attempt. This is the plumbing that
        lets ``summary.json.metadata.model_used`` surface silent pro→flash-lite
        downgrades without re-reading run.log.
        """
        chain = self._build_attempt_chain(starting_model=starting_model)
        last_exc: Exception | None = None
        retries = 0
        max_retries = _max_retries()
        cooldown_secs = _rate_limit_cooldown_secs()
        attempts: list[dict] = []
        effective_starting = starting_model or (chain[0][1] if chain else None)

        if not chain:
            raise RuntimeError("All configured Gemini key/model slots are on cooldown")

        for position, (key_index, model) in enumerate(chain):
            try:
                client = self._get_client(key_index)
                response = await client.aio.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config or {},
                )
                self._next_gen_key = (key_index + 1) % len(self._keys)
                if telemetry_sink is not None:
                    fallback_reason: str | None = None
                    if attempts:
                        first = attempts[0]
                        fallback_reason = f"{first['model']}-{first['reason']}"
                    telemetry_sink.append({
                        "label": label,
                        "model_used": model,
                        "starting_model": effective_starting,
                        "key_index": key_index,
                        "fallback_reason": fallback_reason,
                        "failed_attempts": list(attempts),
                    })
                return response, model, key_index
            except Exception as exc:
                last_exc = exc
                if _is_retryable(exc):
                    retries += 1
                    cooldown_secs = self._mark_cooldown(
                        key_index, model, attempt=retries, exc=exc
                    )
                    next_key_role = None
                    if position + 1 < len(chain):
                        next_key_role = self._role_for_key(chain[position + 1][0])
                    self._log_quota_exhausted(
                        model=model,
                        current_key_role=self._role_for_key(key_index),
                        next_key_role=next_key_role,
                    )
                    if _is_rate_limited(exc):
                        reason = "rate-limited"
                    elif "503" in str(exc) or "UNAVAILABLE" in str(exc):
                        reason = "unavailable"
                    else:
                        reason = "timeout"
                    attempts.append({"model": model, "key_index": key_index, "reason": reason})
                    logger.warning(
                        "%s %s on key[%d]/%s; retry %d/%d, cooldown %.1fs",
                        label or "Gemini",
                        reason,
                        key_index,
                        model,
                        retries,
                        max_retries,
                        cooldown_secs,
                    )
                    if retries >= max_retries:
                        logger.error("%s exhausted %d retries; giving up", label or "Gemini", max_retries)
                        _send_slack_alert(
                            f":warning: *Gemini API failure* - `{label or 'generate_content'}` "
                            f"exhausted {max_retries} retries. "
                            f"Last error: `{reason}` on key[{key_index}]/{model}. "
                            f"Exception: `{exc}`"
                        )
                        raise
                    continue
                raise

        _send_slack_alert(
            f":warning: *Gemini API failure* - `{label or 'generate_content'}` "
            f"all key/model slots exhausted after {retries} retries. "
            f"Last error: `{last_exc}`"
        )
        raise last_exc  # type: ignore[misc]

    async def generate_structured(
        self,
        *,
        prompt: str,
        response_schema: dict,
        model_preference: str = "flash-lite",
        label: str = "",
    ):
        """Structured-JSON variant of generate_content with strict response_schema.

        Wraps generate_content with response_mime_type=application/json + the
        caller-supplied schema, then parses the response text into a dict.

        iter-03 fix: this method was referenced by website/features/rag_pipeline/
        query/metadata.py:_a_pass and ingest/metadata_enricher.py:_extract_entities
        but never existed on KeyPool, causing both call sites to log
        "'GeminiKeyPool' object has no attribute 'generate_structured'" and fall
        back to slower C-pass paths. The retry pressure compounded with stage-2
        rerank memory to OOM-kill workers under realistic load.

        Returns the parsed JSON object (dict). Returns the raw text string when
        JSON parsing fails so the caller can decide. Returns {} on empty
        response.
        """
        starting_model = {
            "flash-lite": "gemini-2.5-flash-lite",
            "flash": "gemini-2.5-flash",
            "pro": "gemini-2.5-pro",
        }.get(model_preference, "gemini-2.5-flash-lite")
        config = {
            "response_mime_type": "application/json",
            "response_schema": response_schema,
        }
        response, _model_used, _key_index = await self.generate_content(
            contents=prompt,
            config=config,
            starting_model=starting_model,
            label=label or "generate_structured",
        )
        text = getattr(response, "text", None) or ""
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def embed_content(self, contents, *, config: dict | None = None):
        chain = self._build_embedding_chain()
        last_exc: Exception | None = None
        cooldown_secs = _rate_limit_cooldown_secs()

        if not chain:
            raise RuntimeError("All configured Gemini embedding key slots are on cooldown")

        for position, (key_index, model) in enumerate(chain):
            try:
                client = self._get_client(key_index)
                response = client.models.embed_content(
                    model=model,
                    contents=contents,
                    config=config or {},
                )
                self._next_emb_key = (key_index + 1) % len(self._keys)
                return response
            except Exception as exc:
                last_exc = exc
                if _is_rate_limited(exc):
                    # `position` is 0-indexed position in the attempt chain;
                    # +1 gives the 1-indexed attempt number for exp-backoff.
                    cooldown_secs = self._mark_cooldown(
                        key_index, model, attempt=position + 1, exc=exc
                    )
                    next_key_role = None
                    if position + 1 < len(chain):
                        next_key_role = self._role_for_key(chain[position + 1][0])
                    self._log_quota_exhausted(
                        model=model,
                        current_key_role=self._role_for_key(key_index),
                        next_key_role=next_key_role,
                    )
                    logger.warning(
                        "Embedding rate-limited on key[%d]; cooldown %.1fs, trying next",
                        key_index,
                        cooldown_secs,
                    )
                    continue
                raise

        raise last_exc  # type: ignore[misc]

    def embed_content_safe(self, contents, *, config: dict | None = None):
        try:
            return self.embed_content(contents, config=config)
        except Exception as exc:
            logger.error("All embedding keys exhausted: %s", exc)
            return None
