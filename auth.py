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
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.middleware.sessions import SessionMiddleware

import accounts
import usage

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
        usage.count("signin")
        return RedirectResponse(request.session.pop("next", "/"))

    @app.get("/login/demo")
    async def login_demo(request: Request):
        if not DEMO_ON:
            return RedirectResponse("/")
        host = urlparse(str(request.base_url)).hostname or ""
        user = accounts.sign_in("demo", f"demo-{host}", "demo@hackfindr.local", "Demo Student")
        request.session["uid"] = user["id"]
        return RedirectResponse(request.session.pop("next", "/"))

    @app.get("/unsubscribe")
    async def unsubscribe(request: Request):
        """One-click link from every email: turns all emails off (no sign-in needed, the link is signed)."""
        import reminders
        try:
            user_id = int(request.query_params.get("u", ""))
        except ValueError:
            user_id = 0
        if not user_id or not accounts.get_user(user_id) or not reminders.check_token(user_id, request.query_params.get("t")):
            return HTMLResponse(_page("Link not valid", "This unsubscribe link is broken or the account no longer exists."), 400)
        accounts.set_email_prefs(user_id, reminders=False, digest=False)
        return HTMLResponse(_page("You are unsubscribed",
                                  "HackFindr will not email you any more. You can turn emails back on in your profile."))

    @app.get("/qr.png")
    async def qr_png(request: Request):
        """QR code of the address people should open (for posters and the demo table)."""
        import io
        import qrcode
        img = qrcode.make(share_url(request), box_size=12, border=2)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/share")
    async def share(request: Request):
        """A printable page with a big QR code, to put on the table at the event."""
        return HTMLResponse(_share_page(share_url(request)))

    @app.get("/stats")
    async def stats(request: Request):
        """Usage and feedback numbers for the README and slides. Needs the key printed at start-up."""
        import hmac
        if not hmac.compare_digest(str(request.query_params.get("key", "")), usage.stats_key()):
            return HTMLResponse(_page("Stats are private", "This page needs the stats key from the app's console."), 403)
        return HTMLResponse(_stats_page(usage.summary()))

    @app.get("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/?signed_out=1")

    return gr.mount_gradio_app(app, gradio_blocks, path="/", **mount_kwargs)


def share_url(request=None):
    """The address to share: HACKFINDR_PUBLIC_URL, else the address this page was opened on.
    Opened on localhost, that would be useless on a phone, so the Wi-Fi address is used instead."""
    if PUBLIC_URL:
        return PUBLIC_URL + "/"
    base = str(request.base_url) if request is not None else "http://localhost:7860/"
    host = urlparse(base).hostname or ""
    if host in ("localhost", "127.0.0.1", "::1"):
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))     # no data is sent; this only picks the Wi-Fi address
                base = base.replace(host, sock.getsockname()[0], 1)
        except OSError:
            pass
    return base


STYLE = ("<style>body{font-family:'Segoe UI',system-ui,sans-serif;background:#f4f3fb;color:#1d1b29;margin:0;padding:24px 16px}"
         ".wrap{max-width:880px;margin:auto}.card{background:#fff;border:1px solid #ebe9f5;border-radius:18px;padding:18px 20px;"
         "margin:0 0 14px}h1{margin:0 0 4px;font-size:26px}h2{font-size:16px;margin:0 0 12px}.muted{color:#6f6c85;font-size:14px}"
         ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}.num{font-size:28px;font-weight:800;"
         "color:#6d5dfc}.lbl{font-size:13px;color:#6f6c85}table{border-collapse:collapse;width:100%;font-size:13px}"
         "td,th{padding:6px 8px;border-bottom:1px solid #ebe9f5;text-align:right}td:first-child,th:first-child{text-align:left}"
         ".bar{height:10px;border-radius:6px;background:#6d5dfc;display:inline-block;vertical-align:middle}"
         ".c{border-left:3px solid #d9d5f7;padding:6px 12px;margin:8px 0}@media print{.noprint{display:none}body{background:#fff}}</style>")


