"""Shared setup for HackFindr's tests. Every test file imports this first.

The tests run offline and cost nothing:
- a throwaway data folder (events, caches, database, email outbox), so data/ and the real caches are never touched
- Gemma is faked; any real API call fails the test, so no quota is used
- no emails are sent (no SMTP settings, so mail lands in the throwaway outbox)
- no geocoding requests: city lookups come only from the test cities below

Run all tests from the repo folder:   .venv\\Scripts\\python -m unittest discover -s tests -t . -v
"""

import atexit
import json
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="hackfindr-tests-"))

# Must happen before any HackFindr module is imported: they read these (and .env) at import time.
# An empty value still counts as "set", so the real .env cannot fill it in.
os.environ.update({
    "HACKFINDR_DATA_DIR": str(TMP),
    "HACKFINDR_DB": str(TMP / "data" / "hackfindr.db"),
    "GEMINI_API_KEY": "", "GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": "",
    "SMTP_USER": "", "SMTP_PASSWORD": "", "MAIL_FROM": "",
    "HACKFINDR_PUBLIC": "", "HACKFINDR_PUBLIC_URL": "", "NGROK_AUTHTOKEN": "", "NGROK_DOMAIN": "",
    "SESSION_SECRET": "test-session-secret", "STATS_KEY": "test-stats-key",
    "GEMMA_CACHE": "0", "HACKFINDR_READ_RULES": "0", "GRADIO_ANALYTICS_ENABLED": "False",
    "HACKFINDR_DEMO_LOGIN": "1",
})
sys.path.insert(0, str(ROOT))

TODAY = date.today()


def day(n):
    """The ISO date n days from today (negative = in the past)."""
    return (TODAY + timedelta(days=n)).isoformat()


CITIES = {"raipur": [21.2514, 81.6296], "bhilai": [21.1938, 81.3509], "bengaluru": [12.9716, 77.5946],
          "delhi": [28.6139, 77.2090], "nowhere-ville": None}

EVENTS = [
    {"id": "ev-raipur", "title": "Raipur AI Hackathon", "organiser": "NIT Raipur", "type": "hackathon", "mode": "offline",
     "city": "Raipur", "lat": 21.2514, "lon": 81.6296, "start": day(-10), "end": day(3), "event_end": day(6),
     "is_free": True, "description": "Build AI for social good with Python and machine learning",
     "eligibility": "Open to 2nd year and above B.Tech students", "requirements": "Laptop", "team_size": "2-4",
     "register_url": "https://example.com/raipur", "poster_url": "", "source": "unstop"},
    {"id": "ev-bhilai", "title": "Bhilai Python Workshop", "organiser": "", "type": "workshop", "mode": "offline",
     "city": "Bhilai", "lat": 21.1938, "lon": 81.3509, "start": day(-2), "end": day(1), "event_end": day(2),
     "is_free": False, "description": "Learn Python basics", "eligibility": "Open to all students, first year welcome",
     "requirements": "", "team_size": "1", "register_url": "https://example.com/bhilai", "poster_url": "",
     "source": "knowafest"},
    {"id": "ev-online", "title": "Global Online Ideathon", "organiser": "", "type": "ideathon", "mode": "online",
     "city": "", "lat": None, "lon": None, "start": day(-5), "end": day(8), "event_end": day(12), "is_free": True,
     "description": "Pitch ideas online", "eligibility": "Women only", "requirements": "", "team_size": "",
     "register_url": "https://example.com/online", "poster_url": "", "source": "devpost"},
    {"id": "ev-far", "title": "Bengaluru Robotics Contest", "organiser": "", "type": "competition", "mode": "offline",
     "city": "Bengaluru", "lat": 12.9716, "lon": 77.5946, "start": day(-1), "end": day(15), "event_end": day(21),
     "is_free": None, "description": "", "eligibility": "", "requirements": "", "team_size": "",
     "register_url": "https://example.com/blr", "poster_url": "", "source": "unstop"},
    {"id": "ev-xss", "title": "<script>alert(1)</script> Hack", "organiser": "<b>bad</b>", "type": "hackathon",
     "mode": "online", "city": "", "lat": None, "lon": None, "start": "", "end": day(4), "event_end": "",
     "is_free": None, "description": "<img src=x onerror=alert(1)>", "eligibility": "", "requirements": "",
     "team_size": "", "register_url": "javascript:alert(1)", "poster_url": "javascript:alert(2)", "source": "poster"},
    {"id": "ev-over", "title": "Old Event", "organiser": "", "type": "hackathon", "mode": "online", "city": "",
     "lat": None, "lon": None, "start": day(-30), "end": day(-3), "event_end": day(-1), "is_free": True,
     "description": "", "eligibility": "", "requirements": "", "team_size": "",
     "register_url": "https://example.com/old", "poster_url": "", "source": "devpost"},
    {"id": "ev-today", "title": "Closes Today Hack", "organiser": "", "type": "hackathon", "mode": "online", "city": "",
     "lat": None, "lon": None, "start": day(-7), "end": day(0), "event_end": day(2), "is_free": True,
     "description": "", "eligibility": "", "requirements": "", "team_size": "",
     "register_url": "https://example.com/today", "poster_url": "", "source": "devfolio"},
]


