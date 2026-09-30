"""Discourse recordings and offline contracts for the Epic Forums lane."""

import copy
import json
import socket
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from lib import doctor, env, epicforums as ef, fusion, health, http, normalize, pipeline, planner, render, rerank, schema

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "epicforums"
FROM, TO = "2026-08-31", "2026-09-30"


def recording(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())["response"]


def response(name="search_path_tracer_glass"):
    return dict(recording(name), from_date=FROM, to_date=TO, categories={11: "Rendering"})


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    state = SimpleNamespace(now=100.0, waits=[])

    def sleep(seconds):
        state.waits.append(seconds)
        state.now += seconds

    monkeypatch.setattr(ef, "time", SimpleNamespace(monotonic=lambda: state.now, sleep=sleep))
    monkeypatch.setattr(ef, "_hosts", {})
    return state


@pytest.mark.parametrize("topic", ["Unreal Engine 5.8 glass", "UE5", "UE4 materials", "UEFN", "Epic Games", "Fortnite", "Metahuman", "MetaHuman", "Nanite", "Lumen", "Twinmotion", "RealityCapture", "Megascans"])
def test_relevance_gate(topic):
    assert ef.is_relevant(topic)


@pytest.mark.parametrize("topic", ["OpenAI coding", "bread recipes", "epicurean meals", "UE50", "lumenous", "", "fortnites"])
def test_unrelated_topics_do_not_run(topic):
    assert not ef.is_relevant(topic)
    assert planner.topic_sources(topic, ["reddit", "epicforums"]) == ["reddit"]


@pytest.mark.parametrize("depth", ["quick", "default", "deep"])
@pytest.mark.parametrize("external", [False, True])
def test_plans_always_route_related_topics_even_if_host_omits_source(depth, external):
    available = ["reddit", "x", "github", "epicforums"]
    raw = {"intent": "factual", "source_weights": {"reddit": 1}, "subqueries": [{"label": "core", "search_query": "glass", "ranking_query": "Unreal glass", "sources": ["reddit"]}]}
    plan = planner._sanitize_plan(raw, "Unreal glass", available, None, depth) if external else planner._fallback_plan("Unreal glass", available, None, depth)
    assert all("epicforums" in sq.sources for sq in plan.subqueries)
    off = planner._sanitize_plan(raw, "bread recipes", available, None, depth)
    assert "epicforums" not in off.source_weights
    assert all("epicforums" not in sq.sources for sq in off.subqueries)


def test_explicit_subset_and_disabled_setting(monkeypatch):
    monkeypatch.setattr(env, "get_x_source", lambda *a, **k: None)
    monkeypatch.setattr(env, "x_pending_browser_auth", lambda *a, **k: False)
    monkeypatch.setattr(pipeline, "which", lambda _: None)
    assert "epicforums" in pipeline.available_sources({})
    assert "epicforums" not in pipeline.available_sources({"LAST30DAYS_EPICFORUMS": "off"})
    assert "epicforums" not in pipeline.available_sources({"EXCLUDE_SOURCES": "epicforums"})
    assert planner.topic_sources("bread", ["epicforums"], ["epicforums"]) == ["epicforums"]
    plan = planner._fallback_plan("Unreal glass", ["reddit", "epicforums"], ["reddit"], "quick")
    assert plan.subqueries[0].sources == ["reddit"]


def test_recorded_search_join_and_normalize():
    raw = ef.parse_epicforums_response(response(), "Unreal path tracer glass")
    assert len(raw) == 2
    assert raw[0]["url"].startswith(ef.BASE_URL + "/t/")
    assert all(item["author"] for item in raw)
    items = normalize.normalize_source_items("epicforums", raw, FROM, TO)
    assert len(items) == 2
    assert all(FROM <= item.published_at <= TO for item in items)
    assert all(set(item.engagement) == {"likes", "replies", "views"} for item in items)
    assert all("<" not in item.snippet for item in items)


def test_recorded_lumen_response_and_category():
    data = response("search_lumen_reflections")
    data["categories"] = {t["category_id"]: "Rendering" for t in data["topics"]}
    raw = ef.parse_epicforums_response(data, "Lumen reflections")
    assert len(raw) == 22
    items = normalize.normalize_source_items("epicforums", raw, FROM, TO)
    assert all(item.container == "Rendering" for item in items)


