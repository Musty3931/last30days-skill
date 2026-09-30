"""Normalization of source-specific payloads into the v3 generic item model."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from . import dates, epicforums, schema


def filter_by_date_range(
    items: list[schema.SourceItem],
    from_date: str,
    to_date: str,
    require_date: bool = False,
) -> list[schema.SourceItem]:
    """Keep only items within the requested window."""
    filtered: list[schema.SourceItem] = []
    for item in items:
        if not item.published_at:
            if not require_date:
                filtered.append(item)
            continue
        if item.published_at < from_date or item.published_at > to_date:
            continue
        filtered.append(item)
    return filtered


def normalize_source_items(
    source: str,
    items: list[dict[str, Any]],
    from_date: str,
    to_date: str,
    freshness_mode: str = "balanced_recent",
) -> list[schema.SourceItem]:
    """Normalize raw source items, filter by date range, with evergreen fallback for how_to queries."""
    source = source.lower()
    normalizers = {
        "reddit": _normalize_reddit,
        "x": _normalize_x,
        "xquik": _normalize_x,
        "digg": _normalize_digg,
        "arxiv": _normalize_arxiv,
        "epicforums": _normalize_epicforums,
        "github": _normalize_github,
        "youtube": _normalize_youtube,
    }
    normalizer = normalizers.get(source)
    if normalizer is None:
        raise ValueError(f"Unsupported source: {source}")
    normalized = [
        normalizer(source, item, index, from_date, to_date)
        for index, item in enumerate(items)
    ]
    if source == "jobs":
        # A careers board is a snapshot of CURRENTLY OPEN roles. An open posting
        # is current evidence regardless of when it was posted, so date-windowing
        # it drops still-open roles (the "Founding Research Scientist, Human
        # Simulation" miss: 26 open roles filtered to 3 by a 30-day window).
        # Keep the full board; recency is annotated, not used to drop.
        return normalized
    return filter_by_date_range(normalized, from_date, to_date, require_date=source in {"epicforums", "youtube"})


def _remap_comments(
    raw: list[Any],
    score_keys: tuple[str, ...],
    excerpt_keys: tuple[str, ...],
    *,
    preserve_absent_score: bool = False,
) -> list[dict[str, Any]]:
    """Normalize comments from any source into the shared Reddit-compatible shape.

    Downstream code (signals._top_comment_score, render._top_comments_list,
    entity_extract, rerank) all expect `score` and `excerpt`. This helper maps
    per-source field names (YT: likes/text, TikTok: digg_count/text) onto that
    shape while preserving author/date/url passthrough.

    Sources that distinguish an absent vote from a measured zero can opt into
    preserving the absent value as ``None``.
    """
    out: list[dict[str, Any]] = []
    for raw_c in raw:
        if not isinstance(raw_c, dict):
            continue
        score = _first_present(
            raw_c,
            score_keys,
            default=None if preserve_absent_score else 0,
        )
        excerpt = _first_present(raw_c, excerpt_keys, default="")
        if score is None and preserve_absent_score:
            normalized_score = None
        else:
            try:
                normalized_score = int(score or 0)
            except (TypeError, ValueError):
                normalized_score = 0
        entry: dict[str, Any] = {
            "score": normalized_score,
            "excerpt": str(excerpt or "")[:400],
            "author": str(raw_c.get("author") or ""),
            "date": str(raw_c.get("date") or ""),
        }
        if raw_c.get("url"):
            entry["url"] = str(raw_c["url"])
        out.append(entry)
    return out


def _first_present(d: dict[str, Any], keys: tuple[str, ...], default: Any) -> Any:
    for key in keys:
        if key in d and d[key] not in (None, ""):
            return d[key]
    return default


def _join_comment_excerpts(
    top_comments: list[Any],
    key: str,
    limit: int = 3,
) -> str:
    """Space-join the `key` field from the first `limit` dict-shaped comments."""
    return " ".join(
        str(comment.get(key) or "").strip()
        for comment in top_comments[:limit]
        if isinstance(comment, dict)
    )


def _domain_from_url(url: str) -> str | None:
    if not url:
        return None
    domain = urlparse(url).netloc.strip().lower()
    return domain or None


def _date_confidence(
    item: dict[str, Any], from_date: str, to_date: str, default: str = "low"
) -> str:
    if item.get("date_confidence"):
        return str(item["date_confidence"])
    date_value = item.get("date")
    if not date_value:
        return default
    return dates.get_date_confidence(str(date_value), from_date, to_date)


def _source_item(
    *,
    item_id: str,
    source: str,
    title: str,
    body: str,
    url: str,
    published_at: str | None,
    date_confidence: str,
    relevance_hint: float,
    why_relevant: str,
    author: str | None = None,
    container: str | None = None,
    engagement: dict[str, float | int] | None = None,
    snippet: str = "",
    metadata: dict[str, Any] | None = None,
) -> schema.SourceItem:
    return schema.SourceItem(
        item_id=item_id,
        source=source,
        title=title.strip() or body.strip()[:160] or item_id,
        body=body.strip(),
        url=url.strip(),
        author=(author or "").strip() or None,
        container=(container or "").strip() or None,
        published_at=published_at,
        date_confidence=date_confidence,
        engagement=engagement or {},
        relevance_hint=max(0.0, min(1.0, float(relevance_hint or 0.0))),
        why_relevant=why_relevant.strip(),
        snippet=snippet.strip(),
        metadata=metadata or {},
    )


def _normalize_reddit(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    top_comments = item.get("top_comments") or []
    comment_text = _join_comment_excerpts(top_comments, "excerpt")
    body = "\n".join(
        part
        for part in [
            str(item.get("title") or "").strip(),
            str(item.get("selftext") or "").strip(),
            comment_text,
        ]
        if part
    )
    return _source_item(
        item_id=str(item.get("id") or f"R{index + 1}"),
        source=source,
        title=str(item.get("title") or ""),
        body=body,
        url=str(item.get("url") or ""),
        author=None,
        container=str(item.get("subreddit") or ""),
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=comment_text or str(item.get("selftext") or "")[:400],
        metadata={
            "top_comments": top_comments,
            "comment_insights": item.get("comment_insights") or [],
        },
    )


def _normalize_x(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    text = str(item.get("text") or "").strip()
    mentioned = item.get("mentioned_handles") or []
    return _source_item(
        item_id=str(item.get("id") or f"X{index + 1}"),
        source=source,
        title=text[:140] or f"X post {index + 1}",
        body=text,
        url=str(item.get("url") or ""),
        author=str(item.get("author_handle") or "").lstrip("@"),
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        metadata={"mentioned_handles": list(mentioned)} if mentioned else {},
    )


def _normalize_digg(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    """Normalizer for Digg AI 1000 clusters.

    Each cluster is one item. The TLDR carries the most useful body for
    rerank and synthesis. Top-ranked X posts attached at search time are
    passed through under metadata['posts'] so render can emit them as
    inline 'via Digg' quotes.
    """
    title = str(item.get("title") or "").strip()
    tldr = str(item.get("tldr") or "").strip()
    body = "\n\n".join(part for part in [title, tldr] if part)
    posts = item.get("posts") or []
    if not isinstance(posts, list):
        posts = []
    cluster_url_id = str(item.get("id") or f"DG{index + 1}")
    return _source_item(
        item_id=cluster_url_id,
        source=source,
        title=title or f"Digg cluster {index + 1}",
        body=body,
        url=str(item.get("url") or f"https://di.gg/ai/{cluster_url_id}"),
        author="",
        container="Digg",
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date, default="high"),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=tldr[:400],
        metadata={
            "clusterUrlId": cluster_url_id,
            "tldr": tldr,
            "rank": (item.get("engagement") or {}).get("rank"),
            "uniqueAuthors": (item.get("engagement") or {}).get("uniqueAuthors"),
            "postCount": (item.get("engagement") or {}).get("postCount"),
            "firstPostAge": item.get("first_post_age"),
            "posts": posts,
        },
    )


def _normalize_epicforums(source, item, index, from_date, to_date) -> schema.SourceItem:
    title = epicforums.strip_html(item.get("title"))
    text = epicforums.strip_html(item.get("snippet") or item.get("blurb") or item.get("cooked"))
    return _source_item(
        item_id=str(item.get("id") or f"EF{index + 1}"), source=source,
        title=title, body=f"{title}\n\n{text}", url=str(item.get("url") or ""),
        author=item.get("author") or None, container=item.get("category") or "Epic Forums",
        published_at=epicforums.item_date(item, from_date, to_date), date_confidence="high",
        engagement=item.get("engagement") or {}, relevance_hint=item.get("relevance", 0.5),
        why_relevant="Epic Developer Community discussion", snippet=text[:600],
        metadata={"topic_id": item.get("id"), "category_id": item.get("category_id"),
                  "marketplace_ad": bool(item.get("marketplace_ad")),
                  "tags": item.get("tags") or [], "created_at": item.get("created_at"),
                  "last_posted_at": item.get("last_posted_at"), "matched_at": item.get("matched_at"),
                  "from_date": from_date, "to_date": to_date},
    )


def _normalize_arxiv(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    """Normalizer for arXiv papers.

    The abstract (summary) is the body that feeds rerank and synthesis. arXiv
    has no engagement signal, so engagement is empty and ranking leans on
    relevance and recency.
    """
    title = str(item.get("title") or "").strip()
    summary = str(item.get("summary") or "").strip()
    body = "\n\n".join(part for part in [title, summary] if part)
    authors = item.get("authors") or []
    if not isinstance(authors, list):
        authors = []
    paper_id = str(item.get("id") or f"AX{index + 1}")
    return _source_item(
        item_id=paper_id,
        source=source,
        title=title or f"arXiv paper {index + 1}",
        body=body,
        url=str(item.get("url") or ""),
        author=str(item.get("author") or "") or None,
        container="arXiv",
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date, default="high"),
        engagement={},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=summary[:400],
        metadata={
            "authors": authors,
            "summary": summary,
        },
    )


def _normalize_github(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    title = str(item.get("title") or "").strip()
    snippet_text = str(item.get("snippet") or "").strip()
    top_comments = item.get("metadata", {}).get("top_comments") or []
    comment_text = _join_comment_excerpts(top_comments, "excerpt")
    body = "\n".join(part for part in [title, snippet_text, comment_text] if part)
    metadata = item.get("metadata") or {}
    return _source_item(
        item_id=str(item.get("id") or f"GH{index + 1}"),
        source=source,
        title=title or f"GitHub item {index + 1}",
        body=body,
        url=str(item.get("url") or ""),
        author=str(item.get("author") or ""),
        container=str(item.get("container") or ""),
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date, default="high"),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=comment_text or snippet_text[:400],
        metadata={
            "top_comments": top_comments,
            "labels": metadata.get("labels") or [],
            "state": metadata.get("state", ""),
            "is_pr": metadata.get("is_pr", False),
        },
    )




def _normalize_youtube(
    source: str,
    item: dict[str, Any],
    index: int,
    from_date: str,
    to_date: str,
) -> schema.SourceItem:
    transcript = str(item.get("transcript_snippet") or "").strip()
    description = str(item.get("description") or "").strip()
    title = str(item.get("title") or "").strip()
    highlights = item.get("transcript_highlights") or []
    metadata: dict[str, Any] = {"transcript_snippet": transcript, "channel_boost": item.get("channel_boost", 0)}
    if highlights:
        metadata["transcript_highlights"] = highlights
    if item.get("captions_disabled"):
        # Surfaced for quality_nudge: uploader disabled captions, so this
        # video should be subtracted from the degraded-transcript-ratio
        # denominator (it was never going to produce a transcript).
        metadata["captions_disabled"] = True
    metadata["top_comments"] = _remap_comments(
        item.get("top_comments") or [],
        score_keys=("score", "likes"),
        excerpt_keys=("excerpt", "text"),
    )
    return _source_item(
        item_id=str(item.get("video_id") or item.get("id") or f"YT{index + 1}"),
        source=source,
        title=title,
        body="\n".join(part for part in [title, description, transcript] if part),
        url=str(item.get("url") or ""),
        author=str(item.get("channel_name") or ""),
        published_at=item.get("date"),
        date_confidence=_date_confidence(item, from_date, to_date, default="high"),
        engagement=item.get("engagement") or {},
        relevance_hint=item.get("relevance", 0.5),
        why_relevant=str(item.get("why_relevant") or ""),
        snippet=transcript,
        metadata=metadata,
    )
