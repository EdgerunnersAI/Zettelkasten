"""Shared pytest fixtures for the website test suite."""

import pytest


def pytest_addoption(parser):
    try:
        parser.addoption(
            '--live',
            action='store_true',
            default=False,
            help='Run live API integration tests',
        )
    except ValueError as exc:
        if '--live' not in str(exc):
            raise
    try:
        parser.addoption(
            '--destructive',
            action='store_true',
            default=False,
            help='Run destructive tests (mutate shared state, e.g. delete users)',
        )
    except ValueError as exc:
        if '--destructive' not in str(exc):
            raise


@pytest.fixture(autouse=True)
def skip_live(request):
    if request.node.get_closest_marker('live') and not request.config.getoption('--live'):
        pytest.skip('Live test — pass --live to run')


@pytest.fixture(autouse=True)
def skip_destructive(request):
    if (
        request.node.get_closest_marker('destructive')
        and not request.config.getoption('--destructive')
    ):
        pytest.skip('Destructive test — pass --destructive to run')


@pytest.fixture
def sample_reddit_url() -> str:
    return "https://www.reddit.com/r/python/comments/abc123/test_post/"


@pytest.fixture
def sample_youtube_url() -> str:
    return "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture
def sample_github_url() -> str:
    return "https://github.com/user/repo"


@pytest.fixture
def sample_newsletter_url() -> str:
    return "https://example.substack.com/p/my-post"


@pytest.fixture
def sample_generic_url() -> str:
    return "https://example.com/article"
