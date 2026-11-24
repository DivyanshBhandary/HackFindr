"""The event pipeline: dates, duplicates, coordinates and safe file writes (no network)."""

import json
import os
import unittest
from unittest import mock

from tests import helpers

import events  # noqa: E402


class DevpostDatesTest(unittest.TestCase):
    def test_deadline_from_the_submission_period(self):
        self.assertEqual(events.devpost_end("Aug 31 - Oct 23, 2026"), "2026-10-23")
        self.assertEqual(events.devpost_end("Dec 15, 2026 - Jan 05, 2027"), "2027-01-05")
        self.assertEqual(events.devpost_end("Nov 30, 2026"), "2026-11-30")

    def test_deadline_when_the_period_is_inside_one_month(self):
        self.assertEqual(events.devpost_end("Oct 05 - 27, 2026"), "2026-10-27")
        self.assertEqual(events.devpost_end("Feb 1 - 9, 2027"), "2027-02-09")

    def test_unreadable_text(self):
        self.assertEqual(events.devpost_end("coming soon"), "")
        self.assertEqual(events.devpost_end(None), "")
        self.assertEqual(events.devpost_end("Feb 30 - 31, 2027"), "")


class OverAndDuplicatesTest(unittest.TestCase):
    def test_is_over(self):
        self.assertTrue(events.is_over({"end": helpers.day(-1), "event_end": helpers.day(3)}))
        self.assertTrue(events.is_over({"event_end": helpers.day(-1)}))
        self.assertFalse(events.is_over({"end": helpers.day(0), "event_end": helpers.day(2)}))
        self.assertFalse(events.is_over({}))

    def test_saved_events_leave_out_ended_ones(self):
        helpers.write_events()
        ids = {e["id"] for e in events.load_events()}
        self.assertNotIn("ev-over", ids)
        self.assertIn("ev-today", ids)

    def test_same_event_on_two_sites_is_kept_once(self):
        a = {"title": "Hack CG 2026!", "source": "unstop"}
        b = {"title": "hack cg 2026", "source": "devfolio"}
        c = {"title": "Robo Soccer", "source": "knowafest"}
        d = {"title": "Robo Soccer", "source": "knowafest"}
        self.assertEqual(events.one_per_event([a, b, c, d]), [a, c, d])

    def test_titles_are_tidied(self):
        ev = events.tidy({"title": "  PCB   DESIGN 2 ", "organiser": " IIT\nBhilai ", "city": "Raipur ,"})
        self.assertEqual((ev["title"], ev["organiser"], ev["city"]), ("PCB DESIGN 2", "IIT Bhilai", "Raipur"))


class CoordinatesTest(unittest.TestCase):
    def test_venue_then_city(self):
        evs = events.add_coords([{"mode": "offline", "city": "Main Auditorium, Raipur"},
                                 {"mode": "offline", "city": "Block 45, Bhilai, "},
                                 {"mode": "online", "city": "Raipur", "lat": 1, "lon": 2}])
        self.assertIsNotNone(evs[0]["lat"])
        self.assertIsNotNone(evs[1]["lat"], "a trailing comma must not hide the city")
        self.assertEqual((evs[2]["lat"], evs[2]["lon"]), (None, None))

    def test_unknown_place_has_no_pin(self):
        ev = events.add_coords([{"mode": "offline", "city": "Atlantis Hall"}])[0]
        self.assertIsNone(ev["lat"])

    def test_distance(self):
        self.assertAlmostEqual(events.haversine_km(21.2514, 81.6296, 21.1938, 81.3509), 29.6, delta=1.5)


class SafeFilesTest(unittest.TestCase):
    def test_save_waits_while_another_program_holds_the_file(self):
        path = str(helpers.TMP / "busy.json")
        real = os.replace
        tries = []

        def flaky(src, dst):
            tries.append(1)
            if len(tries) < 3:
                raise PermissionError("in use")
            return real(src, dst)
        with mock.patch.object(events.os, "replace", flaky), mock.patch.object(events.time, "sleep"):
            events.save_json(path, {"a": 1})
        self.assertEqual(events.load_json(path, None), {"a": 1})
        self.assertEqual(len(tries), 3)

    def test_save_never_leaves_half_a_file(self):
        path = str(helpers.TMP / "whole.json")
        events.save_json(path, {"ok": True})
        with mock.patch.object(events.json, "dump", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                events.save_json(path, {"ok": False})
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"ok": True})

    def test_missing_file_gives_the_default(self):
        self.assertEqual(events.load_json(str(helpers.TMP / "nope.json"), []), [])


if __name__ == "__main__":
    unittest.main()
