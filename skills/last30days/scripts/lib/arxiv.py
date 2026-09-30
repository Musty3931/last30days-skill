"""arXiv research-paper source for last30days.

OpenAlex is the default backend, with arxiv-pp-cli as a fallback on errors.
LAST30DAYS_ARXIV_BACKEND=cli preserves the direct Atom API path; auto uses
OpenAlex first too. OpenAlex searches titles/abstracts with relevance sorting;
the CLI uses a quoted phrase then an AND-term retry. Both feed the same parser
and RECENCY_DAYS cutoff, keeping old keyword matches out of current research.
"""

from __future__ import annotations

import json
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from . import env, log, subproc
from .relevance import token_overlap_relevance


CLI_BIN = "arxiv-pp-cli"
OPENALEX_URL = "https://api.openalex.org/works"
OPENALEX_SOURCE = "S4306400194"
OPENALEX_TIMEOUT = 20
OPENALEX_FIELDS = (
    "id,doi,ids,title,publication_date,authorships,primary_location,"
    "abstract_inverted_index,relevance_score"
)


def get_backend(config: Optional[Dict[str, Any]] = None) -> str:
    """Resolve the active primary backend; auto is OpenAlex-first."""
    if config is None:
        config = env.get_config()
    selected = str(config.get("LAST30DAYS_ARXIV_BACKEND") or "openalex").strip().lower()
    return "cli" if selected == "cli" else "openalex"

# Per-depth result counts.
DEPTH_CONFIG = {
    "quick": 5,
    "default": 10,
    "deep": 20,
}

# Recency window for arXiv specifically. Papers do not trend daily; a year keeps
# the source current (the off-topic 2017 paper still drops) without discarding
# the genuinely-relevant work from the last few months.
RECENCY_DAYS = 365

SEARCH_TIMEOUT = 50


def _log(msg: str) -> None:
    log.source_log("arXiv", msg, tty_only=False)


def _is_available() -> bool:
    """True when the arxiv-pp-cli binary is on PATH."""
    return shutil.which(CLI_BIN) is not None


def _today() -> datetime:
    return datetime.now(timezone.utc)


def _build_search_query(topic: str, *, quoted: bool = True) -> str:
    """Build the arXiv search-query string for ``topic``.

    Quoted (default): phrase-scoped exact match across all fields. Precise
    for topics that genuinely appear as a phrase in a title/abstract, but a
    natural-language multi-word topic ("AI video generation advances") almost
    never appears verbatim, so it returns zero results (#908). Unquoted uses
    an AND-conjoined clause for every individual term as a fallback retry.

    Inner double-quotes are stripped (arXiv has no phrase-escaping) either way.
    """
    phrase = _clean_phrase(topic)
    if quoted:
        return f'all:"{phrase}"'
    return " AND ".join(f'all:"{term}"' for term in phrase.split())


def _clean_phrase(topic: str) -> str:
    """Strip quotes and collapse whitespace into a phrase for the query."""
    return " ".join(topic.replace('"', " ").split())


def _build_search_args(topic: str, limit: int, *, quoted: bool = True) -> List[str]:
    return [
        CLI_BIN,
        "query",
        "--search-query",
        _build_search_query(topic, quoted=quoted),
        "--sort-by",
        "relevance",
        "--max-results",
        str(limit),
        "--agent",
    ]


def _run_cli(cmd: List[str], timeout: int) -> Dict[str, Any]:
    """Invoke arxiv-pp-cli and parse the JSON envelope.

    arXiv returns ``{"meta": ..., "results": {"entries": [...]}}``. This
    normalizes to ``{"results": [...entries...]}`` so the parse step sees a
    flat list, matching the other sources' shape. Never raises.
    """
    if not _is_available():
        return {"results": [], "error": f"{CLI_BIN} not on PATH"}
    try:
        result = subproc.run_with_timeout(cmd, timeout=timeout)
    except subproc.SubprocTimeout as exc:
        _log(f"Timeout: {exc}")
        return {"results": [], "error": str(exc)}
    except FileNotFoundError as exc:
        _log(f"Binary missing: {exc}")
        return {"results": [], "error": str(exc)}
    except OSError as exc:
        _log(f"Spawn failed: {exc}")
        return {"results": [], "error": str(exc)}

    if result.returncode != 0:
        snippet = (result.stderr or "").strip().splitlines()[:1]
        first = snippet[0] if snippet else f"exit {result.returncode}"
        _log(f"CLI exit {result.returncode}: {first}")
        return {"results": [], "error": first}

    stdout = result.stdout or ""
    if not stdout.strip():
        _log("CLI returned empty stdout")
        return {"results": [], "error": "empty stdout"}
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        _log(f"JSON decode failed: {exc}")
        return {"results": [], "error": f"json decode: {exc}"}

    if not _is_entry_envelope(data):
        _log("CLI returned an unrecognized JSON response")
        return {"results": [], "error": "unrecognized JSON response"}

    return {"results": _extract_entries(data)}