def test_date_window_recent_reply_on_old_topic_and_no_future_or_undated():
    data = {"from_date": FROM, "to_date": TO, "topics": [{"id": 1, "title": "Glass", "created_at": "2020-01-01", "last_posted_at": "2026-10-01"}], "posts": [{"topic_id": 1, "created_at": "2026-09-10", "username": "example", "blurb": "Glass"}]}
    assert ef.parse_epicforums_response(data)[0]["date"] == "2026-09-10"
    data["posts"][0]["created_at"] = "2026-10-01"
    assert ef.parse_epicforums_response(data) == []
    assert normalize.normalize_source_items("epicforums", [{"id": 2, "title": "Undated"}], FROM, TO, "evergreen_ok") == []
    assert ef.item_date({"created_at": FROM}, FROM, TO) == FROM
    assert ef.item_date({"last_posted_at": TO}, FROM, TO) == TO


def test_html_cleaning_and_malformed_rows():
    assert ef.strip_html('<p>Glass &amp; light</p><script>bad()</script><p>reflections<br>work</p>') == "Glass & light reflections work"
    data = response()
    data["topics"] += [None, {}, data["topics"][0]]
    data["posts"] += [None]
    assert len(ef.parse_epicforums_response(data)) == 2


def test_short_queries_strip_engine_prefix_stopwords_and_operators():
    assert ef.search_terms("Unreal Engine 5.8 path tracer glass after:1900-01-01 order:views") == "path tracer glass"
    assert ef.query_variants("Unreal Engine 5.8 path tracer glass", "default") == ["path tracer glass", "path tracing glass", "path tracer"]
    assert len(ef.query_variants("how to create Unreal archviz curtain wall photoreal", "deep")) == 3
    assert ef.search_terms("UE5") == "Unreal Engine"
    assert ef.search_terms("UE5.8 path tracer glass") == "path tracer glass"
    assert ef.search_terms("UE4.27.2 glass") == "glass"
    assert ef.search_terms("UEFN glass") == "UEFN glass"


def test_local_result_cap_reports_incomplete_coverage(monkeypatch):
    def get(url, **kwargs):
        if "/categories" in url:
            return {"category_list": {"categories": []}}
        return dict(recording("search_lumen_reflections"), grouped_search_result={"more_full_page_results": False})

    monkeypatch.setattr(http, "get", get)
    result = ef.search_epicforums("lumen reflections", FROM, TO)
    assert result["more_results"]
    assert len(ef.parse_epicforums_response(result)) == ef.DEPTH_CONFIG["default"]["results"]


def test_search_window_merges_topics_and_caches_categories(monkeypatch, clock):
    calls = []

    def get(url, **kwargs):
        calls.append((url, clock.now, kwargs))
        if "/categories" in url:
            return {"category_list": {"categories": [{"id": 11, "name": "Development"}], "subcategories": [{"id": 36, "name": "Rendering"}]}}
        return recording("search_path_tracer_glass")

    monkeypatch.setattr(http, "get", get)
    client = ef.Client()
    result = ef.search_epicforums("Unreal Engine 5.8 path tracer glass", FROM, TO, client=client)
    assert len(result["topics"]) == 2
    assert result["categories"] == {11: "Development", 36: "Rendering"}
    for url, _, kwargs in calls:
        assert kwargs["headers"]["User-Agent"] == ef.USER_AGENT
        assert kwargs["retries"] == 1 and kwargs["retry_dns"] is False
        if "/search.json" in url:
            q = parse_qs(urlsplit(url).query)["q"][0]
            assert "after:2026-08-31 before:2026-10-01 order:latest" in q
    assert all(b[1] - a[1] >= 1 for a, b in zip(calls, calls[1:]))
    assert ef.Client().categories() == {11: "Development", 36: "Rendering"}
    assert sum("categories" in row[0] for row in calls) == 1


def test_429_backoff_and_recovery_not_reported_as_failure(monkeypatch, clock):
    calls = []

    def get(url, **kwargs):
        calls.append(clock.now)
        if len(calls) == 1:
            error = http.HTTPError("HTTP 429", 429, headers={"Retry-After": "4"})
            http._raise(error)
        return {"posts": [], "topics": []}

    monkeypatch.setattr(http, "get", get)
    with http.capture_failures() as failures:
        result = ef.Client().get("/search.json", search=True)
    assert result == {"posts": [], "topics": []}
    assert calls == [100, 104]
    assert not failures


def test_retry_attempts_count_towards_search_budget(monkeypatch, clock):
    calls = []

    def get(*args, **kwargs):
        calls.append(clock.now)
        raise http.HTTPError("HTTP 429", 429)

    monkeypatch.setattr(http, "get", get)
    client = ef.Client(depth="quick")
    result = ef.search_epicforums("UE5 glass", FROM, TO, "quick", client=client)
    assert "429" in result["error"]
    assert client.requests == client.search_requests == 2
    assert calls == [100, 102]


