"""OAuth2 login/callback routes for the dashboard.

Provides:
- GET /auth/github — redirect to GitHub OAuth2
- GET /auth/github/callback — handle GitHub callback, set JWT cookie
- GET /auth/me — return current user info from JWT cookie
- POST /auth/logout — clear session cookie
- GET /login — simple login page with GitHub button
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

from .auth import create_oauth, create_session_token, get_base_url, verify_session_token
from .user_store import (
    approve_pending,
    approve_user,
    async_get_or_create_user,
    deactivate_user,
    get_or_create_user,
    list_pending,
    list_users,
    reject_pending,
    update_user_role,
)

logger = logging.getLogger(__name__)


router = APIRouter()


def _login_page_html() -> str:
    """Simple login page with GitHub button."""
    github_enabled = bool(os.environ.get("OAUTH_CLIENT_ID"))

    buttons = ""
    if github_enabled:
        buttons += '<a href="/auth/github" class="btn github">Login with GitHub</a>'
    if not buttons:
        buttons = "<p>No OAuth providers configured.</p>"

    return f"""<!DOCTYPE html>
<html><head><title>Login — Agent Orchestrator</title>
<style>
  body {{ font-family: system-ui; display: flex; justify-content: center;
         align-items: center; min-height: 100vh; margin: 0;
         background: #1a1a2e; color: #e0e0e0; }}
  .container {{ text-align: center; padding: 2rem; }}
  h1 {{ margin-bottom: 2rem; }}
  .btn {{ display: block; padding: 12px 24px; margin: 12px auto;
          border-radius: 8px; text-decoration: none; color: white;
          font-size: 16px; width: 250px; text-align: center; }}
  .github {{ background: #333; }}
  .btn:hover {{ opacity: 0.9; }}
  .denied {{ color: #ff6b6b; margin-top: 1rem; }}
</style></head>
<body><div class="container">
  <h1>Agent Orchestrator</h1>
  {buttons}
</div></body></html>"""


def _denied_page_html(login: str) -> str:
    """Page shown when a user is not approved."""
    return f"""<!DOCTYPE html>
<html><head><title>Access Denied — Agent Orchestrator</title>
<style>
  body {{ font-family: system-ui; display: flex; justify-content: center;
         align-items: center; min-height: 100vh; margin: 0;
         background: #1a1a2e; color: #e0e0e0; }}
  .container {{ text-align: center; padding: 2rem; max-width: 500px; }}
  h1 {{ color: #ff6b6b; }}
  .login {{ color: #4fc3f7; font-weight: bold; }}
  a {{ color: #4fc3f7; }}
</style></head>
<body><div class="container">
  <h1>Access Denied</h1>
  <p>User <span class="login">{login}</span> is not authorized.</p>
  <p>Ask the admin to approve your GitHub account.</p>
  <p><a href="/login">Try again</a></p>
</div></body></html>"""


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
        response = RedirectResponse("/", status_code=302)
        response.set_cookie(
            "auth_session", jwt_token, httponly=True, secure=True, samesite="lax", max_age=14400
        )
        logger.info("AUTH login success: %s (role=%s)", github_login, user["role"])
        return response
    except Exception as exc:
        logger.warning("AUTH login failed: %s", exc)
        return JSONResponse({"error": "GitHub authentication failed"}, status_code=400)


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
