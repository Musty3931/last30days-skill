"""YouTube window, budget, captions, and engine contract regressions."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import pytest

from lib import doctor, health, normalize, pipeline, render, schema, signals, youtube_yt as yt
from lib.subproc import SubprocResult, SubprocTimeout

FROM = "2026-08-31"
TO = "2026-09-30"
VIDEO_ID = "Example0001"
FIXTURE = Path(__file__).parent / "fixtures/youtube/captions.vtt"


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    for name in ("LAST30DAYS_YOUTUBE", "LAST30DAYS_YTDLP", "LAST30DAYS_YT_SUB_LANGS", "LAST30DAYS_YOUTUBE_CHANNELS"):
        monkeypatch.delenv(name, raising=False)
    yt.reset_search_cache()
    yield
    yt.reset_search_cache()


def video(video_id=VIDEO_ID, date="20260915", **kwargs):
    return {"id": video_id, "title": "Unreal Engine archviz glass tutorial", "upload_date": date,
            "channel": "Example channel", "view_count": 1000, **kwargs}


def result(videos=(), code=0, stderr=""):
    return SubprocResult(code, "\n".join(json.dumps(v) for v in videos), stderr)


def search_runner(monkeypatch, videos):
    calls = []
    monkeypatch.setattr(yt, "is_ytdlp_installed", lambda *a: True)
    def run(cmd, **kwargs):
        calls.append(cmd)
        if "--flat-playlist" in cmd:
            return result([{k: v for k, v in row.items() if k != "upload_date"} for row in videos])
        vid = cmd[-1].split("v=")[-1]
        return result([next(row for row in videos if row["id"] == vid)])
    monkeypatch.setattr(yt, "_run_ytdlp", run)
    return calls


def test_date_window_includes_bounds_and_rejects_old_future_missing_invalid(monkeypatch):
    rows = [video(f"Example{i:04d}", d) for i, d in enumerate([
        "20260831", "20260930", "20260830", "20261001", None, "20260999",
    ])]
    calls = search_runner(monkeypatch, rows)
    found = yt.search_youtube("Unreal Engine archviz glass", FROM, TO, "quick")
    assert sorted(v["date"] for v in found["items"]) == [FROM, TO]
    assert len(calls) == 7
    assert "--flat-playlist" in calls[0]
    assert all("--flat-playlist" not in cmd for cmd in calls[1:])


def test_ytsearch_prefix_fallback_for_builds_without_ytsearchdate(monkeypatch):
    # Emulate the installed build: date-prefix searches are unsupported.
    monkeypatch.setattr(yt, "is_ytdlp_installed", lambda: True)
    def run(cmd, **kwargs):
        assert not any(arg.startswith("ytsearchdate") for arg in cmd)
        if "--flat-playlist" in cmd:
            assert "ytsearch18:unreal engine" in cmd
        return result([video()])
    monkeypatch.setattr(yt, "_run_ytdlp", run)
    assert len(yt.search_youtube("Unreal Engine", FROM, TO, "quick")["items"]) == 1


def test_old_results_never_fill_a_thin_recent_window(monkeypatch):
    search_runner(monkeypatch, [video(date="20260101")])
    assert yt.search_youtube("Unreal Engine", FROM, TO)["items"] == []


@pytest.mark.parametrize("depth,cap", [("quick", 6), ("default", 8), ("deep", 16)])
def test_metadata_budget_and_cache(monkeypatch, depth, cap):
    rows = [video(f"Example{i:04d}") for i in range(50)]
    calls = search_runner(monkeypatch, rows)
    first = yt.search_youtube("Unreal Engine", FROM, TO, depth)
    assert len(calls) == cap + 1
    assert len(first["items"]) == cap
    second = yt.search_youtube("Unreal Engine", FROM, TO, depth)
    second["items"][0]["title"] = "mutated"
    assert first["items"][0]["title"] != "mutated"
    assert len(calls) == cap + 1
    yt.search_youtube("Unreal Engine glass", FROM, TO, depth)
    assert len(calls) == cap + 2  # metadata shared across queries
    assert not yt.search_youtube("Unreal Engine", "2026-09-16", TO, depth)["items"]


def test_metadata_pool_is_small(monkeypatch):
    search_runner(monkeypatch, [video(f"Example{i:04d}") for i in range(8)])
    lock = threading.Lock()
    active = peak = 0
    def metadata(v):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.005)
        with lock:
            active -= 1
        return {**v, "upload_date": "20260915"}, None
    monkeypatch.setattr(yt, "_video_metadata", metadata)
    assert yt.search_youtube("Unreal Engine", FROM, TO)["items"]
    assert peak == 2


@pytest.mark.parametrize("failure,state", [
    ("HTTP Error 429: Too Many Requests", health.RATE_LIMITED),
    ("Sign in to confirm you're not a bot", health.RATE_LIMITED),
    ("Search timed out", health.TIMEOUT),
    ("yt-dlp not installed", health.SKIPPED_UNCONFIGURED),
])
def test_failures_surface_in_pipeline(monkeypatch, failure, state):
    monkeypatch.setattr(yt, "is_ytdlp_installed", lambda: True)
    monkeypatch.setattr(yt, "_run_ytdlp", lambda *a, **k: result(code=1, stderr=failure))
    payload = yt.search_youtube("Unreal Engine", FROM, TO)
    assert payload["error"] == failure
    assert pipeline._result_outcome_artifact("youtube", payload)["_source_outcome"]["state"] == state


def test_metadata_partial_failure_preserves_valid_items(monkeypatch):
    search_runner(monkeypatch, [video(), video("Example0002")])
    monkeypatch.setattr(yt, "_video_metadata", lambda v: (video(), None) if v["id"] == VIDEO_ID else ({}, "HTTP Error 429"))
    found = yt.search_youtube("Unreal Engine", FROM, TO)
    assert len(found["items"]) == 1
    assert "429" in found["error"]


def test_caption_flags_cleaning_length_and_cache(monkeypatch, tmp_path):
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        (tmp_path / f"{VIDEO_ID}.en.vtt").write_text(FIXTURE.read_text() + " word" * 6000)
        return result()
    monkeypatch.setattr(yt, "_run_ytdlp", run)
    transcript = yt.fetch_transcript(VIDEO_ID, str(tmp_path))
    assert len(transcript.split()) == yt.TRANSCRIPT_MAX_WORDS
    assert "WEBVTT" not in transcript and "-->" not in transcript
    assert yt.fetch_transcript(VIDEO_ID, str(tmp_path)) == transcript
    assert len(calls) == 1
    assert {"--write-subs", "--write-auto-subs", "--skip-download", "--ignore-config", "--no-cookies-from-browser"} <= set(calls[0])
    assert "--extract-audio" not in calls[0]


def test_caption_absence_and_failure_are_distinct(monkeypatch, tmp_path):
    monkeypatch.setattr(yt, "_run_ytdlp", lambda *a, **k: result())
    status = {}
    assert yt.fetch_transcript(VIDEO_ID, str(tmp_path), status) is None
    assert status["no_caption_tracks"]
    yt.reset_search_cache()
    monkeypatch.setattr(yt, "_run_ytdlp", lambda *a, **k: result(code=1, stderr="HTTP Error 429"))
    status = {}
    assert yt.fetch_transcript(VIDEO_ID, str(tmp_path), status) is None
    assert "429" in status["ytdlp_error"]
    assert not status.get("no_caption_tracks")


def test_caption_timeout_salvages_written_track(monkeypatch, tmp_path):
    def run(*a, **k):
        (tmp_path / f"{VIDEO_ID}.en.vtt").write_text(FIXTURE.read_text())
        raise SubprocTimeout("timeout")
    monkeypatch.setattr(yt, "_run_ytdlp", run)
    assert yt.fetch_transcript(VIDEO_ID, str(tmp_path))


@pytest.mark.parametrize("depth,limit", [("quick", 0), ("default", 2), ("deep", 8)])
def test_caption_budget_and_strict_window_after_merge(monkeypatch, depth, limit):
    raw = [{"video_id": f"Example{i:04d}", "date": "2026-09-15", "engagement": {"views": i}, "relevance": 1} for i in range(20)]
    raw.append({"video_id": "old00000000", "date": "2020-01-01", "engagement": {"views": 999999999}})
    monkeypatch.setattr(yt, "search_youtube", lambda *a: {"items": raw})
    captured = []
    def captions(ids, **kwargs):
        captured.extend(ids)
        return {vid: "The glass material uses 3 refraction settings for realistic Unreal Engine rendering." for vid in ids}
    monkeypatch.setattr(yt, "fetch_transcripts_parallel", captions)
    found = yt.search_and_transcribe("Unreal Engine", FROM, TO, depth)
    assert len(captured) == limit and "old00000000" not in captured
    assert len(found["items"]) == 20
    assert sum(bool(v["transcript_highlights"]) for v in found["items"]) == limit


def test_command_override_and_off_gate(monkeypatch):
    monkeypatch.setattr(yt.shutil, "which", lambda name: "/bin/uvx" if name == "uvx" else None)
    assert not yt.is_ytdlp_installed()
    assert yt.ytdlp_command({"LAST30DAYS_YTDLP": "uvx yt-dlp"}) == ["uvx", "yt-dlp"]
    monkeypatch.setenv("LAST30DAYS_YTDLP", "uvx yt-dlp")
    assert yt.is_ytdlp_installed()
    monkeypatch.setenv("LAST30DAYS_YOUTUBE", "off")
    assert not yt.is_enabled()
    assert "youtube" not in pipeline.available_sources({}, local_only=True)
    with mock.patch.object(yt.subproc, "run_with_timeout") as run:
        assert not yt.live_probe()["probed"]
        assert yt.search_youtube("Unreal", FROM, TO) == {"items": []}
        run.assert_not_called()


def test_doctor_record_and_live_probe_use_configured_command(monkeypatch):
    monkeypatch.setattr(yt.shutil, "which", lambda name: "/bin/uvx" if name == "uvx" else None)
    cfg = {"LAST30DAYS_YTDLP": "uvx yt-dlp"}
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        return SubprocResult(0, "2026.08.19", "") if "--version" in cmd else result([video()])
    monkeypatch.setattr(yt.subproc, "run_with_timeout", run)
    assert doctor._youtube_record(cfg)["status"] == health.OK
    assert doctor._probe_source("youtube", cfg, 2)["ok"]
    assert all(cmd[:2] == ["uvx", "yt-dlp"] for cmd in calls)
    assert "ytsearch1:Unreal Engine" in calls[1]


def test_normalization_and_caption_footer():
    raw = [{"video_id": VIDEO_ID, "title": "Glass", "date": "2026-09-15", "channel_name": "Example",
            "engagement": {"views": 1000}, "transcript_snippet": "A useful caption.", "transcript_highlights": ["A useful caption."]}]
    items = normalize.normalize_source_items("youtube", raw, FROM, TO)
    assert items[0].metadata["transcript_snippet"] == items[0].snippet
    assert items[0].author == "Example"
    assert normalize.normalize_source_items("youtube", [{**raw[0], "date": None}], FROM, TO) == []
    report = mock.Mock(items_by_source={"youtube": items})
    line = render._build_source_footer_lines(report)[0]
    assert "YouTube: 1 video" in line and "1/1 with transcripts" in line and "1,000 views" in line
    items[0].metadata = {}
    assert "0/1 with transcripts" in render._build_source_footer_lines(report)[0]


def test_optional_channel_boost_is_small_and_cannot_rescue_irrelevance(monkeypatch):
    monkeypatch.setenv("LAST30DAYS_YOUTUBE_CHANNELS", "Unreal Engine, Unreal Sensei")
    assert yt.channel_boost("unreal engine") == .03
    assert yt.channel_boost("Other") == 0
    item = normalize.normalize_source_items("youtube", [{"video_id": VIDEO_ID, "title": "Unreal Engine glass", "date": "2026-09-15", "channel_boost": .03}], FROM, TO)[0]
    boosted = signals.local_relevance(item, "Unreal Engine glass tutorial")
    item.metadata["channel_boost"] = 0
    assert 0 <= boosted - signals.local_relevance(item, "Unreal Engine glass tutorial") <= .03000001
    item.metadata["channel_boost"] = .03
    assert signals.local_relevance(item, "cooking pancakes") == 0


def test_pipeline_youtube_only_mock():
    report = pipeline.run(topic="Unreal Engine archviz glass", config={}, depth="default", requested_sources=["youtube"], mock=True)
    assert report.items_by_source["youtube"]
    assert "with transcripts" in "\n".join(render._build_source_footer_lines(report))


def test_search_cache_coalesces_concurrent_calls(monkeypatch):
    monkeypatch.setattr(yt, "is_ytdlp_installed", lambda: True)
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        time.sleep(.01)
        return result([video()])
    monkeypatch.setattr(yt, "_run_ytdlp", run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(yt.search_youtube, "Unreal Engine", FROM, TO) for _ in range(2)]
        assert all(f.result()["items"] for f in futures)
    assert len(calls) == 2  # one search + one metadata request


def test_failed_metadata_is_cached_across_queries(monkeypatch):
    monkeypatch.setattr(yt, "is_ytdlp_installed", lambda: True)
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        return result([video()]) if "--flat-playlist" in cmd else result(code=1, stderr="HTTP Error 429")
    monkeypatch.setattr(yt, "_run_ytdlp", run)
    assert yt.search_youtube("Unreal Engine", FROM, TO)["error"]
    assert yt.search_youtube("Unreal Engine glass", FROM, TO)["error"]
    assert len(calls) == 3


def test_doctor_cache_changes_with_youtube_settings():
    base = doctor._config_fingerprint({})
    assert doctor._config_fingerprint({"LAST30DAYS_YOUTUBE": "off"}) != base
    assert doctor._config_fingerprint({"LAST30DAYS_YTDLP": "uvx yt-dlp"}) != base


def test_transcript_selection_prefers_relevant_video_over_viral_noise():
    raw = [
        {"video_id": "Example0001", "title": "Cooking pancakes", "date": "2026-09-15", "engagement": {"views": 10000000}},
        {"video_id": "Example0002", "title": "Unreal Engine archviz glass tutorial", "date": "2026-09-15", "engagement": {"views": 1000}},
    ]
    ranked = yt._rank_transcript_candidates(raw, "Unreal Engine archviz glass", FROM, TO)
    assert ranked[0]["video_id"] == "Example0002"


def test_run_command_uses_argv_override_without_shell(monkeypatch):
    monkeypatch.setenv("LAST30DAYS_YTDLP", "uvx yt-dlp")
    monkeypatch.setattr(yt.shutil, "which", lambda name: "/bin/uvx")
    with mock.patch.object(yt.subproc, "run_with_timeout", return_value=result()) as run:
        yt._run_ytdlp(["yt-dlp", "--version"], timeout=1)
    run.assert_called_once_with(["uvx", "yt-dlp", "--version"], timeout=1)
