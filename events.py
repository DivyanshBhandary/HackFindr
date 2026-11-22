"""Data layer: fetch events from Unstop + Devpost, normalize, geocode, save.

Run directly to refresh events.json:  python events.py
"""
import hashlib
import json
import math
import os
import re
import threading
import time
from datetime import date, datetime

import requests

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- Tunable values --------------------------------------------------------
GEMMA_SUMMARY_LIMIT = 0    # new Gemma summaries per refresh; the rest are made when someone opens an event
HTTP_TIMEOUT = 40          # seconds to wait for each API before giving up
REFRESH_HOURS = 6          # auto-refresh interval; lower = fresher events but more calls to the event sites

# ---- Fixed values ----------------------------------------------------------
EVENTS_FILE = os.path.join(HERE, "events.json")
GEOCACHE_FILE = os.path.join(HERE, "geocache.json")
SUMMARY_CACHE_FILE = os.path.join(HERE, "summary_cache.json")
UNSTOP_URL = "https://unstop.com/api/public/opportunity/search-result"
# Unstop category -> (our event type, how many pages of 100 to fetch)
UNSTOP_CATEGORIES = {"hackathons": ("hackathon", 3), "workshops": ("workshop", 1),
                     "conferences": ("conference", 1), "competitions": ("competition", 2)}
DEVPOST_URL = "https://devpost.com/api/hackathons"
DEVPOST_PAGES = 5          # Devpost sends 9 events per page
DEVFOLIO_URL = "https://api.devfolio.co/api/search/hackathons"
HACKEREARTH_URL = "https://www.hackerearth.com/chrome-extension/events/"
MLH_URL = "https://www.mlh.com/seasons/2027/events"
EVENTBRITE_URL = "https://www.eventbrite.com/d/{}/"
# Eventbrite search -> our event type. Its plain workshop lists are mostly not for students, so search tech topics.
EVENTBRITE_SEARCHES = {"india/tech-workshop": "workshop", "india/ai-workshop": "workshop", "india/hackathon": "hackathon",
                       "online/tech-webinar": "webinar", "online/hackathon": "hackathon"}
CLUBS_FILE = os.path.join(HERE, "clubs.json")   # the college club pages to read: name, url, city
HEADERS = {"User-Agent": "Mozilla/5.0 (hackfindr student project)"}


# ---------- small helpers ----------
def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def parse_json(text):
    """Pull the first {...} block out of a Gemma reply. Raises ValueError if there is none."""
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        raise ValueError("Gemma did not return JSON:\n" +(text or "")[:300])
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("Gemma returned JSON, but not an object.")
    return data


def make_id(source, key):
    return source + "-" + hashlib.md5(str(key).encode()).hexdigest()[:8]


def haversine_km(lat1, lon1, lat2, lon2):
    """Straight-line distance between two points on Earth, in km."""
    r = 6371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------- geocoding (cached, 1 request/sec) ----------
_geocache = load_json(GEOCACHE_FILE, {})
_last_geocode = 0.0


def geocode(place):
    """City name -> (lat, lon) or (None, None). Cached in geocache.json."""
    if not place:
        return None, None
    key = place.strip().lower()
    if key in _geocache:
        return tuple(_geocache[key]) if _geocache[key] else (None, None)
    global _last_geocode
    try:
        from geopy.geocoders import Nominatim
        wait = 1.0 - (time.time() - _last_geocode)   # Nominatim rule: max 1 req/sec
        if wait > 0:
            time.sleep(wait)
        _last_geocode = time.time()
        loc = Nominatim(user_agent="hackfindr").geocode(place, timeout=10)
        _geocache[key] = [loc.latitude, loc.longitude] if loc else None
    except Exception as e:
        print("geocode failed for", place, "->", e)
        return None, None          # don't cache network errors
    save_json(GEOCACHE_FILE, _geocache)
    return tuple(_geocache[key]) if _geocache[key] else (None, None)


# ---------- Unstop ----------
def unstop_link(seo_url):
    """seo_url is sometimes a full link and sometimes only a path."""
    return seo_url if seo_url.startswith("http") else "https://unstop.com/" + seo_url.lstrip("/")


def guess_type(title, default):
    """Ideathons and webinars are often listed under other categories, so check the title."""
    low = title.lower()
    for word in ("ideathon", "webinar", "workshop"):
        if word in low:
            return word
    return default


