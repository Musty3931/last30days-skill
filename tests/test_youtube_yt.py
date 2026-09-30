"""Applicable yt-dlp/caption tests ported from upstream v3.25.0.

Direct HTTP, SSH, ScrapeCreators and audio transcription are intentionally
not ported: the fork uses only yt-dlp captions.
"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from lib import youtube_yt
import pytest

@pytest.fixture(autouse=True)
def configured_command(monkeypatch):
    monkeypatch.setattr(youtube_yt, "ytdlp_command", lambda *a: ["yt-dlp"])
    youtube_yt.reset_search_cache()


class TestYtDlpFlags(unittest.TestCase):
    def setUp(self):
        youtube_yt.reset_search_cache()

    def _fake_result(self, stdout: str = "", returncode: int = 0):
        from lib.subproc import SubprocResult
        return SubprocResult(returncode=returncode, stdout=stdout, stderr="")

    def test_search_ignores_global_config_and_browser_cookies(self):
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=self._fake_result()) as run_mock:
            youtube_yt.search_youtube("Claude Code", "2026-02-01", "2026-03-01")

        cmd = run_mock.call_args.args[0]
        self.assertIn("--ignore-config", cmd)
        self.assertIn("--no-cookies-from-browser", cmd)

    def test_transcript_fetch_ignores_global_config_and_browser_cookies(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
             mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=self._fake_result()) as run_mock:
            youtube_yt.fetch_transcript("Example0001", temp_dir)

        cmd = run_mock.call_args.args[0]
        self.assertIn("--ignore-config", cmd)
        self.assertIn("--no-cookies-from-browser", cmd)

class TestYtDlpSubLangs(unittest.TestCase):
    """Verify LAST30DAYS_YT_SUB_LANGS knob and language-agnostic VTT matching."""

    def _fake_result(self, stdout: str = "", returncode: int = 0):
        from lib.subproc import SubprocResult
        return SubprocResult(returncode=returncode, stdout=stdout, stderr="")

    def test_default_sub_langs_when_env_unset(self):
        """When LAST30DAYS_YT_SUB_LANGS is not set, the default is en,es,pt."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LAST30DAYS_YT_SUB_LANGS", None)
            self.assertEqual(youtube_yt._ytdlp_sub_langs(), "en,es,pt")

    def test_env_var_overrides_default(self):
        with mock.patch.dict(os.environ, {"LAST30DAYS_YT_SUB_LANGS": "fr,de"}):
            self.assertEqual(youtube_yt._ytdlp_sub_langs(), "fr,de")

    def test_env_var_normalizes_whitespace_and_case(self):
        with mock.patch.dict(os.environ, {"LAST30DAYS_YT_SUB_LANGS": " EN , Es , PT "}):
            self.assertEqual(youtube_yt._ytdlp_sub_langs(), "en,es,pt")

    def test_env_var_handles_empty_segments(self):
        with mock.patch.dict(os.environ, {"LAST30DAYS_YT_SUB_LANGS": "en,,pt,"}):
            self.assertEqual(youtube_yt._ytdlp_sub_langs(), "en,pt")

    def test_env_var_empty_string_falls_back_to_default(self):
        with mock.patch.dict(os.environ, {"LAST30DAYS_YT_SUB_LANGS": "   "}):
            self.assertEqual(youtube_yt._ytdlp_sub_langs(), "en,es,pt")

    def test_transcript_cmd_uses_default_sub_langs(self):
        """Regression: the --sub-lang arg is en,es,pt by default (issue #469)."""
        with tempfile.TemporaryDirectory() as temp_dir, \
             mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=self._fake_result()) as run_mock:
            os.environ.pop("LAST30DAYS_YT_SUB_LANGS", None)
            youtube_yt.fetch_transcript("Example0001", temp_dir)

        cmd = run_mock.call_args_list[0].args[0]
        idx = cmd.index("--sub-lang")
        self.assertEqual(cmd[idx + 1], "en,es,pt")

    def test_transcript_cmd_respects_env_var_override(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
             mock.patch.dict(os.environ, {"LAST30DAYS_YT_SUB_LANGS": "fr,de,it"}), \
             mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=self._fake_result()) as run_mock:
            youtube_yt.fetch_transcript("Example0001", temp_dir)

        cmd = run_mock.call_args_list[0].args[0]
        idx = cmd.index("--sub-lang")
        self.assertEqual(cmd[idx + 1], "fr,de,it")

    def test_vtt_matching_picks_non_english_track(self):
        """When yt-dlp writes a Spanish track (no English available), we read it."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Simulate yt-dlp output: only a Spanish VTT is available
            (Path(temp_dir) / "Example0001.es.vtt").write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHola mundo esta es una prueba.\n",
                encoding="utf-8",
            )
            with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
                 mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=self._fake_result()):
                vtt = youtube_yt._fetch_transcript_ytdlp("Example0001", temp_dir)

        self.assertIsNotNone(vtt)
        self.assertIn("Hola mundo", vtt)

    def test_partial_success_returns_vtt_despite_nonzero_exit(self):
        """A non-zero yt-dlp exit must not discard a VTT already on disk.

        Regression for the 0/N-transcripts bug: with the default
        ``--sub-lang en,es,pt``, an English video fetches ``en`` successfully,
        then ``es``/``pt`` hit a 429 and yt-dlp exits non-zero. The ``en``
        track is already written and must be returned, not discarded (and not
        retried back into the same rate limit).
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / "Example0001.en.vtt").write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nThis is the english transcript.\n",
                encoding="utf-8",
            )
            status: dict = {}
            with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
                 mock.patch.object(
                     youtube_yt.subproc,
                     "run_with_timeout",
                     return_value=self._fake_result(returncode=1),
                 ) as run_mock:
                vtt = youtube_yt._fetch_transcript_ytdlp("Example0001", temp_dir, status)

        self.assertIsNotNone(vtt)
        self.assertIn("english transcript", vtt)
        self.assertNotIn("ytdlp_error", status)
        # Salvage must short-circuit the retry loop: yt-dlp must not be called a
        # second time when a partial VTT is already on disk (locks in the
        # no-retry guarantee against a future salvage-after-retry regression).
        self.assertEqual(run_mock.call_count, 1)

    def test_vtt_matching_respects_non_default_priority(self):
        """When multiple tracks exist, the user-requested priority wins
        over alphabetical order (regression for the Greptile review on #486)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / "Example0001.en.vtt").write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nEnglish first track.\n",
                encoding="utf-8",
            )
            (Path(temp_dir) / "Example0001.es.vtt").write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nSpanish second track.\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"LAST30DAYS_YT_SUB_LANGS": "es,en"}), \
                 mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
                 mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=self._fake_result()):
                vtt = youtube_yt._fetch_transcript_ytdlp("Example0001", temp_dir)

        self.assertIsNotNone(vtt)
        self.assertIn("Spanish", vtt)

    def test_vtt_matching_unknown_suffix_sorts_last(self):
        """A non-lang suffix (e.g. a stray .tmp or .live_chat) must not
        win over a real track that just happens to be alphabetically later."""
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / "Example0001.zz.vtt").write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nZZ track content.\n",
                encoding="utf-8",
            )
            (Path(temp_dir) / "Example0001.es.vtt").write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nSpanish content.\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"LAST30DAYS_YT_SUB_LANGS": "es,en,pt"}), \
                 mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
                 mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=self._fake_result()):
                vtt = youtube_yt._fetch_transcript_ytdlp("Example0001", temp_dir)

        self.assertIsNotNone(vtt)
        self.assertIn("Spanish", vtt)

class TestExtractTranscriptHighlights(unittest.TestCase):
    def test_extracts_specific_sentences(self):
        transcript = (
            "Hey guys welcome back to the channel. "
            "In today's video we're looking at something special. "
            "The Lego Bugatti Chiron took 13,438 hours to build with over 1 million pieces. "
            "Don't forget to subscribe and hit the bell. "
            "The tolerance on each brick is 0.002 millimeters which is insane for injection molding. "
            "So yeah that's pretty cool. "
            "Thanks for watching see you next time."
        )
        highlights = youtube_yt.extract_transcript_highlights(transcript, "Lego")
        self.assertTrue(len(highlights) > 0)
        joined = " ".join(highlights)
        self.assertIn("13,438", joined)
        self.assertNotIn("subscribe", joined)
        self.assertNotIn("welcome back", joined)

    def test_empty_transcript(self):
        self.assertEqual(youtube_yt.extract_transcript_highlights("", "test"), [])

    def test_respects_limit(self):
        sentences = ". ".join(
            f"The model {i} has {i * 100} parameters and runs at {i * 10} tokens per second"
            for i in range(20)
        ) + "."
        highlights = youtube_yt.extract_transcript_highlights(sentences, "model", limit=3)
        self.assertEqual(len(highlights), 3)

    def test_punctuation_free_transcript_produces_highlights(self):
        # Auto-generated YouTube captions often lack sentence-ending punctuation
        words = (
            "the new Tesla Model Y has 350 miles of range and costs about 45000 dollars "
            "which makes it one of the most affordable electric vehicles on the market today "
            "compared to the BMW iX which starts at 87000 the value proposition is pretty clear "
            "and with the 7500 dollar tax credit you can get it for under 40000"
        )
        highlights = youtube_yt.extract_transcript_highlights(words, "Tesla Model Y")
        self.assertTrue(len(highlights) > 0, "Should produce highlights from punctuation-free text")

class TestExpandYouTubeQueries(unittest.TestCase):
    """Tests for expand_youtube_queries() multi-query generation."""

    def test_default_depth_returns_two_plus_queries(self):
        queries = youtube_yt.expand_youtube_queries("Kanye West", "default")
        self.assertGreaterEqual(len(queries), 2)
        # First query is the core subject
        self.assertEqual(queries[0].lower(), "kanye west")

    def test_how_to_intent_includes_tutorial_variant(self):
        # Use deep depth so the intent variant isn't capped out by core + original
        queries = youtube_yt.expand_youtube_queries("how to use Docker", "deep")
        variant_found = any(
            "tutorial" in q.lower() or "guide" in q.lower() or "explained" in q.lower()
            for q in queries
        )
        self.assertTrue(
            variant_found,
            f"Expected tutorial/guide/explained in queries: {queries}",
        )

    def test_product_intent_includes_review_variant(self):
        # Use deep depth so the intent variant isn't capped out
        queries = youtube_yt.expand_youtube_queries("best running shoes", "deep")
        variant_found = any("review" in q.lower() for q in queries)
        self.assertTrue(variant_found, f"Expected 'review' in queries: {queries}")

    def test_comparison_intent_includes_vs_variant(self):
        queries = youtube_yt.expand_youtube_queries("Claude vs Gemini", "default")
        variant_found = any("vs" in q.lower() or "compared" in q.lower() for q in queries)
        self.assertTrue(variant_found, f"Expected 'vs' or 'compared' in queries: {queries}")

    def test_quick_depth_returns_one_query(self):
        queries = youtube_yt.expand_youtube_queries("Kanye West", "quick")
        self.assertEqual(len(queries), 1)

    def test_deep_depth_returns_three_queries(self):
        queries = youtube_yt.expand_youtube_queries("Kanye West", "deep")
        self.assertEqual(len(queries), 3)

    def test_single_word_returns_at_least_one(self):
        queries = youtube_yt.expand_youtube_queries("React", "default")
        self.assertGreaterEqual(len(queries), 1)

    def test_temporal_words_stripped_from_core(self):
        queries = youtube_yt.expand_youtube_queries("kanye west last 30 days", "default")
        core = queries[0].lower()
        self.assertNotIn("last", core)
        self.assertNotIn("days", core)
        self.assertIn("kanye", core)
        self.assertIn("west", core)

class TestTranscriptCandidateSortKey(unittest.TestCase):
    """Tests for _transcript_candidate_sort_key recency-boosted ordering."""

    @staticmethod
    def _d(days_ago: int) -> str:
        return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")

    def _make_item(self, video_id, views, date_str):
        return {
            "video_id": video_id,
            "title": f"Video {video_id}",
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "channel_name": "TestChannel",
            "date": date_str,
            "engagement": {"views": views, "likes": 10, "comments": 5},
            "relevance": 0.8,
            "why_relevant": "test",
            "description": "test desc",
            "duration": 600,
        }

    def test_recency_breaks_views_tie(self):
        """When views are equal, the more recent video gets a higher sort key."""
        new = self._make_item("new", 100_000, self._d(1))
        old = self._make_item("old", 100_000, self._d(13))
        self.assertGreater(
            youtube_yt._transcript_candidate_sort_key(new),
            youtube_yt._transcript_candidate_sort_key(old),
        )

    def test_old_high_view_can_still_qualify_for_transcript(self):
        """An old video with very high views still gets a transcript slot;
        recency is a tiebreaker, not a gate."""
        old_high = self._make_item("old_high", 10_000_000, self._d(45))
        recent_low = self._make_item("recent_low", 100, self._d(1))
        self.assertGreater(
            youtube_yt._transcript_candidate_sort_key(old_high),
            youtube_yt._transcript_candidate_sort_key(recent_low),
        )

    def test_no_date_falls_to_back(self):
        """An item with no date gets recency 0, sorting behind dated items."""
        no_date = self._make_item("no_date", 50_000, "")
        dated = self._make_item("dated", 50_000, self._d(5))
        self.assertGreater(
            youtube_yt._transcript_candidate_sort_key(dated),
            youtube_yt._transcript_candidate_sort_key(no_date),
        )
