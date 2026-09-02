"""Post-research quality score and upgrade nudge.

Computes a quality score based on the non-blocking core sources and builds
a nudge message describing what the user missed and how to fix it.

Fix text comes from ``lib.prescriptions`` (the single remediation
vocabulary shared with the doctor command, KTD 7); only the trigger
logic and the message framing live here.
"""

from typing import List

from . import prescriptions


# Sources whose absence can justify a post-run quality repair. X remains a
# supported source, but it is optional: declining cookie access must not turn a
# successful multi-source run into a setup prompt or a lower quality grade.
CORE_SOURCES = ["x", "reddit"]

# Labels for display
SOURCE_LABELS = {
    "x": "X/Twitter",
    "reddit": "Reddit",
}


def _is_x_active(config: dict, research_results: dict) -> bool:
    """Check if X source is active (has credentials AND didn't error)."""
    if "x" in (research_results.get("active_sources") or []):
        return not bool(research_results.get("x_error"))
    has_creds = _has_x_credentials(config)
    if not has_creds:
        return False
    # If X errored this run, it's configured but broken
    if research_results.get("x_error"):
        return False
    return True


def _has_x_credentials(config: dict) -> bool:
    """Return True when any X/Twitter source credential is configured."""
    return bool(
        config.get("AUTH_TOKEN")
        or config.get("XAI_API_KEY")
        or config.get("XQUIK_API_KEY")
    )


def compute_quality_score(config: dict, research_results: dict) -> dict:
    """Compute research quality score from the non-blocking core sources.

    Args:
        config: Configuration dict from env.get_config()
        research_results: Dict with keys like x_error, reddit_error and
            active_sources reflecting what happened this run.

    Returns:
        {
            "score_pct": 0-100,
            "core_active": ["reddit", ...],
            "core_missing": ["x"],
            "core_errored": [],          # configured but errored at top level
            "nudge_text": "..." or None if all sources healthy
        }
    """
    core_active: List[str] = []
    core_missing: List[str] = []
    core_errored: List[str] = []

    # Reddit is always active (free public path)
    core_active.append("reddit")

    # X splits three ways. Active counts normally. Configured-but-errored is a
    # real outage: it still docks the score and surfaces a repair, never an
    # "optional omission". Only unconfigured/declined X leaves the denominator.
    optional_omitted: List[str] = []
    x_configured = _has_x_credentials(config) or (
        "x" in (research_results.get("active_sources") or [])
    )
    if _is_x_active(config, research_results):
        core_active.append("x")
    elif x_configured and research_results.get("x_error"):
        core_missing.append("x")
        core_errored.append("x")
    else:
        optional_omitted.append("x")

    scored_source_count = len(CORE_SOURCES) - len(optional_omitted)
    score_pct = int(len(core_active) / scored_source_count * 100)

    nudge_text = _build_nudge_text(
        core_missing,
        core_errored,
        core_total=scored_source_count,
    ) if core_missing else None

    return {
        "score_pct": score_pct,
        "core_active": core_active,
        "core_missing": core_missing,
        "core_errored": core_errored,
        "nudge_text": nudge_text,
    }


def _build_nudge_text(
    core_missing: List[str],
    core_errored: List[str],
    core_total: int | None = None,
) -> str:
    """Build human-readable nudge text describing what was missed."""
    lines: List[str] = []

    # Describe what was missed
    missed_parts: List[str] = []
    for src in core_missing:
        label = SOURCE_LABELS[src]
        if src in core_errored:
            missed_parts.append(f"{label} (errored this run)")
        else:
            missed_parts.append(label)

    effective_total = core_total if core_total is not None else len(CORE_SOURCES)
    active_count = effective_total - len(core_missing)
    lines.append(f"Research quality: {active_count}/{effective_total} core sources.")
    if missed_parts:
        lines.append(f"Missing: {', '.join(missed_parts)}.")
    lines.append("")

    # Free suggestions
    free_suggestions: List[str] = []

    # A configured X that errored is the only X entry that can reach
    # core_missing: unconfigured/declined X is an optional omission and never
    # lands here. Surface the repair instead of hiding the outage.
    if "x" in core_missing and "x" in core_errored:
        x_fix = prescriptions.get("x", "cookies_expired")
        free_suggestions.append(f"X/Twitter errored - {x_fix.fix_nl}.")

    if free_suggestions:
        lines.append("Free fixes:")
        for s in free_suggestions:
            lines.append(f"  - {s}")
        lines.append("")

    lines.append("last30days has no affiliation with any API provider.")

    return "\n".join(lines)
