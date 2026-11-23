"""Phase 3: reminder emails and the Monday digest.

- Reminders: for students who turned them on, each saved event gets an email 3 days and
  1 day before registration closes (each one only once).
- Digest: on Mondays, students who chose it get "Your top 5 best-fit events this week".
- Every email has a one-click unsubscribe link (signed, so nobody can unsubscribe someone else).

start() runs check_all() every CHECK_MINUTES in a background thread while the app is open.
Run  python reminders.py  to check once now.
"""

import hashlib
import hmac
import html
import os
import threading
import time
from datetime import date

import accounts
import eligibility
import events
import mailer

CHECK_MINUTES = int(os.environ.get("REMINDER_CHECK_MINUTES", "60") or 60)
REMIND_DAYS = (3, 1)
_thread = None


def base_url():
    return (os.environ.get("HACKFINDR_PUBLIC_URL") or f"http://localhost:{os.environ.get('GRADIO_SERVER_PORT', 7860)}").rstrip("/")


# ---------- unsubscribe links ----------

def _secret():
    import auth
    return auth.session_secret().encode("utf-8")


def unsub_token(user_id):
    return hmac.new(_secret(), f"unsub:{user_id}".encode(), hashlib.sha256).hexdigest()[:32]


def unsub_url(user_id):
    return f"{base_url()}/unsubscribe?u={user_id}&t={unsub_token(user_id)}"


def check_token(user_id, token):
    return hmac.compare_digest(unsub_token(user_id), str(token or ""))


# ---------- building emails ----------

def _days_left(ev):
    try:
        return (date.fromisoformat(str(ev.get("end"))[:10]) - date.today()).days
    except ValueError:
        return None


def _event_url(ev):
    return f"{base_url()}/?event={ev['id']}"


def _wrap(user, title, body_html, body_text):
    """Shared email frame with the footer and unsubscribe link."""
    name = html.escape((user.get("name") or "there").split()[0])
    unsub = unsub_url(user["id"])
    page = (f'<div style="font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:560px;margin:auto;color:#1d1b29">'
            f'<h2 style="color:#6d5dfc;margin:0 0 4px">HackFindr</h2><p>Hi {name},</p>'
            f'<h3 style="margin:16px 0 8px">{html.escape(title)}</h3>{body_html}'
            f'<p style="color:#77748a;font-size:12px;margin-top:28px">You get this because you turned on emails in your '
            f'HackFindr profile. <a href="{unsub}" style="color:#77748a">Unsubscribe</a> or change it in your profile.</p></div>')
    text = f"Hi {(user.get('name') or 'there').split()[0]},\n\n{title}\n\n{body_text}\n\nUnsubscribe: {unsub}\n"
    return page, text, unsub


def _event_block(ev, extra=""):
    when = str(ev.get("end") or "")[:10]
    link = _event_url(ev)
    reg = ev.get("register_url") if str(ev.get("register_url") or "").startswith(("http://", "https://")) else ""
    btn = (f'<a href="{html.escape(reg)}" style="display:inline-block;background:#6d5dfc;color:#fff;padding:8px 14px;'
           f'border-radius:8px;text-decoration:none;margin-right:8px">Register</a>') if reg else ""
    return (f'<div style="border:1px solid #e6e5ee;border-radius:12px;padding:12px 14px;margin:10px 0">'
            f'<b>{html.escape(ev.get("title") or "")}</b><br><span style="color:#77748a;font-size:13px">'
            f'{html.escape(ev.get("city") or "Online")} · registration closes {when}</span>'
            f'{f"<br><span style=font-size:13px>{html.escape(extra)}</span>" if extra else ""}'
            f'<div style="margin-top:8px">{btn}<a href="{link}" style="color:#6d5dfc">View on HackFindr</a></div></div>')


def reminder_email(user, ev, days):
    when = "tomorrow" if days == 1 else f"in {days} days"
    title = f"Registration for {ev.get('title')} closes {when}"
    page, text, unsub = _wrap(user, title, _event_block(ev),
                              f"{ev.get('title')}\nCloses: {str(ev.get('end'))[:10]}\n{ev.get('register_url') or _event_url(ev)}")
    return f"⏰ {title}", page, text, unsub


