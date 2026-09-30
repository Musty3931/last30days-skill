"""Contract tests for the first-run NUX wizard in SKILL.md (five-source fork).

Step 0 has two branches: a **Claude Code Modal Flow** (AskUserQuestion-driven,
the restored v3.0.0 NUX) and a **Non-Modal Prose Flow** for hosts without modals
(OpenClaw, Codex, Cursor, Gemini CLI). These tests assert the structural
guarantees of both branches, plus the cross-cutting copy rules: the hard
"Step 0 before Step 1" gate, Digg threaded alongside arXiv (the only two CLIs
setup installs), the 10,000-free-calls credit count, ScrapeCreators framed ONLY
as the Reddit search backup, and every removed source (YouTube, TikTok,
Instagram, Threads, Pinterest, Hacker News, Polymarket, Techmeme, ...) kept out
of onboarding entirely. They read SKILL.md as text - the model's runtime
contract - matching tests/test_runtime_preflight_contract.py.

These lock the flow against silent re-erosion (the failure mode that orphaned the
wizard in PR #659 and flattened it before this restoration) and against a
removed source creeping back into the offers.
"""

import unittest
from pathlib import Path

from lib import setup_wizard

ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = ROOT / "skills" / "last30days" / "SKILL.md"

REMOVED_SOURCES = (
    "YouTube",
    "yt-dlp",
    "TikTok",
    "Instagram",
    "Threads",
    "Pinterest",
    "LinkedIn",
    "Hacker News",
    "Polymarket",
    "Techmeme",
    "Trustpilot",
    "Bluesky",
    "Perplexity",
    "Xiaohongshu",
    "DripStack",
    "Telegram",
    "INCLUDE_SOURCES=",
)


