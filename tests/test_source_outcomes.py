import socket
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from lib import (
    bird_x,
    health,
    http,
    pipeline,
    reddit,
    reddit_listing,
    reddit_rss,
    render,
    schema,
)


def _report(*, source_status=None, items_by_source=None, errors_by_source=None):
    return schema.Report(
        topic="test topic",
        range_from="2026-06-10",
        range_to="2026-07-10",
        generated_at="2026-07-10T18:22:03Z",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="gemini",
            planner_model="test-planner",
            rerank_model="test-reranker",
        ),
        query_plan=schema.QueryPlan(
            intent="general",
            freshness_mode="balanced_recent",
            cluster_mode="story",
            raw_topic="test topic",
            subqueries=[
                schema.SubQuery(
                    label="primary",
                    search_query="test topic",
                    ranking_query="test topic",
                    sources=["x"],
                )
            ],
            source_weights={"x": 1.0},
        ),
        clusters=[],
        ranked_candidates=[],
        items_by_source=items_by_source or {},
        errors_by_source=errors_by_source or {},
        source_status=source_status or {},
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (http.HTTPError("HTTP 429", status_code=429), schema.RATE_LIMITED),
        (http.HTTPError("HTTP 401", status_code=401), schema.AUTH_FAILED),
        (http.HTTPError("HTTP 402", status_code=402), schema.AUTH_FAILED),
        (http.HTTPError("HTTP 403", status_code=403), schema.AUTH_FAILED),
        (http.HTTPError("Invalid JSON response"), schema.SCHEMA_DRIFT),
        (http.HTTPError("Connection error: reset"), schema.UNREACHABLE),
        (http.HTTPError("Request timed out"), health.TIMEOUT),
    ],
)
def test_http_error_exposes_run_outcome_state(error, expected):
    assert error.outcome_state == expected


@patch("lib.http.time.sleep")
@patch("lib.http.urllib.request.urlopen")
def test_http_wrapper_classifies_dns_failure(mock_urlopen, _mock_sleep):
    mock_urlopen.side_effect = urllib.error.URLError(
        socket.gaierror(-2, "Name or service not known")
    )

    with pytest.raises(http.HTTPError) as caught:
        http.get("https://unreachable.example", retries=1)

    assert caught.value.outcome_state == schema.UNREACHABLE


def test_source_specific_text_failures_are_mapped():
    assert bird_x.classify_run_failure("likely Twitter anti-bot interstitial") == schema.SCHEMA_DRIFT
    assert reddit.classify_run_failure("blocked by Reddit interstitial") == schema.RATE_LIMITED


def test_bundle_distinguishes_clean_no_results_from_failure():
    clean = schema.RetrievalBundle()
    clean.mark_attempted("x")

    failed = schema.RetrievalBundle()
    failed.mark_attempted("x")
    failed.record_failure("x", schema.RATE_LIMITED, "HTTP 429")

    assert clean.source_status["x"].state == schema.NO_RESULTS
    assert failed.source_status["x"].state == schema.RATE_LIMITED
    assert failed.source_status["x"].fix_hint == "doctor"


@patch("lib.http.urllib.request.urlopen")
def test_stream_adapter_recovers_http_failure_laundered_as_empty(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.HTTPError(
        "https://api.example.com",
        401,
        "Unauthorized",
        {},
        None,
    )

    def source_that_launders_failure(*_args, **_kwargs):
        try:
            http.get("https://api.example.com", retries=1)
        except http.HTTPError:
            return [], {}
        raise AssertionError("request should have failed")

    with patch("lib.pipeline._retrieve_stream_impl", side_effect=source_that_launders_failure):
        items, artifact = pipeline._retrieve_stream()

    assert items == []
    assert artifact["_source_outcome"]["state"] == schema.AUTH_FAILED


@patch("lib.http.time.sleep")
@patch("lib.http.urllib.request.urlopen")
def test_reddit_nested_worker_propagates_failure_capture(mock_urlopen, _mock_sleep):
    mock_urlopen.side_effect = urllib.error.HTTPError(
        "https://api.scrapecreators.com/v1/reddit/search",
        429,
        "Too Many Requests",
        {},
        None,
    )

    with http.capture_failures() as failures:
        result = reddit.search_reddit(
            "test topic",
            "2026-06-10",
            "2026-07-10",
            depth="quick",
            token="dummy-token",
        )

    assert result["items"] == []
    assert failures[-1].outcome_state == schema.RATE_LIMITED


def _reddit_429(url="https://www.reddit.com/search.rss"):
    return urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)


@patch("lib.http.time.sleep")
@patch("lib.http.urllib.request.urlopen")
def test_reddit_rss_fanout_propagates_failure_capture(mock_urlopen, _mock_sleep):
    # get_text launders the 429 into None; the sink is what must survive the
    # ThreadPoolExecutor hop into the feed workers (issue #899).
    mock_urlopen.side_effect = _reddit_429()

    with http.capture_failures() as failures:
        posts = reddit_rss.search_rss("test topic", depth="quick")

    assert posts == []
    assert failures[-1].outcome_state == schema.RATE_LIMITED


