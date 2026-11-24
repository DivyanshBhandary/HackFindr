"""Eligibility: reading rules (Gemma faked), matching a profile, and Best fit."""

import json
import os
import time
import unittest
from unittest import mock

from tests import helpers
from tests.helpers import FakeGemma, event_ids_in

import eligibility   # noqa: E402
import events        # noqa: E402
import gemma_client  # noqa: E402

ALL_YEARS = ["ug1", "ug2", "ug3", "ug4", "ug5"]


def rules(**kw):
    base = {"audience": [], "open_to_all": False, "degrees": [], "branches": [], "team_min": None, "team_max": None,
            "women_only": False, "states": [], "fee": None, "note": ""}
    return {**base, **kw}


class MatchTest(unittest.TestCase):
    def test_year_outside_the_rules_is_not_eligible_with_reason(self):
        status, reason = eligibility.match({"year": "1st year"}, rules(audience=["ug2", "ug3"]))
        self.assertEqual(status, "no")
        self.assertEqual(reason, "Open to 2nd years and 3rd years")

    def test_year_inside_the_rules_is_eligible(self):
        self.assertEqual(eligibility.match({"year": "2nd year"}, rules(audience=["ug2", "ug3"])), ("yes", ""))

    def test_rule_needs_a_year_the_profile_lacks(self):
        self.assertEqual(eligibility.match({"branch": "CSE"}, rules(audience=["ug2"])),
                         ("check", "Add your year to your profile"))

    def test_women_only_asks_to_check(self):
        self.assertEqual(eligibility.match({"year": "2nd year"}, rules(women_only=True)), ("check", "Women-only event"))

    def test_branch_written_differently_still_matches(self):
        r = rules(audience=ALL_YEARS, branches=["Computer Science and Engineering"])
        self.assertEqual(eligibility.match({"year": "3rd year", "branch": "CSE"}, r)[0], "yes")

    def test_other_branch_is_not_eligible(self):
        status, reason = eligibility.match({"year": "3rd year", "branch": "Mechanical"},
                                           rules(audience=ALL_YEARS, branches=["CSE", "IT"]))
        self.assertEqual((status, reason), ("no", "Only for CSE and IT"))

    def test_any_branch_rule_is_open(self):
        r = rules(audience=ALL_YEARS, branches=["Any branch of engineering"])
        self.assertEqual(eligibility.match({"year": "1st year", "branch": "Civil"}, r)[0], "yes")

    def test_unknown_branch_asks_to_check(self):
        r = rules(audience=ALL_YEARS, branches=["Marine Biology"])
        self.assertEqual(eligibility.match({"year": "1st year", "branch": "Astrophysics"}, r)[0], "check")

    def test_team_too_big_asks_to_check(self):
        r = rules(audience=ALL_YEARS, team_min=2, team_max=4)
        self.assertEqual(eligibility.match({"year": "2nd year", "team_size": "5"}, r), ("check", "Teams of 2–4; yours is 5"))

    def test_state_only_event_asks_to_check(self):
        r = rules(audience=ALL_YEARS, states=["Odisha"])
        self.assertEqual(eligibility.match({"year": "2nd year"}, r), ("check", "Only for students from Odisha"))

    def test_open_to_all_is_eligible_without_a_year(self):
        self.assertEqual(eligibility.match({"skills": "Python"}, rules(open_to_all=True)), ("yes", ""))

    def test_no_rules_written_means_check_details(self):
        self.assertEqual(eligibility.match({"year": "2nd year"}, rules()), ("check", ""))

    def test_no_profile_gives_no_tags(self):
        evs = eligibility.annotate(None, [helpers.EVENTS[0]])
        self.assertEqual(evs[0]["_elig"], ["", ""])
        self.assertFalse(eligibility.has_profile({"name": "Only a name"}))


class QuickRulesTest(unittest.TestCase):
    def test_first_year_text_opens_every_year(self):
        self.assertEqual(eligibility.quick_rules({"eligibility": "Freshers welcome"})["audience"], ALL_YEARS)

    def test_open_to_all_and_women_only(self):
        self.assertTrue(eligibility.quick_rules({"eligibility": "Open to all students"})["open_to_all"])
        self.assertTrue(eligibility.quick_rules({"description": "A women-only coding night"})["women_only"])

    def test_team_sizes(self):
        self.assertEqual(eligibility._team_range("2-4"), (2, 4))
        self.assertEqual(eligibility._team_range("2 to 4 members"), (2, 4))
        self.assertEqual(eligibility._team_range("3"), (3, 3))
        self.assertEqual(eligibility._team_range("100-200"), (None, None))   # not a team size
        self.assertEqual(eligibility._team_range(None), (None, None))

    def test_fee_comes_from_is_free(self):
        self.assertIs(eligibility.quick_rules({"is_free": True})["fee"], False)
        self.assertIsNone(eligibility.quick_rules({})["fee"])


