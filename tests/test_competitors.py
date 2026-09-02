"""Tests for scripts/lib/competitors.discover_competitors."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from unittest import mock

from lib import competitors


def _serp(items: list[tuple[str, str]]) -> list[dict]:
    """Build a minimal SERP items list from (title, snippet) pairs."""
    return [
        {"title": title, "snippet": snippet, "url": "https://example.test/"}
        for title, snippet in items
    ]

OPENAI_SERP = _serp(
    [
        ("OpenAI vs Anthropic vs xAI: which is better?", "xAI and Anthropic now compete directly with OpenAI."),
        ("Top OpenAI alternatives in 2026", "Anthropic, Google Gemini, and xAI are the leading alternatives this year."),
        ("xAI and Anthropic challenge OpenAI dominance", "xAI and Anthropic push Google Gemini hard; xAI keeps shipping."),
        ("Anthropic vs xAI: head to head", "Anthropic and xAI trade punches; Google Gemini is not far behind."),
    ]
)

KANYE_SERP = _serp(
    [
        ("Kanye West vs Drake: the feud explained", "Drake responded to Kanye with a diss track."),
        ("Top rappers of the decade: Kendrick Lamar, Drake, J Cole", "Kendrick Lamar released a new album; Drake toured Europe."),
        ("Drake and Kendrick Lamar trade shots", "J Cole stayed out of the Drake vs Kendrick Lamar feud."),
    ]
)


if __name__ == "__main__":
    unittest.main()
