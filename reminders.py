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


# Email look. Email apps ignore most CSS, so everything is inline styles and tables (works in Gmail,
# Outlook and phone mail apps). Colours match the website.
ACCENT, ACCENT_2, INK, MUTED, LINE, PAGE_BG = "#6d5dfc", "#9b5cff", "#1d1b29", "#6f6c85", "#ebe9f5", "#f4f3fb"
FONT = "font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
TYPE_COLORS = {"hackathon": ("#6d5dfc", "#efedff"), "ideathon": ("#6d5dfc", "#efedff"), "workshop": ("#0f9f6e", "#e3f8ef"),
               "webinar": ("#0f9f6e", "#e3f8ef"), "competition": ("#c77700", "#fff3dc"), "conference": ("#0b84c6", "#e2f3fc")}
esc = html.escape


def _nice_day(text):
    try:
        return date.fromisoformat(str(text)[:10]).strftime("%a, %d %b").replace(" 0", " ")
    except ValueError:
        return ""


def _button(url, label, primary=True):
    style = (f"background:{ACCENT};color:#ffffff;border:1px solid {ACCENT};" if primary
             else f"background:#ffffff;color:{ACCENT};border:1px solid #d9d5f7;")
    return (f'<a href="{esc(url)}" style="{style}{FONT}display:inline-block;padding:10px 18px;border-radius:10px;'
            f'font-size:14px;font-weight:600;text-decoration:none;margin:0 8px 8px 0">{label}</a>')


def _pill(text, fg, bg):
    return (f'<span style="{FONT}display:inline-block;background:{bg};color:{fg};font-size:11px;font-weight:700;'
            f'letter-spacing:.04em;text-transform:uppercase;padding:3px 9px;border-radius:999px;margin-right:6px">{esc(text)}</span>')


def _wrap(user, preheader, hero_html, body_html, body_text):
    """The shared email frame: header band, hero, content, footer with the unsubscribe link."""
    first = (user.get("name") or "there").split()[0]
    unsub = unsub_url(user["id"])
    page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta name="color-scheme" content="light">
<style>@media (max-width:520px){{.hf-pad{{padding-left:16px!important;padding-right:16px!important}}
.hf-tag{{display:none!important}} .hf-thumb,.hf-thumb img,.hf-thumb div{{width:48px!important;height:48px!important;line-height:48px!important}}
.hf-thumbcell{{width:58px!important}} .hf-h1{{font-size:21px!important}} .hf-card{{padding:12px!important}}}}</style></head>
<body style="margin:0;padding:0;background:{PAGE_BG}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{esc(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{PAGE_BG}"><tr><td align="center" style="padding:28px 8px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border-radius:20px;overflow:hidden;border:1px solid {LINE}">
  <tr><td style="background:{ACCENT};background-image:linear-gradient(135deg,{ACCENT},{ACCENT_2});padding:22px 28px" class="hf-pad">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="{FONT}font-size:22px;color:#ffffff;letter-spacing:-.01em"><span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:#ff5a7a;border:3px solid rgba(255,255,255,.55);vertical-align:middle;margin-right:8px"></span>Hack<b>Findr</b></td>
      <td align="right" class="hf-tag" style="{FONT}font-size:12px;color:rgba(255,255,255,.85)">Events near you, made for you</td>
    </tr></table>
  </td></tr>
  <tr><td style="padding:28px 28px 8px" class="hf-pad">
    <p style="{FONT}margin:0 0 6px;font-size:15px;color:{MUTED}">Hi {esc(first)} 👋</p>
    {hero_html}
  </td></tr>
  <tr><td style="padding:8px 28px 24px" class="hf-pad">{body_html}</td></tr>
  <tr><td style="background:#faf9fe;border-top:1px solid {LINE};padding:18px 28px" class="hf-pad">
    <p style="{FONT}margin:0;font-size:12px;line-height:1.6;color:{MUTED}">You're getting this because you turned on emails in your
    HackFindr profile. <a href="{esc(base_url())}" style="color:{ACCENT};text-decoration:none">Change email settings</a> ·
    <a href="{esc(unsub)}" style="color:{MUTED}">Unsubscribe</a></p>
  </td></tr>
