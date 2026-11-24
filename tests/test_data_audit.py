"""Checks the real data files (events.json and the caches) for anything abnormal. Read-only.

These catch problems in what the fetchers saved: events that can never be hidden once over, broken
dates, links that are not web links, pins in the wrong place, messy titles, malformed Gemma results.
"""

import json
import re
import unittest
from datetime import date

from tests import helpers

ROOT = helpers.ROOT
TYPES = {"hackathon", "ideathon", "workshop", "webinar", "competition", "conference", "quiz", "contest"}
INDIA_SOURCES = {"unstop", "knowafest", "hack2skill"}     # Indian event sites (Devfolio also lists events abroad)
AUDIENCE = {"school", "ug1", "ug2", "ug3", "ug4", "ug5", "pg", "graduate", "professional"}


def load(name, default):
    try:
        with open(ROOT / name, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


EVENTS = load("events.json", [])


def iso(text):
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


@unittest.skipUnless(EVENTS, "no events.json yet: run  python events.py  first")
class EventsFileTest(unittest.TestCase):
    def bad(self, events, show=lambda e: (e.get("source"), e.get("title", "")[:50])):
        return [show(e) for e in events][:8]

    def test_every_event_has_the_basic_fields(self):
        broken = [e for e in EVENTS if not str(e.get("id") or "").strip() or not str(e.get("title") or "").strip()
                  or e.get("mode") not in ("online", "offline") or e.get("type") not in TYPES
                  or e.get("is_free") not in (True, False, None) or not e.get("source")]
        self.assertEqual(self.bad(broken), [])

    def test_ids_are_unique(self):
        ids = [e["id"] for e in EVENTS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_dates_are_readable(self):
        broken = [e for e in EVENTS for k in ("end", "event_end") if e.get(k) and iso(e[k]) is None]
        broken += [e for e in EVENTS if e.get("start") and e["source"] != "devpost" and iso(e["start"]) is None]
        self.assertEqual(self.bad(broken), [])

    def test_every_event_can_be_hidden_once_it_is_over(self):
        """Without a registration deadline or an end date, an event stays listed forever."""
        no_end = [e for e in EVENTS if not iso(e.get("end")) and not iso(e.get("event_end"))]
        self.assertEqual(self.bad(no_end, lambda e: (e["source"], e.get("start"))), [])

    def test_registration_does_not_close_after_the_event_ends(self):
        wrong = [e for e in EVENTS if iso(e.get("end")) and iso(e.get("event_end")) and iso(e["end"]) > iso(e["event_end"])]
        self.assertEqual(self.bad(wrong), [])

    def test_links_are_web_links(self):
        for key in ("register_url", "poster_url"):
            broken = [e for e in EVENTS if e.get(key) and not re.match(r"https?://", e[key])]
            self.assertEqual(self.bad(broken), [], key)
        self.assertEqual(self.bad([e for e in EVENTS if not e.get("register_url")]), [])

    def test_titles_are_tidy(self):
        messy = [e for e in EVENTS for k in ("title", "organiser", "city")
                 if str(e.get(k) or "") != " ".join(str(e.get(k) or "").split())]
        self.assertEqual(self.bad(messy, lambda e: repr(e["title"][:40])), [])

    def test_titles_have_no_html(self):
        html = [e for e in EVENTS if re.search(r"<[a-zA-Z/][^>]*>|&(amp|lt|gt|quot|#\d+);", e["title"])]
        self.assertEqual(self.bad(html), [])

    def test_coordinates(self):
        wrong = [e for e in EVENTS if e.get("lat") is not None
                 and not (-90 <= e["lat"] <= 90 and -180 <= e["lon"] <= 180)]
        online_pins = [e for e in EVENTS if e["mode"] == "online" and e.get("lat") is not None]
        self.assertEqual(self.bad(wrong + online_pins), [])

    def test_indian_events_are_on_the_map_in_india(self):
        """An in-person event that names a place but has no pin is shown nowhere (neither map nor list).
        One with no place at all is fine: the list shows it without a distance."""
        offline = [e for e in EVENTS if e["mode"] == "offline" and e["source"] in INDIA_SOURCES]
        missing = [e for e in offline if e.get("lat") is None and e.get("city")]
        abroad = [e for e in offline if e.get("lat") is not None
                  and not (6 <= e["lat"] <= 37.5 and 68 <= e["lon"] <= 97.5)]
        self.assertEqual(self.bad(missing, lambda e: (e["source"], e.get("city"))), [], "no pin")
        self.assertEqual(self.bad(abroad, lambda e: (e["source"], e.get("city"), e["lat"], e["lon"])), [], "outside India")

    def test_indian_venues_that_failed_to_geocode(self):
        """Events from any site whose place is in India by its own words but got no pin."""
        hints = re.compile(r"india|delhi|mumbai|bengaluru|bangalore|hyderabad|chennai|pune|kolkata|noida|gurugram|"
                           r"university|iit|nit|iiit|college", re.I)
        lost = [e for e in EVENTS if e["mode"] == "offline" and e.get("lat") is None and e.get("city")
                and e["source"] != "devpost" and hints.search(e["city"])]
        self.assertEqual(self.bad(lost, lambda e: (e["source"], e.get("city"))), [])


class CachesTest(unittest.TestCase):
    def test_eligibility_cache_is_well_formed(self):
        cache = load("eligibility_cache.json", {})
        for event_id, entry in cache.items():
            rules = entry.get("rules") or {}
            self.assertTrue(entry.get("hash"), event_id)
            self.assertLessEqual(set(rules.get("audience") or []), AUDIENCE, event_id)
            for key in ("team_min", "team_max"):
                self.assertTrue(rules.get(key) is None or 0 < rules[key] <= 50, (event_id, key))
            self.assertIn(rules.get("by"), ("gemma", "quick"), event_id)

    def test_geocache_is_well_formed(self):
        for place, value in load("geocache.json", {}).items():
            self.assertTrue(value is None or (len(value) == 2 and -90 <= value[0] <= 90 and -180 <= value[1] <= 180),
                            place)

    def test_summary_cache_is_well_formed(self):
        for event_id, summary in load("summary_cache.json", {}).items():
            self.assertIsInstance(summary, dict, event_id)


if __name__ == "__main__":
    unittest.main()
