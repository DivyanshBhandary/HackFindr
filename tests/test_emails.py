"""Reminder emails, the Monday digest and unsubscribe links (nothing is really sent)."""

import email
import email.policy
import shutil
import unittest
from datetime import timedelta
from unittest import mock

from tests import helpers

import accounts      # noqa: E402
import gemma_client  # noqa: E402
import mailer        # noqa: E402
import reminders     # noqa: E402


def outbox():
    """(subject, message) for every email in the throwaway outbox, oldest first."""
    if not mailer.OUTBOX.exists():
        return []
    out = []
    for path in sorted(mailer.OUTBOX.glob("*.eml")):
        msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
        out.append((str(msg["Subject"]), msg))
    return out


class EmailTestCase(unittest.TestCase):
    def setUp(self):
        helpers.reset_data()
        shutil.rmtree(mailer.OUTBOX, ignore_errors=True)
        self.assertFalse(mailer.live(), "tests must never send real email")


class UnsubscribeTest(EmailTestCase):
    def test_tokens_are_signed_per_user(self):
        token = reminders.unsub_token(7)
        self.assertTrue(reminders.check_token(7, token))
        self.assertFalse(reminders.check_token(8, token))
        self.assertFalse(reminders.check_token(7, token[:-1] + ("0" if token[-1] != "0" else "1")))
        self.assertFalse(reminders.check_token(7, None))
        self.assertIn(f"u=7&t={token}", reminders.unsub_url(7))


class RemindersTest(EmailTestCase):
    def setUp(self):
        super().setUp()
        with accounts.connect() as con:               # only this test's user wants emails
            con.execute("UPDATE users SET reminders = 0, digest = 0")
        self.user = helpers.make_user(self.id(), year="2nd year", skills="Python")   # a fresh user per test
        accounts.set_email_prefs(self.user["id"], reminders=True)
        accounts.set_saved(self.user["id"], ["ev-raipur", "ev-bhilai", "ev-online", "ev-over"])

    def test_due_three_days_and_one_day_before_closing(self):
        import events
        due = {(ev["id"], days) for ev, days in reminders.due_reminders(self.user, events.load_events())}
        self.assertEqual(due, {("ev-raipur", 3), ("ev-bhilai", 1)})

    def test_each_reminder_goes_out_once(self):
        not_monday = helpers.TODAY + timedelta(days=(1 - helpers.TODAY.weekday()) % 7)   # a Tuesday
        self.assertEqual(reminders.check_all(today=not_monday, log=lambda *a: None), 2)
        self.assertEqual(reminders.check_all(today=not_monday, log=lambda *a: None), 0)
        subjects = [s for s, _ in outbox()]
        self.assertEqual(len(subjects), 2)
        self.assertTrue(any(s.startswith("🔥") and "Bhilai Python Workshop" in s for s in subjects))   # 1 day left
        self.assertTrue(any(s.startswith("⏰") and "Raipur AI Hackathon" in s for s in subjects))       # 3 days left

    def test_a_failed_send_is_tried_again_next_time(self):
        not_monday = helpers.TODAY + timedelta(days=(1 - helpers.TODAY.weekday()) % 7)
        with mock.patch.object(mailer, "send", side_effect=OSError("SMTP down")), mock.patch("builtins.print"):
            self.assertEqual(reminders.check_all(today=not_monday, log=lambda *a: None), 0)
        self.assertEqual(reminders.check_all(today=not_monday, log=lambda *a: None), 2)

    def test_monday_digest_once_a_week_even_when_gemma_is_busy(self):
        accounts.set_email_prefs(self.user["id"], reminders=False, digest=True)
        monday = helpers.TODAY - timedelta(days=helpers.TODAY.weekday())
        with mock.patch.object(gemma_client, "ask", side_effect=RuntimeError("503 UNAVAILABLE")):
            self.assertEqual(reminders.check_all(today=monday, log=lambda *a: None), 1)
            self.assertEqual(reminders.check_all(today=monday, log=lambda *a: None), 0)
        subject, msg = outbox()[0]
        self.assertIn("events this week", subject)
        self.assertIn("unsubscribe", msg.get_body(("html",)).get_content().lower())


class EmailContentTest(EmailTestCase):
    def setUp(self):
        super().setUp()
        self.user = helpers.make_user(self.id())
        self.bad = next(e for e in helpers.EVENTS if e["id"] == "ev-xss")

    def test_event_text_is_escaped_and_bad_links_are_dropped(self):
        subject, page, text, unsub = reminders.reminder_email(self.user, self.bad, 1)
        self.assertNotIn("<script>", page)
        self.assertNotIn("<b>bad</b>", page)
        self.assertNotIn("javascript:", page)
        self.assertIn("unsubscribe?u=", unsub)

    def test_sent_email_has_both_parts_and_a_list_unsubscribe_header(self):
        ev = next(e for e in helpers.EVENTS if e["id"] == "ev-raipur")
        where = mailer.send("student@example.com", *reminders.reminder_email(self.user, ev, 3))
        self.assertTrue(where.endswith(".eml"))
        (subject, msg), = outbox()
        self.assertIn("Raipur AI Hackathon", subject)
        self.assertTrue(msg["List-Unsubscribe"].startswith("<http"))
        self.assertIn("https://example.com/raipur", msg.get_body(("plain",)).get_content())
        self.assertIn("Raipur AI Hackathon", msg.get_body(("html",)).get_content())

    def test_test_email_covers_an_event_closing_today(self):
        accounts.set_saved(self.user["id"], ["ev-today"])
        result = reminders.send_test(self.user)
        self.assertIn("data/outbox", result)
        (subject, _), = outbox()
        self.assertIn("Closes Today Hack", subject)

    def test_test_email_without_saved_events_sends_the_digest(self):
        user = helpers.make_user("digest-only", skills="Python")
        reminders.send_test(user)
        (subject, _), = outbox()
        self.assertIn("events this week", subject)

    def test_test_email_with_nothing_to_send(self):
        user = helpers.make_user("empty-user")
        helpers.write_events([])
        self.assertIn("Save an event", reminders.send_test(user))
        self.assertEqual(outbox(), [])


if __name__ == "__main__":
    unittest.main()