def get_unstop_page(params):
    """One page of Unstop results, trying twice because Unstop sometimes drops the connection."""
    for attempt in (1, 2):
        try:
            r = requests.get(UNSTOP_URL, params=params, headers=HEADERS, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            return r.json()["data"]["data"]
        except Exception as e:
            print("Unstop", params["opportunity"], "page", params["page"], "try", attempt, "failed ->", e)
            time.sleep(3)
    return None


def fetch_unstop():
    events = []
    for category, (event_type, pages) in UNSTOP_CATEGORIES.items():
        for page in range(1, pages + 1):
            params = {"opportunity": category, "oppstatus": "open", "per_page": 100, "page": page}
            items = get_unstop_page(params)
            if items is None:          # keep the pages we already have
                break
            events += unstop_events(items, event_type)
            if len(items) < 100:      # last page reached
                break
    if not events:
        raise RuntimeError("Unstop sent no events")
    return events


def unstop_events(items, event_type):
    """Turn raw Unstop items into our event schema."""
    events = []
    for it in items:
        reg = it.get("regnRequirements") or {}
        if reg.get("reg_status") == "FINISHED":
            continue
        addr = it.get("address_with_country_logo") or {}
        mode = "offline" if (it.get("region") or "").lower() == "offline" else "online"
        city = ", ".join(x for x in [addr.get("city"), addr.get("state")] if x)
        team = f"Team size {reg.get('min_team_size', '?')}-{reg.get('max_team_size', '?')}"
        events.append({
            "id": make_id("unstop", it.get("id") or it.get("title")),
            "title": it.get("title", ""),
            "organiser": (it.get("organisation") or {}).get("name", ""),
            "type": guess_type(it.get("title", ""), event_type),
            "mode": mode,
            "city": city if mode == "offline" else "",
            "start": reg.get("start_regn_dt", ""),
            "end": reg.get("end_regn_dt", ""),
            "event_end": it.get("end_date") or "",    # when the event itself ends
            "is_free": not it.get("isPaid"),
            "description": "",
            "eligibility": "",
            "requirements": team,
            "register_url": unstop_link(it.get("seo_url") or ""),
            "poster_url": it.get("logoUrl2") or "",
            "source": "unstop",
            "details_html": it.get("details") or "",
        })
    return events


# ---------- Devpost ----------
def devpost_end(dates):
    """'Aug 31 - Oct 23, 2026' -> '2026-10-23'. Returns '' if the text looks different."""
    match = re.search(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4})\s*$", dates or "")
    if not match:
        return ""
    try:
        return datetime.strptime(" ".join(match.groups()), "%b %d %Y").date().isoformat()
    except ValueError:
        return ""


