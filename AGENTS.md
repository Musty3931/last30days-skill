# last30days Skill

Agent Skills package for researching any topic across exactly six sources: Reddit, X, GitHub, Digg, arXiv, and Epic Forums. This is a personal fork (`Musty3931/last30days-skill`) of `mvanhorn/last30days-skill` with every other upstream source removed. Installable across Claude Code (most common host), Codex, Cursor, GitHub Copilot, Gemini CLI, Grok (xAI), and 50+ other [Agent Skills](https://agentskills.io) hosts. Python scripts with multi-source search aggregation.

## Structure
- `skills/last30days/SKILL.md` — canonical skill definition / runtime spec the model reads when the slash command fires
- `skills/last30days/scripts/last30days.py` — main research engine
- `skills/last30days/scripts/lib/` — search, enrichment, rendering modules
- `skills/last30days/scripts/lib/vendor/bird-search/` — vendored X search client
- `docs/solutions/` — documented solutions to past problems (bugs, best practices, workflow patterns), organized by category with YAML frontmatter (`module`, `tags`, `problem_type`)
- `CONCEPTS.md` — shared domain vocabulary (Skill, Engine, Harness, Beta channel) — relevant when orienting to the codebase or discussing project terminology
- `CONFIGURATION.md` — user-facing knobs (env vars, flags, per-host install patterns); keep in sync per the rules below
- `CHANGELOG.md` — structured release history built by towncrier at release time (launch copy lives in GitHub Releases)
- `changelog.d/` — per-PR news fragments; feature PRs write here, never edit `CHANGELOG.md` directly
- `CONTRIBUTING.md` — setup, fragments, and release notes for humans and agents (towncrier is release-only)
- `.github/scripts/prepare_release.py` — lockstep version bump + towncrier build (release PRs only)
- `HERMES_SETUP.md` — install instructions for the Hermes harness specifically

## Orientation
- This is an Agent Skills package, not a CLI tool. The product is the slash-command-invoked skill (`/last30days <topic>` in most harnesses); `scripts/last30days.py` is implementation. Claude Code is the most common host but not the only one — features must work across every harness the skill installs into.
- Feature design starts from the slash-command UX. A new engine flag with no SKILL.md integration is incomplete — the model invoking the skill won't know the flag exists.
- README and PR examples show `/last30days <topic>` first. Direct CLI invocation (`python3 scripts/last30days.py ...`) is a fallback for scripting, cron, and dev-time engine testing; label it as such, never as the primary path.
- Slash commands don't pass shell mechanics through. `/last30days OpenClaw --emit=html | pbcopy` is invalid in any harness — either use the slash form (no flags or pipes; let the model translate user intent into engine flags) or use the direct CLI form (full `python3 ...` with explicit flags and a real shell).

## Commands
```bash
# Dev/fallback: direct engine invocation (scripting, cron, or engine testing only).
# Saves to $LAST30DAYS_MEMORY_DIR when set in shell or ~/.config/last30days/.env;
# add --save-dir <path> for a one-off override. Mirrors LAST30DAYS_STORE convention.
python3 skills/last30days/scripts/last30days.py "test query" --emit=compact
npx skills add . -g -y   # copies skill into ~/.agents/skills/<name>/ (frozen at install time); re-run to sync working-tree edits — see Rules below

# Tests (pytest, ~89 files under tests/, configured in pyproject.toml)
uv run pytest                              # full suite
uv run pytest tests/test_dedupe_v3.py      # single file
uv run pytest tests/test_dedupe_v3.py -k some_case   # single case
uv run pytest --cov                        # with coverage (skips lib/vendor/)

# Release prep (maintainers / release automation — not feature PRs):
# Prefer GitHub Actions → "Prepare release". Local equivalent:
uv run python .github/scripts/prepare_release.py --bump patch   # or --version X.Y.Z
```

Python 3.12+ required. Use `uv` for the env; the venv lives at `.venv/`.

## Changelog and releases (agents)

Agents open most PRs. Follow this so `CHANGELOG.md` stops conflicting and versions stay lockstep:

1. **Feature/fix PRs:** add `changelog.d/<pr-or-issue>.<type>.md` (`added` / `changed` / `fixed` / `removed` / `deprecated` / `security`) when the change belongs in the next release notes. See `changelog.d/README.md` and `CONTRIBUTING.md`. Fill the PR template’s Summary, Agent disclosure, and Relationship sections.
2. **Never** edit `CHANGELOG.md` in a feature PR. **Never** bump version strings in `pyproject.toml`, `SKILL.md`, plugin/marketplace JSON, or `uv.lock` outside a release PR. CI (`changelog-guard.yml`) enforces this.
3. **Nothing for release notes:** omit the fragment, check Skip changelog in the template, and add the `skip-changelog` label.
4. **Cutting a release:** run Actions → **Prepare release** (patch/minor/major). That opens a `chore(release): bump version to X.Y.Z` PR which runs towncrier and bumps every lockstep surface. Merging to `main` triggers **Tag release**, which pushes `vX.Y.Z` and existing `release.yml` publishes `.skill` / `.mcpb` artifacts. Do not hand-edit ten version files. Contributors do not need a global towncrier install — `uv sync --group dev` (or the Action) provides it for release prep only.
5. Lockstep gate remains `tests/test_plugin_contract.py::test_versions_match_across_manifests`. Workflow contract: `tests/test_changelog_workflow.py`.

## Rules
- `lib/__init__.py` must be bare package marker (comment only, NO eager imports)
- One-time setup: `npx skills add . -g -y` copies the skill into `~/.agents/skills/<name>/` (real directory) and, for harnesses that support symlinked skill dirs, drops a per-host symlink pointing at that copy. **Working-tree edits do NOT propagate automatically** — the `~/.agents/skills/<name>/` copy is frozen at install time. To sync after edits, re-run `npx skills add . -g -y`. For live-edit on a dev machine, replace the install copy with a symlink to the working tree: `ln -sfn "$PWD/skills/last30days" ~/.agents/skills/last30days` (run from the repo root).
- Git remote: origin = `Musty3931/last30days-skill` (personal fork); upstream = `mvanhorn/last30days-skill`. Never push to upstream.
- **Six sources only.** The engine searches Reddit, X (backends: bird/browser cookies, xAI, xurl, xquik, opt-in grok), GitHub, Digg (`digg-pp-cli`), arXiv (`arxiv-pp-cli`), and Epic Developer Community forums (`epicforums`, anonymous Discourse JSON). Do not reintroduce a removed source, a web-search backend, or a flag for one; `build_parser()` in `scripts/last30days.py` is the authoritative flag list and `lib/env.py` the authoritative env-key list. Docs must never advertise a source or key that is not there.
- **Epic Forums** uses anonymous Discourse JSON, is enabled by default for Epic/Unreal-related topics, and is disabled by `LAST30DAYS_EPICFORUMS=off`. `LAST30DAYS_EPICFORUMS_BASE` supports another Discourse host with explicit source selection. Share the depth-scaled request budget across all subqueries and enrichment, pace at no more than one request per second, and preserve the requested date window.
- Do not reduce `fail_under` in `pyproject.toml` (`[tool.coverage.report]`) without documenting why in the PR. The coverage gate is a floor meant to rise over time, not to be relaxed when new code is under-tested.
- Every `lib/*.py` call to `log.source_log(...)` must pass `tty_only=False`. The default is `True`, which silently drops every line when stderr isn't a TTY (Claude Code, Codex, CI, captured output) — turning source observability into invisible failure. Enforced by `tests/test_source_log_visibility.py`.
- **CLI-gated optional sources** (Digg via `digg-pp-cli`, arXiv via `arxiv-pp-cli`) activate only when `shutil.which` resolves the binary on the **agent subprocess PATH** — not merely when the file exists on disk. First-run setup installs both through `@mvanhorn/printing-press-library` (default `$HOME/.local/bin`); Hermes/OpenClaw gateways often need that directory on PATH. Setup must distinguish PATH-visible installs from off-PATH binaries and must not claim "now active" unless the engine gate would pass. See `docs/solutions/integration-issues/digg-cli-agent-path-setup-wizard.md`.
- **First-run onboarding is consent-driven, model-led, and host-split.** The setup subprocess does only mechanical work (cookie reads, Digg/arXiv CLI installs, GitHub device-auth, and emitting the engine-owned welcome via `--welcome`) — it cannot prompt, so consent lives in `SKILL.md` Step 0. Two flows avoid model-authored prose that Claude Code folds or the model skips: in the **Modal Flow** the welcome pitch is embedded in the setup modal's question (the AskUserQuestion modal is the only always-fully-visible surface — a separate welcome message or `--welcome` Bash run gets buried behind "ctrl+o to expand"); the **Non-Modal Prose Flow** still uses `last30days.py --welcome` (relayed verbatim) since it has no modal. The GitHub device code is surfaced by a two-command split — `setup --github-start` returns the code fast (foreground, copies to clipboard) and `setup --github-poll` waits for authorization (`setup --github` still chains both for back-compat). Step 0 has TWO branches: a **Claude Code Modal Flow** (the restored v3.0.0 `AskUserQuestion`-driven NUX — welcome, Auto/Manual/Skip, cookie consent, ScrapeCreators Reddit-backup offer, first-topic picker) for hosts with modals, and a **Non-Modal Prose Flow** for hosts without (OpenClaw, Codex, Cursor, Gemini CLI, Grok). Both ask before reading cookies, surface the macOS Full Disk Access fix on permission-denied, and offer the ScrapeCreators GitHub signup (10,000 free calls) on every first run. ScrapeCreators has exactly one role in this fork: the Reddit search backup when the free path returns no items (`LAST30DAYS_REDDIT_SC_MIN_ITEMS` / `LAST30DAYS_REDDIT_BACKEND` are the only escalation knobs). There is no `INCLUDE_SOURCES` source-tier step and no Threads/Pinterest/TikTok/Instagram offer anywhere in onboarding. A successful `setup --github` persists `SCRAPECREATORS_API_KEY` automatically (via `setup_wizard.write_api_key`, 0o600) and masks the key in stdout. Do NOT collapse the modal flow back into a bare silent `setup` call or flatten it to prose-only — the guided modals are the feature (they eroded once and were restored). The onboarding contract is locked by `tests/test_onboarding_contract.py`. The cross-platform "Top Community Comments" list (`render._render_top_comments`) selects **round-robin by within-platform rank** (every platform's #1, then #2, then #3) so a viral platform can't crowd out a smaller one.

## Security hygiene
- Never commit real API keys, browser cookies, auth tokens, app passwords, access tokens, or `.env` contents.
- Use the env-based auth patterns in `skills/last30days/scripts/lib/env.py`; tests and fixtures must use obvious dummy values only.
- Keep examples safe by redacting secrets and avoiding copy/pasteable live credentials in docs, fixtures, and test data.
- Do not weaken or disable the advisory security workflow (`.github/workflows/security.yml`) without explaining why in the PR description or review thread.

## README

`README.md` is the only README. The upstream translations (`README.*.md`) were deleted in this fork because they cannot be maintained here; do not recreate them or add a language switcher.

## Maintaining CONFIGURATION.md

`CONFIGURATION.md` is the user-facing configuration reference — save paths, per-source API keys, X backend priority, trend-monitoring stack, per-client install patterns. Distinct from `SKILL.md` (the canonical runtime spec).

Update `CONFIGURATION.md` when:

- adding a new env var (e.g. `LAST30DAYS_*`, `*_API_KEY`, `GITHUB_TOKEN`)
- adding a new CLI flag that affects configuration (e.g. `--store`, `--search`)
- adding a new per-client install pattern (Claude Code, Gemini, Codex, Cursor, Grok, Hermes…)
- adding a new optional source that requires its own credential
- changing the priority order of config layers (per-run flag > env > `.env` file > defaults)

Keep the existing structure organized by how often each layer is touched: per-run flags → env vars / `.env` → optional trend-monitoring stack → per-client patterns. Never document a source, key, or flag that the engine does not have. Add new content into the right section rather than appending at the end.

When a new config concept lands in `SKILL.md` or `AGENTS.md`, mirror the user-facing knob in `CONFIGURATION.md` so non-agent readers can configure the skill without reverse-engineering it from the runtime spec.

## Plugin manifests (Grok)

The repo doubles as a native Grok Build plugin via `.grok-plugin/plugin.json` + `.grok-plugin/marketplace.json`. Grok also reads `.claude-plugin/*` for compatibility; the native pair is the first-class lane and what an official xAI marketplace listing points at. The self-hosted catalog uses a bare Git URL source (`{"source":"url","url":"https://github.com/Musty3931/last30days-skill.git"}`) so `grok plugin marketplace add Musty3931/last30days-skill` tracks HEAD — not a self-referential local `path: "."` (Grok does not enumerate those). Version lockstep with Claude/Codex/Gemini manifests is enforced by `tests/test_plugin_contract.py`. Validate with `grok plugin validate .`.

## Submitting to the xAI plugin marketplace

(Upstream procedure, kept for reference; this personal fork is not submitted to the xAI catalog.) Getting last30days into xAI's official catalog (`xai-org/plugin-marketplace`) is an outbound PR to *their* repo — an index that only points at our source, so nothing of last30days is vendored there. Do this **after** the change you want to ship has merged to `main`: the entry pins a commit that must already exist.

1. Fork `xai-org/plugin-marketplace` and branch from `main`.
2. Get the commit to pin — a full 40-char lowercase SHA; a branch, tag, or short SHA is rejected by their validator:
   ```bash
   git ls-remote https://github.com/mvanhorn/last30days-skill.git HEAD
   ```
3. Add one entry to their `.grok-plugin/marketplace.json` under `plugins[]`, a remote source pinned to that SHA:
   ```json
   {
     "name": "last30days",
     "description": "Research any topic across Reddit, X, GitHub, Digg, arXiv, and Epic Forums. AI agent scores by upvotes, likes, and stars - not editors.",
     "category": "productivity",
     "source": {
       "source": "url",
       "url": "https://github.com/mvanhorn/last30days-skill.git",
       "sha": "<full-40-char-sha-from-step-2>"
     },
     "homepage": "https://github.com/mvanhorn/last30days-skill",
     "keywords": ["last30days", "last 30 days"]
   }
   ```
4. Regenerate their component index (never hand-edit it) and validate exactly as their CI does:
   ```bash
   python3 scripts/generate-plugin-index.py
   python3 scripts/validate-catalog.py
   python3 scripts/generate-plugin-index.py --check
   ```
5. Open the PR, fill in their template, and wait for code-owner review.

To roll out a later update in their catalog, bump the pinned `sha` in the existing entry — never open a second, parallel entry.

Do not confuse this with our own `.grok-plugin/marketplace.json`: that file makes this repo directly addable as a Grok marketplace (`grok plugin marketplace add Musty3931/last30days-skill`) and uses a **bare URL** source (no SHA) so it tracks HEAD; the xAI entry above lives in *their* repo and uses a **remote** source pinned to a SHA.

## Beta channel

Experimental changes get tested on `mvanhorn/last30days-skill-private`, which installs as a parallel `/last30days-beta` slash command. Beta-only changes never ship to public without a review PR here. Workflow guide lives at `BETA.md` in the private repo. Plan that established this setup: `docs/plans/2026-04-17-005-feat-beta-skill-from-private-repo-plan.md`.