@patch("lib.http.time.sleep")
@patch("lib.http.urllib.request.urlopen")
def test_reddit_listing_fanout_propagates_failure_capture(mock_urlopen, _mock_sleep):
    mock_urlopen.side_effect = _reddit_429(
        "https://www.reddit.com/svc/shreddit/community-more-posts/hot/"
    )

    with http.capture_failures() as failures:
        posts = reddit_listing.fetch_listings(["example"], depth="quick", query="test topic")

    assert posts == []
    assert failures[-1].outcome_state == schema.RATE_LIMITED


@patch("lib.http.time.sleep")
@patch("lib.http.urllib.request.urlopen")
def test_reddit_discovery_listing_fanout_propagates_failure_capture(mock_urlopen, _mock_sleep):
    mock_urlopen.side_effect = _reddit_429(
        "https://www.reddit.com/svc/shreddit/community-more-posts/rising/"
    )

    with http.capture_failures() as failures:
        result = reddit_listing.fetch_discovery_listings(["example"], query="test topic")

    assert result["items"] == []
    # The discovery path reads this list, not the sink — a blocked feed must not
    # look like an empty one there either.
    assert result["errors"]
    assert any("429" in error for error in result["errors"])
    assert failures[-1].outcome_state == schema.RATE_LIMITED


@patch("lib.http.urllib.request.urlopen")
def test_tee_failures_does_not_hide_from_parent_sink(mock_urlopen):
    # tee_failures must never become capture_failures: the latter replaces the
    # sink, which would silently re-break the Reddit lanes above.
    mock_urlopen.side_effect = urllib.error.HTTPError(
        "https://api.example.com/missing", 404, "Not Found", {}, None
    )

    with http.capture_failures() as parent:
        with http.tee_failures() as local:
            with pytest.raises(http.HTTPError):
                http.get("https://api.example.com/missing", retries=1)

    assert len(local) == 1
    assert local == parent


def test_captured_http_failure_overrides_generic_artifact_error():
    failure = http.HTTPError("HTTP 429: Too Many Requests", status_code=429)
    outcome = pipeline._resolve_stream_outcome(
        "tiktok",
        pipeline._outcome_artifact(health.ERROR, "request failed"),
        [failure],
    )

    assert outcome["state"] == schema.RATE_LIMITED


def test_bundle_records_items_then_429_as_partial():
    item = schema.SourceItem(
        item_id="x1",
        source="x",
        title="A post",
        body="body",
        url="https://x.com/example/status/1",
    )
    bundle = schema.RetrievalBundle()
    bundle.mark_attempted("x")
    bundle.add_items("primary", "x", [item])
    bundle.record_failure("x", schema.RATE_LIMITED, "429 after first page")

    outcome = bundle.source_status["x"]
    assert outcome.state == schema.PARTIAL
    assert outcome.items_returned == 1
    assert outcome.detail == "429 after first page"


def test_pipeline_records_clean_empty_source_as_no_results():
    plan = {
        "intent": "general",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "story",
        "subqueries": [
            {
                "label": "primary",
                "search_query": "test topic",
                "ranking_query": "test topic",
                "sources": ["x"],
            }
        ],
        "source_weights": {"x": 1.0},
    }
    with patch("lib.pipeline._retrieve_stream", return_value=([], {})):
        report = pipeline.run(
            topic="test topic",
            config={"LAST30DAYS_REASONING_PROVIDER": "gemini"},
            depth="quick",
            requested_sources=["x"],
            mock=True,
            external_plan=plan,
        )

    assert report.source_status["x"].state == schema.NO_RESULTS
    assert "x" not in report.errors_by_source


def test_pipeline_preserves_typed_http_failure():
    plan = {
        "intent": "general",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "story",
        "subqueries": [
            {
                "label": "primary",
                "search_query": "test topic",
                "ranking_query": "test topic",
                "sources": ["x"],
            }
        ],
        "source_weights": {"x": 1.0},
    }
    failure = http.HTTPError("HTTP 429: Too Many Requests", status_code=429)
    with patch("lib.pipeline._retrieve_stream", side_effect=failure):
        report = pipeline.run(
            topic="test topic",
            config={"LAST30DAYS_REASONING_PROVIDER": "gemini"},
            depth="quick",
            requested_sources=["x"],
            mock=True,
            external_plan=plan,
        )

    assert report.source_status["x"].state == schema.RATE_LIMITED
    assert report.source_status["x"].items_returned == 0
    assert "x" in report.errors_by_source


def test_footer_and_synthesis_note_surface_failed_source():
    report = _report(
        source_status={
            "x": schema.SourceOutcome(
                source="x",
                state=schema.RATE_LIMITED,
                detail="HTTP 429 after retry budget",
                fix_hint="doctor",
            )
        },
        errors_by_source={"x": "HTTP 429 after retry budget"},
    )

    text = render.render_compact(report)

    # A failed source that returned zero items is surfaced to synthesis via the
    # evidence blocks (## Partial Coverage), NOT as a user-facing footer line -
    # zero-item sources are dropped from the emoji tree (see test_render_footer).
    assert "## Partial Coverage" in text
    assert "Do not interpret a failed source as no discussion" in text
    assert "🔵 X: rate-limited: HTTP 429 after retry budget (run doctor for fixes)" not in text


