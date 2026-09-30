"""Keyless YouTube search and captions, ported from upstream v3.25.0.

Only yt-dlp is used: no account, cookies, paid API, or audio downloads.
Flat search is cheap; bounded metadata extraction supplies verified dates.
"""
from __future__ import annotations

import copy
import html
import json
import math
import os
import re
import shlex
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from . import dates, health, http, log, subproc
from .query import infer_query_intent
from .relevance import token_overlap_relevance as _compute_relevance

DEPTH_CONFIG = {"quick": 6, "default": 8, "deep": 16}
TRANSCRIPT_LIMITS = {"quick": 0, "default": 2, "deep": 8}
TRANSCRIPT_MAX_WORDS = 5000
_SEARCH_TIMEOUT = 30
_METADATA_TIMEOUT = 20
_TRANSCRIPT_TIMEOUT = 30
_TRANSCRIPT_MAX_RETRIES = 0
_TRANSCRIPT_BACKOFF_BASE = 2.0
_YTDLP_MAX_CONCURRENT = 2
_ytdlp_slots = threading.Semaphore(_YTDLP_MAX_CONCURRENT)
_search_cache = {}
_search_inflight = {}
_search_cache_lock = threading.Lock()
_metadata_cache = {}
_metadata_lock = threading.Lock()
_transcript_cache = {}
_transcript_lock = threading.Lock()
_TRANSCRIPT_FETCH_STATS = {"attempts": 0, "failures": 0}
_TRANSIENT_RE = re.compile(r"429|too many requests|temporarily|timed out|connection", re.I)
_NO_CAPTION_RE = re.compile(r"no subtitles|requested (format|language)|there'?s no .*subtitles", re.I)


def ytdlp_command(config=None):
    """Resolve an executable or argv override without invoking a shell."""
    raw = os.environ.get("LAST30DAYS_YTDLP", (config or {}).get("LAST30DAYS_YTDLP") or "")
    try:
        argv = shlex.split(raw) if raw else ["yt-dlp"]
    except ValueError:
        return []
    return argv if argv and shutil.which(argv[0]) else []


def is_ytdlp_installed(config=None) -> bool:
    return bool(ytdlp_command(config))


def is_enabled(config=None) -> bool:
    value = os.environ.get("LAST30DAYS_YOUTUBE", (config or {}).get("LAST30DAYS_YOUTUBE") or "on")
    return value.lower().strip() not in {"off", "0", "false", "no"}


def _run_ytdlp(cmd, *, timeout):
    command = ytdlp_command()
    if not command:
        raise FileNotFoundError("yt-dlp not found")
    with _ytdlp_slots:
        return subproc.run_with_timeout(command + cmd[1:], timeout=timeout)


def reset_search_cache():
    """Reset bounded in-run caches before a top-level research pass."""
    with _search_cache_lock:
        _search_cache.clear()
        _search_inflight.clear()
    with _metadata_lock:
        _metadata_cache.clear()
    with _transcript_lock:
        _transcript_cache.clear()
    reset_transcript_fetch_stats()


def _error_result(result):
    return (result.stderr or f"yt-dlp exited {result.returncode}").strip()[-500:]


def _date(value):
    try:
        return datetime.strptime(str(value), "%Y%m%d").date().isoformat()
    except (ValueError, TypeError):
        return None


def _json_lines(stdout):
    videos = []
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict) and re.fullmatch(r"[A-Za-z0-9_-]{11}", str(value.get("id", ""))):
                videos.append(value)
        except (ValueError, TypeError):
            continue
    return videos


def channel_boost(channel):
    """Optional, capped preference; never makes unrelated videos relevant."""
    preferred = {name.strip().casefold() for name in os.environ.get("LAST30DAYS_YOUTUBE_CHANNELS", "").split(",") if name.strip()}
    return 0.03 if str(channel or "").casefold() in preferred else 0.0


