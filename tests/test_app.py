"""The website's logic: search, filters, cards, limits and the Gemma features (all with Gemma faked)."""

import json
import unittest
from types import SimpleNamespace
from unittest import mock

from tests import helpers
from tests.helpers import FakeGemma, FakeRequest, tunnel_request

app = helpers.load_app()
import accounts      # noqa: E402
import events        # noqa: E402
import gemma_client  # noqa: E402
import usage         # noqa: E402

gr = app.gr


class AppTestCase(unittest.TestCase):
    def setUp(self):
        helpers.reset_data()
        app._usage.clear()
        for name in ("Info", "Warning"):
            patcher = mock.patch.object(gr, name, lambda *a, **k: None)
            patcher.start()
            self.addCleanup(patcher.stop)

    def ids(self, found):
        return [e["id"] for e in found]


class FindEventsTest(AppTestCase):
    def test_nearby_offline_first_then_online_and_nothing_ended(self):
        lat, lon, found = app.find_events("Raipur", 300, "All", "Nearest")
        self.assertIsNotNone(lat)
        ids = self.ids(found)
        self.assertEqual(ids[:2], ["ev-raipur", "ev-bhilai"])          # nearest first
        self.assertIn("ev-online", ids)
        self.assertNotIn("ev-far", ids)                                  # Bengaluru is ~1100 km away
        self.assertNotIn("ev-over", ids)                                 # registration already closed
        self.assertEqual(found[0]["distance_km"], 0)

    def test_bigger_radius_reaches_further(self):
        self.assertIn("ev-far", self.ids(app.find_events("Raipur", 2000, "All", "Nearest")[2]))

    def test_unknown_city_says_so(self):
        out = app.show_events("Nowhere-ville", 300, "All", "Nearest")
        self.assertIn("Couldn't find the city", out[0])
        self.assertEqual(out[1], [])

    def test_chips(self):
        def chip(name, saved=()):
            return set(self.ids(app.find_events("Raipur", 300, name, "Nearest", saved)[2]))
        self.assertEqual(chip("Hackathons"), {"ev-raipur", "ev-online", "ev-xss", "ev-today"})
        self.assertEqual(chip("Workshops"), {"ev-bhilai"})
        self.assertEqual(chip("Free"), {"ev-raipur", "ev-online", "ev-today"})
        self.assertEqual(chip("Open to 1st year"), {"ev-bhilai"})
        self.assertEqual(chip("Saved", {"ev-bhilai", "gone"}), {"ev-bhilai"})
        self.assertIn("ev-today", chip("This week"))
        self.assertNotIn("ev-online", chip("This week"))               # closes in 8 days, held in 12

    def test_search_matches_every_word_of_the_title(self):
        found = app.find_events("Raipur", 300, "All", "Nearest", query="python BHILAI")[2]
        self.assertEqual(self.ids(found), ["ev-bhilai"])

    def test_closing_soon_order(self):
        found = app.find_events("Raipur", 300, "All", "Closing soon")[2]
        left = [app.days_left(e) for e in found]
        self.assertEqual(left, sorted(left, key=lambda d: 9999 if d is None else d))
        self.assertEqual(found[0]["id"], "ev-today")

    def test_i_can_join_uses_the_profile(self):
        user = helpers.make_user("chip-user", year="1st year", skills="Python")
        found = app.find_events("Raipur", 300, "I can join", "Nearest", user=user)[2]
        self.assertEqual(self.ids(found), ["ev-bhilai"])                # the only event open to 1st years
        all_found = app.find_events("Raipur", 300, "All", "Nearest", user=user)[2]
        raipur = next(e for e in all_found if e["id"] == "ev-raipur")
        self.assertEqual(raipur["_elig"][0], "check")                   # rules not read yet: no false "no"

    def test_saved_box_parsing_never_crashes(self):
        self.assertEqual(app.saved_ids("not json"), set())
        self.assertEqual(app.saved_ids('{"a": 1}'), set())
        self.assertEqual(app.saved_ids(None), set())
        self.assertEqual(app.saved_ids('["a", 1]'), {"a", "1"})


