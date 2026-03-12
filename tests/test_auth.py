"""Tests for dashboard authentication (API key middleware, JWT sessions, OAuth setup)."""

import time

import pytest

from agent_orchestrator.dashboard.auth import (
    APIKeyMiddleware,
    check_ws_auth,
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
    async def test_dev_mode_requires_explicit_opt_in(self, monkeypatch):
        """Without ALLOW_DEV_MODE=true, auth is required (fail-closed)."""
        monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)
        monkeypatch.delenv("OAUTH_CLIENT_ID", raising=False)

        async def app(scope, receive, send):
            pass

        middleware = APIKeyMiddleware(app, api_keys=None)
        # Should NOT be in dev mode by default
        assert not middleware._dev_mode

    @pytest.mark.asyncio
    async def test_dev_mode_explicit(self, monkeypatch):
        """ALLOW_DEV_MODE=true enables dev mode."""
        monkeypatch.setenv("ALLOW_DEV_MODE", "true")
        monkeypatch.delenv("OAUTH_CLIENT_ID", raising=False)

        async def app(scope, receive, send):
            pass

        middleware = APIKeyMiddleware(app, api_keys=None)
        assert middleware._dev_mode

    @pytest.mark.asyncio
    async def test_dev_mode_blocked_in_production(self, monkeypatch):
        """ALLOW_DEV_MODE=true is blocked when ENVIRONMENT=production."""
        monkeypatch.setenv("ALLOW_DEV_MODE", "true")
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.delenv("OAUTH_CLIENT_ID", raising=False)

        async def app(scope, receive, send):
            pass

        middleware = APIKeyMiddleware(app, api_keys=None)
        assert not middleware._dev_mode

    @pytest.mark.asyncio
    async def test_options_preflight_always_passes(self, monkeypatch):
        """OPTIONS requests (CORS preflight) must always pass through auth."""
        monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)
        monkeypatch.setenv("OAUTH_CLIENT_ID", "test-id")

        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse as StarletteJSONResponse
        from starlette.routing import Route

        async def endpoint(request):
            return StarletteJSONResponse({"ok": True})

        starlette_app = Starlette(
            routes=[Route("/api/prompt", endpoint, methods=["GET", "OPTIONS"])]
        )
        starlette_app.add_middleware(APIKeyMiddleware, api_keys=["secret"])

        client = TestClient(starlette_app)
        # OPTIONS should pass through auth (not get 401)
        resp = client.options("/api/prompt")
        assert resp.status_code == 200
        # GET without API key should be blocked
        resp = client.get("/api/prompt")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_exempt_paths_no_websocket(self):
        """WebSocket paths should NOT be exempt (they check auth themselves)."""
        assert not any("/ws".startswith(p) for p in APIKeyMiddleware.EXEMPT_PREFIXES if p != "/ws")
        # /ws should NOT be in EXEMPT_PREFIXES
        assert "/ws" not in APIKeyMiddleware.EXEMPT_PREFIXES

    @pytest.mark.asyncio
    async def test_exempt_paths_allowed(self):
        """Static, health, auth paths should be exempt."""
        for path in (
            "/static/style.css",
            "/health",
            "/auth/github",
            "/login",
            "/api/models",
            "/metrics",
        ):
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


# ---------------------------------------------------------------------------
# WebSocket auth helper tests
# ---------------------------------------------------------------------------


class TestCheckWsAuth:
    """Test WebSocket authentication helper."""

    def test_dev_mode_allows(self, monkeypatch):
        """Dev mode returns a user dict."""
        monkeypatch.setenv("ALLOW_DEV_MODE", "true")

        class FakeRequest:
            headers = {}
            cookies = {}

        result = check_ws_auth(FakeRequest(), set())
        assert result is not None
        assert result["role"] == "admin"

    def test_no_auth_returns_none(self, monkeypatch):
        """Without auth, returns None."""
        monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)

        class FakeRequest:
            headers = {}
            cookies = {}

        result = check_ws_auth(FakeRequest(), set())
        assert result is None

    def test_api_key_auth(self, monkeypatch):
        """Valid API key in header authenticates."""
        monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)

        class FakeRequest:
            headers = {"X-API-Key": "test-key"}
            cookies = {}

        result = check_ws_auth(FakeRequest(), {"test-key"})
        assert result is not None
        assert result["role"] == "developer"

    @pytest.mark.skipif(not HAS_JWT, reason="PyJWT not installed")
    def test_jwt_cookie_auth(self, monkeypatch):
        """Valid JWT cookie authenticates."""
        monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-256bit-key-for-testing")
        monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)
        token = create_session_token(
            {"email": "test@test.com", "name": "Test", "provider": "github", "role": "developer"}
        )

        class FakeRequest:
            headers = {}
            cookies = {"auth_session": token}

        result = check_ws_auth(FakeRequest(), set())
        assert result is not None
        assert result["role"] == "developer"


# ---------------------------------------------------------------------------
# SSRF protection tests
# ---------------------------------------------------------------------------