def _video_metadata(video):
    video_id = video["id"]
    cache_key = (tuple(ytdlp_command()), video_id)
    with _metadata_lock:
        if cache_key in _metadata_cache:
            return copy.deepcopy(_metadata_cache[cache_key])
    try:
        result = _run_ytdlp([
            "yt-dlp", "--ignore-config", "--no-cookies-from-browser",
            "--skip-download", "--ignore-no-formats-error", "--dump-single-json", "--no-playlist",
            f"https://www.youtube.com/watch?v={video_id}",
        ], timeout=_METADATA_TIMEOUT)
        parsed = _json_lines(result.stdout)
        payload = (parsed[0] if parsed else {}, _error_result(result) if result.returncode else None)
        if not parsed and not payload[1]:
            payload = ({}, "YouTube metadata returned no valid video JSON")
    except (OSError, subproc.SubprocTimeout) as exc:
        payload = ({}, str(exc))
    # Cache failures too: never hammer the same blocked video again this run.
    with _metadata_lock:
        _metadata_cache[cache_key] = copy.deepcopy(payload)
    return payload


def search_youtube(topic, from_date, to_date, depth="default"):
    if not is_enabled():
        return {"items": []}
    if not is_ytdlp_installed():
        return {"items": [], "error": "yt-dlp not installed"}
    count = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    core = _extract_core_subject(topic)
    key = (tuple(ytdlp_command()), core, count, from_date, to_date)
    cached, event, slot, leader = _claim_search_slot(key)
    if cached is not None:
        return cached
    if not leader:
        return _await_search_slot(event, slot)
    payload = {"items": [], "error": "YouTube search failed"}
    try:
        _log(f"Searching '{core}' ({from_date} through {to_date}, metadata cap {count})")
        # ytsearchdate is unsupported in current yt-dlp. Always use the
        # compatible ytsearchN prefix; recency is enforced after metadata.
        result = _run_ytdlp([
            "yt-dlp", "--ignore-config", "--no-cookies-from-browser",
            f"ytsearch{count * 3}:{core}", "--flat-playlist", "--dump-json", "--skip-download",
        ], timeout=_SEARCH_TIMEOUT)
        raw = _json_lines(result.stdout)
        error = _error_result(result) if result.returncode else None
        if result.stdout.strip() and not raw and not error:
            error = "YouTube search returned no valid video JSON"
        unique = {video["id"]: video for video in raw}
        candidates = sorted(unique.values(), key=lambda v: (
            _compute_relevance(core, str(v.get("title") or "")),
            v.get("view_count") or 0,
        ), reverse=True)[:count]
        items = []
        with ThreadPoolExecutor(max_workers=_YTDLP_MAX_CONCURRENT) as pool:
            futures = [pool.submit(_video_metadata, v) for v in candidates]
            for future in futures:
                video, failure = future.result()
                if failure:
                    error = _prefer_search_error(error, failure)
                published = _date(video.get("upload_date"))
                if not published or not from_date <= published <= to_date:
                    continue
                title = str(video.get("title") or "")
                description = str(video.get("description") or "")[:500]
                items.append({
                    "video_id": video["id"], "title": title,
                    "url": f"https://www.youtube.com/watch?v={video['id']}",
                    "channel_name": video.get("channel") or video.get("uploader") or "",
                    "date": published, "description": description,
                    "engagement": {"views": video.get("view_count") or 0,
                                   "likes": video.get("like_count") or 0,
                                   "comments": video.get("comment_count") or 0},
                    "duration": video.get("duration"),
                    "channel_boost": channel_boost(video.get("channel") or video.get("uploader")),
                    "relevance": _compute_relevance(core, f"{title} {description}"),
                    "why_relevant": f"YouTube: {title[:60]}",
                })
        items.sort(key=_transcript_candidate_sort_key, reverse=True)
        _log(f"{len(items)} videos with verified dates in the requested window")
        payload = {"items": items, **({"error": error} if error else {})}
    except Exception as exc:
        payload = {"items": [], "error": str(exc)}
    finally:
        _finish_search_slot(key, payload, event=event, slot=slot)
    return payload


def fetch_transcript(video_id, temp_dir, status=None):
    status = status if status is not None else {}
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        return None
    key = (tuple(ytdlp_command()), video_id, _ytdlp_sub_langs())
    with _transcript_lock:
        if key in _transcript_cache:
            text, saved_status = _transcript_cache[key]
            status.update(saved_status)
            return text
    raw = _fetch_transcript_ytdlp(video_id, temp_dir, status=status)
    text = _clean_vtt(raw) if raw else ""
    text = " ".join(text.split()[:TRANSCRIPT_MAX_WORDS]) or None
    if not raw and not status.get("ytdlp_error"):
        status["no_caption_tracks"] = True
    with _transcript_lock:
        _transcript_cache[key] = (text, dict(status))
    return text