def _extract_entries(data: Any) -> List[Dict[str, Any]]:
    """Pull the entries list out of arXiv's nested envelope.

    Tolerates ``{"results": {"entries": [...]}}`` (current shape),
    ``{"entries": [...]}``, and a bare list.
    """
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    if isinstance(data, dict):
        results = data.get("results")
        if isinstance(results, dict):
            entries = results.get("entries")
            if isinstance(entries, list):
                return [e for e in entries if isinstance(e, dict)]
        if isinstance(results, list):
            return [e for e in results if isinstance(e, dict)]
        entries = data.get("entries")
        if isinstance(entries, list):
            return [e for e in entries if isinstance(e, dict)]
    return []


def _is_entry_envelope(data: Any) -> bool:
    """Return whether ``data`` has one of the supported entry-list shapes."""
    if isinstance(data, list):
        return True
    if not isinstance(data, dict):
        return False
    results = data.get("results")
    return (
        isinstance(results, list)
        or (isinstance(results, dict) and isinstance(results.get("entries"), list))
        or isinstance(data.get("entries"), list)
    )


def _search_cli(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
) -> Dict[str, Any]:
    """Search arXiv via arxiv-pp-cli using a quoted, relevance-sorted query.

    Returns a dict with a flat ``results`` list of entry dicts. On failure,
    ``results`` is empty and an ``error`` key carries a one-line description.
    """
    if not topic or not topic.strip():
        return {"results": []}
    # A topic of only quote characters cleans to an empty phrase (all:""),
    # which is a topic-blind query; bail rather than search for nothing.
    if not _clean_phrase(topic):
        return {"results": []}
    limit = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    cmd = _build_search_args(topic, limit)
    _log(f"cli query '{topic}' (relevance, max={limit})")
    response = _run_cli(cmd, timeout=SEARCH_TIMEOUT)
    _log(f"cli found {len(response.get('results') or [])} entries")
    # Retry a clean zero-result phrase match with individually quoted AND terms.
    # CLI failures, malformed responses, and missing binaries skip the retry.
    if not response.get("error") and not response.get("results"):
        retry_cmd = _build_search_args(topic, limit, quoted=False)
        _log(f"cli quoted phrase matched nothing; retrying unquoted for '{topic}'")
        response = _run_cli(retry_cmd, timeout=SEARCH_TIMEOUT)
        _log(f"cli unquoted retry found {len(response.get('results') or [])} entries")
    return response


