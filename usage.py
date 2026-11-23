"""Phase 5: how many people used HackFindr at the event, and what they thought.

Privacy: no names, emails or IP addresses are stored here. A visitor is counted once a day through a
hash of their address mixed with a secret that changes every day, so visits cannot be traced back
or linked across days. Feedback is anonymous unless the person writes something identifying.

Numbers are shown on /stats?key=... (the key is printed when the app starts).
"""

import hashlib
import secrets
import time
from datetime import date, timedelta

import accounts

METRICS = {          # what we count, with the label used on the stats page
    "visit": "Page opens",
    "search": "Searches and filter changes",
    "details": "Events opened",
    "bestfit": "Best fit rankings",
    "fitcheck": "\"Is this for me?\" checks",
    "chat": "Questions asked in event chat",
    "poster": "Posters read",
    "signin": "Sign-ins",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_counts (
    day     TEXT NOT NULL,
    metric  TEXT NOT NULL,
    n       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, metric)
);
CREATE TABLE IF NOT EXISTS usage_visitors (     -- one row per visitor per day, as a daily-salted hash
    day     TEXT NOT NULL,
    who     TEXT NOT NULL,
    PRIMARY KEY (day, who)
);
CREATE TABLE IF NOT EXISTS feedback (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    at       REAL NOT NULL,
    rating   INTEGER NOT NULL,                  -- 1 to 5
    useful   TEXT NOT NULL DEFAULT '',          -- which feature helped most
    comment  TEXT NOT NULL DEFAULT ''
);
"""
FEATURES = ["Map and distance filter", "Eligibility check", "Best fit / Top 5", "Is this for me?",
            "Event chat", "Add from poster", "Reminder emails", "Something else"]


def init():
    with accounts.connect() as con:
        con.executescript(SCHEMA)


def _salt(day):
    """A secret per day, so visitor hashes from different days can't be matched."""
    path = accounts.DB_PATH.parent / "usage_salt.txt"
    if not path.exists():
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    return hashlib.sha256((path.read_text(encoding="utf-8").strip() + day).encode()).hexdigest()


def count(metric, visitor=None, n=1):
    """Add to today's count. visitor (an address) is only used for the unique-visitor number."""
    if metric not in METRICS:
        return
    day = str(date.today())
    try:
        with accounts.connect() as con:
            con.execute("INSERT INTO usage_counts (day, metric, n) VALUES (?, ?, ?) "
                        "ON CONFLICT (day, metric) DO UPDATE SET n = n + excluded.n", (day, metric, n))
            if visitor:
                who = hashlib.sha256((_salt(day) + str(visitor)).encode()).hexdigest()[:24]
                con.execute("INSERT OR IGNORE INTO usage_visitors (day, who) VALUES (?, ?)", (day, who))
    except Exception:
        pass   # counting must never break the app


def add_feedback(rating, useful="", comment=""):
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        return False
    if not 1 <= rating <= 5:
        return False
    useful = useful if useful in FEATURES else ""
    with accounts.connect() as con:
        con.execute("INSERT INTO feedback (at, rating, useful, comment) VALUES (?, ?, ?, ?)",
                    (time.time(), rating, useful, str(comment or "").strip()[:500]))
    return True


def summary(days=14):
    """Numbers for the stats page: totals, per day, and feedback."""
    since = str(date.today() - timedelta(days=days - 1))
    with accounts.connect() as con:
        totals = {r["metric"]: r["n"] for r in con.execute(
            "SELECT metric, SUM(n) AS n FROM usage_counts GROUP BY metric")}
        per_day = {}
        for r in con.execute("SELECT day, metric, n FROM usage_counts WHERE day >= ? ORDER BY day", (since,)):
            per_day.setdefault(r["day"], {})[r["metric"]] = r["n"]
        visitors = {r["day"]: r["n"] for r in con.execute(
            "SELECT day, COUNT(*) AS n FROM usage_visitors WHERE day >= ? GROUP BY day", (since,))}
        all_visitors = con.execute("SELECT COUNT(*) FROM usage_visitors").fetchone()[0]
        fb = con.execute("SELECT COUNT(*) AS n, AVG(rating) AS avg FROM feedback").fetchone()
        stars = {r["rating"]: r["n"] for r in con.execute("SELECT rating, COUNT(*) AS n FROM feedback GROUP BY rating")}
        useful = [(r["useful"], r["n"]) for r in con.execute(
            "SELECT useful, COUNT(*) AS n FROM feedback WHERE useful != '' GROUP BY useful ORDER BY n DESC")]
        comments = [dict(r) for r in con.execute(
            "SELECT at, rating, useful, comment FROM feedback WHERE comment != '' ORDER BY at DESC LIMIT 50")]
        users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    return {"totals": totals, "per_day": per_day, "visitors": visitors, "visitor_days": all_visitors,
            "feedback_count": fb["n"], "feedback_avg": round(fb["avg"], 2) if fb["avg"] else None,
            "stars": stars, "useful": useful, "comments": comments, "accounts": users}


def stats_key():
    """Secret that opens /stats. From .env (STATS_KEY) or made once and kept in data/."""
    import os
    if os.environ.get("STATS_KEY", "").strip():
        return os.environ["STATS_KEY"].strip()
    path = accounts.DB_PATH.parent / "stats_key.txt"
    if not path.exists():
        path.write_text(secrets.token_urlsafe(12), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


init()
