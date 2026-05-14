from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from website.api import module_runners
from website.api import zettels_routes
from website.api.module_runners import summarization as runner


@pytest.mark.asyncio
async def test_add_zettel_checks_entitlement_before_expensive_work(monkeypatch) -> None:
    called = {"entitlement": False, "summarize": False}

    async def deny(*args, **kwargs):
        called["entitlement"] = True
        raise HTTPException(
            status_code=402, detail={"code": "quota_exhausted", "meter": "zettel"}
        )

    async def expensive(*args, **kwargs):
        called["summarize"] = True
        return {"url": args[0] if args else kwargs.get("url")}

    monkeypatch.setattr(runner, "require_entitlement", deny)
    monkeypatch.setattr(runner, "summarize_url_bundle", expensive)
    zettels_routes._RATE_STORE.clear()

    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))

    response = await zettels_routes.add_zettel(
        zettels_routes.AddZettelRequest(
            url="https://example.com",
            client_action_id="quota-preflight",
            persist=True,
            surface="home",
        ),
        request,
        {"sub": "00000000-0000-0000-0000-000000000001"},
    )

    assert response.status_code == 402
    assert called == {"entitlement": True, "summarize": False}
