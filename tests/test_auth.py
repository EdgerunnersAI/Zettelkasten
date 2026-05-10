"""Tests for user authentication."""

from __future__ import annotations

import time
from unittest.mock import patch

import jwt as pyjwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

TEST_SECRET = "test-jwt-secret-that-is-long-enough-for-hs256!!"


def _make_jwt(payload: dict, secret: str = TEST_SECRET) -> str:
    """Create a signed JWT for testing."""
    defaults = {
        "sub": "550e8400-e29b-41d4-a716-446655440000",
        "email": "test@example.com",
        "aud": "authenticated",
        "role": "authenticated",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
        "user_metadata": {
            "full_name": "Test User",
            "avatar_url": "https://example.com/avatar.png",
        },
    }
    defaults.update(payload)
    return pyjwt.encode(defaults, secret, algorithm="HS256")


class TestAuthSettings:
    """SUPABASE_JWT_SECRET is available from environment."""

    def test_jwt_secret_loaded_from_env(self):
        import os
        os.environ["SUPABASE_JWT_SECRET"] = "test-secret-at-least-32-chars-long!!"
        try:
            from website.api.auth import _get_jwt_secret
            secret = _get_jwt_secret()
            assert secret is not None
            assert len(secret) > 0
        finally:
            os.environ.pop("SUPABASE_JWT_SECRET", None)


from website.api.auth import get_current_user, get_optional_user


class TestGetCurrentUser:
    """get_current_user validates JWT and returns claims."""

    @patch("website.api.auth._get_jwt_secret", return_value=TEST_SECRET)
    def test_valid_token_returns_claims(self, mock_secret):
        app = FastAPI()

        @app.get("/test")
        async def test_route(user: dict = Depends(get_current_user)):
            return {"sub": user["sub"], "email": user["email"]}

        client = TestClient(app)
        token = _make_jwt({})
        resp = client.get("/test", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["sub"] == "550e8400-e29b-41d4-a716-446655440000"
        assert resp.json()["email"] == "test@example.com"

    @patch("website.api.auth._get_jwt_secret", return_value=TEST_SECRET)
    def test_expired_token_returns_401(self, mock_secret):
        app = FastAPI()

        @app.get("/test")
        async def test_route(user: dict = Depends(get_current_user)):
            return user

        client = TestClient(app)
        token = _make_jwt({"exp": int(time.time()) - 100})
        resp = client.get("/test", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    @patch("website.api.auth._get_jwt_secret", return_value=TEST_SECRET)
    def test_missing_token_returns_401(self, mock_secret):
        app = FastAPI()

        @app.get("/test")
        async def test_route(user: dict = Depends(get_current_user)):
            return user

        client = TestClient(app)
        resp = client.get("/test")
        assert resp.status_code == 401

    @patch("website.api.auth._get_jwt_secret", return_value=TEST_SECRET)
    def test_malformed_token_returns_401(self, mock_secret):
        app = FastAPI()

        @app.get("/test")
        async def test_route(user: dict = Depends(get_current_user)):
            return user

        client = TestClient(app)
        resp = client.get("/test", headers={"Authorization": "Bearer not.a.valid.jwt"})
        assert resp.status_code == 401

    @patch("website.api.auth._get_jwt_secret", return_value=TEST_SECRET)
    def test_wrong_secret_returns_401(self, mock_secret):
        app = FastAPI()

        @app.get("/test")
        async def test_route(user: dict = Depends(get_current_user)):
            return user

        client = TestClient(app)
        token = _make_jwt({}, secret="wrong-secret-wrong-secret-wrong-secret!!")
        resp = client.get("/test", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401


class TestGetOptionalUser:
    """get_optional_user returns None instead of 401 when no auth."""

    @patch("website.api.auth._get_jwt_secret", return_value=TEST_SECRET)
    def test_no_token_returns_none(self, mock_secret):
        app = FastAPI()

        @app.get("/test")
        async def test_route(user: dict | None = Depends(get_optional_user)):
            return {"user": user}

        client = TestClient(app)
        resp = client.get("/test")
        assert resp.status_code == 200
        assert resp.json()["user"] is None

    @patch("website.api.auth._get_jwt_secret", return_value=TEST_SECRET)
    def test_valid_token_returns_claims(self, mock_secret):
        app = FastAPI()

        @app.get("/test")
        async def test_route(user: dict | None = Depends(get_optional_user)):
            return {"sub": user["sub"] if user else None}

        client = TestClient(app)
        token = _make_jwt({})
        resp = client.get("/test", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["sub"] == "550e8400-e29b-41d4-a716-446655440000"

    @patch("website.api.auth._get_jwt_secret", return_value=TEST_SECRET)
    def test_invalid_token_returns_none(self, mock_secret):
        app = FastAPI()

        @app.get("/test")
        async def test_route(user: dict | None = Depends(get_optional_user)):
            return {"user": user}

        client = TestClient(app)
        resp = client.get("/test", headers={"Authorization": "Bearer garbage"})
        assert resp.status_code == 200
        assert resp.json()["user"] is None


from website.app import create_app


@pytest.fixture
def auth_client():
    """TestClient with rate limiter cleared."""
    from website.api import routes
    routes._rate_store.clear()
    app = create_app()
    return TestClient(app)


class TestAuthConfigEndpoint:
    """GET /api/auth/config returns Supabase public config."""

    @patch.dict("os.environ", {
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_ANON_KEY": "test-anon-key",
    })
    def test_returns_supabase_config(self, auth_client):
        resp = auth_client.get("/api/auth/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["supabase_url"] == "https://test.supabase.co"
        assert data["supabase_anon_key"] == "test-anon-key"


class TestMeEndpoint:
    """GET /api/me returns user profile when authenticated."""

    @patch("website.api.auth._get_jwt_secret", return_value=TEST_SECRET)
    def test_authenticated_returns_profile(self, mock_secret, auth_client):
        token = _make_jwt({
            "sub": "550e8400-e29b-41d4-a716-446655440000",
            "email": "user@example.com",
            "user_metadata": {"full_name": "Test User", "avatar_url": "https://img.test/a.png"},
        })
        resp = auth_client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "550e8400-e29b-41d4-a716-446655440000"
        assert data["email"] == "user@example.com"
        assert data["name"] == "Test User"
        assert data["avatar_url"] == "https://img.test/a.png"

    def test_unauthenticated_returns_401(self, auth_client):
        resp = auth_client.get("/api/me")
        assert resp.status_code == 401


class TestGraphEndpointAuth:
    """GET /api/graph works with and without auth."""

    def test_unauthenticated_returns_graph(self, auth_client):
        """Backwards compatible — no auth still works."""
        resp = auth_client.get("/api/graph")
        assert resp.status_code == 200
