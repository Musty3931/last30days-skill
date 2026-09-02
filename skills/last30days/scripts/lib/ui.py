"""Terminal UI utilities for last30days skill."""

import sys
import time
import threading
import random
from typing import Optional

from .render import _skill_version

# Check if we're in a real terminal (not captured by Claude Code)
IS_TTY = sys.stderr.isatty()

# ANSI color codes
class Colors:
    PURPLE = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RESET = '\033[0m'


BANNER = f"""{Colors.PURPLE}{Colors.BOLD}
  ██╗      █████╗ ███████╗████████╗██████╗  ██████╗ ██████╗  █████╗ ██╗   ██╗███████╗
  ██║     ██╔══██╗██╔════╝╚══██╔══╝╚════██╗██╔═████╗██╔══██╗██╔══██╗╚██╗ ██╔╝██╔════╝
  ██║     ███████║███████╗   ██║    █████╔╝██║██╔██║██║  ██║███████║ ╚████╔╝ ███████╗
  ██║     ██╔══██║╚════██║   ██║    ╚═══██╗████╔╝██║██║  ██║██╔══██║  ╚██╔╝  ╚════██║
  ███████╗██║  ██║███████║   ██║   ██████╔╝╚██████╔╝██████╔╝██║  ██║   ██║   ███████║
  ╚══════╝╚═╝  ╚═╝╚══════╝   ╚═╝   ╚═════╝  ╚═════╝ ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚══════╝
{Colors.RESET}{Colors.DIM}  30 days of research. 30 seconds of work.{Colors.RESET}
"""

MINI_BANNER = f"""{Colors.PURPLE}{Colors.BOLD}/last30days{Colors.RESET} {Colors.DIM}· researching...{Colors.RESET}"""

# Fun status messages for each phase
REDDIT_MESSAGES = [
    "Diving into Reddit threads...",
    "Scanning subreddits for gold...",
    "Reading what Redditors are saying...",
    "Exploring the front page of the internet...",
    "Finding the good discussions...",
    "Upvoting mentally...",
    "Scrolling through comments...",
]

X_MESSAGES = [
    "Checking what X is buzzing about...",
    "Reading the timeline...",
    "Finding the hot takes...",
    "Scanning tweets and threads...",
    "Discovering trending insights...",
    "Following the conversation...",
    "Reading between the posts...",
]

ENRICHING_MESSAGES = [
    "Getting the juicy details...",
    "Fetching engagement metrics...",
    "Reading top comments...",
    "Extracting insights...",
    "Analyzing discussions...",
]

PROCESSING_MESSAGES = [
    "Crunching the data...",
    "Scoring and ranking...",
    "Finding patterns...",
    "Removing duplicates...",
    "Organizing findings...",
]

SOURCE_COMPLETION_ORDER = [
    "reddit",
    "x",
    "github",
    "digg",
    "arxiv",
]

SOURCE_COMPLETION_META = {
    "reddit": ("Reddit", "thread", "threads", Colors.YELLOW),
    "x": ("X", "post", "posts", Colors.CYAN),
    "github": ("GitHub", "repo", "repos", Colors.PURPLE),
    "digg": ("Digg", "cluster", "clusters", Colors.YELLOW),
    "arxiv": ("arXiv", "paper", "papers", Colors.RED),
}


def _completion_sources(source_counts: dict[str, int], display_sources: list[str] | None) -> list[str]:
    requested = list(dict.fromkeys(display_sources or []))
    if not requested:
        requested = [source for source, count in source_counts.items() if count]
    if not requested and source_counts:
        requested = list(source_counts)

    candidate_set = set(requested) | set(source_counts)
    ordered = [source for source in SOURCE_COMPLETION_ORDER if source in candidate_set]
    for source in requested + list(source_counts):
        if source in candidate_set and source not in ordered:
            ordered.append(source)
    return ordered


def _format_completion_part(source: str, count: int, tty: bool) -> str:
    label, singular, plural, color = SOURCE_COMPLETION_META.get(
        source,
        (source.replace("_", " ").title(), "result", "results", Colors.RESET),
    )
    unit = singular if count == 1 else plural
    if tty:
        return f"{color}{label}:{Colors.RESET} {count} {unit}"
    return f"{label}: {count} {unit}"