def fetch_transcripts_parallel(video_ids, max_workers=2, out_captions_disabled=None):
    if not video_ids:
        return {}
    results = {}
    statuses = {vid: {} for vid in video_ids}
    with tempfile.TemporaryDirectory() as temp_dir:
        with ThreadPoolExecutor(max_workers=min(max_workers, 2)) as pool:
            futures = {pool.submit(fetch_transcript, vid, temp_dir, statuses[vid]): vid for vid in video_ids}
            for future in as_completed(futures):
                vid = futures[future]
                try:
                    results[vid] = future.result()
                except Exception as exc:
                    _log(f"Transcript fetch failed for {vid}: {exc}")
                    results[vid] = None
    if out_captions_disabled is not None:
        out_captions_disabled.update(vid for vid, status in statuses.items() if status.get("no_caption_tracks"))
    _log(f"Got transcripts for {sum(bool(v) for v in results.values())}/{len(video_ids)} videos")
    return results


def live_probe(config=None, timeout=10):
    """Verify live search extraction, not merely installation/version."""
    if not is_enabled(config):
        return {"ok": False, "detail": "LAST30DAYS_YOUTUBE=off", "probed": False}
    command = ytdlp_command(config)
    if not command:
        return {"ok": False, "detail": "yt-dlp not installed", "probed": False}
    try:
        result = subproc.run_with_timeout(command + [
            "--ignore-config", "--no-cookies-from-browser", "--flat-playlist",
            "--dump-json", "--skip-download", "ytsearch1:Unreal Engine",
        ], timeout=timeout)
        ok = result.returncode == 0 and bool(_json_lines(result.stdout))
        return {"ok": ok, "detail": "live YouTube search returned video metadata" if ok else _error_result(result), "probed": True}
    except (OSError, subproc.SubprocTimeout) as exc:
        return {"ok": False, "detail": str(exc), "probed": True}


def get_transcript_fetch_stats() -> Dict[str, int]:
    """Return cumulative transcript-fetch stats for this process."""
    return dict(_TRANSCRIPT_FETCH_STATS)


def reset_transcript_fetch_stats() -> None:
    """Reset cumulative transcript-fetch stats (used by tests)."""
    _TRANSCRIPT_FETCH_STATS["attempts"] = 0
    _TRANSCRIPT_FETCH_STATS["failures"] = 0


def extract_transcript_highlights(transcript: str, topic: str, limit: int = 5) -> list[str]:
    """Extract quotable highlights from a YouTube transcript.

    Filters filler (subscribe, welcome back, etc.), scores sentences by
    specificity (numbers, proper nouns, topic relevance), and returns
    the top highlights.
    """
    if not transcript:
        return []

    sentences = re.split(r'(?<=[.!?])\s+', transcript)

    # Fallback for punctuation-free transcripts (common with auto-captions):
    # chunk into ~20-word segments so they pass the 8-50 word filter.
    if len(sentences) <= 1 and len(transcript.split()) > 50:
        words = transcript.split()
        sentences = [' '.join(words[i:i+20]) for i in range(0, len(words), 20)]

    filler = [
        r"^(hey |hi |what's up|welcome back|in today's video|don't forget to)",
        r"(subscribe|like and comment|hit the bell|check out the link|down below)",
        r"^(so |and |but |okay |alright |um |uh )",
        r"(thanks for watching|see you (next|in the)|bye)",
    ]

    topic_words = [w.lower() for w in topic.lower().split() if len(w) > 2]

    candidates = []
    for sent in sentences:
        sent = sent.strip()
        words = sent.split()
        if len(words) < 8 or len(words) > 50:
            continue
        if any(re.search(p, sent, re.IGNORECASE) for p in filler):
            continue

        score = 0
        if re.search(r'\d', sent):
            score += 2
        if re.search(r'[A-Z][a-z]+', sent):
            score += 1
        if '?' in sent:
            score += 1
        sent_lower = sent.lower()
        if any(w in sent_lower for w in topic_words):
            score += 2

        candidates.append((score, sent))

    candidates.sort(key=lambda x: -x[0])
    return [sent for _, sent in candidates[:limit]]


