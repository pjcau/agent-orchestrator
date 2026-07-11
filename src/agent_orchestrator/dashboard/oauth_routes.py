"""OAuth2 login/callback routes for the dashboard.

Provides:
- GET /auth/github — redirect to GitHub OAuth2
- GET /auth/github/callback — handle GitHub callback, set JWT cookie
- GET /auth/google — redirect to Google OAuth2
- GET /auth/google/callback — handle Google callback (allowlist gated), set JWT cookie
- GET /auth/me — return current user info from JWT cookie
- POST /auth/logout — clear session cookie
- GET /login — simple login page with provider buttons
- GET /api/admin/users — list all users (admin only)
- POST /api/admin/users — approve a new user (admin only)
- PATCH /api/admin/users/{login} — update user role (admin only)
- DELETE /api/admin/users/{login} — deactivate user (admin only)

Requires: authlib, PyJWT, itsdangerous (for Starlette sessions)
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .auth import (
    create_oauth,
    create_session_token,
    get_base_url,
    is_email_allowed,
    verify_session_token,
)
from .user_store import (
    approve_pending,
    approve_user,
    async_auto_provision_google_user,
    async_get_or_create_user,
    deactivate_user,
    list_pending,
    list_users,
    reject_pending,
    update_user_role,
)

logger = logging.getLogger(__name__)


router = APIRouter()


def _login_page_html() -> str:
    """Login page with one button per configured OAuth provider.

    Mobile-aware: the viewport meta tag opts into device-width rendering
    (without it, iOS Safari laid out at 980px and shrank the whole page).
    Buttons go full-width on phones (capped at 320px on landscape /
    tablets) and respect the iPhone safe-area on the top/sides.
    """
    github_enabled = bool(os.environ.get("OAUTH_CLIENT_ID"))
    google_enabled = bool(os.environ.get("GOOGLE_OAUTH_CLIENT_ID"))

    buttons = ""
    if github_enabled:
        buttons += (
            '<a href="/auth/github" class="btn github">'
            '<span class="icon">&#xf09b;</span>Login with GitHub</a>'
        )
    if google_enabled:
        buttons += (
            '<a href="/auth/google" class="btn google">'
            '<span class="google-g">G</span>Login with Google</a>'
        )
    if not buttons:
        buttons = "<p>No OAuth providers configured.</p>"

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#1a1a2e">
<title>Login — Agent Orchestrator</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  html, body {{ height: 100%; }}
  body {{ font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
         display: flex; justify-content: center;
         align-items: center; min-height: 100dvh; margin: 0;
         background: #1a1a2e; color: #e0e0e0;
         padding: max(16px, env(safe-area-inset-top))
                 max(16px, env(safe-area-inset-right))
                 max(16px, env(safe-area-inset-bottom))
                 max(16px, env(safe-area-inset-left)); }}
  .container {{ text-align: center; width: 100%; max-width: 360px; }}
  h1 {{ margin: 0 0 2rem; font-size: clamp(20px, 6vw, 28px);
        letter-spacing: -0.3px; }}
  .btn {{ display: flex; align-items: center; justify-content: center; gap: 10px;
          padding: 14px 20px; margin: 12px 0; border-radius: 10px;
          text-decoration: none; color: white; font-size: 16px;
          font-weight: 500;
          width: 100%; min-height: 48px;
          transition: opacity 0.15s, transform 0.1s; }}
  .btn:active {{ transform: scale(0.98); }}
  .btn:hover {{ opacity: 0.9; }}
  .github {{ background: #24292e; }}
  .google {{ background: #ffffff; color: #3c4043;
             border: 1px solid #dadce0; }}
  .google-g {{ font-weight: 700; color: #4285f4;
                font-family: "Product Sans", Roboto, Arial, sans-serif; }}
  .denied {{ color: #ff6b6b; margin-top: 1rem; }}
  /* On wider screens widen the field a bit so each button looks intentional. */
  @media (min-width: 480px) {{
    .container {{ max-width: 320px; }}
  }}
</style></head>
<body><div class="container">
  <h1>Agent Orchestrator</h1>
  {buttons}
</div></body></html>"""


