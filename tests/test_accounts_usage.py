"""Accounts (SQLite) and anonymous usage stats."""

import unittest

from tests import helpers

import accounts  # noqa: E402
import usage     # noqa: E402


class AccountsTest(unittest.TestCase):
    def test_signing_in_twice_is_the_same_account(self):
        a = accounts.sign_in("google", "sub-1", "old@example.com", "Asha")
        b = accounts.sign_in("google", "sub-1", "new@example.com", "Asha")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(b["email"], "new@example.com")
        self.assertNotEqual(accounts.sign_in("demo", "sub-1", "x@example.com")["id"], a["id"])

    def test_profile_fields_are_trimmed_and_checked(self):
        user = helpers.make_user("trim-user")
        saved = accounts.update_profile(user["id"], name="N" * 200, year="7th year", skills="  Python  ",
                                        email="hacker@example.com", id=999)
        self.assertEqual(len(saved["name"]), accounts.PROFILE_FIELDS["name"])
        self.assertEqual(saved["year"], "")
        self.assertEqual(saved["skills"], "Python")
        self.assertEqual(saved["email"], "trim-user@example.com")     # not an editable field
        self.assertEqual(saved["id"], user["id"])

    def test_saved_list_is_deduplicated_and_capped(self):
        user = helpers.make_user("saver")
        self.assertEqual(accounts.set_saved(user["id"], ["a", "b", "a", "", None]), ["a", "b"])
        self.assertEqual(accounts.saved_ids(user["id"]), ["a", "b"])
        self.assertEqual(len(accounts.set_saved(user["id"], [f"e{i}" for i in range(700)])), 500)
        self.assertEqual(accounts.set_saved(user["id"], []), [])

    def test_an_email_is_recorded_once(self):
        user = helpers.make_user("mailer")
        self.assertTrue(accounts.mark_sent(user["id"], "remind3", "ev-1"))
        self.assertFalse(accounts.mark_sent(user["id"], "remind3", "ev-1"))
        accounts.unmark_sent(user["id"], "remind3", "ev-1")
        self.assertTrue(accounts.mark_sent(user["id"], "remind3", "ev-1"))

    def test_deleting_an_account_removes_everything_about_it(self):
        user = helpers.make_user("leaver")
        accounts.set_saved(user["id"], ["a"])
        accounts.add_fit_check(user["id"], "a", "Title", "Good fit")
        accounts.mark_sent(user["id"], "digest", "2026-W01")
        accounts.delete_user(user["id"])
        with accounts.connect() as con:
            for table in ("saved_events", "fit_checks", "sent_emails"):
                n = con.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (user["id"],)).fetchone()[0]
                self.assertEqual(n, 0, table)

    def test_fit_checks_newest_last(self):
        user = helpers.make_user("fitter")
        for i in range(10):
            accounts.add_fit_check(user["id"], f"e{i}", f"T{i}", "Maybe")
        checks = accounts.fit_checks(user["id"])
        self.assertEqual(len(checks), 8)
        self.assertEqual(checks[-1][0], "T9")


class UsageTest(unittest.TestCase):
    def test_counts_and_unique_visitors(self):
        before = usage.summary()
        usage.count("visit", "198.51.100.1")
        usage.count("visit", "198.51.100.1")
        usage.count("visit", "198.51.100.2")
        usage.count("made-up-metric")
        after = usage.summary()
        self.assertEqual(after["totals"]["visit"], before["totals"].get("visit", 0) + 3)
        self.assertEqual(after["visitor_days"], before["visitor_days"] + 2)
        self.assertNotIn("made-up-metric", after["totals"])

    def test_no_addresses_are_stored(self):
        usage.count("visit", "203.0.113.99")
        with accounts.connect() as con:
            rows = [r["who"] for r in con.execute("SELECT who FROM usage_visitors")]
        self.assertTrue(rows)
        self.assertFalse(any("203.0.113.99" in r for r in rows))

    def test_feedback_is_checked(self):
        self.assertFalse(usage.add_feedback(0))
        self.assertFalse(usage.add_feedback(6))
        self.assertFalse(usage.add_feedback("lots"))
        self.assertTrue(usage.add_feedback(5, "Not a feature", "x" * 900))
        last = usage.summary()["comments"][0]
        self.assertEqual(last["useful"], "")
        self.assertEqual(len(last["comment"]), 500)

    def test_stats_key_comes_from_settings(self):
        self.assertEqual(usage.stats_key(), "test-stats-key")


if __name__ == "__main__":
    unittest.main()
