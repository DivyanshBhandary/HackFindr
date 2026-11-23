"""Phase 2: who can join an event, and which events suit a student best.

1. Rules: Gemma reads each event once and writes down its eligibility rules as JSON
   (years, degrees, branches, team size, women-only, state-only, fee). They are cached in
   eligibility_cache.json, so every event costs one share of a batched call, once.
   Until Gemma has read an event, a quick rule-of-thumb reading from the text is used.
2. Match: plain code compares a profile with those rules -> yes / no / check, with a reason.
3. Best fit: Gemma scores up to 25 already-eligible events against the profile in one call,
   cached per student per day.

Run  python eligibility.py  to read every event now instead of in the background.
"""

import hashlib
import json
import re
import threading
import time
from datetime import date

import events

CACHE_FILE = events.os.path.join(events.DATA_DIR, "eligibility_cache.json")
FIT_CACHE_FILE = events.os.path.join(events.DATA_DIR, "bestfit_cache.json")
RULES_VERSION = 1           # bump to make Gemma re-read every event
BATCH = 8                   # events per Gemma call when reading rules
TOP_N = 25                  # events Gemma ranks for Best fit
BACKGROUND_GAP = 30        # seconds between background batches, so students' own AI calls come first
_lock = threading.RLock()
_worker = None
_last_student_call = 0.0   # when a student last asked for Best fit; background reading waits after it

AUDIENCE = ["school", "ug1", "ug2", "ug3", "ug4", "ug5", "pg", "graduate", "professional"]
YEAR_TOKEN = {"School": "school", "1st year": "ug1", "2nd year": "ug2", "3rd year": "ug3", "4th year": "ug4",
              "5th year": "ug5", "Postgraduate": "pg", "Graduated": "graduate"}
AUDIENCE_NAMES = {"school": "school students", "ug1": "1st years", "ug2": "2nd years", "ug3": "3rd years",
                  "ug4": "4th years", "ug5": "5th years", "pg": "postgraduates", "graduate": "graduates",
                  "professional": "working professionals"}

# Branch names people write in many ways. Each group counts as one branch.
BRANCH_GROUPS = [
    {"cse", "cs", "computer science", "computer engineering", "computer science and engineering", "coe", "cse ai",
     "cse aiml", "ai", "aiml", "ai ml", "artificial intelligence", "data science", "ds", "it", "information technology",
     "software engineering", "mca", "bca", "computer applications"},
    {"ece", "ec", "electronics", "electronics and communication", "electronics and telecommunication", "etc", "entc",
     "vlsi"},
    {"eee", "ee", "electrical", "electrical engineering", "electrical and electronics"},
    {"mech", "me", "mechanical", "mechanical engineering"},
    {"civil", "ce", "civil engineering"},
    {"chem", "chemical", "chemical engineering"},
    {"mba", "bba", "management", "business"},
    {"design", "bdes", "mdes"},
]
OPEN_BRANCH = re.compile(r"\b(any|all|every)\b.*\b(branch|stream|discipline|field)|engineering|b\.?tech|"
                         r"\bstem\b|science|undergrad", re.I)

RULES_PROMPT = """For each event below, read who may take part. Return ONLY a JSON array with one object per event:
{"id": "<event id>",
 "audience": [any of "school","ug1","ug2","ug3","ug4","ug5","pg","graduate","professional"] - who may join; [] if not stated,
 "open_to_all": true if anyone may join (e.g. "open to all", "anyone", "all students"), else false,
 "degrees": ["B.Tech", "MBA", ...] or [] if any/not stated,
 "branches": ["CSE", "ECE", ...] or [] if any/not stated,
 "team_min": number or null, "team_max": number or null,
 "women_only": true/false,
 "states": ["Tamil Nadu", ...] if only students from some states/cities may join, else [],
 "fee": true if a registration fee is mentioned, false if free, null if unknown,
 "note": "one short line on any other rule, or empty"}
Use "ug1".."ug5" for undergraduate years: "2nd year and above" = ["ug2","ug3","ug4","ug5"];
"all college students" = ["ug1","ug2","ug3","ug4","ug5","pg"]. Do not guess rules that are not written.

EVENTS:
"""