def test_shared_budget_caps_concurrent_subqueries_and_enrichment(monkeypatch, clock):
    calls = []

    def get(url, **kwargs):
        calls.append(clock.now)
        return {}

    monkeypatch.setattr(http, "get", get)
    client = ef.Client(depth="quick")

    def fetch(i):
        try:
            client.get(f"/t/{i}.json")
        except ef.RequestBudgetExceeded:
            return False
        return True

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(fetch, range(20))) == 5
    assert client.requests == len(calls) == 5
    assert all(b - a >= 1 for a, b in zip(calls, calls[1:]))
    assert ef.Client().requests == 0


def test_host_throttle_shared_between_clients_and_deadline(monkeypatch, clock):
    monkeypatch.setattr(http, "get", lambda *a, **k: {})
    ef.Client().get("/one")
    ef.Client().get("/two")
    assert clock.now == 101
    with pytest.raises(http.DeadlineExceeded):
        ef.Client(deadline=101.5).get("/three")


@pytest.mark.parametrize("payload", [[], {}, {"posts": "bad", "topics": []}])
def test_schema_failures_are_visible(monkeypatch, payload):
    monkeypatch.setattr(http, "get", lambda *a, **k: payload)
    assert "schema" in ef.search_epicforums("UE5", FROM, TO, "quick")["error"]


