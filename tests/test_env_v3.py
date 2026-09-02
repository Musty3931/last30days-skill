import os
import unittest
from pathlib import Path
from unittest import mock

from lib import bird_x, env


class EnvV3Tests(unittest.TestCase):
    def setUp(self):
        self._saved_credentials = dict(bird_x._credentials)

    def tearDown(self):
        bird_x._credentials.clear()
        bird_x._credentials.update(self._saved_credentials)

    def test_x_source_prefers_xai_without_bird_probe(self):
        with mock.patch("lib.bird_x.is_bird_authenticated", side_effect=AssertionError("should not probe bird auth")):
            source = env.get_x_source({"XAI_API_KEY": "test"})
        self.assertEqual("xai", source)

    def test_x_source_uses_bird_with_explicit_cookies(self):
        with mock.patch("lib.bird_x.is_bird_installed", return_value=True):
            source = env.get_x_source({"AUTH_TOKEN": "a", "CT0": "b"})
        self.assertEqual("bird", source)
        self.assertEqual("a", bird_x._credentials["AUTH_TOKEN"])
        self.assertEqual("b", bird_x._credentials["CT0"])

    def test_bird_auth_never_checks_browser_cookies(self):
        # The guarantee: is_bird_authenticated() must not spawn any child
        # process to probe for cookies. All subprocess paths in bird_x go
        # through subproc.run_with_timeout, so patching that covers it.
        with mock.patch("lib.bird_x.is_bird_installed", return_value=True), mock.patch(
            "lib.bird_x.subproc.run_with_timeout",
            side_effect=AssertionError("browser-cookie whoami should not run"),
        ):
            bird_x._credentials.clear()
            with mock.patch.dict(os.environ, {}, clear=False):
                self.assertIsNone(bird_x.is_bird_authenticated())

    def test_file_permission_check_skips_windows_posix_mode_bits(self):
        path = mock.Mock(spec=Path)
        with mock.patch.object(env.os, "name", "nt"), mock.patch.object(env.sys.stderr, "write") as write:
            env._check_file_permissions(path)

        path.stat.assert_not_called()
        write.assert_not_called()

class XurlSafePathGatingTests(unittest.TestCase):
    """F1: the safe/diagnose path (probe=False — what doctor uses) never
    runs xurl's live `whoami` network check; it keys on local evidence
    (xurl_x.has_stored_auth) instead."""

    BIRD_OFF = {
        "installed": False,
        "authenticated": False,
        "username": None,
        "can_install": True,
    }

    def _status(self, config, probe, stored_mock, live_mock):
        with mock.patch("lib.bird_x.get_bird_status", return_value=dict(self.BIRD_OFF)), \
             mock.patch("lib.bird_x.set_credentials", lambda *a, **k: None), \
             mock.patch("lib.xurl_x.has_stored_auth", **stored_mock), \
             mock.patch("lib.xurl_x.is_available", **live_mock):
            return env.get_x_source_status(config, probe=probe)

    _LIVE_FORBIDDEN = {
        "side_effect": AssertionError(
            "probe=False must not run the live `xurl whoami` network check"
        )
    }
    _STORED_FORBIDDEN = {
        "side_effect": AssertionError("probe=True should use the live check")
    }

    def test_probe_false_uses_local_evidence_only(self):
        status = self._status(
            {}, probe=False,
            stored_mock={"return_value": True}, live_mock=self._LIVE_FORBIDDEN,
        )
        self.assertEqual("xurl", status["source"])
        self.assertTrue(status["xurl_available"])

    def test_probe_false_without_stored_auth_reports_unavailable(self):
        status = self._status(
            {}, probe=False,
            stored_mock={"return_value": False}, live_mock=self._LIVE_FORBIDDEN,
        )
        self.assertIsNone(status["source"])
        self.assertFalse(status["xurl_available"])

    def test_probe_true_keeps_the_live_check(self):
        status = self._status(
            {}, probe=True,
            stored_mock=self._STORED_FORBIDDEN, live_mock={"return_value": True},
        )
        self.assertEqual("xurl", status["source"])
        self.assertTrue(status["xurl_available"])

    def test_x_backend_chain_local_only_never_calls_live_check(self):
        with mock.patch(
            "lib.xurl_x.is_available",
            side_effect=AssertionError("local_only chain must not run `xurl whoami`"),
        ), mock.patch("lib.xurl_x.has_stored_auth", return_value=True):
            chain = env.x_backend_chain({}, local_only=True)
        self.assertEqual(["xurl"], chain)

    def test_x_backend_chain_default_stays_live(self):
        with mock.patch("lib.xurl_x.is_available", return_value=True), \
             mock.patch(
                 "lib.xurl_x.has_stored_auth",
                 side_effect=AssertionError("default chain should use the live check"),
             ):
            chain = env.x_backend_chain({})
        self.assertEqual(["xurl"], chain)


if __name__ == "__main__":
    unittest.main()