FIT_RULE = ("You rank events for ONE student. Score each event 0-100 for how well it suits them: their skills, "
            "interests, year, location (online needs no travel; far offline events score lower) and anything they "
            "checked before. Return ONLY a JSON array: [{\"id\": \"...\", \"score\": 0-100, \"reason\": \"under 14 "
            "words, concrete, e.g. 'Matches your Python + ML skills; online, so no travel'\"}]. Include every id.")


# ---------- reading the rules ----------

def _text_of(ev):
    return " ".join(str(ev.get(k) or "") for k in ("eligibility", "requirements", "team_size", "description"))


def _source_hash(ev):
    raw = f"{RULES_VERSION}|{_text_of(ev)}|{str(ev.get('details_text') or '')[:900]}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _team_range(text):
    m = re.search(r"(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})", str(text or ""))
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return (min(a, b), max(a, b)) if 0 < a <= 20 and 0 < b <= 20 else (None, None)
    m = re.fullmatch(r"\s*(\d{1,2})\s*", str(text or ""))
    return (int(m.group(1)), int(m.group(1))) if m else (None, None)


def quick_rules(ev):
    """Rule-of-thumb reading used until Gemma has read the event. Only claims what is plainly written."""
    text = _text_of(ev)
    low = text.lower()
    tmin, tmax = _team_range(ev.get("team_size"))
    audience = []
    if re.search(r"\b(1st|first)[- ]year|all years|any year|freshers?", low):
        audience = ["ug1", "ug2", "ug3", "ug4", "ug5"]
    open_all = bool(re.search(r"open to (all|everyone|anyone)|anyone can|everyone can|all students|across india", low))
    return {"audience": audience, "open_to_all": open_all, "degrees": [], "branches": [],
            "team_min": tmin, "team_max": tmax, "women_only": bool(re.search(r"\b(women|girls|female)[- ]only\b|only (for )?(women|girls|female)", low)),
            "states": [], "fee": None if ev.get("is_free") is None else not ev["is_free"], "note": "", "by": "quick"}


def _clean(raw):
    """Keep only well-formed fields from Gemma's answer."""
    def strs(v):
        return [str(x)[:40] for x in v if str(x).strip()][:12] if isinstance(v, list) else []

    def num(v):
        try:
            n = int(v)
            return n if 0 < n <= 50 else None
        except (TypeError, ValueError):
            return None
    return {"audience": [a for a in strs(raw.get("audience")) if a in AUDIENCE],
            "open_to_all": raw.get("open_to_all") is True, "degrees": strs(raw.get("degrees")),
            "branches": strs(raw.get("branches")), "team_min": num(raw.get("team_min")),
            "team_max": num(raw.get("team_max")), "women_only": raw.get("women_only") is True,
            "states": strs(raw.get("states")),
            "fee": raw.get("fee") if isinstance(raw.get("fee"), bool) else None,
            "note": str(raw.get("note") or "")[:140], "by": "gemma"}