def test_partial_results_survive_later_query_failure(monkeypatch):
    count = 0

    def get(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 1:
            return recording("search_path_tracer_glass")
        raise http.HTTPError("HTTP 503", 503)

    monkeypatch.setattr(http, "get", get)
    result = ef.search_epicforums("path tracer glass", FROM, TO)
    assert len(result["topics"]) == 2 and "503" in result["error"]


@pytest.mark.parametrize("config", [{"LAST30DAYS_EPICFORUMS_BASE": "file:///tmp/foo"}, {"LAST30DAYS_EPICFORUMS_BASE": "https://dummy:dummy@forum.example"}, {"LAST30DAYS_EPICFORUMS_BASE": "https://forum.example?token=dummy"}])
def test_base_rejects_non_http_credentials_and_query(config):
    with pytest.raises(ValueError):
        ef.Client(config)


def test_other_discourse_host(monkeypatch):
    urls = []

    def get(url, **kwargs):
        urls.append(url)
        return {"posts": [], "topics": []}

    monkeypatch.setattr(http, "get", get)
    ef.search_epicforums("bread", FROM, TO, "quick", client=ef.Client({"LAST30DAYS_EPICFORUMS_BASE": "https://forum.example/"}))
    assert urls[0].startswith("https://forum.example/search.json")


@pytest.mark.parametrize("fields", [{"primary_group_name": "Epic_Games_Inc"}, {"flair_name": "Epic_Games_Inc"}, {"user_title": "Staff"}])
def test_staff_is_detected_without_moderator_flags(fields):
    assert ef.is_epic_staff(dict(staff=False, moderator=False, **fields))
    assert not ef.is_epic_staff({"staff": True, "moderator": True})


def test_recorded_topic_enrichment_staff_and_authoritative_engagement(monkeypatch):
    topic = recording("topic")
    monkeypatch.setattr(http, "get", lambda *a, **k: topic)
    raw = dict(topic, date="2026-09-24", snippet="Substrate glass transmission", engagement={"likes": 0, "replies": 0, "views": 0})
    item = normalize.normalize_source_items("epicforums", [raw], "2026-08-01", TO)[0]
    ef.enrich_source_items([item], ef.Client())
    assert item.engagement["replies"] == topic["posts_count"] - 1
    assert item.engagement["views"] == topic["views"]
    assert item.metadata["epic_staff_answered"] is True
    assert any(c["epic_staff"] for c in item.metadata["top_comments"])
    assert "<p>" not in item.metadata["topic_body"]
    # This recorded staff answer is outside the ordinary 30-day window.
    current = normalize.normalize_source_items("epicforums", [raw], FROM, TO)[0]
    ef.enrich_source_items([current], ef.Client())
    assert not current.metadata.get("epic_staff_answered")
    assert current.metadata["top_comments"] == []


def test_staff_score_label_and_marketplace_penalty():
    report = pipeline.run(topic="Unreal glass", config={}, depth="quick", requested_sources=["epicforums"], mock=True)
    candidate = report.ranked_candidates[0]
    primary = schema.candidate_primary_item(candidate)
    baseline = rerank._final_score(candidate)
    primary.metadata["epic_staff_answered"] = True
    assert rerank._final_score(candidate) == pytest.approx(min(100, baseline + 15))
    assert "Epic staff answered" in "\n".join(render._render_candidate(candidate, "1."))
    primary.metadata["epic_staff_answered"] = False
    primary.metadata["marketplace_ad"] = True
    assert rerank._final_score(candidate) == pytest.approx(baseline * 0.6)


def test_duplicate_stream_preserves_staff_enrichment():
    item = normalize.normalize_source_items("epicforums", ef.parse_epicforums_response(response()), FROM, TO)[0]
    enriched = copy.deepcopy(item)
    enriched.metadata.update(epic_staff_answered=True, topic_body="Glass transmission evidence")
    enriched.metadata["top_comments"] = [{"excerpt": "Fixed next release", "epic_staff": True}]
    merged = fusion.merge_source_items(item, enriched)
    assert merged.metadata["epic_staff_answered"]
    assert merged.metadata["topic_body"] == "Glass transmission evidence"
    assert merged.metadata["top_comments"][0]["epic_staff"]


def test_official_forum_supplies_engine_context_but_other_hosts_do_not():
    report = pipeline.run(topic="Unreal glass", config={}, depth="quick", requested_sources=["epicforums"], mock=True)
    candidate = report.ranked_candidates[0]
    candidate.title = "Glass transmission bug"
    candidate.snippet = "Double sided glass materials"
    assert "unreal engine" in rerank._candidate_haystack(candidate)
    candidate.url = "https://forum.example/t/glass/123"
    assert "unreal engine" not in rerank._candidate_haystack(candidate)


def test_pipeline_dispatch_shares_one_client_and_enriches(monkeypatch):
    calls = []
    topic = recording("topic")

    def get(url, **kwargs):
        calls.append(url)
        if "/categories" in url:
            return {"category_list": {"categories": []}}
        if "/t/" in url:
            return topic
        return recording("search_path_tracer_glass")

    monkeypatch.setattr(http, "get", get)
    client = ef.Client()
    config = {"_epicforums_client": client}
    query = schema.SubQuery(label="glass", search_query="path tracer glass", ranking_query="glass", sources=["epicforums"], weight=1)
    kwargs = dict(source="epicforums", subquery=query, date_range=(FROM, TO), config=config, depth="default", topic="Unreal glass", mock=False, runtime=None)
    raw, artifact = pipeline._retrieve_stream(**kwargs)
    assert len(raw) == 2
    assert artifact["epicforums"]["requests"] == 4
    pipeline._retrieve_stream(**kwargs)
    assert len(calls) == 4
    items = normalize.normalize_source_items("epicforums", raw, FROM, TO)
    final = pipeline._finalize_items_by_source({"epicforums": items}, config=config)
    assert all("topic_body" in i.metadata for i in final["epicforums"])
    assert client.requests <= client.settings["requests"]


def test_marketplace_demoted_unless_assets_requested():
    data = response()
    data["topics"][0]["title"] = "Glass Kit"
    regular = ef.parse_epicforums_response(data, "Unreal glass")
    asset = ef.parse_epicforums_response(data, "Unreal glass assets")
    assert next(r for r in regular if r["title"] == "Glass Kit")["marketplace_ad"]
    assert not next(r for r in asset if r["title"] == "Glass Kit")["marketplace_ad"]
    # Fab posts can be in a category absent from /categories.json; their
    # public tag still identifies the ad without per-category requests.
    assert all(r["marketplace_ad"] for r in ef.parse_epicforums_response(response(), "Unreal glass"))


def test_doctor_keyless_probe_and_disable(monkeypatch):
    assert doctor._epicforums_record({})["status"] == health.OK
    assert doctor._epicforums_record({"LAST30DAYS_EPICFORUMS": "off"})["status"] == "opt-in"
    monkeypatch.setattr(http, "get", lambda *a, **k: {"posts": [], "topics": []})
    assert doctor._probe_source("epicforums", {}, 5)["ok"]
    assert doctor._probe_source("epicforums", {"LAST30DAYS_EPICFORUMS": "off"}, 5) is None
    monkeypatch.setattr(http, "get", lambda *a, **k: {"html": "challenge"})
    assert not doctor._probe_source("epicforums", {}, 5)["ok"]


def test_http_single_attempt_budget_also_applies_to_dns(monkeypatch):
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise urllib.error.URLError(socket.gaierror(-2, "name resolution failed"))

    monkeypatch.setattr(http.urllib.request, "urlopen", fail)
    with pytest.raises(http.HTTPError):
        http.get("https://forum.example/search.json", retries=1, retry_dns=False)
    assert len(calls) == 1


def test_pipeline_mock_and_footer():
    report = pipeline.run(topic="Unreal Engine glass", config={}, depth="quick", requested_sources=["epicforums"], mock=True)
    assert report.items_by_source["epicforums"]
    footer = "\n".join(render._build_source_footer_lines(report))
    assert "Epic Forums" in footer and "5 likes" in footer and "12 replies" in footer
    assert report.source_status["epicforums"].items_returned == 1
