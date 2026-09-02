import io
import unittest
from contextlib import redirect_stderr
from unittest import mock

from lib import ui


class PromoMessageTests(unittest.TestCase):
    def test_x_promo_mentions_browser_support_and_fallbacks(self):
        msg = ui.PROMO_SINGLE_KEY["x"]
        self.assertIn("Firefox", msg, "Firefox should be listed as supported")
        self.assertIn("Windows", msg, "Windows limitation should be mentioned")
        self.assertIn("FROM_BROWSER=auto", msg, "Chrome opt-in should be mentioned")
        self.assertIn("AUTH_TOKEN", msg, "AUTH_TOKEN/CT0 fallback should be listed")
        self.assertIn("XAI_API_KEY", msg, "XAI_API_KEY fallback should be listed")

    def test_x_promo_does_not_say_firefox_or_safari_without_qualification(self):
        msg = ui.PROMO_SINGLE_KEY["x"]
        self.assertNotIn(
            "Firefox or Safari",
            msg,
            "Promo should not say 'Firefox or Safari' without qualification",
        )
        self.assertNotIn(
            "Chrome/Safari",
            msg,
            "Promo should not list Chrome alongside Safari as if both are default",
        )


class UiV3Tests(unittest.TestCase):
    def test_show_diagnostic_banner_uses_v3_source_model(self):
        diag = {
            "available_sources": ["github", "digg", "arxiv"],
            "providers": {"google": True, "openai": False, "xai": False},
            "x_backend": None,
            "bird_installed": True,
            "bird_authenticated": False,
            "bird_username": None,
        }
        with mock.patch.object(ui, "IS_TTY", False):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                ui.show_diagnostic_banner(diag)
        output = stderr.getvalue()
        self.assertIn("Reddit", output)
        self.assertIn("unavailable", output)
        self.assertIn("Add AUTH_TOKEN/CT0 or XAI_API_KEY", output)
        self.assertNotIn("YouTube", output)
        self.assertNotIn("Web", output)

    def test_show_diagnostic_banner_silent_when_reddit_and_x_available(self):
        diag = {
            "available_sources": ["reddit", "x", "github"],
            "x_backend": "xai",
            "bird_installed": True,
        }
        with mock.patch.object(ui, "IS_TTY", False):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                ui.show_diagnostic_banner(diag)
        self.assertEqual("", stderr.getvalue())

    def test_show_promo_prints_reddit_hint_and_ignores_unknown(self):
        with mock.patch.object(ui, "IS_TTY", False):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                progress = ui.ProgressDisplay("test topic", show_banner=False)
                progress.show_promo("reddit", diag={"available_sources": ["reddit"]})
                progress.show_promo("web")
        output = stderr.getvalue()
        self.assertIn("SCRAPECREATORS_API_KEY", output)
        self.assertNotIn("BRAVE_API_KEY", output)
        self.assertNotIn("yt-dlp", output)

    def test_show_complete_uses_actual_sources_for_source_restricted_runs(self):
        with mock.patch.object(ui, "IS_TTY", False):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                progress = ui.ProgressDisplay("test topic", show_banner=False)
                progress.show_complete(
                    source_counts={"github": 2},
                    display_sources=["github"],
                )
        output = stderr.getvalue()
        self.assertIn("GitHub: 2 repos", output)
        self.assertNotIn("Reddit:", output)
        self.assertNotIn("X:", output)

    def test_show_complete_orders_and_labels_every_surviving_source(self):
        with mock.patch.object(ui, "IS_TTY", False):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                progress = ui.ProgressDisplay("test topic", show_banner=False)
                progress.show_complete(
                    source_counts={
                        "arxiv": 1,
                        "digg": 3,
                        "x": 4,
                        "reddit": 2,
                        "github": 5,
                    },
                    display_sources=["arxiv", "digg", "x", "reddit", "github"],
                )
        output = stderr.getvalue()
        self.assertIn(
            "Reddit: 2 threads, X: 4 posts, GitHub: 5 repos, Digg: 3 clusters, arXiv: 1 paper",
            output,
        )

    def test_show_complete_falls_back_to_title_case_for_unknown_source(self):
        with mock.patch.object(ui, "IS_TTY", False):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                progress = ui.ProgressDisplay("test topic", show_banner=False)
                progress.show_complete(
                    source_counts={"some_feed": 2},
                    display_sources=["some_feed"],
                )
        self.assertIn("Some Feed: 2 results", stderr.getvalue())

if __name__ == "__main__":
    unittest.main()
