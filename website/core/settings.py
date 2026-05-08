"""Website application settings.

Pydantic BaseSettings layering (env > .env > ops/config.yaml) for the FastAPI
app.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Tuple, Type

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

logger = logging.getLogger(__name__)

# Module-level latch so the Reddit OAuth warning fires exactly once per process.
_reddit_warning_emitted = False

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_DEFAULT_CONFIG_YAML = _PROJECT_ROOT / "ops" / "config.yaml"
_DEFAULT_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """Website configuration loaded from env, .env, and YAML."""

    model_config = SettingsConfigDict(
        env_file=str(_DEFAULT_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    gemini_api_key: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "ZettelkastenWeb/1.0"

    @property
    def reddit_oauth_configured(self) -> bool:
        """True iff both Reddit OAuth credentials are non-empty.

        When False, the Reddit ingestor degrades to the public JSON endpoint
        plus HTML scraping, which often returns thin content behind Reddit's
        anti-bot wall.
        """
        return bool(self.reddit_client_id.strip() and self.reddit_client_secret.strip())

    reddit_comment_depth: int = 10

    data_dir: str = "./data"

    server_port: int = 10000
    """Port the dev-mode uvicorn binds to. Production overrides via env PORT."""

    model_name: str = "gemini-2.5-flash"
    rag_chunks_enabled: bool = True

    log_level: str = "INFO"

    newsletter_domains: list[str] = [
        "substack.com",
        "buttondown.email",
        "beehiiv.com",
        "mailchimp.com",
        "medium.com",
        "stackoverflow.com",
        "stackexchange.com",
        "news.ycombinator.com",
        "dev.to",
        "hackernoon.com",
    ]

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        yaml_settings = YamlConfigSettingsSource(
            settings_cls,
            yaml_file=_DEFAULT_CONFIG_YAML,
        )
        return (init_settings, env_settings, dotenv_settings, yaml_settings)


def _is_production() -> bool:
    """Production is signalled by ENV=production. Anything else is dev-like."""
    return os.environ.get("ENV", "").strip().lower() == "production"


def validate_reddit_credentials(settings: Settings) -> None:
    """Validate Reddit OAuth credentials.

    Behaviour:
      - production AND creds missing → ``RuntimeError`` (hard fail-fast).
      - non-production AND creds missing → one-shot warning.
      - creds present → no-op.

    The warning fires at most once per process via a module-level latch.
    """
    global _reddit_warning_emitted

    if settings.reddit_oauth_configured:
        return

    if _is_production():
        raise RuntimeError(
            "Reddit OAuth credentials are required in production. "
            "Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET, or unset ENV=production."
        )

    if _reddit_warning_emitted:
        return
    logger.warning(
        "Reddit OAuth credentials missing (REDDIT_CLIENT_ID and/or "
        "REDDIT_CLIENT_SECRET are unset). Reddit ingestion will use public "
        "JSON fallback; set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET for "
        "full-quality extraction."
    )
    _reddit_warning_emitted = True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    validate_reddit_credentials(settings)
    return settings
