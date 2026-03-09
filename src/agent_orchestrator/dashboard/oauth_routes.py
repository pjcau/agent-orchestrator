"""OAuth2 login/callback routes for the dashboard.

Provides:
- GET /auth/google — redirect to Google OAuth2
- GET /auth/google/callback — handle Google callback, set JWT cookie
- GET /auth/github — redirect to GitHub OAuth2
- GET /auth/github/callback — handle GitHub callback, set JWT cookie
- GET /auth/me — return current user info from JWT cookie
- POST /auth/logout — clear session cookie
- GET /login — simple login page with provider buttons

Requires: authlib, PyJWT, itsdangerous (for Starlette sessions)
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .auth import create_oauth, create_session_token, get_base_url, verify_session_token

router = APIRouter()


def _login_page_html() -> str:
    """Simple login page with Google/GitHub buttons."""
    google_enabled = bool(os.environ.get("GOOGLE_CLIENT_ID"))
    github_enabled = bool(os.environ.get("GITHUB_OAUTH_CLIENT_ID"))

    buttons = ""
    if google_enabled:
        buttons += '<a href="/auth/google" class="btn google">Login with Google</a>'
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
  .google {{ background: #4285F4; }}
  .github {{ background: #333; }}
  .btn:hover {{ opacity: 0.9; }}
</style></head>
<body><div class="container">
  <h1>Agent Orchestrator</h1>
  {buttons}
</div></body></html>"""


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    """Render login page with OAuth provider buttons."""
    return HTMLResponse(content=_login_page_html())


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
    """Handle Google OAuth2 callback, create JWT session cookie."""
    oauth = create_oauth()
    if not oauth or not hasattr(oauth, "google"):
        return JSONResponse({"error": "Google OAuth not configured"}, status_code=501)

    try:
        token = await oauth.google.authorize_access_token(request)
        user_info = token.get("userinfo", {})
        jwt_token = create_session_token(
            {
                "email": user_info.get("email", ""),
                "name": user_info.get("name", ""),
                "provider": "google",
            }
        )
        response = RedirectResponse("/")
        response.set_cookie(
            "session", jwt_token, httponly=True, secure=True, samesite="lax", max_age=86400
        )
        return response
    except Exception as exc:
        return JSONResponse({"error": f"Google auth failed: {exc}"}, status_code=400)


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
    """Handle GitHub OAuth2 callback, create JWT session cookie."""
    oauth = create_oauth()
    if not oauth or not hasattr(oauth, "github"):
        return JSONResponse({"error": "GitHub OAuth not configured"}, status_code=501)

    try:
        token = await oauth.github.authorize_access_token(request)
        # GitHub requires a separate API call to get user info
        resp = await oauth.github.get("user", token=token)
        user_data = resp.json()

        # Get email (may need separate call if private)
        email = user_data.get("email", "")
        if not email:
            emails_resp = await oauth.github.get("user/emails", token=token)
            emails = emails_resp.json()
            primary = next((e for e in emails if e.get("primary")), None)
            email = primary["email"] if primary else ""

        jwt_token = create_session_token(
            {
                "email": email,
                "name": user_data.get("name", user_data.get("login", "")),
                "provider": "github",
            }
        )
        response = RedirectResponse("/")
        response.set_cookie(
            "session", jwt_token, httponly=True, secure=True, samesite="lax", max_age=86400
        )
        return response
    except Exception as exc:
        return JSONResponse({"error": f"GitHub auth failed: {exc}"}, status_code=400)


@router.get("/auth/me")
async def auth_me(request: Request):
    """Return current user info from JWT session cookie."""
    session_token = request.cookies.get("session")
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
        }
    )


@router.post("/auth/logout")
async def auth_logout():
    """Clear session cookie."""
    response = RedirectResponse("/login")
    response.delete_cookie("session")
    return response