class SafeHtmlTest(AppTestCase):
    def setUp(self):
        super().setUp()
        self.bad = next(e for e in helpers.EVENTS if e["id"] == "ev-xss")

    def assertNoMarkup(self, out):
        self.assertNotIn("<script>", out)
        self.assertNotIn("<img src=x", out)
        self.assertNotIn("<b>bad</b>", out)
        self.assertNotIn("javascript:", out)

    def test_card_and_details_escape_event_text(self):
        self.assertNoMarkup(app.card_html(dict(self.bad, _fit=[90, "<i>why</i>"], _elig=["no", "<u>x</u>"])))
        out = app.details_html(self.bad)
        self.assertNoMarkup(out)
        self.assertNotIn("ce-register", out)          # no Register button for a javascript: link

    def test_lists_and_strip_escape_titles(self):
        ev = dict(self.bad, _fit=[90, "<i>why</i>"])
        self.assertNoMarkup(app.top_html([ev]))
        self.assertNoMarkup(app.deadline_strip([ev]))

    def test_map_escapes_tooltips(self):
        out = app.map_html(21.25, 81.63, 300, [dict(self.bad, lat=21.2, lon=81.6)])
        self.assertNoMarkup(out)
        self.assertIn("<iframe srcdoc=", out)

    def test_poster_links_must_be_web_links(self):
        self.assertEqual(app.safe_url("javascript:alert(1)"), "")
        self.assertEqual(app.safe_url(" HTTPS://ok.example "), "HTTPS://ok.example")


class DatesTest(AppTestCase):
    def test_closing_words(self):
        self.assertEqual(app.closes_text({"end": helpers.day(0)}), "closes today")
        self.assertEqual(app.closes_text({"end": helpers.day(1)}), "closes in 1 day")
        self.assertEqual(app.closes_text({"end": helpers.day(3)}), "closes in 3 days")
        self.assertEqual(app.closes_text({"end": helpers.day(-1)}), "")
        self.assertEqual(app.closes_text({"end": ""}), "")

    def test_nice_date_keeps_text_dates(self):
        self.assertEqual(app.nice_date("2026-10-04T09:00:00+05:30"), "4 Oct")
        self.assertEqual(app.nice_date("Oct 20 - Dec 21, 2026"), "Oct 20 - Dec 21, 2026")

    def test_calendar_file(self):
        ev = dict(helpers.EVENTS[0], title="Hack, Build; Ship")
        with open(app.ics_file(ev), encoding="utf-8", newline="") as f:
            text = f.read()
        self.assertEqual(text.count("BEGIN:VEVENT"), 2)
        self.assertNotIn("\n", text.replace("\r\n", ""), "calendar lines must end with CRLF")
        uids = [line for line in text.split("\r\n") if line.startswith("UID:")]
        self.assertEqual(len(set(uids)), 2)
        self.assertIn("SUMMARY:Hack\\, Build\\; Ship", text)
        bad = app.ics_file(next(e for e in helpers.EVENTS if e["id"] == "ev-xss"))
        with open(bad, encoding="utf-8") as f:
            self.assertNotIn("javascript:", f.read())


