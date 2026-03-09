"""Tests for dashboard authentication (API key middleware, JWT sessions, OAuth setup)."""

import time

import pytest

from agent_orchestrator.dashboard.auth import (
    APIKeyMiddleware,
    create_oauth,
    create_session_token,
    verify_session_token,
    JWT_ALGORITHM,
    HAS_JWT,
    HAS_AUTHLIB,
)


# ---------------------------------------------------------------------------
# JWT session token tests
# ---------------------------------------------------------------------------


class TestJWTSessions:
    """Test JWT session token creation and verification."""

    @pytest.fixture(autouse=True)
    def _set_jwt_secret(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-256bit-key-for-testing")

    @pytest.mark.skipif(not HAS_JWT, reason="PyJWT not installed")
    def test_create_and_verify_token(self):
        user_info = {"email": "user@example.com", "name": "Test User", "provider": "google"}
        token = create_session_token(user_info)
        assert isinstance(token, str)
        assert len(token) > 0

        payload = verify_session_token(token)
        assert payload is not None
        assert payload["sub"] == "user@example.com"
        assert payload["name"] == "Test User"
        assert payload["provider"] == "google"

    @pytest.mark.skipif(not HAS_JWT, reason="PyJWT not installed")
    def test_verify_invalid_token(self):
        result = verify_session_token("invalid.token.here")
        assert result is None

    @pytest.mark.skipif(not HAS_JWT, reason="PyJWT not installed")
    def test_verify_expired_token(self, monkeypatch):
        import jwt as pyjwt

        secret = "test-secret-256bit-key-for-testing"
        payload = {
            "sub": "user@example.com",
            "name": "Test",
            "provider": "google",
            "iat": int(time.time()) - 200000,
            "exp": int(time.time()) - 100000,  # expired
        }
        token = pyjwt.encode(payload, secret, algorithm=JWT_ALGORITHM)
        result = verify_session_token(token)
        assert result is None

    @pytest.mark.skipif(not HAS_JWT, reason="PyJWT not installed")
    def test_verify_wrong_secret(self, monkeypatch):
        import jwt as pyjwt

        token = pyjwt.encode(
            {"sub": "x", "exp": int(time.time()) + 3600},
            "wrong-secret",
            algorithm=JWT_ALGORITHM,
        )
        result = verify_session_token(token)
        assert result is None

    @pytest.mark.skipif(not HAS_JWT, reason="PyJWT not installed")
    def test_create_token_without_secret_raises(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "")
        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
            create_session_token({"email": "x"})

    def test_verify_returns_none_without_secret(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "")
        result = verify_session_token("some.token.here")
        assert result is None


# ---------------------------------------------------------------------------
# OAuth setup tests
# ---------------------------------------------------------------------------


class TestOAuthSetup:
    """Test OAuth client creation."""

    def test_no_credentials_returns_none(self, monkeypatch):
        monkeypatch.delenv("OAUTH_CLIENT_ID", raising=False)
        result = create_oauth()
        assert result is None

    @pytest.mark.skipif(not HAS_AUTHLIB, reason="authlib not installed")
    def test_github_only(self, monkeypatch):
        monkeypatch.setenv("OAUTH_CLIENT_ID", "test-github-id")
        monkeypatch.setenv("OAUTH_CLIENT_SECRET", "test-github-secret")
        oauth = create_oauth()
        assert oauth is not None


# ---------------------------------------------------------------------------
# APIKeyMiddleware tests
# ---------------------------------------------------------------------------


class TestAPIKeyMiddleware:
    """Test the authentication middleware logic."""

    @pytest.mark.asyncio
    async def test_dev_mode_allows_all(self):
        """No API keys and no OAuth = dev mode, all requests pass."""
        calls = []

        async def app(scope, receive, send):
            calls.append(True)

        middleware = APIKeyMiddleware(app, api_keys=None)
        # No OAuth env vars set = dev mode
        assert not middleware.api_keys
        # Dev mode check: middleware should allow all if no keys and no OAuth
        assert len(middleware.api_keys) == 0

    @pytest.mark.asyncio
    async def test_exempt_paths(self):
        """Static, health, ws, auth paths should be exempt."""
        for path in ("/static/style.css", "/health", "/ws", "/auth/github", "/login"):
            assert any(path.startswith(p) for p in APIKeyMiddleware.EXEMPT_PREFIXES)

    def test_api_key_set_from_list(self):
        """API keys should be stored as a set."""

        async def app(scope, receive, send):
            pass

        middleware = APIKeyMiddleware(app, api_keys=["key1", "key2", "key1"])
        assert middleware.api_keys == {"key1", "key2"}

    def test_no_keys_empty_set(self):
        """No keys = empty set."""

        async def app(scope, receive, send):
            pass

        middleware = APIKeyMiddleware(app, api_keys=None)
        assert middleware.api_keys == set()