def _log(msg: str):
    log.source_log("YouTube", msg, tty_only=False)


def _claim_search_slot(
    cache_key: Tuple[str, int, str],
) -> tuple[Optional[Dict[str, Any]], Optional[threading.Event], Optional[list], bool]:
    """Return ``(cached, event, slot, is_leader)`` for search coalesce.

    - Cache hit: ``(payload, None, None, False)`` — caller returns ``payload``.
    - Waiter: ``(None, event, slot, False)`` — caller awaits ``slot`` via ``event``.
    - Leader: ``(None, event, slot, True)`` — caller runs yt-dlp and finishes the slot.
    """
    with _search_cache_lock:
        cached = _search_cache.get(cache_key)
        if cached is not None:
            return copy.deepcopy(cached), None, None, False
        existing = _search_inflight.get(cache_key)
        if existing is not None:
            return None, existing[0], existing[1], False
        event = threading.Event()
        slot: list = [None]
        _search_inflight[cache_key] = (event, slot)
        return None, event, slot, True


def _finish_search_slot(
    cache_key: Tuple[str, int, str],
    payload: Dict[str, Any],
    *,
    event: threading.Event,
    slot: list,
) -> Dict[str, Any]:
    """Publish a search result to waiters; cache only clean (non-error) payloads.

    Ownership is by slot identity: after ``reset_search_cache()`` clears the
    registry, a stale leader must still wake its own waiters but must not pop
    or overwrite a newer run's registration for the same key.
    """
    shared = copy.deepcopy(payload)
    with _search_cache_lock:
        if slot[0] is not None:
            # Idempotent re-finish of this slot (e.g. finally after return).
            event.set()
            return payload
        slot[0] = shared
        current = _search_inflight.get(cache_key)
        if current is not None and current[1] is slot:
            if not payload.get("error"):
                _search_cache[cache_key] = shared
            _search_inflight.pop(cache_key, None)
        # else: stale leader after a reset — wake local waiters only.
        event.set()
    return payload


def _await_search_slot(
    event: threading.Event,
    slot: list,
) -> Dict[str, Any]:
    """Wait for a leader search to publish.

    Waiters block until the leader finishes (success or failure). The leader
    path always publishes via ``_finish_search_slot``, including on unexpected
    exceptions, so a timed wait would only invent a false timeout while the
    leader was still queued behind other yt-dlp work.
    """
    event.wait()
    shared = slot[0]
    if isinstance(shared, dict):
        return copy.deepcopy(shared)
    return {"items": [], "error": "YouTube search failed"}


def classify_run_failure(detail: str) -> str:
    """Map yt-dlp's text-only throttling and bot-gate errors."""
    text = detail.lower()
    if any(marker in text for marker in ("yt-dlp not installed", "yt-dlp not found")):
        return health.SKIPPED_UNCONFIGURED
    if any(marker in text for marker in ("timed out", "timeout")):
        return health.TIMEOUT
    if any(
        marker in text
        for marker in ("http error 429", "confirm you're not a bot", "confirm you’re not a bot", "bot-gate")
    ):
        return health.RATE_LIMITED
    if any(marker in text for marker in ("sign in", "login required", "cookies are no longer valid")):
        return health.AUTH_FAILED
    return http.classify_failure(message=detail)


def _extract_core_subject(topic: str) -> str:
    """Extract core subject from verbose query for YouTube search.

    NOTE: 'tips', 'tricks', 'tutorial', 'guide', 'review', 'reviews'
    are intentionally KEPT — they're YouTube content types that improve search.
    """
    from .query import VIRAL_NOISE, extract_core_subject
    # YouTube extends VIRAL_NOISE with temporal/meta words the planner emits
    # that don't appear in YouTube titles (months, recent year tokens, etc.).
    _YT_EXTRA = frozenset({
        'last', 'days', 'recent', 'recently', 'month', 'week',
        'january', 'february', 'march', 'april', 'may', 'june',
        'july', 'august', 'september', 'october', 'november', 'december',
        '2025', '2026', '2027',
        'music', 'public', 'appearances', 'developments', 'discussions', 'coverage',
    })
    return extract_core_subject(topic, noise=VIRAL_NOISE | _YT_EXTRA)


