"""The Gemma helper: fallback model, retries, friendly errors and the cache (no real calls)."""

import unittest
from types import SimpleNamespace
from unittest import mock

from tests import helpers  # noqa: F401  (sets up the offline environment)

import gemma_client  # noqa: E402


class ApiError(Exception):
    def __init__(self, code, text):
        super().__init__(f"{code} {text}")
        self.code = code


class FallbackTest(unittest.TestCase):
    def test_busy_main_model_goes_to_the_fallback(self):
        used = []

        def call(name):
            used.append(name)
            if name == gemma_client.MODEL:
                raise ApiError(503, "UNAVAILABLE high demand")
            return "ok"
        self.assertEqual(gemma_client.with_fallback(call), "ok")
        self.assertEqual(used, [gemma_client.MODEL, gemma_client.FALLBACK_MODEL])

    def test_rate_limit_also_goes_to_the_fallback(self):
        calls = iter([ApiError(429, "RESOURCE_EXHAUSTED"), "ok"])

        def call(name):
            value = next(calls)
            if isinstance(value, Exception):
                raise value
            return value
        self.assertEqual(gemma_client.with_fallback(call), "ok")

    def test_a_real_error_is_not_hidden(self):
        used = []

        def call(name):
            used.append(name)
            raise ApiError(400, "INVALID_ARGUMENT")
        with self.assertRaises(ApiError):
            gemma_client.with_fallback(call)
        self.assertEqual(used, [gemma_client.MODEL])

    def test_both_busy_raises(self):
        with self.assertRaises(ApiError):
            gemma_client.with_fallback(lambda name: (_ for _ in ()).throw(ApiError(503, "UNAVAILABLE")))

    def test_fallback_can_be_turned_off(self):
        with mock.patch.object(gemma_client, "FALLBACK_MODEL", ""):
            self.assertEqual(gemma_client.models(), [gemma_client.MODEL])


class RetryTest(unittest.TestCase):
    def setUp(self):
        self.sleeps = []
        for patcher in (mock.patch.object(gemma_client.time, "sleep", lambda s: self.sleeps.append(s)),
                        mock.patch("builtins.print")):          # the "Waiting 10 seconds" lines are expected here
            patcher.start()
            self.addCleanup(patcher.stop)
        gemma_client._last_call_time = 0.0

    def test_waits_and_retries_when_busy(self):
        results = iter([ApiError(503, "UNAVAILABLE"), ApiError(429, "RESOURCE_EXHAUSTED"), "done"])

        def call():
            value = next(results)
            if isinstance(value, Exception):
                raise value
            return value
        self.assertEqual(gemma_client._call_api(call), "done")
        self.assertIn(10, self.sleeps)
        self.assertIn(20, self.sleeps)

    def test_gives_up_after_three_retries_and_keeps_the_reason(self):
        def call():
            raise ApiError(503, "UNAVAILABLE high demand")
        with self.assertRaises(RuntimeError) as caught:
            gemma_client._call_api(call)
        self.assertIsInstance(caught.exception.__cause__, ApiError)
        self.assertIn("high demand", gemma_client.describe_error(caught.exception))

    def test_other_errors_are_not_retried(self):
        calls = []

        def call():
            calls.append(1)
            raise ValueError("bad request")
        with self.assertRaises(ValueError):
            gemma_client._call_api(call)
        self.assertEqual(len(calls), 1)


class FriendlyErrorTest(unittest.TestCase):
    def test_messages(self):
        self.assertIn("high demand", gemma_client.describe_error(ApiError(503, "This model is in high demand")))
        self.assertIn("Too many requests", gemma_client.describe_error(ApiError(429, "RESOURCE_EXHAUSTED")))
        self.assertIn("did not accept your key", gemma_client.describe_error(ApiError(403, "PERMISSION_DENIED")))
        self.assertIn("Network problem", gemma_client.describe_error(OSError("Connection reset")))
        self.assertIn("No API key", gemma_client.describe_error(gemma_client.MissingKeyError("No API key found.")))

    def test_a_key_in_an_error_is_hidden(self):
        text = gemma_client.describe_error(ValueError("bad thing AIza" + "x" * 35))
        self.assertNotIn("AIza", text)


class ConfigAndCacheTest(unittest.TestCase):
    def test_thinking_levels(self):
        self.assertIsNone(gemma_client._make_config(None, False))
        self.assertEqual(gemma_client._make_config(None, "minimal").thinking_config.thinking_level.lower(), "minimal")
        self.assertEqual(gemma_client._make_config("rule", True).thinking_config.thinking_level.lower(), "high")

    def test_same_question_twice_costs_one_call(self):
        calls = []

        def generate(contents, config=None):
            calls.append(contents)
            return SimpleNamespace(text="answer")
        with mock.patch.object(gemma_client, "USE_CACHE", True), \
                mock.patch.object(gemma_client, "generate_raw", generate):
            self.assertEqual(gemma_client.ask("unique question 123"), "answer")
            self.assertEqual(gemma_client.ask("unique question 123"), "answer")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