class VisitorLimitsTest(AppTestCase):
    def test_who_counts_as_this_computer(self):
        self.assertTrue(app.is_local(FakeRequest()))
        self.assertTrue(app.is_local(FakeRequest(host="localhost:7860")))
        self.assertFalse(app.is_local(tunnel_request()))
        self.assertFalse(app.is_local(FakeRequest(client="127.0.0.1", host="creole-silk-bright.ngrok-free.dev")))
        self.assertFalse(app.is_local(FakeRequest(client="172.16.85.20", host="172.16.85.59:7860")))

    def test_no_limits_when_only_this_computer_can_reach_the_app(self):
        with mock.patch.object(app, "GUARDED", False):
            for _ in range(app.AI_PER_VISITOR_HOUR + 5):
                self.assertEqual(app.limit_message(tunnel_request()), "")

    def test_each_visitor_has_an_hourly_allowance(self):
        with mock.patch.object(app, "GUARDED", True):
            for _ in range(app.AI_PER_VISITOR_HOUR):
                self.assertEqual(app.limit_message(tunnel_request(ip="198.51.100.1")), "")
            self.assertIn("hourly limit", app.limit_message(tunnel_request(ip="198.51.100.1")))
            self.assertEqual(app.limit_message(tunnel_request(ip="198.51.100.2")), "")     # someone else
            self.assertEqual(app.limit_message(FakeRequest()), "")                          # this computer

    def test_the_whole_site_has_a_daily_allowance(self):
        with mock.patch.object(app, "GUARDED", True), mock.patch.object(app, "AI_PER_DAY", 3):
            for i in range(3):
                self.assertEqual(app.limit_message(tunnel_request(ip=f"198.51.100.{i}")), "")
            self.assertIn("today's AI allowance", app.limit_message(tunnel_request(ip="198.51.100.9")))

    def test_only_this_computer_may_fetch_once_others_can_reach_the_app(self):
        with mock.patch.object(app, "GUARDED", False):
            self.assertTrue(app.can_fetch(tunnel_request()))
        with mock.patch.object(app, "GUARDED", True):
            self.assertTrue(app.can_fetch(FakeRequest()))
            self.assertFalse(app.can_fetch(tunnel_request()))
            with mock.patch.object(events, "refresh_events", side_effect=AssertionError("fetched")):
                out = app.refresh("Raipur", 300, "All", "Nearest", 50, "[]", "", tunnel_request())
            self.assertIn("ev-raipur", self.ids(out[1]))
        with mock.patch.object(app, "PUBLIC", True):
            self.assertFalse(app.can_fetch(FakeRequest()))


class BestFitInAppTest(AppTestCase):
    def setUp(self):
        super().setUp()
        self.user = helpers.make_user("fit-user", year="2nd year", skills="Python, machine learning")
        self.found = app.find_events("Raipur", 300, "All", "Nearest", user=self.user)[2]

    def test_a_ranking_from_the_cache_is_free(self):
        reply = json.dumps([{"id": "ev-raipur", "score": 88, "reason": "Python + ML"}])
        fake = mock.Mock(return_value=reply)
        with mock.patch.object(app, "GUARDED", True), mock.patch.object(app, "ask_gemma", fake):
            first = app.best_scores(self.user, self.found, tunnel_request())
            second = app.best_scores(self.user, self.found, tunnel_request())
        self.assertEqual(first, second)
        self.assertEqual(fake.call_count, 1)
        used = sum(len(v) for (kind, who), v in app._usage.items() if kind == "ai")
        self.assertEqual(used, 1, "only the real Gemma call counts toward the visitor's allowance")

    def test_limit_reached_gives_the_quick_match_without_calling_gemma(self):
        with mock.patch.object(app, "GUARDED", True), mock.patch.object(app, "AI_PER_DAY", 0), \
                mock.patch.object(app, "ask_gemma", side_effect=AssertionError("called Gemma")):
            got = app.best_scores(self.user, self.found, tunnel_request())
        self.assertTrue(got)

    def test_gemma_busy_gives_the_quick_match(self):
        with mock.patch.object(app, "ask_gemma", side_effect=RuntimeError("503 UNAVAILABLE")):
            got = app.best_scores(self.user, self.found, FakeRequest())
        self.assertIn("ev-raipur", got)

    def test_top_five_row(self):
        reply = json.dumps([{"id": e["id"], "score": 50 + i, "reason": "fits"} for i, e in enumerate(self.found)])
        with mock.patch.object(app, "ask_gemma", return_value=reply):
            row = app.top_for_you(self.found, FakeRequest(uid=self.user["id"]))
        self.assertIn("Top 5 for you", row)
        self.assertLessEqual(row.count("ce-top-item"), 5)
        self.assertEqual(app.top_for_you(self.found, FakeRequest()), "")      # signed out: no row