class CleanGemmaAnswerTest(unittest.TestCase):
    def test_bad_values_are_dropped(self):
        got = eligibility._clean({"audience": ["ug2", "aliens", 3], "open_to_all": "yes", "branches": "CSE",
                                  "team_min": "0", "team_max": "999", "women_only": 1, "fee": "maybe",
                                  "note": "x" * 500})
        self.assertEqual(got["audience"], ["ug2"])
        self.assertFalse(got["open_to_all"])          # only a real true counts
        self.assertEqual(got["branches"], [])         # not a list
        self.assertIsNone(got["team_min"])
        self.assertIsNone(got["team_max"])
        self.assertFalse(got["women_only"])
        self.assertIsNone(got["fee"])
        self.assertLessEqual(len(got["note"]), 140)

    def test_json_list_survives_junk(self):
        self.assertEqual(eligibility._json_list("no json here"), [])
        self.assertEqual(eligibility._json_list("[not valid"), [])
        self.assertEqual(eligibility._json_list('Here you go: [{"id": "a"}, 5, "x"] done'), [{"id": "a"}])


class ReadingRulesTest(unittest.TestCase):
    def setUp(self):
        helpers.reset_data()
        self.waits = []
        patcher = mock.patch.object(eligibility, "_wait", lambda s: self.waits.append(s))
        patcher.start()
        self.addCleanup(patcher.stop)

    def answer_all(self, prompt):
        return json.dumps([{"id": i, "audience": ["ug2"], "open_to_all": False} for i in event_ids_in(prompt)])

    def test_reads_every_unread_event_once_then_costs_nothing(self):
        fake = FakeGemma(self.answer_all)
        with mock.patch.object(gemma_client, "ask", fake):
            n = eligibility.read_all(gap=0, log=lambda *a: None)
            self.assertEqual(n, len(events.load_events()))
            self.assertEqual(eligibility.unread(events.load_events()), [])
            calls = len(fake.prompts)
            eligibility.read_all(gap=0, log=lambda *a: None)
        self.assertEqual(len(fake.prompts), calls, "a second run must not call Gemma again")
        self.assertFalse(os.path.exists(eligibility.LOCK_FILE), "the lock must be released")

    def test_batches_are_at_most_eight_events(self):
        many = [dict(helpers.EVENTS[2], id=f"ev-{i}", title=f"Event {i}") for i in range(19)]
        helpers.write_events(many)
        fake = FakeGemma(self.answer_all)
        with mock.patch.object(gemma_client, "ask", fake):
            eligibility.read_all(gap=0, log=lambda *a: None)
        self.assertEqual([len(event_ids_in(p)) for p in fake.prompts], [8, 8, 3])

    def test_uses_minimal_thinking(self):
        seen = []
        with mock.patch.object(gemma_client, "ask", lambda p, system=None, thinking=False: seen.append(thinking) or "[]"):
            eligibility.read_batch(events.load_events()[:2])
        self.assertEqual(seen, ["minimal"])

    def test_an_event_gemma_skips_is_not_asked_again_in_the_same_run(self):
        fake = FakeGemma(lambda p: json.dumps([{"id": i} for i in event_ids_in(p) if i != "ev-raipur"]))
        with mock.patch.object(gemma_client, "ask", fake):
            eligibility.read_all(gap=0, log=lambda *a: None)
        self.assertEqual(len(fake.prompts), 1)
        self.assertEqual([e["id"] for e in eligibility.unread(events.load_events())], ["ev-raipur"])

    def test_busy_google_waits_and_tries_again(self):
        answers = [RuntimeError("Google is busy or says too many requests, even after 3 retries."),
                   RuntimeError("503 UNAVAILABLE: high demand")]
        fake = FakeGemma(lambda p: answers.pop(0) if answers else self.answer_all(p))
        with mock.patch.object(gemma_client, "ask", fake):
            n = eligibility.read_all(gap=0, log=lambda *a: None)
        self.assertEqual(n, len(events.load_events()))
        self.assertEqual(self.waits, [60, 120], "waits 1 min, then 2 min")

    def test_daily_quota_stops_at_once(self):
        try:
            raise ValueError("429 RESOURCE_EXHAUSTED quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier")
        except ValueError as cause:
            error = RuntimeError("Google is busy or says too many requests, even after 3 retries.")
            error.__cause__ = cause
        fake = FakeGemma(error)
        with mock.patch.object(gemma_client, "ask", fake):
            self.assertEqual(eligibility.read_all(gap=0, log=lambda *a: None), 0)
        self.assertEqual(len(fake.prompts), 1)
        self.assertEqual(self.waits, [])

    def test_wrong_key_stops_at_once(self):
        fake = FakeGemma(RuntimeError("403 PERMISSION_DENIED: API key not valid"))
        with mock.patch.object(gemma_client, "ask", fake):
            eligibility.read_all(gap=0, log=lambda *a: None)
        self.assertEqual(len(fake.prompts), 1)

    def test_second_reader_does_not_start_while_the_first_is_busy(self):
        with open(eligibility.LOCK_FILE, "w") as f:
            f.write("12345")
        fake = FakeGemma(self.answer_all)
        with mock.patch.object(gemma_client, "ask", fake):
            self.assertEqual(eligibility.read_all(gap=0, log=lambda *a: None), 0)
        self.assertEqual(fake.prompts, [])
        self.assertTrue(os.path.exists(eligibility.LOCK_FILE), "someone else's lock is left alone")

    def test_a_stale_lock_is_taken_over(self):
        with open(eligibility.LOCK_FILE, "w") as f:
            f.write("12345")
        old = time.time() - eligibility.LOCK_STALE - 5
        os.utime(eligibility.LOCK_FILE, (old, old))
        with mock.patch.object(gemma_client, "ask", FakeGemma(self.answer_all)):
            self.assertGreater(eligibility.read_all(gap=0, log=lambda *a: None), 0)

    def test_background_reader_lets_students_go_first(self):
        eligibility._last_student_call = time.time()

        def wait(seconds):
            self.waits.append(seconds)
            eligibility._last_student_call = 0            # the student is done
        with mock.patch.object(eligibility, "_wait", wait), \
                mock.patch.object(gemma_client, "ask", FakeGemma(self.answer_all)):
            eligibility.read_all(gap=0, log=lambda *a: None, background=True)
        self.assertEqual(self.waits[0], 10)

    def test_rules_are_used_only_while_the_text_is_the_same(self):
        with mock.patch.object(gemma_client, "ask", FakeGemma(self.answer_all)):
            eligibility.read_all(gap=0, log=lambda *a: None)
        ev = next(e for e in events.load_events() if e["id"] == "ev-raipur")
        self.assertEqual(eligibility.rules_for(ev)["by"], "gemma")
        changed = dict(ev, eligibility="Only for final years")
        self.assertEqual(eligibility.rules_for(changed)["by"], "quick")
        self.assertEqual([e["id"] for e in eligibility.unread([changed])], ["ev-raipur"])