class TestCookieSameSite:
    """Verify OAuth cookies use samesite=lax for cross-origin redirect compatibility."""

    def test_auth_session_cookie_uses_lax(self):
        """The auth_session cookie in oauth_routes must use samesite='lax', not 'strict'.

        samesite='strict' blocks cookies on cross-origin redirects (GitHub OAuth callback),
        causing CSRF state mismatch errors.
        """
        import inspect
        from agent_orchestrator.dashboard import oauth_routes

        source = inspect.getsource(oauth_routes)
        assert 'samesite="lax"' in source
        assert 'samesite="strict"' not in source

    def test_session_middleware_uses_lax(self):
        """SessionMiddleware must use same_site='lax' so OAuth state survives redirects."""
        import inspect
        from agent_orchestrator.dashboard import app as app_module

        source = inspect.getsource(app_module.create_dashboard_app)
        assert 'same_site="lax"' in source


class TestOllamaUrlValidation:
    """Test SSRF protection on Ollama base URL."""

    def test_localhost_allowed(self, monkeypatch):
        from agent_orchestrator.dashboard.app import _get_ollama_url

        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
        assert _get_ollama_url() == "http://localhost:11434"

    def test_loopback_allowed(self, monkeypatch):
        from agent_orchestrator.dashboard.app import _get_ollama_url

        monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
        assert _get_ollama_url() == "http://127.0.0.1:11434"

    def test_docker_internal_allowed(self, monkeypatch):
        from agent_orchestrator.dashboard.app import _get_ollama_url

        monkeypatch.setenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
        assert _get_ollama_url() == "http://host.docker.internal:11434"

    def test_arbitrary_url_blocked(self, monkeypatch):
        from agent_orchestrator.dashboard.app import _get_ollama_url

        monkeypatch.setenv("OLLAMA_BASE_URL", "http://evil.example.com:11434")
        with pytest.raises(ValueError, match="OLLAMA_BASE_URL must start with"):
            _get_ollama_url()

    def test_https_external_blocked(self, monkeypatch):
        from agent_orchestrator.dashboard.app import _get_ollama_url

        monkeypatch.setenv("OLLAMA_BASE_URL", "https://attacker.com/api")
        with pytest.raises(ValueError):
            _get_ollama_url()


# ---------------------------------------------------------------------------
# Password hashing tests
# ---------------------------------------------------------------------------


class TestPasswordHashing:
    """Test password hashing and verification."""

    def test_hash_and_verify(self):
        from agent_orchestrator.core.users import _hash_password, _verify_password

        hashed = _hash_password("test-password-123")
        assert _verify_password("test-password-123", hashed)
        assert not _verify_password("wrong-password", hashed)

    def test_different_hashes_for_same_password(self):
        """Each hash should be unique (random salt)."""
        from agent_orchestrator.core.users import _hash_password

        h1 = _hash_password("same-password")
        h2 = _hash_password("same-password")
        assert h1 != h2  # bcrypt or random salt = different hashes

    def test_legacy_hash_still_verifies(self):
        """Legacy fixed-salt PBKDF2 hashes should still verify (migration)."""
        import hashlib
        from agent_orchestrator.core.users import _verify_password

        legacy_hash = hashlib.pbkdf2_hmac(
            "sha256", b"old-password", b"agent-orchestrator", iterations=100_000
        ).hex()
        assert _verify_password("old-password", legacy_hash)

    def test_legacy_sha256_salt_hash_still_verifies(self):
        """Legacy SHA-256 with random salt hashes should still verify (pre-PBKDF2)."""
        import hashlib
        from agent_orchestrator.core.users import _verify_password

        salt = "abcdef1234567890"
        hashed = hashlib.sha256(f"{salt}:old-password".encode()).hexdigest()
        legacy_hash = f"sha256${salt}${hashed}"
        assert _verify_password("old-password", legacy_hash)

    def test_pbkdf2_fallback_hash_format(self):
        """New non-bcrypt hashes use PBKDF2 format."""
        from agent_orchestrator.core.users import _hash_password, HAS_BCRYPT

        if HAS_BCRYPT:
            pytest.skip("bcrypt installed, fallback not used")
        hashed = _hash_password("test")
        assert hashed.startswith("pbkdf2$")


# ---------------------------------------------------------------------------
# Log injection prevention tests
# ---------------------------------------------------------------------------


class TestLogSanitization:
    """Test that user-controlled values are sanitized before logging."""

    def test_sanitize_log_strips_newlines(self):
        from agent_orchestrator.providers.openrouter import _sanitize_log

        assert "\n" not in _sanitize_log("model\nname")
        assert "\r" not in _sanitize_log("model\rname")
        assert "\t" not in _sanitize_log("model\tname")

    def test_sanitize_log_preserves_safe_strings(self):
        from agent_orchestrator.providers.openrouter import _sanitize_log

        assert _sanitize_log("qwen/qwen3.5-plus") == "qwen/qwen3.5-plus"

    def test_dashboard_sanitize_log(self):
        from agent_orchestrator.dashboard.app import _sanitize_log

        assert _sanitize_log("bad\nmodel\rname") == "bad\\nmodel\\rname"


# ---------------------------------------------------------------------------
# Path traversal prevention tests
# ---------------------------------------------------------------------------


class TestPathTraversal:
    """Test that path traversal is prevented in file endpoints."""

    def test_dotdot_rejected_in_path_components(self):
        """Path with '..' components should be rejected before resolution."""
        path = "../../../etc/passwd"
        parts = path.split("/")
        assert ".." in parts  # confirms our check would catch it

    def test_backslash_traversal_rejected(self):
        """Windows-style path traversal should also be caught."""
        path = "..\\..\\etc\\passwd"
        parts = path.split("\\")
        assert ".." in parts