def test_report_source_status_round_trips_through_schema_serialization():
    report = _report(
        source_status={
            "x": schema.SourceOutcome(
                source="x",
                state=schema.PARTIAL,
                items_returned=12,
                detail="429 after 12 items",
                at="2026-07-10T18:22:03Z",
                fix_hint="doctor",
            )
        }
    )

    payload = schema.to_dict(report)
    restored = schema.report_from_dict(payload)

    assert payload["source_status"]["x"]["state"] == schema.PARTIAL
    assert restored.source_status["x"] == report.source_status["x"]


# --- strict exit (LAST30DAYS_STRICT_EXIT, issue #384) ---

import last30days as cli


def _outcome(source, state, **kwargs):
    return schema.SourceOutcome(source=source, state=state, **kwargs)


def test_strict_exit_disabled_by_default_even_when_degraded():
    report = _report(
        source_status={"x": _outcome("x", schema.RATE_LIMITED, detail="429")}
    )
    assert cli._strict_exit_code(report, None, {}) == 0


def test_strict_exit_returns_3_for_degraded_run(capsys):
    report = _report(
        source_status={"x": _outcome("x", schema.AUTH_FAILED, detail="401")}
    )
    rc = cli._strict_exit_code(report, None, {"LAST30DAYS_STRICT_EXIT": "1"})
    assert rc == 3
    assert "strict-exit: degraded sources: x" in capsys.readouterr().err


def test_strict_exit_clean_states_return_0():
    report = _report(
        source_status={
            "reddit": _outcome("reddit", health.OK, items_returned=12),
            "hn": _outcome("hn", schema.NO_RESULTS),
            "tiktok": _outcome("tiktok", schema.SKIPPED_UNCONFIGURED, attempted=False),
        }
    )
    assert cli._strict_exit_code(report, None, {"LAST30DAYS_STRICT_EXIT": "true"}) == 0


def test_strict_exit_checks_entity_reports_in_comparison_runs():
    lead = _report(source_status={"reddit": _outcome("reddit", health.OK)})
    entity = _report(
        source_status={"x": _outcome("x", schema.UNREACHABLE, detail="dns")}
    )
    rc = cli._strict_exit_code(lead, [("other", entity)], {"LAST30DAYS_STRICT_EXIT": "on"})
    assert rc == 3


def test_strict_exit_env_key_is_registered():
    # Unregistered keys are silently dropped by env config loading (#707 class).
    from lib import env as env_module
    import inspect

    assert "LAST30DAYS_STRICT_EXIT" in inspect.getsource(env_module)


def test_captured_failure_selection_prefers_most_specific():
    auth = http.HTTPError("HTTP 401: Unauthorized", status_code=401)
    rate = http.HTTPError("HTTP 429: Too Many Requests", status_code=429)
    # Order must not matter: auth-failed wins over rate-limited either way.
    for failures in ([auth, rate], [rate, auth]):
        outcome = pipeline._resolve_stream_outcome("x", None, failures)
        assert outcome["state"] == schema.AUTH_FAILED


def test_bundle_keeps_ok_with_lane_detail_across_subqueries():
    """A swallowed lane failure on a source that delivered items stays ``ok``
    and carries the loss as detail; a later clean subquery keeps that detail."""
    item = schema.SourceItem(
        item_id="r1",
        source="reddit",
        title="A thread",
        body="body",
        url="https://www.reddit.com/r/test/comments/abc/",
    )
    bundle = schema.RetrievalBundle()
    bundle.mark_attempted("reddit")
    bundle.record_detail("reddit", "3 sub-requests rate-limited (HTTP 429)")
    bundle.add_items("primary", "reddit", [item])

    outcome = bundle.source_status["reddit"]
    assert outcome.state == health.OK
    assert outcome.items_returned == 1
    assert outcome.detail == "3 sub-requests rate-limited (HTTP 429)"
    assert outcome.fix_hint is None

    bundle.add_items("secondary", "reddit", [])
    outcome = bundle.source_status["reddit"]
    assert outcome.state == health.OK
    assert outcome.detail == "3 sub-requests rate-limited (HTTP 429)"


def test_finalize_turns_an_empty_ok_source_with_lane_failures_into_that_failure():
    """Zero items after filtering plus swallowed 429s is not 'completed cleanly
    with zero matches'; it is the rate limit, so ## Partial Coverage fires and
    doctor does not list the source as succeeded."""
    bundle = schema.RetrievalBundle()
    bundle.mark_attempted("polymarket")
    bundle.record_detail("polymarket", "5 sub-requests rate-limited (HTTP 429)", state=schema.RATE_LIMITED)
    bundle.add_items("primary", "polymarket", [])
    finalized = pipeline._finalize_source_status(bundle.source_status, {"polymarket": []})
    outcome = finalized["polymarket"]
    assert outcome.state == schema.RATE_LIMITED
    assert outcome.detail == "5 sub-requests rate-limited (HTTP 429)"
    assert outcome.items_returned == 0
