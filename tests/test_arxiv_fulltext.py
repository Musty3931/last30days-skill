"""Offline full-paper lane, including an origin/main compact-output golden guard."""
from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib import arxiv, doctor, env, http, normalize, pipeline, render, schema, subproc

ORIGINAL_HTTP_REQUEST = http.request
ORIGINAL_GET_CONFIG = env.get_config
FIXTURES = Path(__file__).parent / "fixtures" / "arxiv_fulltext"
ON = {"LAST30DAYS_ARXIV_FULLTEXT": "on"}
PDF_TEXT = """1 Related Work
Previous software engineering agents achieve 90 percent accuracy on unrelated tasks.
2 Results
Our software engineering agents resolve 42 percent of the measured tasks successfully.
3 Conclusion
The agent interface improves reliability across the evaluated software engineering tasks.
References
[1] Someone wrote about software engineering agents in 2024 and 2025.
"""


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(arxiv, "FULLTEXT_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(arxiv, "_fulltext_last_request", 0.0)
    monkeypatch.setattr(arxiv, "time", SimpleNamespace(monotonic=time.monotonic, sleep=lambda _: None))
    monkeypatch.setattr(arxiv.env, "get_config", lambda: {})
    monkeypatch.setattr(arxiv.shutil, "which", lambda _: None)

    def no_network(*args, **kwargs):
        raise AssertionError("network is forbidden in full-text tests")
    monkeypatch.setattr(http, "request", no_network)


def _item(number=1, score=50):
    item = normalize.normalize_source_items("arxiv", [{
        "id": f"2603.{number:05d}v1", "title": f"Software engineering agents {number}",
        "summary": "Abstract-only evidence stays intact when enrichment cannot run.",
        "url": f"https://arxiv.org/abs/2603.{number:05d}v1", "date": "2026-03-15",
        "authors": ["Ada Example", "Ben Example"], "author": "Ada Example et al.",
    }], "2026-03-01", "2026-03-30")[0]
    item.local_rank_score = score
    return item


def _paper(text=PDF_TEXT, source="pdf"):
    return {"text": text.strip(), "source": source, "words": len(text.split())}


def _report():
    return schema.report_from_dict(json.loads((FIXTURES / "abstract-report.json").read_text()))


def test_html_hit_keeps_article_and_headings(monkeypatch):
    requests = []
    html = (FIXTURES / "latexml.html").read_text()
    def request(method, url, **kwargs):
        requests.append((method, url, kwargs))
        return html
    monkeypatch.setattr(http, "request", request)
    paper = arxiv.fetch_fulltext("https://arxiv.org/abs/2405.15793v3")
    assert paper["source"] == "html"
    assert paper["words"] >= 1500
    assert "# 5 Results" in paper["text"]
    assert "Outside article navigation" not in paper["text"]
    assert "<" not in paper["text"]
    assert len(requests) == 1
    method, url, kwargs = requests[0]
    assert (method, url) == ("GET", "https://arxiv.org/html/2405.15793")
    assert kwargs["headers"]["User-Agent"] == http.USER_AGENT
    assert kwargs["retries"] == 1 and kwargs["retry_dns"] is False
    assert kwargs["deadline_monotonic"] <= time.monotonic() + arxiv.FULLTEXT_TIMEOUT


@pytest.mark.parametrize("html", [None, "<article><p>Too short.</p></article>"])
def test_html_missing_or_short_falls_back_to_pdf(monkeypatch, html):
    requests = []
    def request(method, url, **kwargs):
        requests.append(url)
        if "/html/" in url:
            if html is None:
                raise http.HTTPError("not found", 404)
            return html
        assert kwargs["binary"] is True
        return b"%PDF-1.4\n\xff\x00"
    def extract(command, timeout):
        assert command[0] == "/usr/bin/pdftotext"
        assert Path(command[-2]).read_bytes() == b"%PDF-1.4\n\xff\x00"
        assert 0 < timeout <= 10
        return subproc.SubprocResult(0, PDF_TEXT, "")
    monkeypatch.setattr(http, "request", request)
    monkeypatch.setattr(arxiv.shutil, "which", lambda _: "/usr/bin/pdftotext")
    monkeypatch.setattr(arxiv.subproc, "run_with_timeout", extract)
    paper = arxiv.fetch_fulltext("cs/0112017v2")
    assert paper == _paper()
    assert requests == ["https://arxiv.org/html/cs/0112017", "https://arxiv.org/pdf/cs/0112017"]


def test_no_pdftotext_keeps_abstract_only(monkeypatch):
    requests = []
    monkeypatch.setattr(http, "request", lambda *a, **k: requests.append(a[1]) or "<html>Missing article</html>")
    item = _item()
    before = copy.deepcopy(item)
    arxiv.enrich_source_items([item], "agents", config=ON, depth="quick", deadline=time.monotonic()+1)
    assert item == before
    assert len(requests) == 1 and "/html/" in requests[0]


@pytest.mark.parametrize("error", [TimeoutError, RuntimeError, http.HTTPError])
def test_transport_failure_is_abstract_only_and_run_continues(monkeypatch, error):
    monkeypatch.setattr(arxiv.shutil, "which", lambda _: "pdftotext")
    def request(*a, **k):
        raise error("unavailable")
    monkeypatch.setattr(http, "request", request)
    items = [_item()]
    before = copy.deepcopy(items)
    finalized = pipeline._finalize_items_by_source({"arxiv": items}, "agents", config=ON)
    assert finalized["arxiv"] == before


def test_cache_hit_does_not_call_http_even_for_version_alias(monkeypatch):
    monkeypatch.setattr(http, "request", lambda *a, **k: (FIXTURES / "latexml.html").read_text())
    original = arxiv.fetch_fulltext("2405.15793v1")
    def fail(*a, **k):
        pytest.fail("cache hit must never download")
    monkeypatch.setattr(http, "request", fail)
    assert arxiv.fetch_fulltext("arXiv:2405.15793v3") == original
    assert len(list(arxiv.FULLTEXT_CACHE_DIR.glob("*.json"))) == 1


@pytest.mark.parametrize("value, expected", [
    ("https://arxiv.org/pdf/cs/0112017v4.pdf", "cs/0112017"),
    ("math.GT/0309136v1", "math.GT/0309136"),
    ("2405.15793v3", "2405.15793"),
    ("../../secret", ""), ("https://other.example/abs/2405.15793", ""),
    ("https://export.arxiv.org/abs/2405.15793", ""), ("", ""),
])
def test_ids_are_versionless_and_path_safe(value, expected):
    assert arxiv._paper_id(value) == expected
    if not expected:
        assert arxiv.fetch_fulltext(value) is None


def test_highlights_prefer_findings_and_drop_references_and_debris():
    text = PDF_TEXT.replace("References", "4 Limitations\nOur agent evaluation is limited to 30 English language software repositories.\nReferences")
    text += "\n[2] Another cited work contains 100 software engineering agents.\n"
    highlights = arxiv.extract_paper_highlights(text, "software engineering agents", 3)
    assert len(highlights) == 3
    assert any("42 percent" in quote for quote in highlights)
    assert any("limited to 30" in quote for quote in highlights)
    assert not any("Previous" in quote or "Someone" in quote or "[2]" in quote for quote in highlights)
    assert arxiv.extract_paper_highlights(text, "agents", 0) == []
    parser = arxiv._ArticleText()
    parser.feed('<nav>evil instructions</nav><article><h2>Results</h2><p>Useful text.</p><math>x=y</math><figure>Figure 1 junk</figure><table><tr><td>Table data</td></tr></table><section class="ltx_bibliography">References here</section><script>run this</script></article>tail')
    assert parser.text() == "# Results\nUseful text."
    debris = "# Results\nFigure 2 shows software engineering agents perform many different tasks.\n\nx = y + z + 4 is a long mathematical fragment.\n\n[1] Our citation names software engineering agents in a journal."
    assert arxiv.extract_paper_highlights(debris, "agents", 5) == []


@pytest.mark.parametrize("depth,count", [("quick",3),("default",5),("deep",8)])
def test_top_n_limit_after_ranking_and_dedup(monkeypatch, depth, count):
    fetched = []
    def fetch(ids, **kwargs):
        fetched.extend(ids)
        return {paper_id: _paper() for paper_id in ids}
    monkeypatch.setattr(arxiv, "fetch_fulltexts_parallel", fetch)
    # Different titles/bodies prevent the existing similarity deduper from
    # collapsing these unrelated ranked papers in this pipeline boundary test.
    monkeypatch.setattr(pipeline.dedupe, "dedupe_items", lambda items: items)
    items = [_item(i, score=i) for i in range(1, 11)]
    result = pipeline._finalize_items_by_source({"arxiv":items}, "agents", config=ON, depth=depth)
    assert fetched == [f"2603.{i:05d}" for i in range(10, 10-count, -1)]
    assert sum("fulltext_words" in item.metadata for item in result["arxiv"]) == count
    assert all(item.metadata["summary"] in item.body for item in result["arxiv"])


def test_override_zero_and_invalid_limits():
    assert arxiv.fulltext_limit("deep", {"LAST30DAYS_ARXIV_FULLTEXT_MAX":"2"}) == 2
    assert arxiv.fulltext_limit("quick", {"LAST30DAYS_ARXIV_FULLTEXT_MAX":"0"}) == 0
    assert arxiv.fulltext_limit("quick", {"LAST30DAYS_ARXIV_FULLTEXT_MAX":"bad"}) == 3
    assert arxiv.fulltext_limit("default", {"LAST30DAYS_ARXIV_FULLTEXT_MAX":None}) == 5


def test_exhausted_run_budget_fetches_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(arxiv, "fetch_fulltext", lambda *a, **k: called.append(a) or _paper())
    pipeline._finalize_items_by_source({"arxiv":[_item()]}, "agents", config=ON, elapsed=pipeline.ENRICH_BUDGET_SECONDS+1)
    assert called == []


def test_budget_exhaustion_stops_queued_fetches(monkeypatch):
    now = [100.0]
    calls = []
    monkeypatch.setattr(arxiv, "time", SimpleNamespace(monotonic=lambda: now[0]))
    def fetch(paper_id, **kwargs):
        calls.append(paper_id)
        now[0] = 200.0
        return _paper()
    monkeypatch.setattr(arxiv, "fetch_fulltext", fetch)
    arxiv.fetch_fulltexts_parallel(["2405.15793", "2405.15794", "2405.15795"], deadline=101.0, max_workers=1)
    assert calls == ["2405.15793"]


def test_hung_fetch_cannot_stall_run_and_at_most_two_workers(monkeypatch):
    released = threading.Event()
    calls = []
    def fetch(paper_id, **kwargs):
        calls.append(paper_id)
        released.wait(2)
        return _paper()
    monkeypatch.setattr(arxiv, "fetch_fulltext", fetch)
    started = time.monotonic()
    try:
        result = arxiv.fetch_fulltexts_parallel([str(i) for i in range(5)], deadline=started+0.08, max_workers=10)
        assert time.monotonic()-started < 0.5
        assert result == {} and len(calls) == 2
    finally:
        released.set()


def test_request_spacing_includes_pdf_and_respects_deadline(monkeypatch):
    now = [100.0]
    starts = []
    monkeypatch.setattr(arxiv, "time", SimpleNamespace(monotonic=lambda:now[0], sleep=lambda n:now.__setitem__(0,now[0]+n)))
    monkeypatch.setattr(http, "request", lambda *a, **k: starts.append(now[0]) or "text")
    arxiv._fulltext_request("2405.15793", "html", 110)
    arxiv._fulltext_request("2405.15793", "pdf", 110)
    assert starts == [100.0,103.0]
    with pytest.raises(TimeoutError):
        arxiv._fulltext_request("2405.15794", "html", 105)
    assert starts == [100.0,103.0]


# Pin the pre-feature origin/main revision so future main changes cannot silently
# redefine the baseline. Neither this test nor its subprocess fetches from Git.
ORIGIN_MAIN_BASELINE = "eaf979e79566ca6f45f5978cbf75c33af0f9b193"
FROZEN_RENDER_DATE = "2026-03-16"
FROZEN_RENDER_VERSION = "3.23.2"


@pytest.fixture
def frozen_compact_renderer(monkeypatch):
    from datetime import date

    frozen_today = date.fromisoformat(FROZEN_RENDER_DATE)
    monkeypatch.setattr(render, "date", SimpleNamespace(today=lambda: frozen_today))
    monkeypatch.setattr(render, "_skill_version", lambda: FROZEN_RENDER_VERSION)


@pytest.mark.parametrize("setting", [None, "off"])
def test_fulltext_off_compact_is_byte_identical_to_origin_main(
    monkeypatch, frozen_compact_renderer, setting,
):
    """The disabled feature preserves the independently verified baseline bytes."""
    if setting is None:
        monkeypatch.delenv("LAST30DAYS_ARXIV_FULLTEXT", raising=False)
    else:
        monkeypatch.setenv("LAST30DAYS_ARXIV_FULLTEXT", setting)
    config = {} if setting is None else {"LAST30DAYS_ARXIV_FULLTEXT":setting}
    report = _report()
    def forbidden(*a, **k):
        pytest.fail("disabled enrichment must not fetch")
    monkeypatch.setattr(arxiv, "fetch_fulltexts_parallel", forbidden)
    report.items_by_source = pipeline._finalize_items_by_source(report.items_by_source, report.topic, config=config)
    assert render.render_compact(report).encode() == (FIXTURES / "abstract-compact.md").read_bytes()


@pytest.fixture(scope="module")
def origin_main_checkout(tmp_path_factory):
    """Export the complete baseline scripts, including their own dependencies."""
    import subprocess
    import tarfile

    root = Path(__file__).resolve().parents[1]
    try:
        available = subprocess.run(
            ["git", "cat-file", "-e", ORIGIN_MAIN_BASELINE + "^{commit}"],
            cwd=root, capture_output=True, timeout=30,
        )
    except FileNotFoundError:
        pytest.skip("origin/main parity requires Git and the pinned baseline commit")
    if available.returncode:
        # Source archives and shallow CI checkouts may not contain old objects.
        # The current-code golden guard above always runs, including there.
        pytest.skip(
            f"origin/main parity requires local commit {ORIGIN_MAIN_BASELINE}: "
            f"{available.stderr.decode().strip()}"
        )
    checkout = tmp_path_factory.mktemp("arxiv-origin-main")
    archive = checkout / "scripts.tar"
    subprocess.run(
        ["git", "archive", "--format=tar", f"--output={archive}",
         ORIGIN_MAIN_BASELINE, "skills/last30days/scripts"],
        cwd=root, check=True, capture_output=True, timeout=30,
    )
    with tarfile.open(archive) as snapshot:
        snapshot.extractall(checkout, filter="data")
    return checkout


@pytest.mark.parametrize("setting", ["absent", "off"])
def test_origin_main_renderer_independently_matches_golden(origin_main_checkout, setting):
    """Run the pre-feature code in isolation with the SAME JSON, clock and version.

    Reproduce offline with:
    uv run pytest tests/test_arxiv_fulltext.py -k origin_main -v
    Both baseline cases must pass (not skip) to establish origin/main provenance.
    """
    import subprocess
    import sys

    script = """
import json
import os
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

scripts, fixture, setting, frozen_date, version = sys.argv[1:]
sys.path.insert(0, scripts)
if setting == "absent":
    os.environ.pop("LAST30DAYS_ARXIV_FULLTEXT", None)
    config = {}
else:
    os.environ["LAST30DAYS_ARXIV_FULLTEXT"] = "off"
    config = {"LAST30DAYS_ARXIV_FULLTEXT": "off"}
from lib import http, pipeline, render, schema
assert Path(render.__file__).resolve() == Path(scripts) / "lib" / "render.py"
render.date = SimpleNamespace(today=lambda: date.fromisoformat(frozen_date))
render._skill_version = lambda: version
with patch.object(http, "request", side_effect=AssertionError("network forbidden")):
    report = schema.report_from_dict(json.loads(Path(fixture).read_text()))
    report.items_by_source = pipeline._finalize_items_by_source(
        report.items_by_source, report.topic, config=config,
    )
    sys.stdout.buffer.write(render.render_compact(report).encode("utf-8"))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script,
         str(origin_main_checkout / "skills/last30days/scripts"),
         str(FIXTURES / "abstract-report.json"), setting,
         FROZEN_RENDER_DATE, FROZEN_RENDER_VERSION],
        cwd=origin_main_checkout, capture_output=True, check=True, timeout=30,
    )
    assert result.stdout == (FIXTURES / "abstract-compact.md").read_bytes()


def test_render_paper_quotes_and_feature_scoped_footer():
    report = _report()
    item = report.items_by_source["arxiv"][0]
    item.metadata.update(fulltext_highlights=["Our software agents resolved 42 percent of tasks successfully."],fulltext_source="html",fulltext_words=1700)
    report.ranked_candidates[0].source_items = [item]
    report.artifacts["arxiv_fulltext_enabled"] = True
    assert render._build_source_footer_lines(report) == ["📄 arXiv: 1 paper │ 1/1 full text"]
    for output in (render.render_compact(report), render.render_full(report)):
        assert 'Paper (full text): "Our software agents resolved 42 percent of tasks successfully."' in output
    item.metadata.clear()
    assert "0/1 full text" in render._build_source_footer_lines(report)[0]
    report.artifacts.clear()
    assert render._build_source_footer_lines(report) == ["📄 arXiv: 1 paper"]


def test_save_fulltext_header_and_safe_old_id(monkeypatch, tmp_path):
    item = _item()
    item.url = "https://arxiv.org/abs/cs/0112017v2"
    monkeypatch.setattr(arxiv, "fetch_fulltexts_parallel", lambda ids, **k: {paper_id:_paper() for paper_id in ids})
    arxiv.enrich_source_items([item], "agents", config=ON, depth="quick", deadline=time.monotonic()+1, save_dir=tmp_path)
    saved = (tmp_path/"arxiv"/"cs_0112017.md").read_text()
    assert item.title in saved and "https://arxiv.org/abs/cs/0112017" in saved
    assert "Source: pdf" in saved and f"Word count: {len(PDF_TEXT.split())}" in saved
    assert "untrusted third-party text" in saved.lower()


def test_normalizer_preserves_paper_metadata():
    raw = {"title":"A paper", "summary":"Abstract", "date":"2026-03-15", "fulltext_highlights":["A quoted finding"], "fulltext_source":"pdf", "fulltext_words":2000}
    item = normalize.normalize_source_items("arxiv", [raw], "2026-03-01", "2026-03-30")[0]
    assert item.metadata["fulltext_words"] == 2000
    assert item.metadata["fulltext_highlights"] == ["A quoted finding"]


def test_doctor_reports_optional_pdf_without_network_and_isolates_html_probe(monkeypatch):
    record = doctor._arxiv_record(ON)
    assert record["fulltext"] == {"enabled":True, "pdftotext":False}
    assert "pdftotext absent (optional)" in doctor._audit_source_line("arxiv", record, "UNVERIFIED")
    assert "fulltext" not in doctor._arxiv_record({})
    monkeypatch.setattr(arxiv.shutil, "which", lambda _:"pdftotext")
    assert doctor._arxiv_record(ON)["fulltext"]["pdftotext"] is True
    calls=[]
    def fetch(paper_id, source, deadline):
        calls.append((paper_id, source, deadline))
        return "<article>paper</article>"
    monkeypatch.setattr(arxiv, "_fulltext_request", fetch)
    result = doctor._probe_source("arxiv_fulltext", ON, 2)
    assert result["ok"] is True and result["probed"] is True
    assert len(calls)==1 and calls[0][1]=="html" and calls[0][2]<=time.monotonic()+2
    assert doctor._probe_source("arxiv", ON, 2) is None  # never claim OpenAlex was probed
    assert doctor._probe_source("arxiv_fulltext", {}, 2) is None
    report = {"sources":{"arxiv":record}}
    doctor._apply_probe(report, {"arxiv_fulltext":result})
    assert "probe" not in record and record["fulltext"]["probe"]==result
    assert "html reachable" in doctor._audit_source_line("arxiv",record,"UNVERIFIED")
    assert doctor._config_fingerprint(ON) != doctor._config_fingerprint({})


def test_doctor_fulltext_probe_failure_and_dispatch(monkeypatch):
    def fail(*args):
        raise TimeoutError("bounded")
    monkeypatch.setattr(arxiv, "_fulltext_request", fail)
    result = doctor._probe_source("arxiv_fulltext", ON, 0.1)
    assert result == {"ok":False,"detail":"TimeoutError","probed":True}
    monkeypatch.setattr(doctor, "_probeable_sources", lambda:())
    assert doctor._probe_sources(ON,1)=={"arxiv_fulltext":result}
    assert doctor._probe_sources({},1)=={}


def test_shared_http_binary_path_preserves_bytes_and_fixture_replay(monkeypatch, tmp_path):
    from unittest.mock import MagicMock
    # Restore the real helper; all transport calls remain mocked offline.
    monkeypatch.setattr(http, "request", ORIGINAL_HTTP_REQUEST)
    response = MagicMock()
    response.__enter__.return_value=response
    response.status=200
    response.read.return_value=b"%PDF-1.4\n\xff\x80\x00"
    monkeypatch.setattr(http.urllib.request,"urlopen",lambda *a,**k:response)
    url="https://arxiv.org/pdf/2405.15793"
    with http.recording_requests(tmp_path/"http.json"):
        content=http.request("GET",url,binary=True,retries=1)
    assert content==response.read.return_value
    monkeypatch.setattr(http.urllib.request,"urlopen",lambda *a,**k:pytest.fail("replay must be offline"))
    with http.replaying_requests(tmp_path/"http.json"):
        assert http.request("GET",url,binary=True,retries=1)==content
    # The opt-in must not change text/JSON defaults for existing callers.
    monkeypatch.setattr(http.urllib.request,"urlopen",lambda *a,**k:response)
    response.read.return_value=b'{"text":"paper"}'
    assert http.request("GET",url,raw=True,retries=1)=='{"text":"paper"}'
    assert http.request("GET",url,retries=1)=={"text":"paper"}


def test_env_knobs_load_from_file_and_shell(monkeypatch,tmp_path):
    from unittest.mock import patch
    settings=tmp_path/"settings.env"
    settings.write_text("LAST30DAYS_ARXIV_FULLTEXT=on\nLAST30DAYS_ARXIV_FULLTEXT_MAX=7\n")
    monkeypatch.setattr(env,"CONFIG_FILE",settings)
    monkeypatch.delenv("LAST30DAYS_ARXIV_FULLTEXT",raising=False)
    monkeypatch.delenv("LAST30DAYS_ARXIV_FULLTEXT_MAX",raising=False)
    with patch.object(env,"_load_keychain",return_value={}), patch.object(env,"_load_pass",return_value={}):
        config=ORIGINAL_GET_CONFIG()
        assert config["LAST30DAYS_ARXIV_FULLTEXT"]=="on"
        assert config["LAST30DAYS_ARXIV_FULLTEXT_MAX"]=="7"
        monkeypatch.setenv("LAST30DAYS_ARXIV_FULLTEXT","off")
        monkeypatch.setenv("LAST30DAYS_ARXIV_FULLTEXT_MAX","2")
        config=ORIGINAL_GET_CONFIG()
        assert not arxiv.fulltext_enabled(config)
        assert arxiv.fulltext_limit("deep",config)==2


def test_save_fulltext_parser_requires_opt_in_and_directory(monkeypatch,capsys):
    import last30days as cli
    args=cli.build_parser().parse_args(["agents","--save-fulltext"])
    assert args.save_fulltext is True
    monkeypatch.setattr(cli.env,"get_config",lambda **kwargs:{})
    monkeypatch.delenv("LAST30DAYS_MEMORY_DIR",raising=False)
    monkeypatch.setattr("sys.argv",["last30days.py","agents","--save-fulltext"])
    assert cli.main()==2
    assert "requires a save directory" in capsys.readouterr().err


def test_mock_run_marks_enabled_footer_without_downloading():
    report = pipeline.run(topic="software engineering agents",config=ON,depth="quick",requested_sources=["arxiv"],mock=True)
    assert report.artifacts["arxiv_fulltext_enabled"] is True
    assert "full text" in render._build_source_footer_lines(report)[0]


def test_replay_preserves_fulltext_metadata_without_refetch(monkeypatch,tmp_path):
    monkeypatch.setattr(arxiv,"fetch_fulltexts_parallel",lambda ids,**k:{paper_id:_paper() for paper_id in ids})
    with http.recording_requests(tmp_path/"fixture.json"):
        first=pipeline._finalize_items_by_source({"arxiv":[_item()]},"agents",config=ON)
    monkeypatch.setattr(arxiv,"enrich_source_items",lambda *a,**k:pytest.fail("replayed enrichment must not fetch"))
    with http.replaying_requests(tmp_path/"fixture.json"):
        replayed=pipeline._finalize_items_by_source({"arxiv":[_item()]},"agents",config=ON)
    assert replayed==first


def test_cross_query_fusion_retains_highlights_from_best_copy(monkeypatch):
    from lib import providers
    monkeypatch.setattr(pipeline,"available_sources",lambda *a:["arxiv"])
    monkeypatch.setattr(providers,"resolve_runtime",lambda c,d:(providers.mock_runtime(c,d),None))
    monkeypatch.setattr(arxiv,"search_arxiv",lambda *a,**k:{"results":[{
        "id":"https://arxiv.org/abs/2603.00001v1", "title":"Software engineering agents",
        "published":"2026-03-15T00:00:00Z", "summary":"Software engineering agents improve software engineering task performance.",
        "authors":[{"name":"Ada Example"}],
    }]})
    original_finalize=pipeline._finalize_items_by_source
    def finalize(items_by_source,*a,**k):
        items=items_by_source["arxiv"]
        assert len(items)>=2
        for i,item in enumerate(items):
            item.local_rank_score=i+1
        return original_finalize(items_by_source,*a,**k)
    monkeypatch.setattr(pipeline,"_finalize_items_by_source",finalize)
    fetched=[]
    def fetch(ids,**kwargs):
        fetched.extend(ids)
        return {paper_id:_paper() for paper_id in ids}
    monkeypatch.setattr(arxiv,"fetch_fulltexts_parallel",fetch)
    plan={"intent":"concept","freshness_mode":"evergreen_ok","cluster_mode":"none","subqueries":[
        {"label":"primary","search_query":"software engineering agents","ranking_query":"software engineering agents","sources":["arxiv"]},
        {"label":"alternate","search_query":"engineering agent evaluation","ranking_query":"software engineering agents","sources":["arxiv"]},
    ]}
    report=pipeline.run(topic="software engineering agents",config=ON,depth="default",requested_sources=["arxiv"],external_plan=plan,as_of_date="2026-03-30")
    assert fetched==["2603.00001"]
    assert report.ranked_candidates[0].source_items[0].metadata["fulltext_source"]=="pdf"
    assert "Paper (full text):" in render.render_compact(report)


def test_pipeline_contains_unexpected_enrichment_exception(monkeypatch,capsys):
    def fail(*a,**k):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(arxiv,"enrich_source_items",fail)
    item=_item()
    assert pipeline._finalize_items_by_source({"arxiv":[item]},config=ON)["arxiv"]==[item]
    assert "Full text enrichment unavailable" in capsys.readouterr().err


def test_corrupt_cache_and_unwritable_cache_degrade_cleanly(monkeypatch,tmp_path):
    arxiv.FULLTEXT_CACHE_DIR.mkdir()
    path=arxiv.FULLTEXT_CACHE_DIR/"2405.15793.json"
    path.write_text("not json")
    monkeypatch.setattr(http,"request",lambda *a,**k:(FIXTURES/"latexml.html").read_text())
    assert arxiv.fetch_fulltext("2405.15793")["source"]=="html"
    assert json.loads(path.read_text())["version"]==1
    blocked=tmp_path/"not-a-directory"
    blocked.write_text("file")
    monkeypatch.setattr(arxiv,"FULLTEXT_CACHE_DIR",blocked)
    assert arxiv.fetch_fulltext("2405.15793")["source"]=="html"


def test_cross_run_requests_share_two_slots(monkeypatch):
    entered=[]
    release=threading.Event()
    ready=threading.Event()
    def request(*a,**k):
        entered.append(a[1])
        if len(entered)==2:
            ready.set()
        release.wait(2)
        return "html"
    monkeypatch.setattr(http,"request",request)
    errors=[]
    def request_one(duration):
        try:
            arxiv._fulltext_request("2405.15793","html",time.monotonic()+duration)
        except TimeoutError:
            errors.append("expired")
    threads=[threading.Thread(target=request_one, args=(duration,)) for duration in (10,10,0.12,0.12)]
    try:
        for thread in threads[:2]:
            thread.start()
        assert ready.wait(1)
        for thread in threads[2:]:
            thread.start()
        # Extra concurrent runs must expire waiting for one of the two slots.
        for thread in threads[2:]:
            thread.join(1)
        assert len(entered)==2 and len(errors)==2
    finally:
        release.set()
        for thread in threads:
            if thread.ident is not None:
                thread.join(1)


def test_reference_continuations_cannot_become_headings():
    text=PDF_TEXT+"\n1 Smith and Example\nSoftware engineering agents were described in 2025 in a paper."
    assert not any("described in 2025" in x for x in arxiv.extract_paper_highlights(text,"agents"))


@pytest.mark.parametrize("code,stdout", [(1,PDF_TEXT),(0,"")])
def test_failed_or_empty_pdf_extraction_stays_abstract_only(monkeypatch,code,stdout):
    monkeypatch.setattr(http,"request",lambda method,url,**k: b"%PDF" if "/pdf/" in url else "")
    monkeypatch.setattr(arxiv.shutil,"which",lambda _:"pdftotext")
    monkeypatch.setattr(arxiv.subproc,"run_with_timeout",lambda *a,**k:subproc.SubprocResult(code,stdout,""))
    assert arxiv.fetch_fulltext("2405.15793") is None


def test_parent_enrichment_budget_wins_over_run_budget(monkeypatch):
    called=[]
    monkeypatch.setattr(arxiv,"fetch_fulltext",lambda *a,**k:called.append(a))
    config={**ON,"_enrichment_deadline":time.monotonic()-1}
    pipeline._finalize_items_by_source({"arxiv":[_item()]},config=config)
    assert called==[]
