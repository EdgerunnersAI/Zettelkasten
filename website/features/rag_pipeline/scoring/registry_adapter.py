"""DB-backed scorer registry adapter for DB v2.

LISTEN must use a direct Postgres connection, not pgbouncer transaction
pooling. The adapter also polls periodically so config changes still arrive
when LISTEN is unavailable during local development.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any

from website.core.supabase_v2.client import get_v2_client, get_v2_config, get_v2_database_url
from website.core.supabase_v2.models import ScorerConfig

logger = logging.getLogger(__name__)


@dataclass
class RegistryAdapter:
    environment: str | None = None
    poll_interval_seconds: int = 60
    _configs: dict[str, ScorerConfig] = field(default_factory=dict, init=False)
    _listen_task: asyncio.Task | None = field(default=None, init=False)
    _poll_task: asyncio.Task | None = field(default=None, init=False)
    _stopped: asyncio.Event = field(default_factory=asyncio.Event, init=False)

    async def start(self) -> None:
        await self.refresh()
        self._poll_task = asyncio.create_task(self._poll_loop())
        self._listen_task = asyncio.create_task(self._listen_loop())

    async def stop(self) -> None:
        self._stopped.set()
        for task in (self._listen_task, self._poll_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    def get_weight(self, scorer_name: str, default: float = 0.0) -> float:
        cfg = self._configs.get(scorer_name)
        if not cfg or not cfg.enabled:
            return default
        return cfg.weight

    def get_params(self, scorer_name: str) -> dict[str, Any]:
        cfg = self._configs.get(scorer_name)
        return dict(cfg.params) if cfg else {}

    async def refresh(self) -> None:
        env = self.environment or get_v2_config().environment
        response = (
            get_v2_client()
            .schema("rag")
            .table("retrieval_pipeline_config")
            .select("environment, scorer_name, version_id, enabled, weight, retrieval_scorer_version(params)")
            .eq("environment", env)
            .execute()
        )
        next_configs: dict[str, ScorerConfig] = {}
        for row in response.data or []:
            version = row.get("retrieval_scorer_version") or {}
            row["params"] = version.get("params") or row.get("params") or {}
            next_configs[row["scorer_name"]] = ScorerConfig(**row)
        self._configs = next_configs

    async def _poll_loop(self) -> None:
        while not self._stopped.is_set():
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.poll_interval_seconds)
            except asyncio.TimeoutError:
                await self.refresh()

    async def _listen_loop(self) -> None:
        try:
            import asyncpg
        except ImportError:
            logger.info("asyncpg unavailable; scorer registry will use polling only")
            return

        while not self._stopped.is_set():
            conn = None
            try:
                conn = await asyncpg.connect(get_v2_database_url(listen=True))

                def _on_notify(_conn, _pid, _channel, payload):
                    if not self.environment or payload == self.environment:
                        asyncio.create_task(self.refresh())

                await conn.add_listener("retrieval_pipeline_config_change", _on_notify)
                await self._stopped.wait()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("registry LISTEN failed; retrying after poll interval")
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stopped.wait(), timeout=self.poll_interval_seconds)
            finally:
                if conn is not None:
                    await conn.close()