def expand_youtube_queries(topic: str, depth: str) -> List[str]:
    """Generate multiple YouTube search queries from a topic.

    Mirrors reddit.py's expand_reddit_queries() pattern:
    1. Extract core subject (strip noise words)
    2. Include original topic if different from core
    3. Add intent-specific OR-joined content-type variants
    4. Cap by depth: 1 for quick, 2 for default, 3 for deep

    Returns 1-3 query strings depending on depth.
    """
    core = _extract_core_subject(topic)
    queries = [core]

    # Include cleaned original topic as variant if different from core
    original_clean = topic.strip().rstrip('?!.')
    if core.lower() != original_clean.lower() and len(original_clean.split()) <= 8:
        queries.append(original_clean)

    qtype = infer_query_intent(topic)

    # Intent-specific YouTube content-type variants
    if qtype == "opinion":
        queries.append(f"{core} review OR reaction OR breakdown")
    elif qtype == "product":
        queries.append(f"{core} review OR comparison OR unboxing")
    elif qtype == "comparison":
        queries.append(f"{core} vs OR compared OR head to head")
    elif qtype == "how_to":
        queries.append(f"{core} tutorial OR guide OR explained")
    else:
        # breaking_news / general — YouTube content types
        queries.append(f"{core} review OR reaction OR breakdown")

    # Deep depth: add full-length content variant
    if depth == "deep":
        queries.append(f"{core} full OR complete OR official")

    # Cap by depth budget
    caps = {"quick": 1, "default": 2, "deep": 3}
    cap = caps.get(depth, 2)
    return queries[:cap]


def _clean_vtt(vtt_text: str) -> str:
    """Convert VTT subtitle format to clean plaintext."""
    # Strip VTT header
    text = re.sub(r'^WEBVTT.*?\n\n', '', vtt_text, flags=re.DOTALL)
    # Strip timestamps
    text = re.sub(r'\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}.*\n', '', text)
    # Strip position/alignment tags
    text = re.sub(r'<[^>]+>', '', text)
    # Strip cue numbers
    text = re.sub(r'^\d+\s*$', '', text, flags=re.MULTILINE)
    # Deduplicate overlapping lines
    lines = text.strip().split('\n')
    seen = set()
    unique = []
    for line in lines:
        stripped = line.strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            unique.append(stripped)
    return html.unescape(re.sub(r'\s+', ' ', ' '.join(unique)).strip())


def _ytdlp_sub_langs() -> str:
    """Caption languages to try, from LAST30DAYS_YT_SUB_LANGS (default en,es,pt)."""
    raw = os.environ.get("LAST30DAYS_YT_SUB_LANGS", "").strip()
    if not raw:
        return "en,es,pt"
    return ",".join(code.strip().lower() for code in raw.split(",") if code.strip()) or "en,es,pt"


def _pick_ytdlp_vtt(video_id: str, temp_dir: str, priority: List[str]) -> Optional[Path]:
    """Return the best on-disk VTT match for video_id, preferring priority order."""
    matches = list(Path(temp_dir).glob(f"{video_id}*.vtt"))
    if not matches:
        return None
    priority_index = {code: i for i, code in enumerate(priority)}

    def rank(p: Path) -> int:
        stem = p.stem
        suffix = stem[len(video_id) + 1:] if stem.startswith(video_id + ".") else ""
        code = suffix.split("-")[0].split(".")[0]
        return priority_index.get(code, len(priority_index))

    return sorted(matches, key=rank)[0]


def _transcript_backoff(video_id: str, attempt: int) -> float:
    """Backoff seconds before a transcript retry.

    Staggered per-video (a sub-second offset derived from the id) so parallel
    workers don't retry in lockstep and re-trip YouTube's limiter.
    """
    offset = (sum(ord(c) for c in video_id) % 1000) / 1000.0  # 0.0–1.0s
    return _TRANSCRIPT_BACKOFF_BASE * (attempt + 1) + offset