def _json_list(text):
    m = re.search(r"\[.*\]", text or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else []
    except ValueError:
        return []
    return [d for d in data if isinstance(d, dict)]


def _cache():
    return events.load_json(CACHE_FILE, {})


def rules_for(ev, cache=None):
    """The best rules we have for an event: Gemma's if read and still current, else the quick reading."""
    entry = (cache if cache is not None else _cache()).get(ev["id"])
    if entry and entry.get("hash") == _source_hash(ev):
        return entry["rules"]
    return quick_rules(ev)


def unread(evs, cache=None):
    cache = cache if cache is not None else _cache()
    return [e for e in evs if (cache.get(e["id"]) or {}).get("hash") != _source_hash(e)]


def read_batch(evs):
    """One Gemma call reads the rules of up to BATCH events and caches them."""
    import gemma_client
    items = [{"id": e["id"], "title": e.get("title"), "eligibility": e.get("eligibility"),
              "requirements": e.get("requirements"), "team_size": e.get("team_size"), "is_free": e.get("is_free"),
              "details": str(e.get("details_text") or e.get("description") or "")[:900]} for e in evs]
    reply = gemma_client.ask(RULES_PROMPT + json.dumps(items, ensure_ascii=False))
    got = {d.get("id"): d for d in _json_list(reply)}
    with _lock:
        cache = _cache()
        for e in evs:
            if e["id"] in got:
                cache[e["id"]] = {"hash": _source_hash(e), "rules": _clean(got[e["id"]]), "at": time.time()}
        events.save_json(CACHE_FILE, cache)
    return len([e for e in evs if e["id"] in got])


def read_all(limit=None, log=print, gap=0):
    """Read every event Gemma has not read yet (or whose text changed).
    gap > 0 (background): wait between batches, and longer while students are using the AI."""
    todo = unread(events.load_events())[:limit]
    done = 0
    for i in range(0, len(todo), BATCH):
        if gap:
            time.sleep(gap)
            while time.time() - _last_student_call < 90:
                time.sleep(10)
        try:
            done += read_batch(todo[i:i + BATCH])
        except Exception as error:     # a busy key: stop for now, the next start carries on
            log(f"Eligibility reading paused: {error}")
            break
        log(f"Eligibility rules read for {done} of {len(todo)} events")
    return done


def start_background():
    """Read rules for unread events in a background thread (one at a time, once per start)."""
    global _worker
    if (_worker and _worker.is_alive()) or events.os.environ.get("HACKFINDR_READ_RULES") == "0":
        return
    _worker = threading.Thread(target=read_all, kwargs={"log": lambda *_: None, "gap": BACKGROUND_GAP}, daemon=True)
    _worker.start()


# ---------- matching a profile ----------

def _norm(text):
    return re.sub(r"[^a-z ]", " ", str(text or "").lower().replace("&", " and ")).split()


def _branch_group(name):
    key = " ".join(_norm(name))
    for i, names in enumerate(BRANCH_GROUPS):
        if key in names:
            return i
    return None


def branch_ok(mine, allowed):
    """True / False, or None when we cannot tell."""
    if not allowed or any(OPEN_BRANCH.search(a) for a in allowed):
        return True
    if not mine:
        return None
    g = _branch_group(mine)
    if g is None:
        mine_words = set(_norm(mine))
        return True if any(mine_words & set(_norm(a)) for a in allowed) else None
    groups = {_branch_group(a) for a in allowed}
    if g in groups:
        return True
    return False if None not in groups else None


def _list(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def match(user, rules):
    """('yes' | 'no' | 'check', reason). 'yes' needs a rule that says the student fits."""
    if not user or not rules:
        return "check", ""
    year = YEAR_TOKEN.get(user.get("year") or "")
    checks = []
    if rules.get("women_only"):
        return "check", "Women-only event"
    aud = rules.get("audience") or []
    if aud:
        if not year:
            return "check", "Add your year to your profile"
        if year not in aud:
            return "no", "Open to " + _list([AUDIENCE_NAMES[a] for a in aud if a in AUDIENCE_NAMES])
        checks.append("year")
    b = branch_ok(user.get("branch"), rules.get("branches") or [])
    if b is False:
        return "no", "Only for " + _list(rules["branches"][:4])
    if b is None:
        return "check", "Only for " + _list(rules["branches"][:4])
    try:
        team = int(re.search(r"\d+", str(user.get("team_size") or "")).group(0))
    except AttributeError:
        team = None
    lo, hi = rules.get("team_min"), rules.get("team_max")
    if team and ((lo and team < lo) or (hi and team > hi)):
        size = f"{lo}–{hi}" if lo and hi and lo != hi else str(lo or hi)
        return "check", f"Teams of {size}; yours is {team}"
    if rules.get("states"):
        return "check", "Only for students from " + _list(rules["states"][:3])
    if checks or rules.get("open_to_all"):
        return "yes", ""
    return "check", ""


def annotate(user, evs):
    """Copies of the events with _elig = [status, reason] filled in for this student."""
    cache = _cache()
    out = []
    for e in evs:
        status, reason = match(user, rules_for(e, cache)) if has_profile(user) else ("", "")
        out.append({**e, "_elig": [status, reason]})
    return out


def has_profile(user):
    return bool(user and (user.get("year") or user.get("branch") or user.get("skills")))


# ---------- Best fit ----------

def _words(text):
    return {w for w in re.findall(r"[a-z][a-z+#.]{1,}", str(text or "").lower()) if len(w) > 2}


def prescore(user, ev):
    """Cheap keyword overlap, used to choose which events Gemma ranks."""
    want = _words(user.get("skills")) | _words(user.get("interests"))
    text = _words(" ".join(str(ev.get(k) or "") for k in ("title", "description", "requirements", "details_text")))
    near = 2 if ev.get("distance_km") is None else max(0, 2 - ev["distance_km"] / 300)
    return len(want & text) * 3 + near + (1 if ev.get("_elig", [""])[0] == "yes" else 0)


def candidates(user, evs):
    """Eligible (or maybe), still open events, best keyword matches first; at most TOP_N."""
    ok = [e for e in evs if e.get("_elig", [""])[0] != "no"
          and (events_days_left(e) is None or events_days_left(e) >= 0)]
    ok.sort(key=lambda e: -prescore(user, e))
    return ok[:TOP_N]


def events_days_left(ev):
    try:
        return (date.fromisoformat(str(ev.get("end"))[:10]) - date.today()).days
    except ValueError:
        return None


def _profile_line(user, feedback):
    parts = [f"{k}: {user.get(k)}" for k in ("year", "branch", "college", "city", "skills", "interests", "team_size")
             if user.get(k)]
    if feedback:
        parts.append("Checked before: " + "; ".join(f"{t} -> {v}" for t, v in feedback[-8:]))
    return " | ".join(parts)


def best_fit(user, evs, feedback=(), ask=None):
    """{event_id: (score, reason)} for the top candidates. One Gemma call, cached per student per day."""
    global _last_student_call
    cands = candidates(user, evs)
    if not cands:
        return {}
    line = _profile_line(user, list(feedback))
    key = hashlib.sha1(f"{user['id']}|{date.today()}|{line}|{','.join(sorted(e['id'] for e in cands))}"
                       .encode("utf-8")).hexdigest()
    with _lock:
        cached = events.load_json(FIT_CACHE_FILE, {})
    if key in cached:
        return {k: tuple(v) for k, v in cached[key].items()}
    items = [{"id": e["id"], "title": e.get("title"), "type": e.get("type"), "mode": e.get("mode"),
              "city": e.get("city"), "km": e.get("distance_km"),
              "about": str(e.get("ai_summary") or e.get("description") or "")[:260]} for e in cands]
    _last_student_call = time.time()
    if ask is None:
        import gemma_client
        ask = lambda prompt, rule: gemma_client.ask(prompt, system=rule)
    reply = ask(f"Student: {line}\n\nEvents (JSON): {json.dumps(items, ensure_ascii=False)}", FIT_RULE)
    ids = {e["id"] for e in cands}
    result = {}
    for d in _json_list(reply):
        try:
            score = max(0, min(100, int(d.get("score"))))
        except (TypeError, ValueError):
            continue
        if d.get("id") in ids:
            result[d["id"]] = (score, str(d.get("reason") or "")[:120])
    if result:
        with _lock:
            cached = events.load_json(FIT_CACHE_FILE, {})
            if len(cached) > 300:          # keep the file small
                cached = dict(list(cached.items())[-200:])
            cached[key] = {k: list(v) for k, v in result.items()}
            events.save_json(FIT_CACHE_FILE, cached)
    return result


def quick_fit(user, evs):
    """Best fit without Gemma (used when Google is busy): keyword overlap with the student's skills
    and interests, plus distance. Same shape as best_fit; never cached, so Gemma is tried again next time."""
    want = _words(user.get("skills")) | _words(user.get("interests"))
    result = {}
    for e in candidates(user, evs):
        text = _words(" ".join(str(e.get(k) or "") for k in ("title", "description", "requirements", "details_text")))
        hits = sorted(want & text)[:3]
        km = e.get("distance_km")
        score = 40 + 12 * len(want & text) + (10 if km is None else max(0, 10 - km // 60))
        where = "online, so no travel" if km is None else f"{km} km away"
        why = (f"Mentions {', '.join(hits)}; " if hits else "") + where
        result[e["id"]] = (int(min(score, 90)), why[:1].upper() + why[1:])
    return result


if __name__ == "__main__":
    n = read_all()
    print(f"Done: read {n} events.")