def digest_email(user, picks):
    """picks: [(event, score, reason)]"""
    title = "Your top 5 best-fit events this week"
    body = "".join(_event_block(ev, f"{score}% · {reason}") for ev, score, reason in picks)
    text = "\n".join(f"- {ev.get('title')} ({score}%): {reason} {_event_url(ev)}" for ev, score, reason in picks)
    page, text, unsub = _wrap(user, title, body, text)
    return f"⭐ {title}", page, text, unsub


# ---------- deciding what to send ----------

def due_reminders(user, all_events):
    """[(event, days)] for this student's saved events that close in exactly 3 or 1 days."""
    by_id = {e["id"]: e for e in all_events}
    out = []
    for event_id in accounts.saved_ids(user["id"]):
        ev = by_id.get(event_id)
        days = _days_left(ev) if ev else None
        if days in REMIND_DAYS:
            out.append((ev, days))
    return out


def digest_picks(user, all_events, use_gemma=True):
    """Top 5 still-open events for the student, Gemma's Best fit if possible, else the quick match."""
    evs = eligibility.annotate(user, [e for e in all_events if (_days_left(e) or 0) >= 0])
    scores = {}
    if use_gemma:
        try:
            scores = eligibility.best_fit(user, evs, accounts.fit_checks(user["id"]))
        except Exception:              # Google busy: fall back to the quick match below
            scores = {}
    scores = scores or eligibility.quick_fit(user, evs)
    by_id = {e["id"]: e for e in evs}
    top = sorted(scores.items(), key=lambda kv: -kv[1][0])[:5]
    return [(by_id[i], s, r) for i, (s, r) in top if i in by_id]


def _send(user, kind, ref, built):
    """Send once: mark first (so two checks at the same time can't both send), undo if sending fails."""
    if not accounts.mark_sent(user["id"], kind, ref):
        return None
    subject, page, text, unsub = built
    try:
        return mailer.send(user["email"], subject, page, text, unsub)
    except Exception as error:
        accounts.unmark_sent(user["id"], kind, ref)
        print(f"Email to user {user['id']} not sent: {error}")
        return None


def check_all(today=None, log=print):
    """One pass: reminders for everyone who wants them, and the digest on Mondays. Returns how many went out."""
    today = today or date.today()
    all_events = events.load_events()
    sent = 0
    for user in accounts.email_users("reminders"):
        for ev, days in due_reminders(user, all_events):
            if _send(user, f"remind{days}", ev["id"], reminder_email(user, ev, days)):
                sent += 1
    if today.weekday() == 0:
        week = f"{today.isocalendar()[0]}-W{today.isocalendar()[1]:02d}"
        for user in accounts.email_users("digest"):
            if not eligibility.has_profile(user):
                continue
            picks = digest_picks(user, all_events)
            if picks and _send(user, "digest", week, digest_email(user, picks)):
                sent += 1
    if sent:
        log(f"Reminders: {sent} email(s) {'sent' if mailer.live() else 'saved to ' + str(mailer.OUTBOX)}")
    return sent


def send_test(user):
    """'Send me a test email' in the profile: a reminder for the soonest-closing saved event, or the digest."""
    all_events = events.load_events()
    by_id = {e["id"]: e for e in all_events}
    saved = [by_id[i] for i in accounts.saved_ids(user["id"]) if i in by_id and (_days_left(by_id[i]) or -1) >= 0]
    if saved:
        ev = min(saved, key=_days_left)
        built = reminder_email(user, ev, _days_left(ev))
    else:
        picks = digest_picks(user, all_events, use_gemma=False)
        if not picks:
            return "Save an event (★) or fill in your skills first, so there is something to send."
        built = digest_email(user, picks)
    subject, page, text, unsub = built
    where = mailer.send(user["email"], "[Test] " + subject, page, text, unsub)
    return (f"Test email sent to {user['email']}." if where == "sent"
            else "Email is not set up yet (no SMTP_USER / SMTP_PASSWORD in .env), so it was saved to "
                 f"data/outbox/ instead.")


def start():
    """Check every CHECK_MINUTES in the background (first check one minute after start)."""
    global _thread
    if _thread and _thread.is_alive():
        return

    def loop():
        time.sleep(60)
        while True:
            try:
                check_all()
            except Exception as error:
                print(f"Reminder check failed: {error}")
            time.sleep(CHECK_MINUTES * 60)
    _thread = threading.Thread(target=loop, daemon=True)
    _thread.start()


if __name__ == "__main__":
    print(f"Checked: {check_all()} email(s).")