def write_events(evs=None):
    """Put a fresh copy of the test events into the throwaway data folder."""
    with open(TMP / "events.json", "w", encoding="utf-8") as f:
        json.dump(EVENTS if evs is None else evs, f)


def reset_data():
    """Fresh events and empty Gemma caches (the database is kept; tests use their own users)."""
    write_events()
    for name in ("eligibility_cache.json", "bestfit_cache.json", "eligibility_cache.json.lock"):
        try:
            os.remove(TMP / name)
        except OSError:
            pass


write_events()
with open(TMP / "geocache.json", "w", encoding="utf-8") as f:
    json.dump(CITIES, f)
with open(TMP / "summary_cache.json", "w", encoding="utf-8") as f:
    json.dump({}, f)

import events          # noqa: E402  (after the environment above)
import gemma_client    # noqa: E402

gemma_client.CACHE_DIR = TMP / ".cache"      # the call counter must not write to the real .cache


def _no_real_gemma(*args, **kwargs):
    raise AssertionError("A test tried to call the real Gemma API")


gemma_client.get_client = _no_real_gemma
_real_geocode = events.geocode


def _offline_geocode(place):
    """Only the test cities are known; nothing ever goes to the geocoding service."""
    key = str(place or "").strip().lower()
    if key and key not in events._geocache:
        return None, None
    return _real_geocode(place)


events.geocode = _offline_geocode


class FakeGemma:
    """Stands in for gemma_client.ask. Set .reply to text, a function of the prompt, or an exception."""

    def __init__(self, reply="[]"):
        self.reply, self.prompts = reply, []

    def __call__(self, prompt, system=None, thinking=False):
        self.prompts.append(prompt)
        value = self.reply(prompt) if callable(self.reply) else self.reply
        if isinstance(value, Exception):
            raise value
        return value


def event_ids_in(prompt):
    """The ids in a rule-reading prompt."""
    return [item["id"] for item in json.loads(prompt.split("EVENTS:\n", 1)[1])]


class FakeRequest:
    """Enough of a gr.Request / starlette Request for the app's functions."""

    def __init__(self, uid=None, client="127.0.0.1", host="127.0.0.1:7860", headers=None, query=None):
        self.headers = {"host": host, **(headers or {})}
        self.client = SimpleNamespace(host=client)
        self.session = {"uid": uid} if uid else {}
        self.query_params = query or {}


def tunnel_request(uid=None, ip="203.0.113.7"):
    """A visitor coming in through the ngrok tunnel."""
    return FakeRequest(uid, client="127.0.0.1", host="creole-silk-bright.ngrok-free.dev",
                       headers={"x-forwarded-for": ip})


_app = None


def load_app():
    """app_template/app.py as a module (the UI is built, nothing is started)."""
    global _app
    if _app is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("hackfindr_app", ROOT / "app_template" / "app.py")
        _app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_app)
    return _app


def make_user(subject, **profile):
    import accounts
    user = accounts.sign_in("demo", subject, f"{subject}@example.com", "Test Student")
    if profile:
        user = accounts.update_profile(user["id"], **profile)
    return user


def cleanup():
    shutil.rmtree(TMP, ignore_errors=True)


atexit.register(cleanup)