def _openalex_entry(work: Dict[str, Any]) -> Dict[str, Any]:
    """Translate one OpenAlex work to the Atom entry shape the parser expects."""
    inverted = work.get("abstract_inverted_index") or {}
    summary = " ".join(
        word for _, word in sorted(
            (position, word)
            for word, positions in inverted.items()
            for position in positions
        )
    )
    ids = work.get("ids") or {}
    arxiv_id = str(ids.get("arxiv") or "").strip()
    if arxiv_id:
        arxiv_id = re.sub(r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/", "", arxiv_id)
        arxiv_id = re.sub(r"^arxiv:", "", arxiv_id, flags=re.IGNORECASE)
        url = f"https://arxiv.org/abs/{arxiv_id.removesuffix('.pdf')}"
    else:
        doi = str(work.get("doi") or ids.get("doi") or "").strip()
        match = re.fullmatch(
            r"(?:https?://(?:dx\.)?doi\.org/)?10\.48550/arxiv\.(.+)",
            doi, flags=re.IGNORECASE,
        )
        url = (f"https://arxiv.org/abs/{match.group(1)}" if match else
               str((work.get("primary_location") or {}).get("landing_page_url") or ""))
    published = work.get("publication_date")
    timestamp = f"{published}T00:00:00Z" if published else ""
    return {
        "id": url or work.get("id") or "",
        "title": work.get("title") or "",
        "summary": summary,
        "published": timestamp,
        "updated": timestamp,
        "authors": [
            {"name": authorship["author"]["display_name"]}
            for authorship in work.get("authorships") or []
            if (authorship.get("author") or {}).get("display_name")
        ],
        "links": [{"rel": "alternate", "href": url}] if url else [],
    }


def _query_openalex(
    query: str, limit: int, config: Dict[str, Any], from_date: str, to_date: str,
) -> Dict[str, Any]:
    # The downstream normalizer enforces the caller's window too. Keep old
    # high-relevance papers from consuming slots that it would then discard.
    cutoff = max((_today() - timedelta(days=RECENCY_DAYS)).date().isoformat(), from_date)
    params = {
        "filter": (f"primary_location.source.id:{OPENALEX_SOURCE},"
                   f"from_publication_date:{cutoff},to_publication_date:{to_date},"
                   f"title_and_abstract.search:{query}"),
        "sort": "relevance_score:desc",
        "per_page": limit,
        "select": OPENALEX_FIELDS,
    }
    if config.get("OPENALEX_API_KEY"):
        params["api_key"] = config["OPENALEX_API_KEY"]
    user_agent = "last30days/3.23.0"
    mailto = str(config.get("LAST30DAYS_MAILTO") or "").strip()
    if mailto:
        user_agent += f" (+mailto:{mailto})"
    request = urllib.request.Request(
        f"{OPENALEX_URL}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": user_agent, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=OPENALEX_TIMEOUT) as response:
        data = json.load(response)
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ValueError("unrecognized OpenAlex response")
    entries = [_openalex_entry(work) for work in data["results"]]
    _log(f"openalex query '{query}' -> {len(entries)} entries")
    return {"results": entries}


def search_arxiv(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
) -> Dict[str, Any]:
    """Search OpenAlex (default/auto), falling back to the unchanged CLI path."""
    if not topic or not _clean_phrase(topic):
        return {"results": []}
    config = env.get_config()
    if get_backend(config) == "cli":
        return _search_cli(topic, from_date, to_date, depth)
    limit = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    # Commas and pipes delimit OpenAlex filters; they must remain topic text,
    # never broaden or inject a source/date filter. Quotes are cleaned as CLI.
    phrase = _clean_phrase(topic.replace(",", " ").replace("|", " "))
    if not phrase:
        return {"results": []}
    try:
        response = _query_openalex(phrase, limit, config, from_date, to_date)
        if not response["results"] and len(phrase.split()) >= 2:
            # Documented Boolean OR syntax; quote each term as literal text.
            retry = " OR ".join(f'"{term}"' for term in phrase.split())
            response = _query_openalex(retry, limit, config, from_date, to_date)
        return response
    except Exception as exc:
        # HTTPError/URLError, timeout, bad JSON, or mapping failure all preserve
        # the CLI escape hatch. Never print a URL containing the API key.
        reason = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else str(exc)
        key = config.get("OPENALEX_API_KEY")
        if key:
            reason = reason.replace(key, "[redacted]").replace(
                urllib.parse.quote_plus(key), "[redacted]"
            )
        _log(f"openalex failed ({type(exc).__name__}: {reason}); falling back to cli")
        return _search_cli(topic, from_date, to_date, depth)


def _parse_published(published: Optional[str]) -> Optional[datetime]:
    """Parse an arXiv ``published`` timestamp (ISO 8601, e.g.
    '2026-06-25T17:59:48Z') into an aware datetime. Returns None on failure."""
    if not published or not isinstance(published, str):
        return None
    text = published.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _alternate_url(entry: Dict[str, Any]) -> str:
    """Return the human-facing abstract URL (rel=alternate), not the PDF."""
    links = entry.get("links")
    if isinstance(links, list):
        for link in links:
            if isinstance(link, dict) and link.get("rel") == "alternate":
                href = str(link.get("href") or "").strip()
                if href:
                    return href
    # Fall back to the abstract URL derived from the entry id.
    entry_id = str(entry.get("id") or "").strip()
    if entry_id.startswith("http"):
        return entry_id
    return ""


def _author_names(entry: Dict[str, Any]) -> List[str]:
    authors = entry.get("authors")
    out: List[str] = []
    if isinstance(authors, list):
        for a in authors:
            if isinstance(a, dict):
                name = str(a.get("name") or "").strip()
                if name:
                    out.append(name)
    return out


def parse_arxiv_response(
    response: Dict[str, Any],
    query: str = "",
    today: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Parse an arXiv envelope into normalized item dicts.

    Applies the recency cutoff (drops entries older than ``RECENCY_DAYS`` and
    entries with an unparseable date) and computes a token-overlap relevance
    hint. Returns dicts ready for ``normalize._normalize_arxiv``.
    """
    raw = response.get("results") if isinstance(response, dict) else None
    if not isinstance(raw, list):
        return []

    now = today or _today()
    items: List[Dict[str, Any]] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        title = " ".join(str(entry.get("title") or "").split()).strip()
        if not title:
            continue
        published = _parse_published(entry.get("published") or entry.get("updated"))
        if published is None:
            # No usable date -> cannot honor the recency contract; drop.
            continue
        age_days = (now - published).days
        # Allow a one-day grace on the future side: a paper announced later in
        # the same UTC day yields age_days == -1 (timedelta.days floors toward
        # negative); dropping it as "future" would discard the freshest work.
        if age_days > RECENCY_DAYS or age_days < -1:
            continue

        summary = " ".join(str(entry.get("summary") or "").split()).strip()
        authors = _author_names(entry)
        url = _alternate_url(entry)

        rank_decay = max(0.3, 1.0 - (i * 0.03))
        if query:
            content_score = token_overlap_relevance(query, f"{title} {summary}".strip())
        else:
            content_score = 0.5
        relevance = min(1.0, 0.6 * rank_decay + 0.4 * content_score)

        primary_author = authors[0] if authors else ""
        author_label = primary_author
        if len(authors) > 1:
            author_label = f"{primary_author} et al."

        items.append(
            {
                "id": str(entry.get("id") or url or f"AX{i + 1}"),
                "title": title,
                "url": url,
                "summary": summary,
                "author": author_label,
                "authors": authors,
                "date": published.date().isoformat(),
                "engagement": {},
                "relevance": round(relevance, 2),
                "why_relevant": (
                    f"arXiv paper ({primary_author}, {published.date().isoformat()})"
                    if primary_author
                    else f"arXiv paper ({published.date().isoformat()})"
                ),
            }
        )

    return items