class GemmaFeaturesTest(AppTestCase):
    def test_is_this_for_me(self):
        self.assertIn("Write your year", app.fit_check("", "ev-raipur")[0])
        self.assertEqual(app.fit_check("2nd year CSE", "no-such-event")[0], "Open an event first.")
        user = helpers.make_user("checker")
        with mock.patch.object(app, "ask_gemma", return_value="**Good fit**\n- Python"):
            answer, me = app.fit_check("2nd year CSE", "ev-raipur", FakeRequest(uid=user["id"]))
        self.assertTrue(answer.startswith("**Good fit**"))
        self.assertEqual(accounts.fit_checks(user["id"]), [("Raipur AI Hackathon", "Good fit")])

    def test_busy_gemma_gives_a_friendly_message(self):
        with mock.patch.object(app, "ask_gemma", side_effect=RuntimeError("503 UNAVAILABLE high demand")):
            answer, _ = app.fit_check("2nd year", "ev-raipur")
        self.assertIn("high demand", answer)

    def test_team_message_and_whatsapp_link(self):
        with mock.patch.object(app, "ask_gemma", return_value="Join my team for Raipur AI Hackathon! & more"):
            text, link = app.team_message("ev-raipur")
        self.assertIn("Raipur AI Hackathon", text)
        self.assertIn("https://wa.me/?text=Join%20my%20team", link)
        with mock.patch.object(app, "ask_gemma", return_value=""):
            self.assertIn("no text", app.team_message("ev-raipur")[0])

    def test_compare_needs_two_events(self):
        self.assertIn("Pick a second event", app.compare_events("ev-raipur", None, ""))
        with mock.patch.object(app, "ask_gemma", return_value="| | A | B |"):
            self.assertEqual(app.compare_events("ev-raipur", "ev-bhilai", ""), "| | A | B |")

    def test_chat_switches_to_the_fallback_model_when_one_is_busy(self):
        used = []

        def stream(model, contents, config):
            used.append(model)
            if model == gemma_client.MODEL:
                raise RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand.")
            yield SimpleNamespace(text="First years ")
            yield SimpleNamespace(text="can join.")
        client = SimpleNamespace(models=SimpleNamespace(generate_content_stream=stream))
        with mock.patch.object(gemma_client, "get_client", return_value=client):
            out = list(app.ask_about_event("Can first years join?", [], "ev-bhilai"))
        self.assertEqual(out[-1][1][-1]["content"], "First years can join.")
        self.assertEqual(used, [gemma_client.MODEL, gemma_client.FALLBACK_MODEL])

    def test_chat_gives_gemma_correctly_named_dates(self):
        prompts = {}

        def stream(model, contents, config):
            prompts[len(prompts)] = contents
            yield SimpleNamespace(text="ok")
        client = SimpleNamespace(models=SimpleNamespace(generate_content_stream=stream))
        with mock.patch.object(gemma_client, "get_client", return_value=client):
            list(app.ask_about_event("When?", [], "ev-raipur"))     # Unstop: start = registration opens
            list(app.ask_about_event("When?", [], "ev-today"))      # Devfolio: start = the event starts
        self.assertIn('"registration_opens"', prompts[0])
        self.assertIn('"event_starts"', prompts[1])
        self.assertNotIn('"registration_opens"', prompts[1])
        self.assertIn('"registration_closes"', prompts[1])

    def test_chat_says_so_when_every_model_is_busy(self):
        def stream(model, contents, config):
            raise RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand.")
            yield  # pragma: no cover
        client = SimpleNamespace(models=SimpleNamespace(generate_content_stream=stream))
        with mock.patch.object(gemma_client, "get_client", return_value=client), mock.patch.object(app.time, "sleep"):
            out = list(app.ask_about_event("Is there a fee?", [], "ev-bhilai"))
        self.assertIn("high demand", out[-1][1][-1]["content"])

    def test_chat_respects_the_visitor_limit(self):
        with mock.patch.object(app, "GUARDED", True), mock.patch.object(app, "AI_PER_DAY", 0):
            out = list(app.ask_about_event("Hi?", [], "ev-bhilai", tunnel_request()))
        self.assertIn("allowance", out[-1][1][-1]["content"])

    def test_summary_is_not_made_when_the_limit_is_reached(self):
        ev = dict(helpers.EVENTS[0], details_text="Long text about the event")
        helpers.write_events([ev])
        with mock.patch.object(app, "GUARDED", True), mock.patch.object(app, "AI_PER_DAY", 0), \
                mock.patch.object(events, "summarize_one", side_effect=AssertionError("called Gemma")):
            out = app.fill_summary("ev-raipur", [ev], tunnel_request())
        self.assertIn("AI summary not available", out)