class BestFitTest(unittest.TestCase):
    user = {"id": 991, "year": "2nd year", "branch": "CSE", "skills": "Python, machine learning", "interests": "AI"}

    def setUp(self):
        helpers.reset_data()
        self.evs = eligibility.annotate(self.user, events.load_events())

    def test_scores_are_cleaned(self):
        reply = json.dumps([{"id": "ev-raipur", "score": 140, "reason": "Python + ML"},
                            {"id": "ev-bhilai", "score": "abc"}, {"id": "not-an-event", "score": 50},
                            {"id": "ev-far", "score": -3, "reason": "r" * 300}])
        got = eligibility.best_fit(self.user, self.evs, ask=lambda p, r: reply)
        self.assertEqual(got["ev-raipur"], (100, "Python + ML"))
        self.assertEqual(got["ev-far"][0], 0)
        self.assertLessEqual(len(got["ev-far"][1]), 120)
        self.assertNotIn("ev-bhilai", got)
        self.assertNotIn("not-an-event", got)

    def test_one_gemma_call_a_day_per_student(self):
        calls = []

        def ask(prompt, rule):
            calls.append(prompt)
            return json.dumps([{"id": "ev-raipur", "score": 80, "reason": "ok"}])
        first = eligibility.best_fit(self.user, self.evs, ask=ask)
        second = eligibility.best_fit(self.user, self.evs, ask=ask)
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)

    def test_nothing_to_rank_means_no_call(self):
        self.assertEqual(eligibility.best_fit(self.user, [], ask=lambda p, r: self.fail("called Gemma")), {})

    def test_candidates_leave_out_ineligible_and_closed_events(self):
        evs = [dict(e, _elig=["no", "x"]) if e["id"] == "ev-raipur" else e for e in self.evs]
        evs.append(dict(helpers.EVENTS[5], _elig=["yes", ""]))      # registration already closed
        ids = {e["id"] for e in eligibility.candidates(self.user, evs)}
        self.assertNotIn("ev-raipur", ids)
        self.assertNotIn("ev-over", ids)
        self.assertLessEqual(len(eligibility.candidates(self.user, evs * 10)), eligibility.TOP_N)

    def test_quick_fit_without_gemma(self):
        got = eligibility.quick_fit(self.user, self.evs)
        self.assertTrue(got)
        for score, reason in got.values():
            self.assertTrue(0 <= score <= 90)
            self.assertTrue(reason)
        self.assertIn("python", got["ev-raipur"][1].lower())


if __name__ == "__main__":
    unittest.main()