</table>
<p style="{FONT}font-size:11px;color:#a5a2b8;margin:14px 0 0">Made with ♥ by HackFindr · built at Hackasol 5.0</p>
</td></tr></table></body></html>"""
    text = f"Hi {first},\n\n{body_text}\n\nChange email settings: {base_url()}\nUnsubscribe: {unsub}\n"
    return page, text, unsub


def _event_card(ev, rank=None, score=None, reason=""):
    """One event as a card: poster or coloured tile, type, title, details, reason and buttons."""
    kind = str(ev.get("type") or "event")
    fg, bg = TYPE_COLORS.get(kind, (ACCENT, "#efedff"))
    poster = ev.get("poster_url") if str(ev.get("poster_url") or "").startswith("https://") else ""
    thumb = (f'<img src="{esc(poster)}" width="64" height="64" alt="" style="display:block;width:64px;height:64px;'
             f'border-radius:14px;object-fit:cover;border:1px solid {LINE}">' if poster else
             f'<div style="width:64px;height:64px;border-radius:14px;background:{bg};color:{fg};{FONT}font-size:26px;'
             f'line-height:64px;text-align:center">✦</div>')
    facts = [("📍", ev.get("city") or "Online")]
    if _nice_day(ev.get("end")):
        facts.append(("⏳", "Registration closes " + _nice_day(ev.get("end"))))
    lo, hi = eligibility._team_range(ev.get("team_size"))
    if hi == 1:
        facts.append(("👤", "Solo event"))
    elif lo and hi:
        facts.append(("👥", f"Teams of {lo}–{hi}" if lo != hi else f"Teams of {hi}"))
    if ev.get("is_free") is True:
        facts.append(("🎟️", "Free to join"))
    facts_html = "".join(f'<div style="{FONT}font-size:13px;color:{MUTED};line-height:1.7">{icon}&nbsp; {esc(t)}</div>'
                         for icon, t in facts)
    head = _pill(f"#{rank}", "#ffffff", INK) if rank else ""
    head += _pill(kind, fg, bg)
    if score is not None:
        head += _pill(f"{score}% match", "#ffffff", ACCENT)
    if ev.get("organiser"):
        organiser = f'<div style="{FONT}font-size:13px;color:{MUTED};margin-top:2px">by {esc(ev["organiser"])}</div>'
    else:
        organiser = ""
    why = (f'<div style="{FONT}margin-top:10px;padding:10px 12px;background:#f6f4ff;border-radius:10px;font-size:13px;'
           f'color:{INK}">✨ {esc(reason)}</div>' if reason else "")
    reg = ev.get("register_url") if str(ev.get("register_url") or "").startswith(("http://", "https://")) else ""
    buttons = (_button(reg, "Register now →") if reg else "") + _button(_event_url(ev), "View on HackFindr", primary=not reg)
    return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {LINE};border-radius:16px;margin:0 0 14px">
<tr><td style="padding:16px" class="hf-card"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
  <td valign="top" width="76" class="hf-thumbcell"><div class="hf-thumb">{thumb}</div></td>
  <td valign="top">
    <div style="margin-bottom:6px">{head}</div>
    <div style="{FONT}font-size:17px;font-weight:700;color:{INK};line-height:1.3">{esc(ev.get("title") or "")}</div>
    {organiser}<div style="margin-top:8px">{facts_html}</div>{why}
    <div style="margin-top:14px">{buttons}</div>
  </td></tr></table></td></tr></table>"""


def reminder_email(user, ev, days):
    when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
    urgent = days <= 1
    color, soft = ("#e5484d", "#fdecec") if urgent else ("#c77700", "#fff3dc")
    hero = f"""<table role="presentation" cellpadding="0" cellspacing="0" style="margin:6px 0 4px"><tr>
  <td style="background:{soft};border-radius:16px;padding:14px 18px" align="center">
    <div style="{FONT}font-size:34px;font-weight:800;color:{color};line-height:1">{days}</div>
    <div style="{FONT}font-size:11px;font-weight:700;color:{color};text-transform:uppercase;letter-spacing:.06em">day{"s" if days != 1 else ""} left</div>
  </td>
  <td style="padding-left:16px">
    <h1 class="hf-h1" style="{FONT}margin:0;font-size:24px;line-height:1.25;color:{INK}">Registration closes {when}</h1>
    <p style="{FONT}margin:6px 0 0;font-size:14px;color:{MUTED}">You saved this event. Don't miss your spot.</p>
  </td></tr></table>"""
    title = f"Registration for {ev.get('title')} closes {when}"
    text = f"{title}\n\n{ev.get('title')}\nCloses: {_nice_day(ev.get('end'))}\nRegister: {ev.get('register_url') or _event_url(ev)}"
    page, text, unsub = _wrap(user, f"{ev.get('title')}: registration closes {when}.", hero, _event_card(ev), text)
    return f"{'🔥' if urgent else '⏰'} {title}", page, text, unsub


def digest_email(user, picks):
    """picks: [(event, score, reason)]"""
    hero = (f'<h1 class="hf-h1" style="{FONT}margin:0;font-size:26px;line-height:1.25;color:{INK}">Your top {len(picks)} events this week</h1>'
            f'<p style="{FONT}margin:6px 0 4px;font-size:14px;color:{MUTED}">Picked for your skills and interests, '
            f'and you can join every one of them. Fresh every Monday.</p>')
    body = "".join(_event_card(ev, rank=i, score=score, reason=reason) for i, (ev, score, reason) in enumerate(picks, 1))
    body += (f'<div style="text-align:center;margin-top:6px">{_button(base_url(), "See all events on the map", primary=False)}</div>')
    text = "Your top events this week:\n\n" + "\n".join(
        f"{i}. {ev.get('title')} ({score}% match): {reason}\n   {_event_url(ev)}" for i, (ev, score, reason) in enumerate(picks, 1))
    page, text, unsub = _wrap(user, "Your best-fit hackathons and workshops for this week.", hero, body, text)
    return f"⭐ Your top {len(picks)} events this week", page, text, unsub


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
