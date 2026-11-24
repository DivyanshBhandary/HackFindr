"""The website's own pages: /share, /qr.png, /stats, /unsubscribe, sign-in and the app page itself."""

import unittest
from unittest import mock

from tests import helpers

from fastapi.testclient import TestClient  # noqa: E402

app = helpers.load_app()
import accounts   # noqa: E402
import auth       # noqa: E402
import reminders  # noqa: E402
import usage      # noqa: E402

CLIENT = None


def setUpModule():
    global CLIENT
    CLIENT = TestClient(auth.build(app.demo, css=app.CSS, head=app.HEAD, ssr_mode=False)).__enter__()


def tearDownModule():
    import gc
    import warnings
    CLIENT.__exit__(None, None, None)
    with warnings.catch_warnings():        # Gradio leaves an event loop open inside its own code; tidy it quietly
        warnings.simplefilter("ignore", ResourceWarning)
        gc.collect()


class RoutesTest(unittest.TestCase):
    def setUp(self):
        CLIENT.cookies.clear()

    def test_app_page_loads(self):
        r = CLIENT.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])

    def test_share_page_and_qr_code(self):
        r = CLIENT.get("/share")
        self.assertEqual(r.status_code, 200)
        self.assertIn("/qr.png", r.text)
        self.assertNotIn("Visit Site", r.text)
        png = CLIENT.get("/qr.png")
        self.assertEqual(png.headers["content-type"], "image/png")
        self.assertTrue(png.content.startswith(b"\x89PNG"))

    def test_share_page_explains_the_ngrok_warning(self):
        with mock.patch.object(auth, "PUBLIC_URL", "https://example-name.ngrok-free.dev"):
            text = CLIENT.get("/share").text
        self.assertIn("https://example-name.ngrok-free.dev/", text)
        self.assertIn("Visit Site", text)

    def test_stats_need_the_key_and_escape_comments(self):
        self.assertEqual(CLIENT.get("/stats").status_code, 403)
        self.assertEqual(CLIENT.get("/stats?key=wrong").status_code, 403)
        usage.add_feedback(5, "Eligibility check", "<script>alert('x')</script>")
        r = CLIENT.get("/stats?key=test-stats-key")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("<script>alert", r.text)
        self.assertIn("&lt;script&gt;", r.text)

    def test_unsubscribe_link(self):
        user = helpers.make_user("unsub-user")
        accounts.set_email_prefs(user["id"], reminders=True, digest=True)
        self.assertEqual(CLIENT.get("/unsubscribe").status_code, 400)
        self.assertEqual(CLIENT.get(f"/unsubscribe?u={user['id']}&t=forged").status_code, 400)
        self.assertEqual(CLIENT.get(f"/unsubscribe?u=abc&t=x").status_code, 400)
        r = CLIENT.get(f"/unsubscribe?u={user['id']}&t={reminders.unsub_token(user['id'])}")
        self.assertEqual(r.status_code, 200)
        saved = accounts.get_user(user["id"])
        self.assertEqual((saved["reminders"], saved["digest"]), (0, 0))

    def test_demo_sign_in_and_out(self):
        self.assertEqual(auth.sign_in_mode(), "demo")
        r = CLIENT.get("/login", follow_redirects=False)
        self.assertEqual(r.headers["location"], "/login/demo")
        r = CLIENT.get("/login/demo", follow_redirects=False)
        self.assertEqual(r.headers["location"], "/")
        self.assertIn("hf_session", CLIENT.cookies)
        r = CLIENT.get("/logout", follow_redirects=False)
        self.assertEqual(r.headers["location"], "/?signed_out=1")

    def test_sign_in_never_sends_people_to_another_site(self):
        for target in ("//evil.example", "https://evil.example", "javascript:alert(1)"):
            CLIENT.cookies.clear()
            CLIENT.get(f"/login?next={target}", follow_redirects=False)
            r = CLIENT.get("/login/demo", follow_redirects=False)
            self.assertEqual(r.headers["location"], "/", target)
        CLIENT.get("/login?next=/?event=ev-raipur", follow_redirects=False)
        self.assertEqual(CLIENT.get("/login/demo", follow_redirects=False).headers["location"], "/?event=ev-raipur")

    def test_google_callback_without_google_goes_home(self):
        r = CLIENT.get("/auth", follow_redirects=False)
        self.assertEqual(r.headers["location"], "/")


if __name__ == "__main__":
    unittest.main()
