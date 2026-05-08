from __future__ import annotations

from website.core.settings import Settings, get_settings


def test_settings_exposes_website_fields() -> None:
    settings = Settings()

    assert "substack.com" in settings.newsletter_domains
    assert isinstance(settings.rag_chunks_enabled, bool)
    assert settings.server_port == 10000


def test_get_settings_returns_singleton() -> None:
    get_settings.cache_clear()

    first = get_settings()
    second = get_settings()

    assert first is second
    assert isinstance(first, Settings)


def test_reddit_oauth_configured_true_when_both_present() -> None:
    s = Settings(
        reddit_client_id="sample-id",
        reddit_client_secret="sample-secret",
    )
    assert s.reddit_oauth_configured is True


def test_reddit_oauth_configured_false_when_secret_missing() -> None:
    s = Settings(reddit_client_id="sample-id", reddit_client_secret="")
    assert s.reddit_oauth_configured is False


def test_reddit_oauth_configured_false_when_id_missing() -> None:
    s = Settings(reddit_client_id="", reddit_client_secret="sample-secret")
    assert s.reddit_oauth_configured is False


def test_reddit_oauth_configured_false_when_whitespace_only() -> None:
    s = Settings(reddit_client_id="   ", reddit_client_secret="   ")
    assert s.reddit_oauth_configured is False