# Promo hints for a single missing free upgrade
PROMO_SINGLE_KEY = {
    "reddit": "\n💡 Unlock Reddit comment enrichment with SCRAPECREATORS_API_KEY - 10,000 free calls, no CC - scrapecreators.com\n",
    "x": "\n💡 Unlock X: log into x.com in your browser, then re-run. "
         "Firefox works on all platforms. Safari works on macOS (detected automatically). "
         "Chrome, Brave, Edge, Arc, Vivaldi, Opera, or Chromium on macOS require "
         "FROM_BROWSER=auto in .env (Keychain dialog). On Windows only Firefox is supported. "
         "Or add AUTH_TOKEN/CT0 or XAI_API_KEY.\n",
}

# Bird auth help (for local users with vendored Bird CLI)
BIRD_AUTH_HELP = f"""
{Colors.YELLOW}Bird authentication failed.{Colors.RESET}

To fix this:
1. Add AUTH_TOKEN and CT0 to ~/.config/last30days/.env, or to trusted .claude/last30days.env with LAST30DAYS_TRUST_PROJECT_CONFIG=1
2. Or set XAI_API_KEY for the xAI fallback backend
"""

BIRD_AUTH_HELP_PLAIN = """
Bird authentication failed.

To fix this:
1. Add AUTH_TOKEN and CT0 to ~/.config/last30days/.env, or to trusted .claude/last30days.env with LAST30DAYS_TRUST_PROJECT_CONFIG=1
2. Or set XAI_API_KEY for the xAI fallback backend
"""

# Spinner frames
SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
DOTS_FRAMES = ['   ', '.  ', '.. ', '...']


class Spinner:
    """Animated spinner for long-running operations."""

    def __init__(self, message: str = "Working", color: str = Colors.CYAN, quiet: bool = False):
        self.message = message
        self.color = color
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.frame_idx = 0
        self.shown_static = False
        self.quiet = quiet  # Suppress non-TTY start message (still shows ✓ completion)

    def _spin(self):
        while self.running:
            frame = SPINNER_FRAMES[self.frame_idx % len(SPINNER_FRAMES)]
            sys.stderr.write(f"\r{self.color}{frame}{Colors.RESET} {self.message}  ")
            sys.stderr.flush()
            self.frame_idx += 1
            time.sleep(0.08)

    def start(self):
        self.running = True
        if IS_TTY:
            # Real terminal - animate
            self.thread = threading.Thread(target=self._spin, daemon=True)
            self.thread.start()
        else:
            # Not a TTY (Claude Code) - just print once
            if not self.shown_static and not self.quiet:
                sys.stderr.write(f"⏳ {self.message}\n")
                sys.stderr.flush()
                self.shown_static = True

    def update(self, message: str):
        self.message = message
        if not IS_TTY and not self.shown_static:
            # Print update in non-TTY mode
            sys.stderr.write(f"⏳ {message}\n")
            sys.stderr.flush()

    def stop(self, final_message: str = ""):
        self.running = False
        if self.thread:
            self.thread.join(timeout=0.2)
        if IS_TTY:
            # Clear the line in real terminal
            sys.stderr.write("\r" + " " * 80 + "\r")
        if final_message:
            sys.stderr.write(f"✓ {final_message}\n")
        sys.stderr.flush()


