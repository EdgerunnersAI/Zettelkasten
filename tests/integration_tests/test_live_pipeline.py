import pytest


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_smoke():
    """Smoke test: send a URL through the real pipeline.
    Requires GEMINI_API_KEY and valid credentials.
    """
    pytest.skip('Live smoke test — requires real credentials and --live flag')
