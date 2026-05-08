"""Load and validate summarization engine config."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class EngineMeta(BaseModel):
    version: str = "2.0.0"
    default_tier: str = "tiered"


class GeminiBatchConfig(BaseModel):
    enabled: bool = True
    threshold: int = 50
    poll_interval_sec: int = 60
    max_turnaround_hours: int = 24


class GeminiConfig(BaseModel):
    reuse_existing_pool: bool = True
    model_chains: dict[str, list[str]] = Field(default_factory=dict)
    phase_tiers: dict[str, str] = Field(default_factory=dict)
    temperature: float = 0.3
    max_output_tokens: int = 8192
    response_mime_type: str = "application/json"
    batch_api: GeminiBatchConfig = Field(default_factory=GeminiBatchConfig)


class ChainOfDensityConfig(BaseModel):
    enabled: bool = True
    iterations: int = 2
    early_stop_if_few_new_entities: int = 2
    pass1_word_target: int = 200


class SelfCheckConfig(BaseModel):
    enabled: bool = True
    max_atomic_claims: int = 12
    patch_threshold: int = 3
    max_patch_rounds: int = 1


class StructuredExtractConfig(BaseModel):
    validation_retries: int = 1
    mini_title_max_chars: int = 60
    brief_summary_max_chars: int = 500
    brief_summary_max_sentences: int = 6
    brief_summary_min_sentences: int = 3
    detailed_summary_max_bullets_per_section: int = 8
    detailed_summary_min_bullets_per_section: int = 1
    tags_min: int = 7
    tags_max: int = 10


class BatchConfig(BaseModel):
    max_concurrency: int = 3
    max_input_size_mb: int = 10
    supported_input_formats: list[str] = Field(default_factory=lambda: ["csv", "json"])
    progress_event_interval: int = 1


class WritersConfig(BaseModel):
    supabase: dict[str, Any] = Field(default_factory=lambda: {"enabled": True})


class LoggingConfig(BaseModel):
    level: str = "INFO"
    per_url_correlation_id: bool = True
    log_token_counts: bool = True


class EngineConfig(BaseModel):
    engine: EngineMeta = Field(default_factory=EngineMeta)
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    chain_of_density: ChainOfDensityConfig = Field(default_factory=ChainOfDensityConfig)
    self_check: SelfCheckConfig = Field(default_factory=SelfCheckConfig)
    structured_extract: StructuredExtractConfig = Field(default_factory=StructuredExtractConfig)
    sources: dict[str, dict[str, Any]] = Field(default_factory=dict)
    batch: BatchConfig = Field(default_factory=BatchConfig)
    writers: WritersConfig = Field(default_factory=WritersConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    rate_limiting: dict[str, str] = Field(default_factory=dict)


_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


@lru_cache(maxsize=1)
def load_config(path: Path | None = None) -> EngineConfig:
    """Load the engine config from config.yaml."""
    config_path = path or _CONFIG_PATH
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return EngineConfig(**raw)


def reset_config_cache() -> None:
    """Clear the config cache for tests."""
    load_config.cache_clear()