class ProgressDisplay:
    """Progress display for research phases."""

    def __init__(self, topic: str, show_banner: bool = True):
        self.topic = topic
        self.spinner: Optional[Spinner] = None
        self.start_time = time.time()

        if show_banner:
            self._show_banner()

    def _show_banner(self):
        if IS_TTY:
            sys.stderr.write(MINI_BANNER + "\n")
            sys.stderr.write(f"{Colors.DIM}Topic: {Colors.RESET}{Colors.BOLD}{self.topic}{Colors.RESET}\n\n")
        else:
            # Simple text for non-TTY
            sys.stderr.write(f"/last30days · researching: {self.topic}\n")
        sys.stderr.flush()

    def start_reddit(self):
        msg = random.choice(REDDIT_MESSAGES)
        self.spinner = Spinner(f"{Colors.YELLOW}Reddit{Colors.RESET} {msg}", Colors.YELLOW)
        self.spinner.start()

    def end_reddit(self, count: int):
        if self.spinner:
            self.spinner.stop(f"{Colors.YELLOW}Reddit{Colors.RESET} Found {count} threads")

    def start_reddit_enrich(self, current: int, total: int):
        if self.spinner:
            self.spinner.stop()
        msg = random.choice(ENRICHING_MESSAGES)
        self.spinner = Spinner(f"{Colors.YELLOW}Reddit{Colors.RESET} [{current}/{total}] {msg}", Colors.YELLOW)
        self.spinner.start()

    def update_reddit_enrich(self, current: int, total: int):
        if self.spinner:
            msg = random.choice(ENRICHING_MESSAGES)
            self.spinner.update(f"{Colors.YELLOW}Reddit{Colors.RESET} [{current}/{total}] {msg}")

    def end_reddit_enrich(self):
        if self.spinner:
            self.spinner.stop(f"{Colors.YELLOW}Reddit{Colors.RESET} Enriched with engagement data")

    def start_x(self):
        msg = random.choice(X_MESSAGES)
        self.spinner = Spinner(f"{Colors.CYAN}X{Colors.RESET} {msg}", Colors.CYAN)
        self.spinner.start()

    def end_x(self, count: int):
        if self.spinner:
            self.spinner.stop(f"{Colors.CYAN}X{Colors.RESET} Found {count} posts")

    def start_processing(self):
        msg = random.choice(PROCESSING_MESSAGES)
        self.spinner = Spinner(f"{Colors.PURPLE}Processing{Colors.RESET} {msg}", Colors.PURPLE)
        self.spinner.start()

    def end_processing(self):
        if self.spinner:
            self.spinner.stop()

    def show_complete(
        self,
        reddit_count: int = 0,
        x_count: int = 0,
        *,
        source_counts: dict[str, int] | None = None,
        display_sources: list[str] | None = None,
    ):
        elapsed = time.time() - self.start_time
        if source_counts is None:
            source_counts = {
                "reddit": reddit_count,
                "x": x_count,
            }
            if display_sources is None:
                display_sources = [source for source, count in source_counts.items() if count]
                if not display_sources:
                    display_sources = ["reddit", "x"]

        ordered_sources = _completion_sources(source_counts, display_sources)
        parts = [
            _format_completion_part(source, source_counts.get(source, 0), tty=IS_TTY)
            for source in ordered_sources
        ]
        if IS_TTY:
            sys.stderr.write(f"\n{Colors.GREEN}{Colors.BOLD}✓ Research complete{Colors.RESET} ")
            sys.stderr.write(f"{Colors.DIM}({elapsed:.1f}s){Colors.RESET}\n")
            sys.stderr.write("  " + "  ".join(parts))
            sys.stderr.write("\n\n")
        else:
            sys.stderr.write(f"✓ Research complete ({elapsed:.1f}s) - {', '.join(parts)}\n")
        sys.stderr.flush()

    def show_cached(self, age_hours: float = None):
        if age_hours is not None:
            age_str = f" ({age_hours:.1f}h old)"
        else:
            age_str = ""
        sys.stderr.write(f"{Colors.GREEN}⚡{Colors.RESET} {Colors.DIM}Using cached results{age_str} - use --refresh for fresh data{Colors.RESET}\n\n")
        sys.stderr.flush()

    def show_error(self, message: str):
        sys.stderr.write(f"{Colors.RED}✗ Error:{Colors.RESET} {message}\n")
        sys.stderr.flush()

    def show_promo(self, missing: str = "reddit", diag: dict | None = None):
        """Show the promotional hint for one missing free upgrade.

        Args:
            missing: which PROMO_SINGLE_KEY entry to print ("reddit" or "x").
            diag: accepted for call compatibility; no longer consulted.
        """
        if missing in PROMO_SINGLE_KEY:
            sys.stderr.write(PROMO_SINGLE_KEY[missing])
        sys.stderr.flush()

    def show_bird_auth_help(self):
        """Show Bird authentication help."""
        if IS_TTY:
            sys.stderr.write(BIRD_AUTH_HELP)
        else:
            sys.stderr.write(BIRD_AUTH_HELP_PLAIN)
        sys.stderr.flush()