def _share_page(url):
    import html
    u = html.escape(url)
    return (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><title>Try HackFindr</title>"
            f"{STYLE}<body><div class=wrap style='text-align:center;max-width:520px'><div class=card style='padding:32px'>"
            f"<div style='font-size:30px;font-weight:800;color:#6d5dfc'>Hack<span style='color:#1d1b29'>Findr</span></div>"
            f"<p class=muted style='font-size:17px;margin:6px 0 18px'>Find hackathons and workshops near you,<br>"
            f"check if you can join, and get your best fit, in one place.</p>"
            f"<img src='/qr.png' alt='QR code' style='width:280px;max-width:80%;image-rendering:pixelated'>"
            f"<p style='font-size:18px;font-weight:600;margin:14px 0 4px;word-break:break-all'>{u}</p>"
            f"<p class=muted>Scan with your phone camera, then tap Feedback and tell us what you think</p></div>"
            f"<button class=noprint onclick='print()' style='padding:10px 18px;border-radius:10px;border:0;background:#6d5dfc;"
            f"color:#fff;font-size:15px;cursor:pointer'>Print this page</button></div>")


def _stats_page(s):
    import html
    from datetime import datetime
    import usage as u
    star, hollow = "\u2605", "\u2606"
    tiles = [("Unique visitors (sum of daily)", s["visitor_days"]), ("Accounts", s["accounts"]),
             ("Feedback received", s["feedback_count"]),
             ("Average rating", f"{s['feedback_avg']} / 5" if s["feedback_avg"] else "-")]
    tiles += [(label, s["totals"].get(key, 0)) for key, label in u.METRICS.items()]
    tile_html = "".join(f"<div><div class=num>{html.escape(str(n))}</div><div class=lbl>{html.escape(l)}</div></div>"
                        for l, n in tiles)
    days = sorted(s["per_day"])
    cols = ["visit", "search", "details", "bestfit", "fitcheck", "chat"]
    rows = "".join(f"<tr><td>{d}</td><td>{s['visitors'].get(d, 0)}</td>" +
                   "".join(f"<td>{s['per_day'][d].get(c, 0)}</td>" for c in cols) + "</tr>" for d in days)
    empty = "<tr><td colspan=8>No visits yet</td></tr>"
    table = ("<table><tr><th>Day</th><th>Visitors</th>" + "".join(f"<th>{html.escape(u.METRICS[c])}</th>" for c in cols) +
             f"</tr>{rows or empty}</table>")
    most = max(list(s["stars"].values()) or [1])
    stars = "".join(f"<div>{star * r}{hollow * (5 - r)} <span class=bar style='width:{int(200 * s['stars'].get(r, 0) / most)}px'>"
                    f"</span> {s['stars'].get(r, 0)}</div>" for r in range(5, 0, -1))
    useful = ("".join(f"<div>{html.escape(name)}: <b>{n}</b></div>" for name, n in s["useful"])
              or "<div class=muted>No answers yet</div>")
    comments = ("".join(f"<div class=c><b>{star * c['rating']}</b> <span class=muted>"
                        f"{datetime.fromtimestamp(c['at']).strftime('%d %b %H:%M')}"
                        f"{' &middot; ' + html.escape(c['useful']) if c['useful'] else ''}"
                        f"</span><br>{html.escape(c['comment'])}</div>" for c in s["comments"])
                or "<div class=muted>No comments yet</div>")
    return (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><title>HackFindr stats</title>"
            f"{STYLE}<body><div class=wrap><h1>HackFindr usage</h1><p class=muted>Anonymous counts: no names, emails or "
            f"addresses are stored. Visitors are counted once a day with a hash that changes daily.</p>"
            f"<div class=card><div class=grid>{tile_html}</div></div>"
            f"<div class=card><h2>Last 14 days</h2><div style='overflow-x:auto'>{table}</div></div>"
            f"<div class=card><h2>Ratings</h2>{stars}<h2 style='margin-top:16px'>What helped most</h2>{useful}</div>"
            f"<div class=card><h2>Comments</h2>{comments}</div></div>")


def _page(title, text):
    import html
    return (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            f"<title>{html.escape(title)}</title><body style='font-family:system-ui;max-width:520px;margin:15vh auto;"
            f"padding:0 16px'><h2>{html.escape(title)}</h2><p>{html.escape(text)}</p><p><a href='/'>Back to HackFindr</a></p>")
