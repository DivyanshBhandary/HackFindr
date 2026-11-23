"""User accounts for HackFindr, kept in a small SQLite database (data/hackfindr.db).

Events stay in events.json; only people and what they saved live here.
Every function opens its own short connection, so it is safe to call from Gradio's worker threads.
"""

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("HACKFINDR_DB", Path(__file__).resolve().parent / "data" / "hackfindr.db"))

# profile fields a student can edit, with the longest text we keep for each
PROFILE_FIELDS = {"name": 80, "college": 120, "city": 60, "year": 20, "branch": 60,
                  "skills": 300, "interests": 300, "team_size": 10}
YEARS = ["", "1st year", "2nd year", "3rd year", "4th year", "5th year", "Postgraduate", "Graduated", "School"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT NOT NULL,              -- 'google' or 'demo'
    subject     TEXT NOT NULL,              -- the provider's id for this person
    email       TEXT NOT NULL,
    name        TEXT NOT NULL DEFAULT '',
    picture     TEXT NOT NULL DEFAULT '',
    college     TEXT NOT NULL DEFAULT '',
    city        TEXT NOT NULL DEFAULT '',
    year        TEXT NOT NULL DEFAULT '',
    branch      TEXT NOT NULL DEFAULT '',
    skills      TEXT NOT NULL DEFAULT '',
    interests   TEXT NOT NULL DEFAULT '',
    team_size   TEXT NOT NULL DEFAULT '',
    consent_at  REAL NOT NULL,              -- when they agreed to us storing their details
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    UNIQUE (provider, subject)
);
CREATE TABLE IF NOT EXISTS saved_events (
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id  TEXT NOT NULL,
    saved_at  REAL NOT NULL,
    PRIMARY KEY (user_id, event_id)
);
CREATE TABLE IF NOT EXISTS fit_checks (      -- "Is this for me?" answers, so Best fit learns from them
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id  TEXT NOT NULL,
    title     TEXT NOT NULL DEFAULT '',
    verdict   TEXT NOT NULL,                 -- 'Good fit', 'Maybe' or 'Not a fit'
    at        REAL NOT NULL,
    PRIMARY KEY (user_id, event_id)
);
CREATE TABLE IF NOT EXISTS sent_emails (     -- so a reminder or digest is never sent twice
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind      TEXT NOT NULL,                 -- 'remind3', 'remind1', 'digest' or 'test'
    ref       TEXT NOT NULL,                 -- event id for reminders, ISO week for the digest
    at        REAL NOT NULL,
    PRIMARY KEY (user_id, kind, ref)
);
"""
# email choices, added to users after Phase 1 (old databases get them on start)
EMAIL_PREFS = {"reminders": 0, "digest": 0}


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init():
    with connect() as con:
        con.executescript(SCHEMA)
        have = {r["name"] for r in con.execute("PRAGMA table_info(users)")}
        for col, default in EMAIL_PREFS.items():
            if col not in have:
                con.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER NOT NULL DEFAULT {default}")


def set_email_prefs(user_id, reminders=None, digest=None):
    """Turn reminder / digest emails on or off (None leaves a choice as it is)."""
    with connect() as con:
        for col, val in (("reminders", reminders), ("digest", digest)):
            if val is not None:
                con.execute(f"UPDATE users SET {col} = ?, updated_at = ? WHERE id = ?", (int(bool(val)), time.time(), user_id))
    return get_user(user_id)


def email_users(kind):
    """Everyone who chose this kind of email ('reminders' or 'digest')."""
    assert kind in EMAIL_PREFS
    with connect() as con:
        return [dict(r) for r in con.execute(f"SELECT * FROM users WHERE {kind} = 1")]


def mark_sent(user_id, kind, ref):
    """Record an email. Returns False if it was already sent (so the caller skips it)."""
    with connect() as con:
        cur = con.execute("INSERT OR IGNORE INTO sent_emails (user_id, kind, ref, at) VALUES (?, ?, ?, ?)",
                          (user_id, kind, str(ref)[:120], time.time()))
        return cur.rowcount == 1


def unmark_sent(user_id, kind, ref):
    """Forget a record (used when sending failed, so it is tried again later)."""
    with connect() as con:
        con.execute("DELETE FROM sent_emails WHERE user_id = ? AND kind = ? AND ref = ?", (user_id, kind, str(ref)[:120]))


def sign_in(provider, subject, email, name="", picture=""):
    """Create the account on first sign-in, refresh email/picture later. Returns the user as a dict."""
    now = time.time()
    with connect() as con:
        con.execute(
            "INSERT INTO users (provider, subject, email, name, picture, consent_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (provider, subject) DO UPDATE SET email = excluded.email, picture = excluded.picture",
            (provider, str(subject), email, name[:PROFILE_FIELDS["name"]], picture, now, now, now))
        row = con.execute("SELECT * FROM users WHERE provider = ? AND subject = ?", (provider, str(subject))).fetchone()
    return dict(row)


def get_user(user_id):
    if not user_id:
        return None
    with connect() as con:
        row = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def update_profile(user_id, **fields):
    """Save the editable fields; anything else is ignored. Returns the updated user."""
    clean = {k: str(v or "").strip()[:limit] for k, limit in PROFILE_FIELDS.items() if k in fields
             for v in [fields[k]]}
    if "year" in clean and clean["year"] not in YEARS:
        clean["year"] = ""
    if clean:
        sets = ", ".join(f"{k} = ?" for k in clean)
        with connect() as con:
            con.execute(f"UPDATE users SET {sets}, updated_at = ? WHERE id = ?", (*clean.values(), time.time(), user_id))
    return get_user(user_id)


def delete_user(user_id):
    """Remove the person and everything stored about them (saved events go with them)."""
    with connect() as con:
        con.execute("DELETE FROM users WHERE id = ?", (user_id,))


def saved_ids(user_id):
    with connect() as con:
        rows = con.execute("SELECT event_id FROM saved_events WHERE user_id = ? ORDER BY saved_at", (user_id,)).fetchall()
    return [r["event_id"] for r in rows]


def set_saved(user_id, event_ids):
    """Make the account's saved list exactly event_ids (keeps the original save time for ones already there)."""
    ids = list(dict.fromkeys(str(e)[:120] for e in event_ids if e))[:500]
    now = time.time()
    with connect() as con:
        if ids:
            marks = ",".join("?" * len(ids))
            con.execute(f"DELETE FROM saved_events WHERE user_id = ? AND event_id NOT IN ({marks})", (user_id, *ids))
        else:
            con.execute("DELETE FROM saved_events WHERE user_id = ?", (user_id,))
        con.executemany("INSERT OR IGNORE INTO saved_events (user_id, event_id, saved_at) VALUES (?, ?, ?)",
                        [(user_id, e, now) for e in ids])
    return ids


def add_fit_check(user_id, event_id, title, verdict):
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO fit_checks (user_id, event_id, title, verdict, at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, str(event_id)[:120], str(title)[:120], verdict, time.time()))


def fit_checks(user_id, limit=8):
    """[(title, verdict), ...] newest last."""
    with connect() as con:
        rows = con.execute("SELECT title, verdict FROM fit_checks WHERE user_id = ? ORDER BY at DESC LIMIT ?",
                           (user_id, limit)).fetchall()
    return [(r["title"], r["verdict"]) for r in reversed(rows)]


init()