def show_diagnostic_banner(diag: dict):
    """Show pre-flight source status banner when sources are missing.

    Args:
        diag: Dict from pipeline.diagnose() with available_sources, x_backend,
            bird status, and provider availability.
    """
    available_sources = set(diag.get("available_sources") or [])
    has_reddit = "reddit" in available_sources
    has_scrapecreators = diag.get("has_scrapecreators", False)
    has_x = "x" in available_sources
    x_backend = diag.get("x_backend")

    # If everything is available, no banner needed
    if has_reddit and has_x:
        return

    lines = []

    if IS_TTY:
        lines.append(f"{Colors.DIM}┌─────────────────────────────────────────────────────┐{Colors.RESET}")
        _header = f"/last30days v{_skill_version()} - Source Status"
        lines.append(f"{Colors.DIM}│{Colors.RESET} {Colors.BOLD}{_header}{Colors.RESET}{' ' * (52 - len(_header))}{Colors.DIM}│{Colors.RESET}")
        lines.append(f"{Colors.DIM}│{Colors.RESET}                                                     {Colors.DIM}│{Colors.RESET}")

        # Reddit
        if has_reddit and has_scrapecreators:
            lines.append(f"{Colors.DIM}│{Colors.RESET}  {Colors.GREEN}✅ Reddit{Colors.RESET}    — full threads with comments          {Colors.DIM}│{Colors.RESET}")
        elif has_reddit:
            lines.append(f"{Colors.DIM}│{Colors.RESET}  {Colors.GREEN}✅ Reddit{Colors.RESET}    — public threads (titles + scores)   {Colors.DIM}│{Colors.RESET}")
        else:
            lines.append(f"{Colors.DIM}│{Colors.RESET}  {Colors.RED}❌ Reddit{Colors.RESET}    — unavailable                         {Colors.DIM}│{Colors.RESET}")

        # X/Twitter
        if has_x:
            username = diag.get("bird_username", "")
            label = f"Bird ({username})" if x_backend == "bird" and username else str(x_backend or "xai").upper()
            lines.append(f"{Colors.DIM}│{Colors.RESET}  {Colors.GREEN}✅ X/Twitter{Colors.RESET} — {label}                          {Colors.DIM}│{Colors.RESET}")
        else:
            lines.append(f"{Colors.DIM}│{Colors.RESET}  {Colors.RED}❌ X/Twitter{Colors.RESET} — No X auth or fallback key        {Colors.DIM}│{Colors.RESET}")
            if diag.get("bird_installed"):
                lines.append(f"{Colors.DIM}│{Colors.RESET}     └─ Add AUTH_TOKEN/CT0 or XAI_API_KEY      {Colors.DIM}│{Colors.RESET}")
            else:
                lines.append(f"{Colors.DIM}│{Colors.RESET}     └─ Needs Node.js 22+ (Bird is bundled)           {Colors.DIM}│{Colors.RESET}")

        lines.append(f"{Colors.DIM}│{Colors.RESET}                                                     {Colors.DIM}│{Colors.RESET}")
        lines.append(f"{Colors.DIM}│{Colors.RESET}  Config: {Colors.BOLD}~/.config/last30days/.env{Colors.RESET}                  {Colors.DIM}│{Colors.RESET}")
        lines.append(f"{Colors.DIM}└─────────────────────────────────────────────────────┘{Colors.RESET}")
    else:
        # Plain text for non-TTY (Claude Code / Codex)
        lines.append("┌─────────────────────────────────────────────────────┐")
        _header_plain = f"/last30days v{_skill_version()} - Source Status"
        lines.append(f"│ {_header_plain}{' ' * (52 - len(_header_plain))}│")
        lines.append("│                                                     │")

        if has_reddit and has_scrapecreators:
            lines.append("│  ✅ Reddit    — full threads with comments          │")
        elif has_reddit:
            lines.append("│  ✅ Reddit    — public threads (titles + scores)   │")
        else:
            lines.append("│  ❌ Reddit    — unavailable                         │")

        if has_x:
            lines.append("│  ✅ X/Twitter — available                            │")
        else:
            lines.append("│  ❌ X/Twitter — No X auth or fallback key          │")
            if diag.get("bird_installed"):
                lines.append("│     └─ Add AUTH_TOKEN/CT0 or XAI_API_KEY           │")
            else:
                lines.append("│     └─ Needs Node.js 22+ (Bird is bundled)           │")

        lines.append("│                                                     │")
        lines.append("│  Config: ~/.config/last30days/.env                  │")
        lines.append("└─────────────────────────────────────────────────────┘")

    sys.stderr.write("\n".join(lines) + "\n\n")
    sys.stderr.flush()


def print_phase(phase: str, message: str):
    """Print a phase message."""
    colors = {
        "reddit": Colors.YELLOW,
        "x": Colors.CYAN,
        "process": Colors.PURPLE,
        "done": Colors.GREEN,
        "error": Colors.RED,
    }
    color = colors.get(phase, Colors.RESET)
    sys.stderr.write(f"{color}▸{Colors.RESET} {message}\n")
    sys.stderr.flush()