def fetch_devpost():
    items = []
    for page in range(1, DEVPOST_PAGES + 1):
        params = {"status[]": ["upcoming", "open"], "page": page}
        r = requests.get(DEVPOST_URL, params=params, headers=HEADERS, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        items += r.json().get("hackathons", [])
    events = []
    for it in items:
        loc = (it.get("displayed_location") or {}).get("location", "")
        mode = "online" if loc.lower() in ("online", "") else "offline"
        thumb = it.get("thumbnail_url") or ""
        if thumb.startswith("//"):
            thumb = "https:" + thumb
        themes = ", ".join(t.get("name", "") for t in it.get("themes", []))
        events.append({
            "id": make_id("devpost", it.get("id") or it.get("url")),
            "title": it.get("title", ""),
            "organiser": it.get("organization_name", ""),
            "type": guess_type(it.get("title", ""), "hackathon"),
            "mode": mode,
            "city": loc if mode == "offline" else "",
            "start": it.get("submission_period_dates", ""),
            "end": devpost_end(it.get("submission_period_dates", "")),
            "description": f"Themes: {themes}" if themes else "",
            "eligibility": "",
            "requirements": "",
            "register_url": it.get("url", ""),
            "event_end": "",
            "is_free": None,                          # Devpost does not say
            "poster_url": thumb,
            "source": "devpost",
        })
    return events


# ---------- Devfolio (Indian hackathons) ----------
def fetch_devfolio():
    body = {"type": "application_open", "from": 0, "size": 30}
    r = requests.post(DEVFOLIO_URL, json=body, headers=HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    events = []
    for hit in r.json().get("hits", {}).get("hits", []):
        it = hit.get("_source", {})
        online = bool(it.get("is_online"))
        city = ", ".join(x for x in [it.get("city"), it.get("state")] if x) or it.get("location") or ""
        slug = it.get("slug", "")
        events.append({
            "id": make_id("devfolio", slug or it.get("name")),
            "title": it.get("name", ""),
            "organiser": "",
            "type": guess_type(it.get("name", ""), "hackathon"),
            "mode": "online" if online or not city else "offline",
            "city": "" if online else city,
            "start": (it.get("starts_at") or "")[:10],
            "end": (it.get("ends_at") or "")[:10],
            "description": it.get("tagline") or "",
            "eligibility": "",
            "requirements": "",
            "register_url": f"https://{slug}.devfolio.co" if slug else "https://devfolio.co/hackathons",
            "event_end": (it.get("ends_at") or "")[:10],
            "is_free": None,
            "poster_url": (it.get("hackathon_setting") or {}).get("logo") or "",
            "source": "devfolio",
        })
    return events


# ---------- HackerEarth (online challenges, hackathons, webinars) ----------
HACKEREARTH_TYPES = {"hackathon": "hackathon", "webinar": "webinar", "hiring": "hackathon"}


def fetch_hackerearth():
    r = requests.get(HACKEREARTH_URL, headers=HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    events = []
    for it in r.json().get("response", []):
        if (it.get("status") or "").upper() == "ENDED":
            continue
        kind = (it.get("challenge_type") or "").lower()
        events.append({
            "id": make_id("hackerearth", it.get("url") or it.get("title")),
            "title": it.get("title", ""),
            "organiser": "HackerEarth",
            "type": next((v for k, v in HACKEREARTH_TYPES.items() if k in kind), "hackathon"),
            "mode": "online",
            "city": "",
            "start": (it.get("start_utc_tz") or it.get("start_tz") or "")[:10],
            "end": (it.get("end_utc_tz") or it.get("end_tz") or "")[:10],
            "description": (it.get("description") or "")[:300],
            "eligibility": "",
            "requirements": "",
            "register_url": it.get("url", ""),
            "event_end": "",
            "is_free": None,
            "poster_url": it.get("cover_image") or "",
            "source": "hackerearth",
        })
    return events


# ---------- MLH (Major League Hacking season page, HTML) ----------
def fetch_mlh():
    from bs4 import BeautifulSoup
    r = requests.get(MLH_URL, headers=HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    # The page keeps its events as JSON in a script tag. A missing key raises, so _refresh keeps the saved events.
    data = BeautifulSoup(r.text, "html.parser").find("script", {"type": "application/json"})
    events = []
    for item in json.loads(data.string)["props"]["upcomingEvents"]:
        address = item.get("venueAddress") or {}
        city = ", ".join(x for x in [address.get("city"), address.get("state")] if x)
        online = item.get("formatType") == "digital" or not city
        events.append({
            "id": make_id("mlh", item["slug"]),
            "title": item["name"],
            "organiser": "MLH",
            "type": "hackathon",
            "mode": "online" if online else "offline",
            "city": "" if online else city,
            "start": item.get("startsAt", ""),
            "end": (item.get("endsAt") or "")[:10],
            "description": "",
            "eligibility": "",
            "requirements": "",
            "register_url": item.get("websiteUrl") or "",
            "event_end": "",
            "is_free": None,
            "poster_url": item.get("logoUrl") or "",
            "source": "mlh",
        })
    return events


def eventbrite_event(item, event_type):
    place = item.get("location") or {}
    address = place.get("address") or {}
    geo = place.get("geo") or {}
    online = "Online" in item.get("eventAttendanceMode", "") or place.get("@type") == "VirtualLocation"
    city = ", ".join(x for x in [address.get("addressLocality"), address.get("addressRegion")] if x)
    return {
        "id": make_id("eventbrite", item["url"]),
        "title": item["name"],
        "organiser": "Eventbrite",
        "type": guess_type(item["name"], event_type),
        "mode": "online" if online or not city else "offline",
        "city": "" if online else city,
        "start": item.get("startDate", ""),
        "end": item.get("endDate", ""),
        "description": short_text(item.get("description", "")),
        "eligibility": "",
        "requirements": "",
        "register_url": item["url"],
        "event_end": "",
        "is_free": None,
        "poster_url": item.get("image", ""),
        "source": "eventbrite",
        "lat": None if online or "latitude" not in geo else float(geo["latitude"]),
        "lon": None if online or "longitude" not in geo else float(geo["longitude"]),
    }


def fetch_eventbrite():
    from bs4 import BeautifulSoup
    events = []
    for search, event_type in EVENTBRITE_SEARCHES.items():
        r = requests.get(EVENTBRITE_URL.format(search), headers=HEADERS, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        blocks = BeautifulSoup(r.text, "html.parser").find_all("script", {"type": "application/ld+json"})
        data = [json.loads(block.string) for block in blocks]
        listing = next(d for d in data if isinstance(d, dict) and d.get("@type") == "ItemList")
        events += [eventbrite_event(row["item"], event_type) for row in listing["itemListElement"]]
    return events


CLUB_PROMPT = ("This is the text of a college club's web page. Find the events on it that have a date. Return ONLY JSON: "
               '{"events": [{"title": "", "type": "hackathon, ideathon, webinar, workshop, competition or conference", '
               '"mode": "online or offline", "city": "", "date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD", '
               '"description": "one sentence for a student"}]}. Use only facts on the page. '
               'If there are no dated events, return {"events": []}.\n\n')


def read_club(club):
    """Ask Gemma for the events on one club page. gemma_client caches by text, so an unchanged page costs no call."""
    import gemma_client
    from bs4 import BeautifulSoup
    r = requests.get(club["url"], headers=HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    text = BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)[:6000]
    events = []
    for item in parse_json(gemma_client.ask(CLUB_PROMPT + text)).get("events", []):
        if not item.get("title") or not item.get("date"):
            continue
        online = item.get("mode") == "online"
        events.append({
            "id": make_id("club", club["url"] + item["title"]),
            "title": item["title"],
            "organiser": club["name"],
            "type": item.get("type") or "workshop",
            "mode": "online" if online else "offline",
            "city": "" if online else item.get("city") or club.get("city", ""),
            "start": item["date"],
            "end": item.get("end_date") or item["date"],
            "description": item.get("description", ""),
            "eligibility": "",
            "requirements": "",
            "register_url": club["url"],
            "event_end": "",
            "is_free": None,
            "poster_url": "",
            "source": "club",
        })
    return events


def fetch_clubs():
    events, saved = [], load_events()
    for club in load_json(CLUBS_FILE, []):
        try:
            events += read_club(club)
        except Exception as e:     # one bad page should not lose that club's saved events
            print("club page failed, keeping its saved events:", club["name"], "->", e)
            events += [ev for ev in saved if ev.get("source") == "club" and ev.get("organiser") == club["name"]]
    return events


# ---------- Gemma summaries of Unstop HTML (cached) ----------
def short_text(text, limit=220):
    """First sentence or two of a long text, cut at a word boundary."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


SUMMARY_PROMPT = ("From this event description, return ONLY JSON with keys "
                  '"description" (2-3 sentence summary for a student), "eligibility" (short), '
                  '"requirements" (short).\n\n')


def apply_summary(ev, summary):
    """Copy Gemma's summary fields onto the event and mark it as AI-summarized."""
    for k in ("description", "eligibility", "requirements"):
        if summary.get(k):
            ev[k] = str(summary[k])
    ev["ai_summary"] = True


def summarize_one(ev):
    """Gemma summary for one event (used when someone opens an event that has none yet).
    Cached in summary_cache.json and saved into events.json, so it runs once per event."""
    cache = load_json(SUMMARY_CACHE_FILE, {})
    if ev["id"] not in cache:
        import gemma_client
        cache[ev["id"]] = parse_json(gemma_client.ask(SUMMARY_PROMPT + ev["details_text"]))
        save_json(SUMMARY_CACHE_FILE, cache)
    apply_summary(ev, cache[ev["id"]])
    events = load_events()
    for i, saved in enumerate(events):
        if saved["id"] == ev["id"]:
            events[i] = {**saved, **{k: ev[k] for k in ("description", "eligibility", "requirements", "ai_summary")}}
    save_json(EVENTS_FILE, events)
    return ev


def summarize_details(events):
    """Ask Gemma to summarize the first N Unstop events. The rest get summarized when someone opens them."""
    cache = load_json(SUMMARY_CACHE_FILE, {})
    done = 0
    from bs4 import BeautifulSoup
    for ev in events:
        html = ev.pop("details_html", "")
        if not html:
            continue
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        ev["details_text"] = text[:4000]   # kept so Gemma can summarize it later, on first view
        if not ev["description"]:      # until Gemma summarizes it, show the start of the real text
            ev["description"] = short_text(text)
        if ev["id"] in cache:
            apply_summary(ev, cache[ev["id"]])
            continue
        if done >= GEMMA_SUMMARY_LIMIT:
            continue
        done += 1
        try:
            import gemma_client
            cache[ev["id"]] = parse_json(gemma_client.ask(SUMMARY_PROMPT + ev["details_text"]))
            save_json(SUMMARY_CACHE_FILE, cache)
            apply_summary(ev, cache[ev["id"]])
        except Exception as e:
            print("Gemma summary skipped for", ev["title"], "->", e)
    return events


# ---------- main entry points ----------
def add_coords(events):
    for ev in events:
        if ev.get("lat") is None and ev["mode"] == "offline":
            ev["lat"], ev["lon"] = geocode(ev["city"])
            if ev["lat"] is None and "," in ev["city"]:   # "Some Hall, Bengaluru" -> try "Bengaluru"
                ev["lat"], ev["lon"] = geocode(ev["city"].rsplit(",", 1)[1].strip())
        elif ev["mode"] != "offline":
            ev["lat"], ev["lon"] = None, None
    return events


def is_over(ev):
    """True if the event (or its registration) has already ended."""
    today = date.today().isoformat()
    ends = [str(ev.get(k) or "")[:10] for k in ("event_end", "end")]
    return any(e and e < today for e in ends)


SOURCES = {
    "unstop": fetch_unstop,
    "devpost": fetch_devpost,
    "devfolio": fetch_devfolio,
    "hackerearth": fetch_hackerearth,
    "mlh": fetch_mlh,
    "eventbrite": fetch_eventbrite,
    "club": fetch_clubs,
}


def same_title(title):
    """'Hack CG 2026!' and 'hack cg 2026' -> the same key, to spot one event listed on two sites."""
    return "".join(ch for ch in title.lower() if ch.isalnum())


def refresh_events():
    """Fetch all sources (one at a time, even if called from two places at once)."""
    with _lock:
        return _refresh()


def _refresh():
    """Fetch live data; on any failure, fall back to the saved events.json."""
    events, saved = [], load_events()
    for source, fetch in SOURCES.items():
        try:
            events += fetch()
        except Exception as e:     # one failing source should not lose its events
            print(fetch.__name__, "failed, keeping its saved events ->", e)
            events += [ev for ev in saved if ev.get("source") == source]
    if not events:
        print("Live fetch failed, using saved events.json")
        return load_events()
    events = list({ev["id"]: ev for ev in events}.values())   # same event in two categories -> keep one
    events = list({same_title(ev["title"]): ev for ev in reversed(events)}.values())[::-1]  # same event on two sites -> keep the first
    for ev in events:                # saved Devpost events from older runs may lack the end date
        if ev["source"] == "devpost" and not ev.get("end"):
            ev["end"] = devpost_end(ev.get("start", ""))
    events = [ev for ev in events if not is_over(ev)]
    events = add_coords(summarize_details(events))
    # keep events added from posters across refreshes
    events += [e for e in load_events() if e.get("source") == "poster"]
    save_json(EVENTS_FILE, events)
    return events


def load_events():
    return load_json(EVENTS_FILE, [])


def add_event(ev):
    """Append one event (e.g. from a poster) to events.json and return it."""
    with _lock:
        events = load_events()
        events.append(ev)
        save_json(EVENTS_FILE, events)
    return ev


_lock = threading.Lock()   # stops a refresh and a poster upload writing events.json at the same time


def start_auto_refresh():
    """Refresh all sources now, then every REFRESH_HOURS, in a background thread."""
    def loop():
        while True:
            try:
                refresh_events()
                print(time.strftime("%H:%M"), "auto-refresh done")
            except Exception as e:
                print("auto-refresh error ->", e)
            time.sleep(REFRESH_HOURS * 3600)
    threading.Thread(target=loop, daemon=True).start()


if __name__ == "__main__":
    evs = refresh_events()
    print(len(evs), "events,", sum(1 for e in evs if e.get("lat")), "with map coordinates")
    for e in evs[:5]:
        print("-", e["title"], "|", e["mode"], "|", e["city"] or "online")