class TestOnboardingContract(unittest.TestCase):
    def setUp(self):
        self.text = SKILL_MD.read_text(encoding="utf-8")
        # Scope assertions to Step 0 so generic substrings elsewhere in the file
        # do not satisfy ordering/presence checks.
        start = self.text.index("## Step 0: First-Run Setup Wizard")
        end = self.text.index("## CRITICAL: Parse User Intent", start)
        self.step0 = self.text[start:end]
        # Branch slices.
        modal_start = self.step0.index("### Claude Code Modal Flow")
        prose_start = self.step0.index("### Non-Modal Prose Flow")
        manual_start = self.step0.index("### Manual Setup Guide")
        self.modal = self.step0[modal_start:prose_start]
        self.prose = self.step0[prose_start:manual_start]
        self.manual = self.step0[manual_start:]

    # --- Platform split + hard gate ---

    def test_platform_split_present(self):
        """Step 0 routes modal-capable hosts and prose hosts to distinct flows."""
        self.assertIn("Platform split", self.step0)
        self.assertIn("### Claude Code Modal Flow", self.step0)
        self.assertIn("### Non-Modal Prose Flow", self.step0)

    def test_hard_gate_step0_before_step1(self):
        """The erosion-resistant gate that orphaned the wizard in #659 is restored."""
        self.assertIn("ALWAYS execute Step 0 BEFORE Step 1", self.step0)

    def test_waiting_topic_continues_after_x_decline_or_setup_skip(self):
        """Declining optional X access must never strand the requested topic."""
        self.assertIn("RESEARCH CONTINUATION OVERRIDE", self.step0)
        self.assertIn("declining or skipping X must never stop", self.step0)
        self.assertIn("immediately research it with `--no-browser-cookies`", self.modal)
        self.assertIn("immediately research it with `--no-browser-cookies`", self.prose)
        self.assertIn("a skip or no answer is never consent", self.step0)

    def test_waiting_topic_defers_optional_prompts_and_x_retry(self):
        self.assertIn("skip the ScrapeCreators offer", self.step0)
        self.assertIn("Do not ask another X question in the same run", self.step0)
        self.assertIn("Offer ONE retry only when no research topic is waiting", self.modal)
        self.assertIn("Offer ONE retry only when no research topic is waiting", self.prose)

    def test_deferred_onboarding_resumes_after_the_findings(self):
        """Deferral is same-run only: SETUP_COMPLETE=true means later runs skip
        Step 0, so a skip-X-with-topic run must itself resume the ScrapeCreators
        offer after the findings or the offer is dropped forever."""
        self.assertIn("RESUME the deferred onboarding in the SAME run", self.step0)
        self.assertIn(
            "this run is the only chance to make the offer", self.step0
        )
        # Both flows: Skip-for-now, Skip-X modal option, and the prose no-path
        # all resume the deferred offer in the same run after the findings.
        self.assertEqual(
            2,
            self.modal.count("then resume Step 4 in the same run"),
        )
        self.assertIn("then resume the deferred onboarding in the same run", self.prose)
        # The resume never turns back into a second X consent ask.
        self.assertIn("the resume never re-asks X/browser-cookie consent", self.step0)
        self.assertIn("Do not re-ask cookie consent as part of the resume", self.prose)

    def test_x_handle_resolution_and_plan_follow_active_sources(self):
        self.assertIn("If `ACTIVE_SOURCES_LIST` contains `x`", self.text)
        self.assertIn("every applicable source from `ACTIVE_SOURCES_LIST`", self.text)
        self.assertIn("Preserve X whenever it is active", self.text)

    def test_post_report_x_note_is_non_blocking(self):
        self.assertNotIn("Just-in-time X unlock", self.text)
        self.assertIn("Optional X omission", self.text)
        self.assertIn("finish the useful findings first", self.text)

    # --- Modal flow: the restored NUX, stages in order ---

    def test_modal_flow_stage_order(self):
        """Welcome -> setup modal -> cookie consent -> Reddit-backup offer -> picker."""
        anchors = [
            "Welcome to /last30days!",  # welcome pitch, embedded in the setup modal
            "How would you like to set up?",
            "your browser's x.com cookies",  # cookie-consent modal
            "Want a Reddit backup lane?",  # ScrapeCreators offer
            "What do you want to research first?",  # topic picker
        ]
        idxs = [self.modal.find(a) for a in anchors]
        for a, i in zip(anchors, idxs):
            self.assertGreater(i, -1, f"modal flow missing stage anchor: {a!r}")
        self.assertEqual(idxs, sorted(idxs), "modal flow stages are out of order")

    def test_modal_uses_askuserquestion(self):
        self.assertIn("AskUserQuestion", self.modal)

    def test_cookie_consent_names_both_installed_clis(self):
        """The cookie-consent modal must not frame X cookies as instead-of the CLIs,
        and must name both Digg and arXiv since auto-setup installs both regardless
        of the cookie choice. No removed CLI (yt-dlp, Techmeme) may appear."""
        consent = self.modal[self.modal.find("your browser's x.com cookies"):]
        consent = consent[: consent.find("Full Disk Access")]  # bound to the consent modal
        for cli in ("Digg", "arXiv"):
            self.assertIn(cli, consent, cli)
        for gone in ("yt-dlp", "Techmeme", "YouTube"):
            self.assertNotIn(gone, consent, gone)
        # The "skip X" option still installs the CLIs (not framed as X-or-CLIs).
        self.assertIn("Skip X - just the CLIs", consent)

    def test_github_option_advertises_auto_clipboard(self):
        """The recommended GitHub option tells the user the code is auto-copied to
        their clipboard, so they just paste it."""
        self.assertIn("clipboard automatically", self.modal)

    def test_modal_cookie_consent_before_setup(self):
        consent = self.modal.find("your browser's x.com cookies")
        setup = self.modal.find("last30days.py setup")
        self.assertGreater(consent, -1, "no cookie-consent modal in modal flow")
        self.assertGreater(setup, -1, "no setup invocation in modal flow")
        self.assertLess(consent, setup, "cookie consent must precede setup in modal flow")

    def test_topic_picker_skips_when_topic_supplied(self):
        """The picker documents skipping when the user already gave a topic."""
        self.assertIn("What do you want to research first?", self.modal)
        self.assertIn("SKIP this picker", self.modal)

    # --- Prose flow: same work, modal-free ---

    def test_prose_flow_has_no_modals(self):
        self.assertNotIn("AskUserQuestion", self.prose)

    def test_prose_cookie_consent_before_setup(self):
        consent = self.prose.find("Cookie consent")
        setup = self.prose.find("last30days.py setup")
        self.assertGreater(consent, -1, "no cookie-consent step in prose flow")
        self.assertGreater(setup, -1, "no setup invocation in prose flow")
        self.assertLess(consent, setup, "cookie consent must precede setup in prose flow")

    def test_prose_decline_uses_from_browser_off(self):
        self.assertIn("FROM_BROWSER=off", self.prose)

    # --- Full Disk Access remediation (both branches) ---

    def test_full_disk_access_remediation_present(self):
        self.assertIn("Permission denied reading Cookies.binarycookies", self.modal)
        self.assertIn("Full Disk Access", self.modal)
        self.assertIn("Permission denied reading Cookies.binarycookies", self.prose)
        self.assertIn("Full Disk Access", self.prose)

    def test_skip_path_writes_setup_complete(self):
        """The 'Skip for now' setup choice must write SETUP_COMPLETE or the wizard loops."""
        skip_idx = self.modal.find("If the user picks Skip for now")
        self.assertGreater(skip_idx, -1, "no Skip-for-now handling in modal flow")
        # The skip branch must persist the completion flag in its own paragraph.
        skip_para = self.modal[skip_idx:skip_idx + 400]
        self.assertIn("SETUP_COMPLETE=true", skip_para)

    # --- ScrapeCreators signup + persisted edge case ---

    def test_scrapecreators_signup_present_both_branches(self):
        self.assertIn("setup --github", self.modal)
        self.assertIn("setup --github", self.prose)

    def test_persisted_false_edge_case_documented(self):
        self.assertIn('"persisted": false', self.step0)

    # --- Digg threaded alongside arXiv everywhere it appears ---

    def test_digg_threaded_with_arxiv(self):
        for slice_name, slice_text in (
            ("modal", self.modal),
            ("prose", self.prose),
            ("manual", self.manual),
        ):
            self.assertIn("Digg", slice_text, slice_name)
            self.assertIn("arXiv", slice_text, slice_name)
        # The Auto-setup modal option names both installed CLIs, no more, no less.
        self.assertIn("Digg and arXiv CLIs", self.modal)

    # --- Credit count = 10,000, no conflicting numbers in onboarding ---

    def test_credit_count_is_10000(self):
        self.assertIn("10,000 free calls", self.step0)
        self.assertNotIn("1,000 free", self.step0)
        self.assertNotIn("1000 free credit", self.step0)
        self.assertNotIn("1000 credits", self.step0)
        self.assertNotIn("100 free call", self.step0)

    # --- Removed sources stay out of onboarding; no INCLUDE_SOURCES tiers ---

    def test_removed_sources_absent_from_step0_offers(self):
        """The five-source fork never offers, installs, or tiers a removed source.
        The one allowed mention is the Manual Setup Guide's explicit 'do not offer'
        sentence, so the check runs over the modal and prose flows."""
        for gone in REMOVED_SOURCES:
            self.assertNotIn(gone, self.modal, gone)
            self.assertNotIn(gone, self.prose, gone)

    def test_manual_guide_denies_removed_sources_explicitly(self):
        self.assertIn("There are no other sources.", self.manual)
        self.assertIn("exactly Reddit, X, GitHub, Digg, arXiv, and Epic Forums", self.manual)

    def test_no_source_tier_step(self):
        """The old Step 5 INCLUDE_SOURCES opt-in (TikTok/Instagram/comments tiers)
        is gone; Step 5 is now the first-topic picker."""
        self.assertNotIn("Which ScrapeCreators sources?", self.step0)
        self.assertNotIn("INCLUDE_SOURCES=", self.step0)
        step5 = self.modal[self.modal.index("**Step 5:"):]
        self.assertIn("First-topic picker", step5)

    # --- ScrapeCreators is the Reddit backup lane, nothing more ---

    def _modal_before_picker(self):
        # Welcome (Step 1) through the Step 4 ScrapeCreators offer.
        return self.modal[: self.modal.index("**Step 5:")]

    def test_offer_copy_frames_key_as_empty_only_reddit_backup(self):
        """The Step 4 offer states Reddit already works free, that the key is the
        search backup ONLY when the free path returns no items, and never claims
        rate-limit escalation or comment enrichment."""
        before = self._modal_before_picker()
        self.assertIn("Reddit", before)
        self.assertIn("returns no items", before)
        self.assertIn("backup when the free path returns no items", before)
        self.assertIn("10,000 free calls", before)
        self.assertNotIn("when they hit rate limits", before)
        self.assertNotIn("prefers ScrapeCreators for Reddit", before)
        self.assertNotIn("enriches Reddit comments", before)
        # The escalation knobs are named but never set on the user's behalf.
        self.assertIn("LAST30DAYS_REDDIT_SC_MIN_ITEMS", before)
        self.assertIn("LAST30DAYS_REDDIT_BACKEND=scrapecreators", before)
        self.assertIn("Never set either on the user's behalf", before)

    def test_prose_offer_frames_key_as_reddit_backup(self):
        offer = self.prose[self.prose.index("ScrapeCreators signup offer"):]
        self.assertIn("returns no items", offer)
        self.assertIn("10,000 free calls", offer)

    # --- Chrome-first cookie scan (U2/U3) ---

    def test_cookie_consent_leads_with_chrome(self):
        """Both flows tell the user Chrome is checked first, with the Keychain cue."""
        for slice_name, slice_text in (("modal", self.modal), ("prose", self.prose)):
            self.assertIn("Chrome", slice_text, f"{slice_name} cookie copy omits Chrome")
            self.assertIn("Always Allow", slice_text, f"{slice_name} omits the Keychain cue")

    def test_fda_reframed_as_safari_fallback(self):
        """Full Disk Access is framed as Safari-only, not the default path."""
        self.assertNotIn("scan your browser (Firefox/Safari)", self.modal)

    def test_welcome_embedded_in_modal(self):
        """The welcome pitch lives INSIDE the setup modal (the only always-visible
        surface), not as a separate message/command that Claude Code folds away.
        The engine --welcome command is kept for the non-modal prose flow."""
        # Pitch is in the modal question.
        self.assertIn("Welcome to /last30days!", self.modal)
        self.assertIn("How would you like to set up?", self.modal)
        # The modal flow explicitly does NOT run a separate --welcome command.
        self.assertIn("Do NOT run a separate `--welcome`", self.modal)
        # The non-modal flow still uses the engine welcome command.
        self.assertIn("last30days.py --welcome", self.prose)

    def test_engine_welcome_names_the_five_sources_only(self):
        """The engine-owned welcome (relayed verbatim on prose hosts) names exactly
        the five sources and none of the removed ones."""
        welcome = setup_wizard.render_welcome()
        for source in ("Reddit", "X", "GitHub", "Digg", "arXiv"):
            self.assertIn(source, welcome, source)
        for gone in ("YouTube", "TikTok", "Instagram", "Hacker News", "HN", "Polymarket", "Techmeme", "StockTwits"):
            self.assertNotIn(gone, welcome, gone)

    # --- Honest GitHub device-code copy (U4/U7) ---

    def test_no_false_instant_gh_promise(self):
        """The '~2 seconds - no browser' claim (a nonexistent code path) is gone."""
        self.assertNotIn("~2 seconds - no browser", self.step0)
        self.assertNotIn("Registers via GitHub CLI in ~2 seconds", self.step0)

    def test_device_code_surfacing_orchestration_present(self):
        """Both flows use the deterministic two-command split (start returns the
        code fast, then poll) instead of a background-and-surface spinner."""
        self.assertIn("setup --github-start", self.modal)
        self.assertIn("setup --github-poll", self.modal)
        self.assertIn("setup --github-start", self.prose)
        self.assertIn("setup --github-poll", self.prose)

    def test_already_registered_status_handled(self):
        self.assertIn("already_registered", self.modal)
        self.assertIn("already_registered", self.prose)

    # --- Welcome must render before the modal (U1) ---

    def test_welcome_pitch_is_in_the_modal_question(self):
        """The welcome pitch names the five sources inside the modal question, so
        the user sees it without expanding folded tool output. The old skip-prone
        'IMMEDIATELY call AskUserQuestion' wording stays gone."""
        for source in ("Reddit", "X,", "GitHub", "Digg", "arXiv"):
            self.assertIn(source, self.modal, source)
        self.assertNotIn("Then IMMEDIATELY call AskUserQuestion", self.modal)

    # --- Device code surfaced with a clipboard-paste hint (U3) ---

    def test_device_code_clipboard_paste_instruction(self):
        """The GitHub flow tells the user the code is on their clipboard to paste,

        and makes surfacing the code a required step (the bug the user hit).
        """
        self.assertIn("on your clipboard", self.modal)
        # Surfacing the code is a required, explicit step in the new split flow.
        self.assertIn("SHOW THE CODE", self.modal)
        self.assertIn("just paste", self.modal)

    # --- Honest 'authorized but no key' branch, distinct from auth-failed (U4) ---

    def test_authorized_but_no_key_branch_present(self):
        """A key-fetch failure after successful auth is handled honestly (likely

        an already-linked account), not lumped into 'auth didn't complete'.
        """
        for slice_name, slice_text in (("modal", self.modal), ("prose", self.prose)):
            self.assertIn("Authorized but failed to fetch API key", slice_text, slice_name)
            self.assertIn("already linked", slice_text, slice_name)

    # --- Legacy guarantees retained ---

    def test_old_silent_wizard_instruction_removed(self):
        self.assertNotIn("Follow the wizard's prompts end-to-end", self.text)

    def test_consent_is_conversational_contract_documented(self):
        self.assertIn("Named onboarding contract", self.step0)
        self.assertIn("non-interactive subprocess", self.step0)


if __name__ == "__main__":
    unittest.main()
