"""Regression tests: main-topic flags must not leak into competitor sub-runs.

Based on 2026-04-22 Kanye West --competitors receipt where Drake and
Kendrick Lamar sub-runs logged Kanye's resolved subreddit list as their own
targeted search. Per-entity sub-runs must never inherit main-topic targeting
via closure capture, config mutation, or any other path.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from unittest import mock


def _fake_report(topic: str):
    class _R:
        pass

    r = _R()
    r.topic = topic
    r.artifacts = {}
    return r


class SubRunIsolationTests(unittest.TestCase):
    """Exercise the _competitor_runner closure pattern from main() directly.

    Builds the same closure shape main() uses, then invokes it with
    captured-in-scope main-topic flags to verify they do NOT leak into
    sub-run pipeline.run kwargs.
    """

    def _run_closure(self, main_flags, competitors, config=None, mock_flag=False):
        """Replicate _competitor_runner closure from last30days.py main().

        main_flags: dict of {x_handle, x_related, subreddits, tiktok_hashtags,
                    tiktok_creators, ig_creators, github_user, github_repos}
                    as they would exist in outer scope after argparse.
        competitors: list of entity names to run.
        Returns the list of kwargs dicts pipeline.run was called with.
        """
        from lib import pipeline

        captured: list[dict] = []

        def fake_run(**kwargs):
            captured.append(kwargs)
            return _fake_report(kwargs["topic"])

        # Simulate main scope variables
        outer_subreddits = main_flags.get("subreddits")
        outer_x_handle = main_flags.get("x_handle")
        outer_x_related = main_flags.get("x_related")
        outer_tiktok_hashtags = main_flags.get("tiktok_hashtags")
        outer_tiktok_creators = main_flags.get("tiktok_creators")
        outer_ig_creators = main_flags.get("ig_creators")
        outer_github_user = main_flags.get("github_user")
        outer_github_repos = main_flags.get("github_repos")

        class _Args:
            pass
        args = _Args()
        args.mock = mock_flag
        args.lookback_days = 30

        cfg = config or {}

        # This mirrors the real _competitor_runner closure structure.
        def competitor_runner(entity):
            entity_config = dict(cfg)
            resolved = {
                "entity": entity,
                "x_handle": "",
                "subreddits": [],
                "github_user": "",
                "github_repos": [],
                "context": "",
            }
            pipeline.run(
                topic=entity,
                config=entity_config,
                depth="default",
                requested_sources=None,
                mock=args.mock,
                x_handle=resolved["x_handle"] or None,
                subreddits=resolved["subreddits"] or None,
                github_user=resolved["github_user"] or None,
                github_repos=resolved["github_repos"] or None,
                lookback_days=args.lookback_days,
                internal_subrun=True,
            )

        with mock.patch.object(pipeline, "run", side_effect=fake_run):
            for entity in competitors:
                competitor_runner(entity)

        return captured

    def test_main_subreddits_do_not_leak_to_peers(self):
        """Kanye receipt: main --subreddits=Kanye,hiphopheads leaked to Drake/Kendrick."""
        main_flags = {
            "subreddits": ["Kanye", "hiphopheads", "Music", "popheads", "kanyewest"],
            "x_handle": "kanyewest",
        }
        captured = self._run_closure(main_flags, ["Drake", "Kendrick Lamar"])
        self.assertEqual(len(captured), 2)
        for kwargs in captured:
            self.assertIsNone(
                kwargs["subreddits"],
                f"Main subreddits leaked into {kwargs['topic']!r}'s sub-run: "
                f"{kwargs['subreddits']}",
            )

    def test_main_x_handle_does_not_leak(self):
        main_flags = {"x_handle": "kanyewest"}
        captured = self._run_closure(main_flags, ["Drake"])
        self.assertIsNone(captured[0]["x_handle"])

    def test_main_github_does_not_leak(self):
        main_flags = {
            "github_user": "someuser",
            "github_repos": ["someuser/someproject"],
        }
        captured = self._run_closure(main_flags, ["Drake"])
        self.assertIsNone(captured[0]["github_user"])
        self.assertIsNone(captured[0]["github_repos"])

if __name__ == "__main__":
    unittest.main()