def _read_vtt(video_id: str, temp_dir: str) -> Optional[str]:
    """Return the VTT text yt-dlp wrote for ``video_id``, or None if absent."""
    vtt_path = _pick_ytdlp_vtt(video_id, temp_dir, _ytdlp_sub_langs().split(","))
    if vtt_path is None:
        return None

    try:
        return vtt_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _fetch_transcript_ytdlp(
    video_id: str,
    temp_dir: str,
    status: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Read manual captions or auto-subs; one bounded attempt, no audio."""
    cmd = [
        "yt-dlp",
        "--ignore-config",
        "--no-cookies-from-browser",
        "--write-subs",
        "--write-auto-subs",
        "--sub-lang", _ytdlp_sub_langs(),
        "--sub-format", "vtt",
        "--skip-download",
        "--no-warnings",
        "-o", f"{temp_dir}/%(id)s",
        f"https://www.youtube.com/watch?v={video_id}",
    ]

    timeout = _TRANSCRIPT_TIMEOUT
    attempts = _TRANSCRIPT_MAX_RETRIES + 1
    last_reason: Optional[str] = None
    for attempt in range(attempts):
        try:
            result = _run_ytdlp(cmd, timeout=timeout)
        except subproc.SubprocTimeout:
            last_reason = f"timed out after {timeout}s"
            _log(f"yt-dlp transcript timed out after {timeout}s for {video_id} "
                 f"(attempt {attempt + 1}/{attempts})")
            # yt-dlp downloads requested languages sequentially. A timeout can
            # therefore leave a complete first-choice VTT on disk; keep it
            # instead of discarding an already downloaded caption track.
            partial_vtt = _read_vtt(video_id, temp_dir)
            if partial_vtt is not None:
                return partial_vtt
            if attempt < attempts - 1:
                time.sleep(_transcript_backoff(video_id, attempt))
                continue
            break
        except FileNotFoundError:
            # yt-dlp binary missing — not transient, not retryable.
            if status is not None:
                status["ytdlp_error"] = "yt-dlp not found"
            return None

        if result.returncode == 0:
            vtt = _read_vtt(video_id, temp_dir)
            if vtt is not None:
                return vtt
            # Exit 0 with no file == the uploader has no matching captions.
            # Genuine no-captions: return quietly.
            return None

        # Non-zero exit, but yt-dlp may have written a usable VTT before the
        # failing language errored. With the default `--sub-lang en,es,pt`, an
        # English video fetches `en` fine, then `es`/`pt` hit a 429 and yt-dlp
        # exits non-zero — yet the `en` track is already on disk. A partial
        # success is still a real transcript, so salvage any VTT before
        # classifying this as an error (and, worse, retrying straight back into
        # the same rate limit). This is the root cause of the 0/N transcript
        # runs reported when every video had captions.
        partial_vtt = _read_vtt(video_id, temp_dir)
        if partial_vtt is not None:
            return partial_vtt

        # Non-zero exit == a real error worth classifying & surfacing.
        stderr = (result.stderr or "").strip()
        snippet = (stderr.splitlines()[-1][:200] if stderr
                   else f"exit {result.returncode}")
        if _NO_CAPTION_RE.search(stderr):
            # yt-dlp can exit non-zero when the requested language is absent.
            # Treat as genuine no-captions, not an error worth retrying.
            return None
        last_reason = snippet
        if _TRANSIENT_RE.search(stderr) and attempt < attempts - 1:
            _log(f"yt-dlp transcript transient failure for {video_id} "
                 f"(attempt {attempt + 1}/{attempts}): {snippet}")
            time.sleep(_transcript_backoff(video_id, attempt))
            continue
        # Non-transient, or retries exhausted — surface the real reason.
        _log(f"yt-dlp transcript failed for {video_id} "
             f"(exit {result.returncode}): {snippet}")
        break

    if status is not None and last_reason is not None:
        status["ytdlp_error"] = last_reason
    return None


def _transcript_candidate_sort_key(item: dict) -> tuple:
    """Sort key for transcript candidate selection.

    Combines views with recency so that recent videos (which survive
    strict_recent freshness pruning) are prioritised over old high-view
    videos whose transcripts would be discarded downstream.
    """
    views = item.get("engagement", {}).get("views", 0) or 0
    recency = dates.recency_score(item.get("date", ""))
    return (views, recency)


def _prefer_search_error(current: Optional[str], new: str) -> str:
    """Keep the most actionable search failure across multi-query merges."""
    if current is None:
        return new
    priority = ("timed out", "timeout", "429", "bot")

    def _rank(text: str) -> int:
        lower = text.lower()
        for index, marker in enumerate(priority):
            if marker in lower:
                return index
        return len(priority)

    return new if _rank(new) < _rank(current) else current


def _rank_transcript_candidates(items, topic, from_date, to_date):
    """Spend captions on the same relevance/recency/engagement ranking as the engine."""
    from . import normalize, signals
    normalized = normalize.normalize_source_items("youtube", items, from_date, to_date)
    ranked = signals.annotate_stream(normalized, topic, "balanced_recent", reference_date=to_date)
    raw_by_id = {item["video_id"]: item for item in items}
    return [raw_by_id[item.item_id] for item in ranked]


def search_and_transcribe(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
) -> Dict[str, Any]:
    """Full YouTube search: find videos, then fetch transcripts for top results.

    Uses expand_youtube_queries() to generate multiple search queries,
    runs yt-dlp for each, and merges/deduplicates results by video ID.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: 'quick', 'default', or 'deep'

    Returns:
        Dict with 'items' list. Each item has a 'transcript_snippet' field.
    """
    # Step 1: Multi-query search — run yt-dlp for each expanded query
    queries = expand_youtube_queries(topic, depth)
    seen_ids: Set[str] = set()
    items: List[Dict[str, Any]] = []
    search_error: Optional[str] = None
    for q in queries:
        search_result = search_youtube(q, from_date, to_date, depth)
        err = search_result.get("error")
        if err:
            search_error = _prefer_search_error(search_error, str(err))
        for item in search_result.get("items", []):
            vid = item.get("video_id", "")
            if vid and vid not in seen_ids:
                seen_ids.add(vid)
                items.append(item)

    items = [item for item in items if item.get("date") and from_date <= item["date"] <= to_date]
    items = _rank_transcript_candidates(items, topic, from_date, to_date)

    if not items:
        return {"items": [], **({"error": search_error} if search_error else {})}

    # Step 2: Fetch transcripts for top videos.
    # Sort candidates by a combination of views and recency so that recent
    # videos (which survive strict_recent pruning) are not starved of
    # transcript budget by older high-view-count outliers.
    # Limit attempts as well as successes so unavailable captions stay cheap.
    transcript_limit = TRANSCRIPT_LIMITS.get(depth, TRANSCRIPT_LIMITS["default"])
    transcripts: Dict[str, Optional[str]] = {}
    captions_disabled_ids: Set[str] = set()
    if transcript_limit > 0:
        attempt_count = min(len(items), transcript_limit)
        transcript_candidates = items
        candidate_ids = [item["video_id"] for item in transcript_candidates[:attempt_count]]
        _log(f"Fetching transcripts for up to {attempt_count} videos (target: {transcript_limit}): {candidate_ids}")
        transcripts = fetch_transcripts_parallel(
            candidate_ids, out_captions_disabled=captions_disabled_ids,
        )
        # Record fetch outcomes (captions-disabled videos can never succeed,
        # so they don't count as failures) for the stale-yt-dlp nudge.
        _TRANSCRIPT_FETCH_STATS["attempts"] += len(candidate_ids)
        _TRANSCRIPT_FETCH_STATS["failures"] += sum(
            1 for vid in candidate_ids
            if not transcripts.get(vid) and vid not in captions_disabled_ids
        )
    else:
        _log(f"Transcript limit is 0 for depth={depth}, skipping transcript fetch")

    # Step 3: Attach transcripts and extract highlights. Mark captions_disabled
    # so quality_nudge can subtract those videos from the degraded-ratio
    # denominator (uploader-disabled captions can never produce a transcript;
    # counting them was producing false-positive stale-yt-dlp nudges).
    core_topic = _extract_core_subject(topic)
    for item in items:
        vid = item["video_id"]
        transcript = transcripts.get(vid)
        item["transcript_snippet"] = transcript or ""
        item["transcript_highlights"] = extract_transcript_highlights(
            transcript or "", core_topic,
        )
        item["captions_disabled"] = vid in captions_disabled_ids

    result: Dict[str, Any] = {"items": items}
    if search_error:
        # Partial coverage: some queries succeeded; keep the failure visible so
        # source_status becomes partial/timeout rather than a quiet OK.
        result["error"] = search_error
    return result


def parse_youtube_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse YouTube search response to normalized format.

    Returns:
        List of item dicts ready for normalization.
    """
    return response.get("items", [])
