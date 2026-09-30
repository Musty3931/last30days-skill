"""OpenAlex adapter, fallback, configuration, and health contracts (no network)."""
from datetime import datetime, timezone
from io import BytesIO
import json
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

import pytest

from lib import arxiv, doctor, env, health, pipeline

NOW = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)


@pytest.fixture
def work():
    return {
        "id": "https://openalex.org/W000000001",
        "doi": "https://doi.org/10.48550/arXiv.2609.00001",
        "ids": {"doi": "https://doi.org/10.48550/arXiv.2609.00001"},
        "title": "Agent Memory Consolidation",
        "publication_date": "2026-09-10",
        "authorships": [
            {"author": {"display_name": "Ada Example"}},
            {"author": {"display_name": "Grace Example"}},
        ],
        "primary_location": {"landing_page_url": "http://arxiv.org/abs/2609.00001"},
        "abstract_inverted_index": {"memory": [1, 4], "Agent": [0], "improves": [2], "agent": [3]},
        "relevance_score": 12.5,
    }


@pytest.fixture
def transport(monkeypatch):
    config = {}
    monkeypatch.setattr(arxiv.env, "get_config", lambda: config)
    monkeypatch.setattr(arxiv, "_today", lambda: NOW)
    urlopen = Mock()
    monkeypatch.setattr(arxiv.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(arxiv, "_search_cli", Mock(side_effect=AssertionError("CLI fallback was not stubbed")))
    return config, urlopen


def reply(works):
    return BytesIO(json.dumps({"results": works}).encode())


def search(topic="agent memory", depth="default"):
    return arxiv.search_arxiv(topic, "2026-08-16", "2026-09-15", depth=depth)


def test_mapping_reconstructs_abstract_and_preserves_parse_contract(work):
    entry = arxiv._openalex_entry(work)
    assert entry["summary"] == "Agent memory improves agent memory"
    assert entry["id"] == "https://arxiv.org/abs/2609.00001"
    assert entry["published"] == entry["updated"] == "2026-09-10T00:00:00Z"
    assert entry["authors"] == [{"name": "Ada Example"}, {"name": "Grace Example"}]
    assert entry["links"] == [{"rel": "alternate", "href": "https://arxiv.org/abs/2609.00001"}]
    items = arxiv.parse_arxiv_response({"results": [entry]}, query="agent memory", today=NOW)
    assert items[0]["author"] == "Ada Example et al."
    assert items[0]["date"] == "2026-09-10"


@pytest.mark.parametrize("identifier", ["2609.99999", "arXiv:2609.99999", "http://arxiv.org/abs/2609.99999", "https://arxiv.org/pdf/2609.99999.pdf"])
def test_arxiv_id_takes_precedence_over_doi(work, identifier):
    work["ids"]["arxiv"] = identifier
    assert arxiv._openalex_entry(work)["id"] == "https://arxiv.org/abs/2609.99999"


@pytest.mark.parametrize("doi", ["10.48550/arxiv.2609.00001", "https://doi.org/10.48550/arXiv.2609.00001", "http://dx.doi.org/10.48550/arxiv.2609.00001"])
def test_doi_derivation(work, doi):
    work["doi"] = doi
    assert arxiv._openalex_entry(work)["id"] == "https://arxiv.org/abs/2609.00001"


def test_missing_doi_uses_landing_page_and_missing_abstract_is_empty(work):
    work.update(doi=None, ids={}, abstract_inverted_index=None, authorships=None)
    entry = arxiv._openalex_entry(work)
    assert entry["links"][0]["href"] == work["primary_location"]["landing_page_url"]
    assert entry["summary"] == ""
    assert entry["authors"] == []


@pytest.mark.parametrize("date,kept", [("2027-01-01", False), ("2026-09-16", True), ("2025-01-01", False), (None, False), ("bad-date", False)])
def test_mapped_dates_keep_existing_recency_and_future_rules(work, date, kept):
    work["publication_date"] = date
    items = arxiv.parse_arxiv_response({"results": [arxiv._openalex_entry(work)]}, today=NOW)
    assert bool(items) is kept


@pytest.mark.parametrize("depth,limit", [("quick", 5), ("default", 10), ("deep", 20), ("unknown", 10)])
def test_request_uses_caller_window_depth_and_one_call(transport, work, depth, limit):
    config, urlopen = transport
    config.update(OPENALEX_API_KEY="dummy-openalex-secret-000", LAST30DAYS_MAILTO="test@example.org")
    urlopen.return_value = reply([work])
    assert len(search(depth=depth)["results"]) == 1
    urlopen.assert_called_once()
    request = urlopen.call_args.args[0]
    query = parse_qs(urlsplit(request.full_url).query)
    assert query["filter"] == ["primary_location.source.id:S4306400194,from_publication_date:2026-08-16,to_publication_date:2026-09-15,title_and_abstract.search:agent memory"]
    assert query["sort"] == ["relevance_score:desc"]
    assert query["per_page"] == [str(limit)]
    assert query["api_key"] == ["dummy-openalex-secret-000"]
    assert "abstract_inverted_index" in query["select"][0]
    assert request.get_header("User-agent") == "last30days/3.23.0 (+mailto:test@example.org)"
    assert urlopen.call_args.kwargs == {"timeout": 20}


def test_keyless_request_works_without_cli(transport, work, monkeypatch):
    _, urlopen = transport
    monkeypatch.setattr(arxiv.shutil, "which", lambda _: None)
    urlopen.return_value = reply([work])
    assert len(search()["results"]) == 1
    request = urlopen.call_args.args[0]
    assert "api_key" not in parse_qs(urlsplit(request.full_url).query)
    assert request.get_header("User-agent") == "last30days/3.23.0"


def test_empty_multiword_result_retries_with_documented_or(transport, work):
    _, urlopen = transport
    urlopen.side_effect = [reply([]), reply([work])]
    assert len(search('"agent memory"')["results"]) == 1
    assert urlopen.call_count == 2
    query = parse_qs(urlsplit(urlopen.call_args.args[0].full_url).query)
    assert query["filter"][0].endswith('title_and_abstract.search:"agent" OR "memory"')


def test_single_term_empty_does_not_retry(transport):
    _, urlopen = transport
    urlopen.return_value = reply([])
    assert search("memory") == {"results": []}
    urlopen.assert_called_once()


def test_clean_empty_or_result_does_not_fall_back(transport, monkeypatch):
    _, urlopen = transport
    urlopen.side_effect = [reply([]), reply([])]
    cli = Mock(side_effect=AssertionError("clean empty is not an error"))
    monkeypatch.setattr(arxiv, "_search_cli", cli)
    assert search() == {"results": []}
    cli.assert_not_called()


def test_filter_delimiters_cannot_change_source_or_recency(transport):
    _, urlopen = transport
    urlopen.side_effect = [reply([]), reply([])]
    search("agent,memory|consolidation")
    filters = parse_qs(urlsplit(urlopen.call_args_list[0].args[0].full_url).query)["filter"][0]
    assert filters.endswith("title_and_abstract.search:agent memory consolidation")
    assert filters.count(",") == 3


@pytest.mark.parametrize("error", [HTTPError("https://api.openalex.org/works", 429, "rate exceeded", {}, None), URLError("offline"), TimeoutError("timed out"), ValueError("bad JSON")])
def test_transport_errors_fall_back_to_original_cli(transport, work, monkeypatch, error):
    _, urlopen = transport
    urlopen.side_effect = error
    cli = Mock(return_value={"results": [arxiv._openalex_entry(work)]})
    monkeypatch.setattr(arxiv, "_search_cli", cli)
    logs = Mock()
    monkeypatch.setattr(arxiv, "_log", logs)
    assert len(search(depth="quick")["results"]) == 1
    cli.assert_called_once_with("agent memory", "2026-08-16", "2026-09-15", "quick")
    assert "falling back to cli" in logs.call_args.args[0]
    if isinstance(error, HTTPError):
        assert "HTTP 429" in logs.call_args.args[0]


def test_malformed_envelope_falls_back(transport, monkeypatch):
    _, urlopen = transport
    urlopen.return_value = BytesIO(b'{"error":"service unavailable"}')
    cli = Mock(return_value={"results": [], "error": "cli unavailable"})
    monkeypatch.setattr(arxiv, "_search_cli", cli)
    assert search()["error"] == "cli unavailable"
    cli.assert_called_once()


def test_retry_failure_falls_back_without_exposing_key(transport, monkeypatch):
    config, urlopen = transport
    config["OPENALEX_API_KEY"] = "dummy-openalex-secret-000"
    urlopen.side_effect = [reply([]), URLError("https://api.openalex.org/works?api_key=dummy-openalex-secret-000")]
    monkeypatch.setattr(arxiv, "_search_cli", lambda *a: {"results": []})
    logs = Mock()
    monkeypatch.setattr(arxiv, "_log", logs)
    search()
    assert "dummy-openalex-secret-000" not in str(logs.call_args_list)
    assert "[redacted]" in str(logs.call_args_list)


@pytest.mark.parametrize("setting,backend", [(None, "openalex"), ("openalex", "openalex"), ("auto", "openalex"), ("cli", "cli"), (" CLI ", "cli"), ("typo", "openalex")])
def test_backend_selection(setting, backend):
    assert arxiv.get_backend({"LAST30DAYS_ARXIV_BACKEND": setting}) == backend


@pytest.mark.parametrize("setting", ["openalex", "auto"])
def test_openalex_and_auto_use_openalex(transport, work, setting):
    config, urlopen = transport
    config["LAST30DAYS_ARXIV_BACKEND"] = setting
    urlopen.return_value = reply([work])
    assert len(search()["results"]) == 1
    urlopen.assert_called_once()


def test_cli_selection_does_not_call_openalex(transport, monkeypatch):
    config, urlopen = transport
    config["LAST30DAYS_ARXIV_BACKEND"] = "cli"
    cli = Mock(return_value={"results": []})
    monkeypatch.setattr(arxiv, "_search_cli", cli)
    search()
    cli.assert_called_once()
    urlopen.assert_not_called()
    assert arxiv.SEARCH_TIMEOUT == 50


@pytest.mark.parametrize("backend,available", [("openalex", True), ("auto", True), ("cli", False)])
def test_pipeline_available_without_cli(monkeypatch, backend, available):
    monkeypatch.setattr(pipeline, "which", lambda _: None)
    monkeypatch.setattr(env, "get_x_source", lambda *a, **kw: None)
    result = pipeline.available_sources({"LAST30DAYS_ARXIV_BACKEND": backend}, local_only=True, x_pending=False)
    assert ("arxiv" in result) is available


@pytest.mark.parametrize("key", [None, "dummy-openalex-secret-000"])
def test_doctor_openalex_reports_key_presence_without_required_cli(monkeypatch, key):
    monkeypatch.setattr(health, "probe_dependency", Mock(side_effect=AssertionError("not a required dependency")))
    record = doctor._arxiv_record({"OPENALEX_API_KEY": key})
    assert record["status"] == health.OK
    assert record["active_backend"] == "openalex"
    assert record["key_configured"] is bool(key)
    assert "optional" in record["requires"]
    assert "dummy-openalex-secret-000" not in json.dumps(record)


def test_doctor_cli_preserves_missing_binary_health(monkeypatch):
    monkeypatch.setattr(health, "probe_dependency", lambda name: health.DependencyProbe(name=name, status=health.MISSING, detail="absent", prescription="install CLI"))
    record = doctor._arxiv_record({"LAST30DAYS_ARXIV_BACKEND": "cli"})
    assert record["active_backend"] == "cli"
    assert record["status"] == "opt-in"
    assert record["detail"].startswith("cli backend")


def test_doctor_fingerprint_changes_with_backend_and_key_presence():
    base = doctor._config_fingerprint({})
    assert doctor._config_fingerprint({"LAST30DAYS_ARXIV_BACKEND": "cli"}) != base
    assert doctor._config_fingerprint({"OPENALEX_API_KEY": "dummy-openalex-secret-000"}) != base
    assert doctor._config_fingerprint({"OPENALEX_API_KEY": "dummy-other-secret-000"}) == doctor._config_fingerprint({"OPENALEX_API_KEY": "dummy-openalex-secret-000"})


def test_new_config_keys_load_from_env_file_with_process_precedence(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("LAST30DAYS_ARXIV_BACKEND=cli\nOPENALEX_API_KEY=dummy-file-secret-000\nLAST30DAYS_MAILTO=test@example.org\n", encoding="utf-8")
    monkeypatch.setattr(env, "CONFIG_FILE", path)
    monkeypatch.setattr(env, "_load_keychain", lambda *a: {})
    monkeypatch.setattr(env, "_load_pass", lambda *a: {})
    monkeypatch.setattr(env, "_project_config_trusted", lambda *a: False)
    for key in ("OPENALEX_API_KEY", "LAST30DAYS_ARXIV_BACKEND", "LAST30DAYS_MAILTO"):
        monkeypatch.delenv(key, raising=False)
    config = env.get_config()
    assert config["LAST30DAYS_ARXIV_BACKEND"] == "cli"
    assert config["OPENALEX_API_KEY"] == "dummy-file-secret-000"
    assert config["LAST30DAYS_MAILTO"] == "test@example.org"
    monkeypatch.setenv("OPENALEX_API_KEY", "dummy-process-secret-000")
    monkeypatch.setenv("LAST30DAYS_ARXIV_BACKEND", "auto")
    config = env.get_config()
    assert config["OPENALEX_API_KEY"] == "dummy-process-secret-000"
    assert config["LAST30DAYS_ARXIV_BACKEND"] == "auto"


def test_recency_cap_applies_to_wider_requested_windows(transport, work):
    _, urlopen = transport
    urlopen.return_value = reply([work])
    arxiv.search_arxiv("agent memory", "2020-01-01", "2026-09-15")
    filters = parse_qs(urlsplit(urlopen.call_args.args[0].full_url).query)["filter"][0]
    assert "from_publication_date:2025-09-15," in filters


def test_recent_openalex_result_survives_pipeline_normalization(transport, work):
    _, urlopen = transport
    urlopen.return_value = reply([work])
    parsed = arxiv.parse_arxiv_response(search(), query="agent memory", today=NOW)
    items = pipeline._normalize_score_dedupe(
        "arxiv", parsed, "2026-08-16", "2026-09-15", "balanced_recent", "agent memory",
    )
    assert len(items) == 1
    assert items[0].published_at == "2026-09-10"
