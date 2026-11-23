"""Sign-in for HackFindr: Google accounts, plus a demo sign-in for local testing.

The Gradio app is mounted inside a small FastAPI app that owns /login, /auth and /logout.
Who is signed in lives in a signed session cookie (only the user id is stored in it).

Google sign-in turns on when GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are set in .env.
Without them, and only when the app is not public, /login/demo signs in a local test user.
"""

import os
import secrets
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

import accounts

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

GOOGLE_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_ON = bool(GOOGLE_ID and GOOGLE_SECRET)
PUBLIC = os.environ.get("HACKFINDR_PUBLIC") == "1"
DEMO_ON = not GOOGLE_ON and not PUBLIC and os.environ.get("HACKFINDR_DEMO_LOGIN", "1") != "0"
# Optional fixed address for the Google callback, e.g. https://hackfindr.example.com (needed behind tunnels).
PUBLIC_URL = os.environ.get("HACKFINDR_PUBLIC_URL", "").rstrip("/")


def session_secret():
    """Signs the session cookie. From .env if set, otherwise made once and kept in data/."""
    if os.environ.get("SESSION_SECRET"):
        return os.environ["SESSION_SECRET"]
    path = accounts.DB_PATH.parent / "session_secret.txt"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def current_user(request):
    """The signed-in user as a dict, or None. Works with a FastAPI request or a gr.Request."""
    try:
        user_id = request.session.get("uid") if request is not None else None
    except (AttributeError, AssertionError, KeyError):
        return None
    user = accounts.get_user(user_id)
    if user_id and not user:          # account was deleted: forget the stale cookie
        request.session.pop("uid", None)
    return user


def sign_in_mode():
    return "google" if GOOGLE_ON else "demo" if DEMO_ON else ""


def _back_to(request):
    """Only return to pages on this site after signing in."""
    nxt = request.query_params.get("next", "/")
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else "/"


def build(gradio_blocks, **mount_kwargs):
    """FastAPI app with the sign-in routes and the Gradio app at /."""
    import gradio as gr

    app = FastAPI()
    https = PUBLIC_URL.startswith("https://")
    app.add_middleware(SessionMiddleware, secret_key=session_secret(), session_cookie="hf_session",
                       max_age=30 * 24 * 3600, same_site="lax", https_only=https)

    oauth = None
    if GOOGLE_ON:
        from authlib.integrations.starlette_client import OAuth
        oauth = OAuth()
        oauth.register("google", client_id=GOOGLE_ID, client_secret=GOOGLE_SECRET,
                       server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
                       client_kwargs={"scope": "openid email profile"})

    @app.get("/login")
    async def login(request: Request):
        request.session["next"] = _back_to(request)
        if oauth:
            redirect = f"{PUBLIC_URL}/auth" if PUBLIC_URL else str(request.url_for("auth"))
            return await oauth.google.authorize_redirect(request, redirect)
        if DEMO_ON:
            return RedirectResponse("/login/demo")
        return HTMLResponse(_page("Sign-in is not set up",
                                  "Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env, then restart HackFindr."), 503)

    @app.get("/auth")
    async def auth(request: Request):
        if not oauth:
            return RedirectResponse("/")
        try:
            token = await oauth.google.authorize_access_token(request)
        except Exception:
            return HTMLResponse(_page("Google sign-in did not finish", "Please go back and try again."), 400)
        info = token.get("userinfo") or {}
        if not info.get("email") or not info.get("email_verified", False):
            return HTMLResponse(_page("Google sign-in did not finish", "Your Google email is not verified."), 400)
        user = accounts.sign_in("google", info["sub"], info["email"], info.get("name", ""), info.get("picture", ""))
        request.session["uid"] = user["id"]
        return RedirectResponse(request.session.pop("next", "/"))

    @app.get("/login/demo")
    async def login_demo(request: Request):
        if not DEMO_ON:
            return RedirectResponse("/")
        host = urlparse(str(request.base_url)).hostname or ""
        user = accounts.sign_in("demo", f"demo-{host}", "demo@hackfindr.local", "Demo Student")
        request.session["uid"] = user["id"]
        return RedirectResponse(request.session.pop("next", "/"))

    @app.get("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/?signed_out=1")

    return gr.mount_gradio_app(app, gradio_blocks, path="/", **mount_kwargs)


def _page(title, text):
    import html
    return (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<title>{html.escape(title)}</title><body style='font-family:system-ui;max-width:520px;margin:15vh auto;"
            f"padding:0 16px'><h2>{html.escape(title)}</h2><p>{html.escape(text)}</p><p><a href='/'>Back to HackFindr</a></p>")
