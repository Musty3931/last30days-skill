"""Upload-filter URLs, recorded flat extraction, and bounded search fallback."""
import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from lib import health, pipeline, youtube_yt as yt
from lib.subproc import SubprocResult, SubprocTimeout


FIXTURES = Path(__file__).parent / "fixtures/youtube"
WINDOWS = json.loads((FIXTURES / "search-windows.json").read_text())
FLAT = [json.loads(line) for line in (FIXTURES / "search-flat.jsonl").read_text().splitlines()]
BOT_CHECK = (FIXTURES / "metadata-bot-check.stderr").read_text()
QUERY = WINDOWS["query"]
TO = WINDOWS["reference_date"]
URL_PREFIX = "https://www.youtube.com/results?search_query=unreal+engine+5.8+path+tracer+glass"


@pytest.fixture(autouse=True)
def isolated_youtube(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 30, 12, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(yt, "datetime", FrozenDateTime)
    monkeypatch.setattr(yt, "is_enabled", lambda *a: True)
    monkeypatch.setattr(yt, "is_ytdlp_installed", lambda *a: True)
    monkeypatch.setattr(yt, "ytdlp_command", lambda *a: ["yt-dlp"])
    yt.reset_search_cache()
    yield
    yt.reset_search_cache()


def start_date(days):
    return (datetime.fromisoformat(TO) - timedelta(days=days)).date().isoformat()


def result(rows=(), *, stderr="", code=0):
    return SubprocResult(code, "\n".join(json.dumps(row) for row in rows), stderr)


@pytest.mark.parametrize("case", WINDOWS["cases"], ids=lambda case: str(case["days"]))
def test_upload_filter_url_for_each_window(case):
    url = yt._search_url(QUERY, start_date(case["days"]), TO)
    assert url == URL_PREFIX + (f"&sp={case['sp']}" if case["sp"] else "")
    if case["sp"]:
        token = unquote(parse_qs(urlsplit(url).query)["sp"][0])
        enum = {"today": 2, "week": 3, "month": 4, "year": 5}[case["filter"]]
        # Verify the upload-date field, not the similarly spelled EgIBAg token
        # or the CAI upload-sort token, which does not filter old videos.
        assert base64.b64decode(token) == bytes([0x12, 0x02, 0x08, enum])


def test_query_is_encoded_without_changing_search_parameters():
    query = "Unreal C++ & glass #1 / 日本語?sp=oops"
    url = yt._search_url(query, start_date(7), TO)
    assert parse_qs(urlsplit(url).query) == {"search_query": [query], "sp": ["EgIIAw%3D%3D"]}


@pytest.mark.parametrize("start,end,sp", [
    ("2026-08-01", "2026-08-08", "EgIIBQ%253D%253D"),
    ("2025-01-01", "2025-01-08", None),
])
def test_historical_window_filter_reaches_its_start(start, end, sp):
    assert yt._search_url(QUERY, start, end) == URL_PREFIX + (f"&sp={sp}" if sp else "")


@pytest.mark.parametrize("days,sp", [(7, "EgIIAw%253D%253D"), (30, "EgIIBA%253D%253D")])
def test_filtered_search_verifies_dates_from_recorded_flat_entries(monkeypatch, days, sp):
    rows = FLAT[:4]
    assert all("upload_date" not in row for row in rows)
    # Dates are synthetic: this host recorded flat results but cannot obtain
    # reliable live metadata through the bot gate.
    dates = [start_date(days), TO, start_date(days + 1), None]
    metadata = {row["id"]: {**row, "upload_date": date.replace("-", "") if date else None}
                for row, date in zip(rows, dates)}
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        assert kwargs["timeout"] in (yt._SEARCH_TIMEOUT, yt._METADATA_TIMEOUT)
        if "--flat-playlist" in cmd:
            assert URL_PREFIX + f"&sp={sp}" in cmd
            assert cmd[cmd.index("--playlist-end") + 1] == "24"
            return result(rows)
        return result([metadata[parse_qs(urlsplit(cmd[-1]).query)["v"][0]]])

    monkeypatch.setattr(yt, "_run_ytdlp", run)
    payload = yt.search_youtube(QUERY, start_date(days), TO, "quick")
    assert "error" not in payload
    assert {item["date"] for item in payload["items"]} == {start_date(days), TO}
    assert len(calls) == 5
    assert all("--ignore-config" in cmd and "--no-cookies-from-browser" in cmd for cmd in calls)
    assert not any(arg.startswith("ytsearch") for cmd in calls for arg in cmd)


@pytest.mark.parametrize("initial", [
    result(code=1, stderr="ERROR: Unsupported URL: https://www.youtube.com/results"),
    result(),
    SubprocResult(0, '{"entries": []}', ""),
    SubprocTimeout("Search timed out"),
    OSError("Cannot launch extraction"),
], ids=["unsupported-url", "empty", "malformed", "timeout", "os-error"])
def test_url_failure_falls_back_once_to_recorded_ytsearch(monkeypatch, initial):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        assert not any(arg.startswith("ytsearchdate") for arg in cmd)
        if len(calls) == 1:
            assert URL_PREFIX + "&sp=EgIIBA%253D%253D" in cmd
            if isinstance(initial, Exception):
                raise initial
            return initial
        if "--flat-playlist" in cmd:
            assert "ytsearch24:" + QUERY in cmd
            assert cmd[cmd.index("--playlist-end") + 1] == "24"
            return result(FLAT[:1])
        return result([{**FLAT[0], "upload_date": "20260915"}])

    monkeypatch.setattr(yt, "_run_ytdlp", run)
    payload = yt.search_youtube(QUERY, start_date(30), TO, "quick")
    assert "error" not in payload
    assert [item["video_id"] for item in payload["items"]] == [FLAT[0]["id"]]
    assert len(calls) == 3


@pytest.mark.parametrize("primary_failed", [False, True])
def test_empty_fallback_preserves_primary_failure(monkeypatch, primary_failed):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if primary_failed and len(calls) == 1:
            return result(code=1, stderr="ERROR: Unsupported URL")
        return result()

    monkeypatch.setattr(yt, "_run_ytdlp", run)
    payload = yt.search_youtube(QUERY, start_date(30), TO, "quick")
    assert payload["items"] == []
    assert bool(payload.get("error")) == primary_failed
    assert len(calls) == 2


@pytest.mark.parametrize("blocked_call", [1, 2])
def test_recorded_bot_refusal_is_rate_limited_and_never_retried(monkeypatch, blocked_call):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return result(stderr=BOT_CHECK) if len(calls) == blocked_call else result()

    monkeypatch.setattr(yt, "_run_ytdlp", run)
    payload = yt.search_youtube(QUERY, start_date(30), TO, "quick")
    assert payload["items"] == []
    assert "Sign in to confirm" in payload["error"]
    assert pipeline._result_outcome_artifact("youtube", payload)["_source_outcome"]["state"] == health.RATE_LIMITED
    assert len(calls) == blocked_call
