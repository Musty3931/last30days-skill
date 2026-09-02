"""Fix-prescription registry: the single remediation vocabulary (KTD 7).

Each (source, failure mode) entry carries a cause line, a natural-language
fix, an exact CLI fix, and an optional CONFIGURATION.md anchor. Two real
consumers keep the vocabulary honest from day one:

- ``lib/quality_nudge.py`` builds its post-research fix text from these
  entries (only the fix strings migrated here; trigger logic is untouched).
- The doctor aggregator (U4) looks entries up per failed source/backend.

Because both surfaces read the same entry, the nudge a user sees after a
degraded run and the prescription doctor prints for the same failure can
never drift apart.

Composition with the other health layers (reference, don't restate):

- U1 (``lib/health.py``) owns the machine-aware package-manager strings
  (brew/pipx/apt/npx install-vs-reinstall, off-PATH PATH edits). Binary-class
  entries here pull their static defaults from U1's tables, and
  ``for_dependency_probe`` lets a live probe's machine-specific prescription
  win the CLI form while the registry supplies cause/NL/anchor vocabulary.
- U2 (``lib/backends.py``) embeds this registry's CLI forms inside its
  chain-failure prescriptions, so a backend finding and a registry lookup
  agree on the command to run.

No secrets: CLI forms use obvious ``<placeholder>`` values only.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, Optional, Tuple

from . import health

# Direct engine invocation prefix (scripting fallback; the slash-command UX
# is "ask the agent to run setup ...", which is the natural-language form).
ENGINE_CLI = "python3 skills/last30days/scripts/last30days.py"
SETUP_BROWSER_COOKIES_CLI = f"{ENGINE_CLI} setup --allow-browser-cookies"
SETUP_GITHUB_CLI = f"{ENGINE_CLI} setup --github"

# U1 owns these remediation strings; reference them instead of restating.
_DIGG_PP_INSTALL_CLI = health.pp_install_cmd("digg")

GENERIC_FIX_NL = "see CONFIGURATION.md for setup options for this source"


@dataclass(frozen=True)
class Prescription:
    """Remediation for one (source, failure mode).

    ``fix_nl`` is the natural-language form ("ask the agent to run setup
    with browser-cookie consent"); ``fix_cli`` is the exact command.
    ``alt_cli`` carries per-platform alternates (Windows/pip) when the
    primary CLI form is macOS/brew. ``anchor`` is a CONFIGURATION.md
    heading anchor ("" when the doc has no dedicated section).
    """

    source: str
    failure: str
    cause: str
    fix_nl: str
    fix_cli: str
    alt_cli: Tuple[str, ...] = ()
    anchor: str = ""


def _entry(source: str, failure: str, **kwargs) -> Tuple[Tuple[str, str], Prescription]:
    return (source, failure), Prescription(source=source, failure=failure, **kwargs)


REGISTRY: Dict[Tuple[str, str], Prescription] = dict((
    _entry(
        "x", "cookies_missing",
        cause="X browser cookies (AUTH_TOKEN/CT0) are not configured",
        fix_nl=(
            "log into x.com in your browser and re-run (cookies detected "
            "automatically), or add XAI_API_KEY to your .env (get key at "
            "api.x.ai), or add XQUIK_API_KEY to your .env (get key at xquik.com)"
        ),
        fix_cli=SETUP_BROWSER_COOKIES_CLI,
        anchor="api-keys-env",
    ),
    _entry(
        "x", "cookies_expired",
        cause="X errored this run: cookies are configured but likely expired or revoked",
        fix_nl="log into x.com in your browser, then re-run",
        fix_cli=SETUP_BROWSER_COOKIES_CLI,
        anchor="api-keys-env",
    ),
    _entry(
        "x", "grok_cli_missing",
        cause="the Grok CLI is not installed, so the keyless X path is unavailable",
        fix_nl=(
            "install the Grok CLI (curl -fsSL https://x.ai/cli/install.sh | bash) "
            "and sign in with `grok login` to search X without any X credential"
        ),
        fix_cli="npm install -g @xai-official/grok",
        anchor="api-keys-env",
    ),
    _entry(
        "x", "grok_not_authenticated",
        cause="the Grok CLI is installed but not signed in",
        fix_nl="sign in to Grok once; no X account or API key is needed after that",
        fix_cli="grok login",
        anchor="api-keys-env",
    ),
    _entry(
        "scrapecreators", "key_missing",
        cause="SCRAPECREATORS_API_KEY is not set",
        fix_nl=(
            "ask the agent to run setup with the GitHub device flow "
            "(free 10,000-call signup; the key is persisted automatically)"
        ),
        fix_cli=SETUP_GITHUB_CLI,
        anchor="api-keys-env",
    ),
    _entry(
        "digg", "pp_cli_missing",
        cause="digg-pp-cli is not installed",
        fix_nl=(
            "install the Digg CLI through the Printing Press library, then "
            "re-run setup so the source activates"
        ),
        fix_cli=_DIGG_PP_INSTALL_CLI,
        anchor="first-run-onboarding",
    ),
    _entry(
        "digg", "pp_cli_broken",
        cause=(
            "digg-pp-cli resolves on PATH but won't execute (broken or "
            "hanging binary left behind by a bad install)"
        ),
        fix_nl=(
            "reinstall the Digg CLI (re-run the Printing Press install) so "
            "the binary actually executes; it is installed but not serving"
        ),
        fix_cli=_DIGG_PP_INSTALL_CLI,
        anchor="first-run-onboarding",
    ),
    _entry(
        "digg", "pp_cli_off_path",
        cause=(
            "digg-pp-cli is installed but its directory is not on the "
            "agent-subprocess PATH"
        ),
        fix_nl=(
            "add the install directory (default ~/.local/bin) to the PATH the "
            "agent subprocess uses; the engine gate only activates the source "
            "when the binary resolves on PATH"
        ),
        fix_cli='export PATH="$HOME/.local/bin:$PATH"',
        anchor="first-run-onboarding",
    ),
))


def lookup(source: str, failure: str) -> Optional[Prescription]:
    """Return the registered entry for (source, failure), or None."""
    return REGISTRY.get((source, failure))


def get(source: str, failure: str) -> Prescription:
    """Return the registered entry, or the generic CONFIGURATION.md fallback.

    Never raises: an unregistered failure mode still yields an actionable
    (if generic) prescription, so a report renderer cannot crash on a
    failure class the registry has not learned yet.
    """
    entry = lookup(source, failure)
    if entry is not None:
        return entry
    return Prescription(
        source=source,
        failure=failure,
        cause=f"{source}: {failure.replace('_', ' ')}",
        fix_nl=GENERIC_FIX_NL,
        fix_cli=f"{ENGINE_CLI} setup",
    )


# ---------------------------------------------------------------------------
# Composition with U1 dependency probes
# ---------------------------------------------------------------------------

def _dependency_failure(probe: health.DependencyProbe) -> Optional[Tuple[str, str]]:
    """Map a failed dependency probe onto a registered (source, failure)."""
    if probe.name == "digg-pp-cli":
        # health reports off-PATH binaries as MISSING with ``off_path=True``;
        # the distinction only picks cause/NL wording — the probe's own
        # prescription wins the CLI form either way.
        if probe.status == health.MISSING:
            if probe.off_path:
                return ("digg", "pp_cli_off_path")
            return ("digg", "pp_cli_missing")
        return ("digg", "pp_cli_broken")  # BROKEN and TIMEOUT: reinstall class
    return None


def for_dependency_probe(probe: health.DependencyProbe) -> Optional[Prescription]:
    """Prescription for a failed U1 dependency probe (None when OK).

    U1's machine-aware prescription (the manager that owns the binary on
    THIS machine, or a PATH edit for off-PATH installs) wins the CLI form;
    the registry entry supplies the shared cause/NL/anchor vocabulary.
    Unregistered dependencies wrap the probe so callers still get both
    fix forms without this module restating U1's strings.
    """
    if probe.ok:
        return None
    key = _dependency_failure(probe)
    entry = REGISTRY.get(key) if key else None
    if entry is None:
        return Prescription(
            source=probe.name,
            failure=probe.status,
            cause=probe.detail or f"{probe.name}: {probe.status}",
            fix_nl=f"repair the {probe.name} install; {GENERIC_FIX_NL}",
            fix_cli=probe.prescription or f"{ENGINE_CLI} setup",
        )
    updates = {}
    if probe.detail:
        updates["cause"] = probe.detail
    if probe.prescription and probe.prescription != entry.fix_cli:
        updates["fix_cli"] = probe.prescription
    return replace(entry, **updates) if updates else entry