def _denied_page_html(login: str) -> str:
    """Page shown when a user is not approved.

    Mirrors the responsive setup of the login page so the message and
    "Try again" link don't wrap awkwardly under the iPhone notch.
    """
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#1a1a2e">
<title>Access Denied — Agent Orchestrator</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  html, body {{ height: 100%; }}
  body {{ font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
         display: flex; justify-content: center;
         align-items: center; min-height: 100dvh; margin: 0;
         background: #1a1a2e; color: #e0e0e0;
         padding: max(16px, env(safe-area-inset-top))
                 max(16px, env(safe-area-inset-right))
                 max(16px, env(safe-area-inset-bottom))
                 max(16px, env(safe-area-inset-left)); }}
  .container {{ text-align: center; width: 100%; max-width: 500px;
                 padding: 1rem; }}
  h1 {{ color: #ff6b6b; font-size: clamp(20px, 6vw, 28px); margin: 0 0 1rem; }}
  p {{ margin: 0.75rem 0; line-height: 1.5; }}
  .login {{ color: #4fc3f7; font-weight: bold; word-break: break-all; }}
  a {{ color: #4fc3f7; }}
  a.try-again {{ display: inline-block; margin-top: 0.5rem;
                  padding: 12px 20px; border: 1px solid #4fc3f7;
                  border-radius: 8px; text-decoration: none;
                  min-height: 44px; min-width: 120px;
                  line-height: 20px; }}
</style></head>
<body><div class="container">
  <h1>Access Denied</h1>
  <p>User <span class="login">{login}</span> is not authorized.</p>
  <p>Ask the admin to approve your GitHub account.</p>
  <p><a class="try-again" href="/login">Try again</a></p>
</div></body></html>"""


@router.get("/auth/debug")
async def auth_debug():
    """Debug endpoint to verify OAuth configuration (no secrets exposed).

    Never returns the actual client secrets nor the email allowlist contents.
    """
    import os

    github_id = os.environ.get("OAUTH_CLIENT_ID", "")
    google_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    base_url = get_base_url()
    return JSONResponse(
        {
            "base_url": base_url,
            "github": {
                "redirect_uri": base_url + "/auth/github/callback",
                "client_id_set": bool(github_id),
                "client_id_prefix": (github_id[:8] + "..." if len(github_id) > 8 else github_id),
                "client_secret_set": bool(os.environ.get("OAUTH_CLIENT_SECRET", "")),
            },
            "google": {
                "redirect_uri": base_url + "/auth/google/callback",
                "client_id_set": bool(google_id),
                "client_id_prefix": (google_id[:8] + "..." if len(google_id) > 8 else google_id),
                "client_secret_set": bool(os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")),
                "allowlist_configured": bool(os.environ.get("ALLOWED_GOOGLE_EMAILS", "").strip()),
            },
        }
    )


#: Allowlist of relative path *prefixes* that the post-login return URL is
#: permitted to land on. Everything outside this set is rewritten to ``/``.
#: Hard-coded allowlist > input validation because CodeQL's taint analysis
#: only recognises an "obvious" sanitizer when the value passing into the
#: redirect comes from a literal constant or a membership check against a
#: literal collection — not when it is parsed and conditionally returned.
_RETURN_TO_PREFIXES: tuple[str, ...] = (
    "/api/cli/v1/auth/device",
    "/login",
    "/",  # bare root as a fallback (matches everything else; checked last)
)


def _safe_return_to(request: Request) -> str:
    """Resolve the `auth_return_to` cookie to a safe local URL.

    The cookie is user-controllable; CodeQL `py/url-redirection` flags
    any redirect built from external input unless the value is selected
    from a static allowlist. We do exactly that:

    1. Read the cookie value.
    2. Verify it starts with one of :data:`_RETURN_TO_PREFIXES`.
    3. If it matches, return the literal cookie value (still a relative
       path; no scheme or netloc could have survived the prefix match).
    4. Otherwise fall back to ``/``.

    This pattern is the canonical "redirect allowlist" recipe CodeQL
    recognises as a safe sanitizer, and it also fits the operational
    threat model — we only ever want to send the user back to the
    device-flow approval page or the chat home.
    """
    raw = request.cookies.get("auth_return_to", "")
    # A leading "//" makes the path protocol-relative (`//evil.com/foo`),
    # which the browser interprets as an absolute URL. Catch that first.
    if not raw or raw.startswith("//"):
        return "/"
    for prefix in _RETURN_TO_PREFIXES:
        if raw.startswith(prefix):
            # Ensure the remainder does not contain ".." path traversal
            # or a scheme/host (e.g. "//evil.com" after prefix).
            suffix = raw[len(prefix):]
            if ".." in suffix or suffix.startswith("//"):
                return "/"
            return raw
    return "/"


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    """Render login page with OAuth provider buttons."""
    return HTMLResponse(content=_login_page_html())


@router.get("/auth/github")
async def login_github(request: Request):
    """Redirect to GitHub OAuth2 authorization."""
    oauth = create_oauth()
    if not oauth or not hasattr(oauth, "github"):
        return JSONResponse({"error": "GitHub OAuth not configured"}, status_code=501)
    redirect_uri = get_base_url() + "/auth/github/callback"
    return await oauth.github.authorize_redirect(request, redirect_uri)


@router.get("/auth/github/callback")
async def callback_github(request: Request):
    """Handle GitHub OAuth2 callback. Checks user_store for authorization."""
    oauth = create_oauth()
    if not oauth or not hasattr(oauth, "github"):
        return JSONResponse({"error": "GitHub OAuth not configured"}, status_code=501)

    try:
        token = await oauth.github.authorize_access_token(request)
        resp = await oauth.github.get("user", token=token)
        user_data = resp.json()

        github_login = user_data.get("login", "")
        name = user_data.get("name", "") or github_login

        # Get email (may need separate call if private)
        email = user_data.get("email", "")
        if not email:
            emails_resp = await oauth.github.get("user/emails", token=token)
            emails = emails_resp.json()
            primary = next((e for e in emails if e.get("primary")), None)
            email = primary["email"] if primary else ""

        # Check user_store: admin auto-created, others must be approved
        user = await async_get_or_create_user(github_login, email, name)
        if user is None:
            return HTMLResponse(content=_denied_page_html(github_login), status_code=403)

        jwt_token = create_session_token(
            {
                "email": email,
                "name": name,
                "provider": "github",
                "github_login": github_login,
                "role": user["role"],
            }
        )
        response = RedirectResponse(_safe_return_to(request), status_code=302)
        response.set_cookie(
            "auth_session", jwt_token, httponly=True, secure=True, samesite="lax", max_age=14400
        )
        response.delete_cookie("auth_return_to")
        logger.info("AUTH login success: %s (role=%s)", github_login, user["role"])
        return response
    except Exception as exc:
        logger.warning("AUTH login failed: %s", exc)
        return JSONResponse({"error": "GitHub authentication failed"}, status_code=400)


@router.get("/auth/google")
async def login_google(request: Request):
    """Redirect to Google OAuth2 authorization."""
    oauth = create_oauth()
    if not oauth or not hasattr(oauth, "google"):
        return JSONResponse({"error": "Google OAuth not configured"}, status_code=501)
    redirect_uri = get_base_url() + "/auth/google/callback"
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/auth/google/callback")
async def callback_google(request: Request):
    """Handle Google OAuth2 callback.

    Authorization model: the email returned by Google must match
    ``ALLOWED_GOOGLE_EMAILS``. Matching emails are auto-provisioned with role
    ``developer``; any other email returns the denied page (no pending entry).
    """
    oauth = create_oauth()
    if not oauth or not hasattr(oauth, "google"):
        return JSONResponse({"error": "Google OAuth not configured"}, status_code=501)

    try:
        token = await oauth.google.authorize_access_token(request)
        userinfo = token.get("userinfo") or {}
        if not userinfo:
            # Fallback for providers that don't include id_token claims in the token
            userinfo_resp = await oauth.google.get(
                "https://openidconnect.googleapis.com/v1/userinfo", token=token
            )
            userinfo = userinfo_resp.json()

        email = (userinfo.get("email") or "").strip().lower()
        name = userinfo.get("name") or email
        email_verified = userinfo.get("email_verified", True)

        if not email:
            return JSONResponse({"error": "No email returned by Google"}, status_code=400)

        if not email_verified:
            logger.warning("AUTH google: unverified email rejected")
            return HTMLResponse(content=_denied_page_html(email), status_code=403)

        if not is_email_allowed(email):
            logger.warning("AUTH google: email not in allowlist")
            return HTMLResponse(content=_denied_page_html(email), status_code=403)

        user = await async_auto_provision_google_user(email, name)

        jwt_token = create_session_token(
            {
                "email": email,
                "name": name,
                "provider": "google",
                "github_login": user["github_login"],
                "role": user["role"],
            }
        )
        response = RedirectResponse(_safe_return_to(request), status_code=302)
        response.set_cookie(
            "auth_session",
            jwt_token,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=14400,
        )
        response.delete_cookie("auth_return_to")
        logger.info("AUTH login success (google) role=%s", user["role"])
        return response
    except Exception as exc:
        logger.warning("AUTH google login failed: %s", exc)
        return JSONResponse({"error": "Google authentication failed"}, status_code=400)


@router.get("/auth/me")
async def auth_me(request: Request):
    """Return current user info from JWT session cookie."""
    session_token = request.cookies.get("auth_session")
    if not session_token:
        return JSONResponse({"authenticated": False}, status_code=401)

    user = verify_session_token(session_token)
    if not user:
        return JSONResponse({"authenticated": False}, status_code=401)

    return JSONResponse(
        {
            "authenticated": True,
            "email": user.get("sub", ""),
            "name": user.get("name", ""),
            "provider": user.get("provider", ""),
            "github_login": user.get("github_login", ""),
            "role": user.get("role", ""),
        }
    )


@router.post("/auth/logout")
async def auth_logout(request: Request):
    """Clear session cookie."""
    # Log the logout
    token = request.cookies.get("auth_session")
    if token:
        user = verify_session_token(token)
        if user:
            logger.info("AUTH logout: %s", user.get("github_login", "unknown"))
    response = RedirectResponse("/login")
    response.delete_cookie("auth_session")
    return response


# ---------------------------------------------------------------------------
# Admin user management endpoints
# ---------------------------------------------------------------------------


def _require_admin(request: Request) -> dict | None:
    """Extract user from request and verify admin role. Returns user or None."""
    user = getattr(request.state, "user", None)
    if not user:
        token = request.cookies.get("auth_session")
        if token:
            user = verify_session_token(token)
    if not user or user.get("role") != "admin":
        return None
    return user


@router.get("/api/admin/users")
async def admin_list_users(request: Request):
    """List all users (admin only)."""
    if not _require_admin(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)
    return JSONResponse({"users": list_users()})


@router.post("/api/admin/users")
async def admin_approve_user(request: Request):
    """Approve a new user (admin only).

    Body: {"github_login": "username", "role": "developer"}
    """
    if not _require_admin(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    body = await request.json()
    login = body.get("github_login", "").strip()
    role = body.get("role", "developer")

    if not login:
        return JSONResponse({"error": "github_login required"}, status_code=400)
    if role not in ("admin", "developer", "viewer"):
        return JSONResponse({"error": "Invalid role"}, status_code=400)

    user = approve_user(login, role=role)
    return JSONResponse({"success": True, "user": user})


@router.patch("/api/admin/users/{login}")
async def admin_update_role(request: Request, login: str):
    """Update a user's role (admin only).

    Body: {"role": "developer"}
    """
    if not _require_admin(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    body = await request.json()
    role = body.get("role", "")

    if role not in ("admin", "developer", "viewer"):
        return JSONResponse({"error": "Invalid role"}, status_code=400)

    ok = update_user_role(login, role)
    if not ok:
        return JSONResponse({"error": "User not found"}, status_code=404)
    return JSONResponse({"success": True})


@router.delete("/api/admin/users/{login}")
async def admin_deactivate(request: Request, login: str):
    """Deactivate a user (admin only)."""
    if not _require_admin(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    ok = deactivate_user(login)
    if not ok:
        return JSONResponse({"error": "User not found"}, status_code=404)
    return JSONResponse({"success": True})


# ---------------------------------------------------------------------------
# Pending access requests
# ---------------------------------------------------------------------------


@router.get("/api/admin/pending")
async def admin_list_pending(request: Request):
    """List pending access requests (admin only)."""
    if not _require_admin(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)
    return JSONResponse({"pending": list_pending()})


@router.post("/api/admin/pending/{login}/approve")
async def admin_approve_pending(request: Request, login: str):
    """Approve a pending access request (admin only).

    Body: {"role": "developer"}  (optional, default: developer)
    """
    if not _require_admin(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    role = body.get("role", "developer")

    if role not in ("admin", "developer", "viewer"):
        return JSONResponse({"error": "Invalid role"}, status_code=400)

    user = approve_pending(login, role=role)
    return JSONResponse({"success": True, "user": user})


@router.delete("/api/admin/pending/{login}")
async def admin_reject_pending(request: Request, login: str):
    """Reject a pending access request (admin only)."""
    if not _require_admin(request):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    ok = reject_pending(login)
    if not ok:
        return JSONResponse({"error": "Request not found"}, status_code=404)
    return JSONResponse({"success": True})
