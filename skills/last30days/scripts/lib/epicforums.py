"""Anonymous Discourse search for Epic Developer Community forums.

One client owns a research run's budget across concurrent planner subqueries
and topic enrichment. Host locks also enforce one request per second across
clients in the same process. No cookies, credentials, or third-party libraries.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import quote, urlencode, urlsplit

from . import http, log
from .relevance import token_overlap_relevance

BASE_URL = "https://forums.unrealengine.com"
USER_AGENT = "last30days-skill (Epic Forums research; https://github.com/Musty3931/last30days-skill)"
# Hard caps include categories, search pages, retries, and topic bodies.
DEPTH_CONFIG = {
    "quick": {"requests": 5, "search": 2, "results": 8, "enrich": 1},
    "default": {"requests": 22, "search": 18, "results": 16, "enrich": 3},
    "deep": {"requests": 32, "search": 24, "results": 30, "enrich": 5},
}
_RELEVANT = re.compile(
    r"\b(?:unreal(?:\s+engine)?|ue[45](?:\.\d+)?|epic(?:\s+games)?|fortnite|"
    r"uefn|meta\s?human|nanite|lumen|twinmotion|realitycapture|megascan[s]?)\b",
    re.IGNORECASE,
)
_hosts_lock = threading.Lock()
_hosts: dict[str, "_Host"] = {}


def _log(message: str) -> None:
    log.source_log("Epic Forums", message, tty_only=False)


def is_relevant(topic: str) -> bool:
    return bool(_RELEVANT.search(topic or ""))


def enabled(config: dict) -> bool:
    return str(config.get("LAST30DAYS_EPICFORUMS") or "on").lower().strip() not in {
        "off", "0", "false", "no",
    }


def base_url(config: dict) -> str:
    value = str(config.get("LAST30DAYS_EPICFORUMS_BASE") or BASE_URL).rstrip("/")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("LAST30DAYS_EPICFORUMS_BASE must be an HTTP(S) URL without credentials, query, or fragment")
    return value


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "br", "div", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"p", "div", "li"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def strip_html(value) -> str:
    parser = _Text()
    parser.feed(str(value or ""))
    return " ".join("".join(parser.parts).split())


class _Host:
    def __init__(self):
        self.lock = threading.Lock()
        self.next_request = 0.0
        self.categories: dict[str, dict[int, str]] = {}
        self.categories_lock = threading.Lock()


class RequestBudgetExceeded(http.HTTPError):
    pass


class Client:
    def __init__(self, config: dict | None = None, depth: str = "default", timeout: float = 15, deadline: float | None = None):
        self.base = base_url(config or {})
        self.settings = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
        self.timeout = timeout
        self.deadline = deadline
        self.requests = 0
        self.search_requests = 0
        self.lock = threading.RLock()
        self.cache: dict[str, dict] = {}
        with _hosts_lock:
            self.host = _hosts.setdefault(urlsplit(self.base).netloc.lower(), _Host())

    def budget(self) -> dict:
        with self.lock:
            return {"requests": self.requests, "request_limit": self.settings["requests"],
                    "remaining_requests": max(0, self.settings["requests"] - self.requests),
                    "remaining_search_requests": max(0, self.settings["search"] - self.search_requests)}

    def get(self, path: str, *, search: bool = False) -> dict:
        with self.lock:
            if path in self.cache:
                return self.cache[path]
            for attempt in range(3):
                if self.requests >= self.settings["requests"] or (search and self.search_requests >= self.settings["search"]):
                    raise RequestBudgetExceeded("Epic Forums request budget exhausted")
                with self.host.lock:
                    wait = self.host.next_request - time.monotonic()
                    if self.deadline is not None and time.monotonic() + max(0, wait) >= self.deadline:
                        raise http.DeadlineExceeded()
                    if wait > 0:
                        time.sleep(wait)
                    self.requests += 1
                    self.search_requests += int(search)
                    self.host.next_request = time.monotonic() + 1.0
                    try:
                        # This adapter owns retries so every attempt counts.
                        # Do not leak recovered 429s to the pipeline failure sink.
                        with http.capture_failures():
                            data = http.get(
                                self.base + path,
                                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                                timeout=self.timeout, retries=1, retry_dns=False,
                                deadline_monotonic=self.deadline,
                            )
                    except http.HTTPError as exc:
                        if exc.status_code != 429:
                            raise
                        delay = http.retry_delay_from_headers(exc.headers, 2 ** (attempt + 1))
                        # Discourse can also return wait_seconds in the JSON body.
                        try:
                            delay = max(delay, float(json.loads(exc.body or "{}").get("extras", {}).get("wait_seconds", 0)))
                        except (ValueError, TypeError, AttributeError):
                            pass
                        self.host.next_request = time.monotonic() + max(1.0, delay)
                        if attempt == 2 or delay > 30 or self.requests >= self.settings["requests"] or (search and self.search_requests >= self.settings["search"]):
                            raise
                        _log(f"HTTP 429; backing off {max(1.0, delay):g}s")
                        continue
                if not isinstance(data, dict):
                    raise http.HTTPError("Epic Forums schema: expected JSON object")
                self.cache[path] = data
                return data
        raise http.HTTPError("Epic Forums retry budget exhausted")

    def categories(self) -> dict[int, str]:
        # Lock order matches get() (client, then host); the category response is
        # cached across runs, not once per planner subquery.
        with self.lock, self.host.categories_lock:
            if self.base not in self.host.categories:
                data = self.get("/categories.json")
                listing = data.get("category_list", {})
                rows = listing.get("categories", []) + listing.get("subcategories", [])
                self.host.categories[self.base] = {
                    row["id"]: strip_html(row.get("name"))
                    for row in rows if isinstance(row, dict) and isinstance(row.get("id"), int)
                }
            return self.host.categories[self.base]


def search_terms(topic: str) -> str:
    # Date/order/category operators belong to this adapter, not the topic.
    text = re.sub(r"\b[\w-]+:\S+", " ", topic)
    # The forum already scopes generic UE prefixes. Keep specific technologies
    # (Lumen, Nanite, Fortnite...) and avoid AND-requiring a release number.
    text = re.sub(r"\b(?:unreal(?:\s+engine)?|ue)(?:\s*\d+(?:\.\d+)*)?\b", " ", text, flags=re.I)
    text = " ".join(re.findall(r"[\w.-]+", text))
    text = " ".join(word for word in text.split() if word.lower() not in {"a", "an", "the", "of", "for", "with", "and", "in", "on", "how", "to", "best"})
    return text or "Unreal Engine"


def query_variants(topic: str, depth: str) -> list[str]:
    terms = search_terms(topic).split()
    variants = [" ".join(terms[:4])]
    if len(terms) > 2:
        if "tracer" in terms:
            variants.append(" ".join("tracing" if t == "tracer" else t for t in terms[-3:]))
        else:
            variants.append(" ".join(terms[-2:]))
        variants.append(" ".join(terms[:2]))
    return list(dict.fromkeys(variants))[:1 if depth == "quick" else 3]


def search_epicforums(topic: str, from_date: str, to_date: str, depth: str = "default", *, client: Client | None = None, order: str = "latest") -> dict:
    if not topic.strip():
        return {"posts": [], "topics": []}
    client = client or Client(depth=depth)
    # Discourse before: excludes that day; use the next day for an inclusive end.
    end = (date.fromisoformat(to_date) + timedelta(days=1)).isoformat()
    result = {"posts": [], "topics": [], "base_url": client.base, "categories": {}, "from_date": from_date, "to_date": to_date, "result_limit": client.settings["results"], "search_query": topic}
    seen = set()
    try:
        for terms in query_variants(topic, depth):
            query = f"{terms} after:{from_date} before:{end} order:{'latest' if order == 'latest' else 'relevance'}"
            _log(f"query '{query}'")
            data = client.get("/search.json?" + urlencode({"q": query}), search=True)
            if not isinstance(data.get("posts"), list) or not isinstance(data.get("topics"), list):
                raise http.HTTPError("Epic Forums schema: missing posts/topics arrays")
            result["posts"].extend(data["posts"])
            for row in data["topics"]:
                if isinstance(row, dict) and row.get("id") not in seen:
                    result["topics"].append(row)
                    seen.add(row.get("id"))
            result["more_results"] = result.get("more_results", False) or bool((data.get("grouped_search_result") or {}).get("more_full_page_results"))
        # Category names and topic bodies are optional enrichment; leave the
        # shared budget available to every planner search stream first.
    except RequestBudgetExceeded as exc:
        result.setdefault("warnings", []).append(str(exc))
        result["more_results"] = True
    except (http.HTTPError, ValueError) as exc:
        result["error"] = str(exc)
        _log(f"search failed: {exc}")
    result.update(client.budget())
    result["more_results"] = result.get("more_results", False) or len(result["topics"]) > client.settings["results"]
    _log(f"found {len(result['topics'])} topics ({client.requests}/{client.settings['requests']} requests; {result['remaining_requests']} remaining)")
    return result


def _day(value) -> str | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def item_date(item: dict, from_date: str, to_date: str) -> str | None:
    for key in ("date", "matched_at", "last_posted_at", "created_at"):
        value = _day(item.get(key))
        if value and from_date <= value <= to_date:
            return value
    return None


def parse_epicforums_response(response: dict, query: str = "") -> list[dict]:
    posts: dict[int, list[dict]] = {}
    for post in response.get("posts") or []:
        if isinstance(post, dict):
            posts.setdefault(post.get("topic_id"), []).append(post)
    categories = response.get("categories") or {}
    base = response.get("base_url") or BASE_URL
    results, seen = [], set()
    for topic in response.get("topics") or []:
        if not isinstance(topic, dict) or not isinstance(topic.get("id"), int) or topic["id"] in seen:
            continue
        title = strip_html(topic.get("title"))
        if not title:
            continue
        matches = posts.get(topic["id"], [])
        post = next((p for p in matches if item_date(p, response.get("from_date", ""), response.get("to_date", "9999"))), matches[0] if matches else {})
        raw = dict(topic, matched_at=post.get("created_at"))
        published = item_date(raw, response.get("from_date", ""), response.get("to_date", "9999"))
        if not published:
            continue
        seen.add(topic["id"])
        snippet = strip_html(post.get("blurb") or post.get("cooked") or topic.get("excerpt"))
        engagement = {"likes": topic.get("like_count", post.get("like_count", 0)), "replies": max(_count(topic.get("reply_count")), _count(topic.get("posts_count")) - 1), "views": topic.get("views", 0)}
        category = categories.get(topic.get("category_id"), categories.get(str(topic.get("category_id")), "Epic Forums"))
        tags = [t.get("name", "") if isinstance(t, dict) else str(t) for t in topic.get("tags") or []]
        ad = bool(re.search(r"\b(?:marketplace|fab|store|kit)\b", f"{category} {title} {' '.join(tags)}", re.I)) and not re.search(r"\b(?:assets?|marketplace|fab|store|kit)\b", query, re.I)
        results.append(dict(
            raw, title=title, date=published,
            url=f"{base}/t/{quote(str(topic.get('slug') or 'topic'), safe='')}/{topic['id']}",
            author=post.get("username") or "", snippet=snippet,
            category=category, marketplace_ad=ad,
            engagement=engagement, relevance=content_relevance(query, f"{title} {snippet}", base) if query else 0.5,
            search_query=response.get("search_query", query),
        ))
    results.sort(key=lambda item: (item["marketplace_ad"], -item["relevance"]))
    return results[:response.get("result_limit", len(results))]


def content_relevance(query: str, text: str, url: str) -> float:
    """Credit official host context only when the text matches the topic too."""
    def terms(value):
        return re.sub(r"\bpath[ -]?(?:tracer|tracing)\b", "path tracer", value, flags=re.I)

    query, text = terms(query), terms(text)
    score = token_overlap_relevance(query, text)
    if urlsplit(url).hostname == "forums.unrealengine.com" and score >= 0.15:
        score = max(score, token_overlap_relevance(query, text + " Unreal Engine Epic UE4 UE5"))
    return score


def _count(value) -> int:
    try:
        return max(0, int(value or 0))
    except (ValueError, TypeError, OverflowError):
        return 0


def is_epic_staff(post: dict) -> bool:
    return any(post.get(key) == "Epic_Games_Inc" for key in ("primary_group_name", "flair_name")) or str(post.get("user_title") or "").casefold() == "staff"


def enrich_source_items(items, client: Client) -> None:
    # Search evidence decides the slots, after cross-stream dedupe. A newer,
    # weak match must not spend the body budget ahead of an on-topic thread.
    ranked = sorted(items, key=lambda i: (bool(i.metadata.get("marketplace_ad")),
                    -(i.local_relevance or 0), -(i.local_rank_score or 0)))
    unique = {}
    for item in ranked:
        unique.setdefault(item.metadata.get("topic_id"), item)
    selected = list(unique.values())[:client.settings["enrich"]]
    for item in selected:
        if not client.budget()["remaining_requests"]:
            item.metadata["enrichment_skipped"] = "request budget exhausted"
            continue
        try:
            data = client.get(f"/t/{int(item.metadata['topic_id'])}.json")
            for key, field in (("likes", "like_count"), ("views", "views")):
                if field in data:
                    item.engagement[key] = _count(data[field])
            if "posts_count" in data:
                item.engagement["replies"] = max(0, _count(data["posts_count"]) - 1)
            posts = data.get("post_stream", {}).get("posts", [])
            comments = []
            for post in posts:
                text = strip_html(post.get("cooked"))
                if not text:
                    continue
                if post.get("post_number") == 1:
                    item.body = f"{item.title}\n\n{text[:6000]}\n\n{item.snippet}"
                    item.metadata["topic_body"] = text[:6000]
                else:
                    # Old replies remain context, never evidence for this window.
                    day = _day(post.get("created_at"))
                    if day and item.metadata["from_date"] <= day <= item.metadata["to_date"]:
                        staff = is_epic_staff(post)
                        if staff:
                            item.metadata["epic_staff_answered"] = True
                            item.source_quality = 1.0
                        likes = next((a.get("count", 0) for a in post.get("actions_summary", []) if a.get("id") == 2), 0)
                        comments.append({"author": post.get("username", ""), "body": text[:1200], "excerpt": text[:1200], "score": likes, "epic_staff": staff, "url": f"{item.url}/{post.get('post_number', 1)}"})
            item.metadata["top_comments"] = sorted(comments, key=lambda p: (p["epic_staff"], p["score"]), reverse=True)[:3]
        except (http.HTTPError, ValueError, KeyError, TypeError) as exc:
            item.metadata["enrichment_error"] = str(exc)
            _log(f"topic enrichment unavailable: {exc}")
    if items and client.budget()["remaining_requests"]:
        try:
            categories = client.categories()
            for item in items:
                item.container = categories.get(item.metadata.get("category_id"), item.container)
        except (http.HTTPError, ValueError) as exc:
            _log(f"Category names unavailable: {exc}")
    budget = client.budget()
    _log(f"enrichment complete ({budget['requests']}/{budget['request_limit']} requests; {budget['remaining_requests']} remaining)")
