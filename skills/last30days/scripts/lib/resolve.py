"""Topic-targeting helpers: subreddit category peers and GitHub repo canonicalization.

The web-search-driven ``auto_resolve`` step was removed along with the web
source; hosting reasoning models resolve handles/subs themselves (SKILL.md
Steps 0.55/0.75) and pass them as flags. What remains here is deterministic
text logic used by the CLI and the competitor plan path.
"""

from __future__ import annotations

import re
from typing import Optional

from . import categories, log

MAX_SUBS = 10


def _log(msg: str) -> None:
    log.source_log("Resolve", msg, tty_only=False)


def _merge_category_peers(topic: str, subreddits: list[str]) -> tuple[list[str], Optional[str]]:
    """Extend the WebSearch-extracted subreddit list with category peers.

    Classifies the topic, fetches the category's peer subs, dedupes
    case-insensitively against the existing list, and appends missing
    peers in priority order. Caps the final list at MAX_SUBS, preserving
    every WebSearch-returned sub (they are the freshest signal) and
    trimming from the peer-additions end.

    Returns a tuple of (merged_subs, matched_category_id_or_None).
    Emits a [Resolve] Matched category log line only when peers were
    actually added (not when every peer was already in the WebSearch set).

    Classification failures degrade to "no match" — the unwidened list
    is returned and a warning is logged.
    """
    try:
        category = categories.detect_category(topic)
    except Exception as exc:
        _log(f"Category classification failed: {exc}")
        return list(subreddits)[:MAX_SUBS], None

    if category is None:
        return list(subreddits)[:MAX_SUBS], None

    peers = categories.peer_subs_for(category)
    if not peers:
        return list(subreddits)[:MAX_SUBS], category

    existing_lower = {s.lower() for s in subreddits}
    merged = list(subreddits)
    added: list[str] = []
    for peer in peers:
        if len(merged) >= MAX_SUBS:
            break
        if peer.lower() in existing_lower:
            continue
        merged.append(peer)
        existing_lower.add(peer.lower())
        added.append(peer)

    if added:
        _log(f"Matched category={category}, adding peers: {', '.join(added)}")

    return merged, category


# Hosts that can never be a brand's own site; their presence in results is
# platform noise, not an official-domain signal.
_PLATFORM_HOSTS = {
    "reddit.com", "x.com", "twitter.com", "github.com", "youtube.com",
    "facebook.com", "instagram.com", "tiktok.com", "linkedin.com",
    "wikipedia.org", "medium.com", "trustpilot.com", "crunchbase.com",
    "bloomberg.com", "apple.com", "play.google.com", "google.com",
    "threads.com", "pinterest.com", "glassdoor.com", "indeed.com",
    "news.ycombinator.com", "substack.com", "amazon.com", "ebay.com",
}


_INTEGRATION_SUFFIX_KEYWORDS: dict[str, set[str]] = {
    "-action": {"action", "actions", "workflow", "workflows"},
    "-sdk": {"sdk", "client", "library"},
    "-plugin": {"plugin", "plugins", "extension", "extensions"},
    "-plugins": {"plugin", "plugins", "extension", "extensions"},
    "-docs": {"docs", "documentation"},
    "-examples": {"example", "examples", "sample", "samples"},
    "-template": {"template", "templates", "starter", "boilerplate"},
}


def _topic_tokens(topic: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (topic or "").lower()))


def _topic_entity_slugs(topic: str) -> list[str]:
    entities = re.split(r"\b(?:vs|versus)\b", (topic or "").lower())
    slugs: list[str] = []
    for entity in entities:
        tokens = re.findall(r"[a-z0-9]+", entity)
        if tokens:
            slugs.append("-".join(tokens))
    return slugs


def _repo_slug(repo: str) -> str:
    parts = repo.split("/", 1)
    if len(parts) != 2:
        return ""
    return parts[1].lower()


def _canonicalize_integration_repo(topic: str, repo: str) -> str:
    """Map integration repos back to canonical product repos when intent allows.

    Example:
      anthropics/claude-code-action -> anthropics/claude-code
    unless topic explicitly asks for "action"/"workflow".
    """
    parts = repo.split("/", 1)
    if len(parts) != 2:
        return repo
    owner, name = parts[0], parts[1]
    lower_name = name.lower()
    topic_words = _topic_tokens(topic)
    for suffix, intent_words in _INTEGRATION_SUFFIX_KEYWORDS.items():
        if not lower_name.endswith(suffix):
            continue
        if topic_words.intersection(intent_words):
            return repo
        base = name[: -len(suffix)]
        if base:
            return f"{owner}/{base}"
    return repo


def canonicalize_github_repos(topic: str, repos: list[str], *, cap: int | None = 5) -> list[str]:
    """Normalize/priority-sort GitHub repos for the current topic.

    - Rewrites common integration suffixes to canonical product repos when
      topic intent does not mention those integrations.
    - Promotes exact topic slug matches (e.g., `claude-code`) over partials.
    """
    canonicalized: list[str] = []
    seen: set[str] = set()
    for repo in repos:
        candidate = _canonicalize_integration_repo(topic, repo.strip())
        if "/" not in candidate:
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        canonicalized.append(candidate)

    topic_slugs = set(_topic_entity_slugs(topic))
    if topic_slugs:
        exact = [r for r in canonicalized if _repo_slug(r) in topic_slugs]
        prefixed = [r for r in canonicalized if any(_repo_slug(r).startswith(f"{slug}-") for slug in topic_slugs) and r not in exact]
        rest = [r for r in canonicalized if r not in exact and r not in prefixed]
        canonicalized = exact + prefixed + rest

    if cap is not None:
        return canonicalized[:cap]
    return canonicalized