class PosterTest(AppTestCase):
    def test_extract_poster(self):
        reply = json.dumps({"title": "Hack CG", "type": "hackathon", "date": "2026-12-20", "mode": "offline",
                            "city": "Raipur", "eligibility": "", "requirements": "", "register_link": "https://x.in"})
        with mock.patch.object(gemma_client, "ask_with_image", FakeGemma(reply)):
            out = app.extract_poster("poster.png")
        self.assertEqual(out[1], "Hack CG")
        with mock.patch.object(gemma_client, "ask_with_image", FakeGemma("sorry, no idea")):
            self.assertIn("not valid JSON", app.extract_poster("poster.png")[0])
        with mock.patch.object(gemma_client, "ask_with_image", FakeGemma('{"title": ""}')):
            self.assertIn("no title", app.extract_poster("poster.png")[0])
        self.assertIn("upload a poster", app.extract_poster(None)[0])

    def test_saved_poster_event_is_safe_and_on_the_map(self):
        status, *_ = app.save_poster("Hack CG", "Hackathon", helpers.day(10), "offline", "Raipur", "", "",
                                     "javascript:alert(1)", "Raipur", 300, "All", "Nearest")
        self.assertTrue(status.startswith("Added: Hack CG"))
        ev = next(e for e in events.load_events() if e["title"] == "Hack CG")
        self.assertEqual(ev["register_url"], "")
        self.assertEqual(ev["source"], "poster")
        self.assertIsNotNone(ev["lat"])
        status, *_ = app.save_poster("Online Thing", "webinar", "", "Online", "Raipur", "", "", "", "Raipur", 300,
                                     "All", "Nearest")
        ev = next(e for e in events.load_events() if e["title"] == "Online Thing")
        self.assertEqual((ev["mode"], ev["city"], ev["lat"]), ("online", "", None))
        self.assertIn("needs a title", app.save_poster(" ", "", "", "", "", "", "", "", "Raipur", 300, "All", "Nearest")[0])


class AccountsInAppTest(AppTestCase):
    def test_profile_needs_sign_in(self):
        self.assertEqual(app.save_profile("A", "", "", "", "", "", "", "", False, False, FakeRequest())[0],
                         "Sign in first.")
        self.assertEqual(app.test_email(FakeRequest()), "Sign in first.")

    def test_save_profile_and_email_choices(self):
        user = helpers.make_user("profile-user")
        status, *_ = app.save_profile("Asha", "NIT", "Raipur", "Wizard year", "CSE", "Python", "AI", "3", True, False,
                                      FakeRequest(uid=user["id"]))
        self.assertEqual(status, "Profile saved ✓")
        saved = accounts.get_user(user["id"])
        self.assertEqual((saved["name"], saved["year"], saved["reminders"], saved["digest"]), ("Asha", "", 1, 0))

    def test_delete_account(self):
        user = helpers.make_user("delete-user")
        with self.assertRaises(gr.Error):
            app.delete_account(False, FakeRequest(uid=user["id"]))
        app.delete_account(True, FakeRequest(uid=user["id"]))
        self.assertIsNone(accounts.get_user(user["id"]))

    def test_stars_merge_between_browser_and_account(self):
        user = helpers.make_user("star-user")
        accounts.set_saved(user["id"], ["ev-far"])
        merged, synced = app.sync_saved('["ev-raipur"]', FakeRequest(uid=user["id"]))
        self.assertEqual(set(json.loads(merged)), {"ev-far", "ev-raipur"})
        app.store_saved('["ev-raipur"]', True, FakeRequest(uid=user["id"]))
        self.assertEqual(accounts.saved_ids(user["id"]), ["ev-raipur"])

    def test_feedback(self):
        before = usage.summary()["feedback_count"]
        self.assertIn("Pick a star", app.send_feedback(None, "", "")[0])
        self.assertIn("Thank you", app.send_feedback("★★★★", "Eligibility check", "Nice")[0])
        self.assertEqual(usage.summary()["feedback_count"], before + 1)

    def test_open_details(self):
        out = app.open_details("no-such-event", [])
        self.assertIn("Event not found", out[2])
        out = app.open_details("ev-raipur", app.find_events("Raipur", 300, "All", "Nearest")[2])
        self.assertIn("Raipur AI Hackathon", out[2])
        self.assertIn("Register on Unstop", out[2])

    def test_nearest_city(self):
        self.assertEqual(app.nearest_city("21.20,81.35,123"), "Bhilai")


if __name__ == "__main__":
    unittest.main()
