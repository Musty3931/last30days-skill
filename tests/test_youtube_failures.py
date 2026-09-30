"""Recorded exit-zero refusals must not become evidence of YouTube silence."""
import json
from pathlib import Path
from unittest import mock

import pytest

from lib import health, pipeline, render, youtube_yt as yt
from lib.subproc import SubprocResult


STDERR = (Path(__file__).parent / "fixtures/youtube/metadata-bot-check.stderr").read_text()
VIDEO = {"id": "Example0001", "title": "Unreal Engine glass", "upload_date": "20260915"}


@pytest.fixture(autouse=True)
def isolated_youtube(monkeypatch):
    monkeypatch.setattr(yt, "is_enabled", lambda *a: True)
    monkeypatch.setattr(yt, "is_ytdlp_installed", lambda *a: True)
    monkeypatch.setattr(yt, "ytdlp_command", lambda *a: ["yt-dlp"])
    yt.reset_search_cache()
    yield
    yt.reset_search_cache()


@pytest.mark.parametrize("code", [0, 1])
@pytest.mark.parametrize("message", [
    "Sign in to confirm you’re not a bot",
    "Sign in to confirm you're not a bot",
    "Sign in to confirm",
    "Please confirm you are not a bot",
    "HTTP Error 429: Too Many Requests",
    "HTTP 429",
])
def test_recorded_refusal_and_wording_variants_survive_truncation(monkeypatch, code, message):
    stderr = STDERR.replace("Sign in to confirm you’re not a bot", message)
    stderr += "WARNING: Requested format is not available\n" * 30
    monkeypatch.setattr(yt, "_run_ytdlp", lambda *a, **k: SubprocResult(code, "", stderr))
    payload = yt.search_youtube("Unreal Engine glass", "2026-08-31", "2026-09-30", "quick")
    assert payload["items"] == []
    assert message in payload["error"]
    assert pipeline._result_outcome_artifact("youtube", payload)["_source_outcome"]["state"] == health.RATE_LIMITED


@pytest.mark.parametrize("metadata_date", ["20260915", "20251014", None])
def test_bot_warning_in_metadata_survives_valid_old_or_undated_rows(monkeypatch, metadata_date):
    def run(cmd, **kwargs):
        flat = "--flat-playlist" in cmd
        row = VIDEO if flat else {**VIDEO, "upload_date": metadata_date}
        return SubprocResult(0, json.dumps(row), "" if flat else STDERR)
    monkeypatch.setattr(yt, "_run_ytdlp", run)
    payload = yt.search_and_transcribe("Unreal Engine glass", "2026-08-31", "2026-09-30", "quick")
    assert len(payload["items"]) == (1 if metadata_date == "20260915" else 0)
    assert "Sign in to confirm" in payload["error"]


@pytest.mark.parametrize("code,stderr,state", [
    (0, "", health.NO_RESULTS),
    (0, "WARNING: No supported JavaScript runtime could be found", health.NO_RESULTS),
    (1, "", health.ERROR),
    (1, " \n", health.ERROR),
    (0, "ERROR: Unable to download API page", health.ERROR),
    (0, STDERR, health.RATE_LIMITED),
])
def test_report_and_synthesis_distinguish_empty_search_from_failed_fetch(monkeypatch, code, stderr, state):
    monkeypatch.setattr(yt, "_run_ytdlp", lambda *a, **k: SubprocResult(code, "", stderr))
    # Use real retrieval/normalization/outcome aggregation, with a mock pipeline
    # to avoid unrelated credentials, planning APIs, and source traffic.
    retrieve = pipeline._retrieve_stream
    def live_youtube(**kwargs):
        kwargs["mock"] = False
        return retrieve(**kwargs)
    with mock.patch.object(pipeline, "_retrieve_stream", side_effect=live_youtube):
        report = pipeline.run(topic="Unreal Engine glass", config={}, depth="quick",
                              requested_sources=["youtube"], mock=True)
    assert report.source_status["youtube"].state == state
    coverage = "\n".join(render._render_source_coverage(report))
    if state == health.NO_RESULTS:
        assert "YouTube: 0 items (no results)" in coverage
    else:
        assert "no results" not in coverage
        assert state in coverage
        note = "\n".join(render._render_source_outcome_note(report))
        assert "Do not interpret a failed source as no discussion" in note


def test_live_probe_rejects_exit_zero_bot_warning_with_video_json(monkeypatch):
    monkeypatch.setattr(yt.subproc, "run_with_timeout", lambda *a, **k: SubprocResult(0, json.dumps(VIDEO), STDERR))
    probe = yt.live_probe()
    assert probe["probed"] and not probe["ok"]
    assert "Sign in to confirm" in probe["detail"]


def test_caption_refusal_is_not_reported_as_absent_tracks(monkeypatch, tmp_path):
    monkeypatch.setattr(yt, "_run_ytdlp", lambda *a, **k: SubprocResult(0, "", STDERR))
    status = {}
    assert yt.fetch_transcript(VIDEO["id"], str(tmp_path), status) is None
    assert "Sign in to confirm" in status["ytdlp_error"]
    assert not status.get("no_caption_tracks")
