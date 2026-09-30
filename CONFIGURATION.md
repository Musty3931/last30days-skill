# Configuration

Everything you can tune in `/last30days` without editing the engine source.
Three layers, in order of how often you'll touch them:

1. **Per-run flags** - what you pass on the command line.
2. **Environment variables and `.env`** - what's enabled across all runs.
3. **Optional trend-monitoring stack** - SQLite store, watchlist, briefings.

Per-client patterns and the experimental beta channel are at the bottom.

> Skip ahead: [Where output is saved](#where-output-is-saved) - [API keys](#api-keys-env) - [Reasoning provider](#reasoning-provider-priority) - [X backends](#x-backend-priority) - [Trend monitoring](#trend-monitoring-store--watchlist--briefings) - [Per-client patterns](#per-client-patterns) - [Beta channel](#beta-channel)

## Why this document exists

This is a focused **configuration reference** maintained alongside the engine. The runtime contract (the voice rules, the planner protocol, the LAWs the synthesizing model follows) lives in [`skills/last30days/SKILL.md`](skills/last30days/SKILL.md) - that file is authoritative when the two ever differ. This file's job is narrower: surface every knob a user or operator can turn, in one place, kept current with the code so client-facing setups stay reliable. This fork searches exactly six sources - Reddit, X, GitHub, Digg, arXiv, and Epic Forums - so every knob below belongs to one of those, to the reasoning provider, or to output/storage. New configuration knobs added to the engine should be reflected here in the same PR.

---

## Where output is saved

| Platform | Default path | Override |
|---|---|---|
| Linux / macOS | `LAST30DAYS_MEMORY_DIR` defaults to `~/Documents/Last30Days/` | set `LAST30DAYS_MEMORY_DIR=/path` |
| Windows | `LAST30DAYS_MEMORY_DIR` defaults to `C:\Users\<you>\Documents\Last30Days\` | set `LAST30DAYS_MEMORY_DIR=C:\path` |

Each run produces one file per topic, slug-named:
`<slug>-raw[-suffix].md`. Same topic + same suffix on the same day overwrites; same topic + same suffix on different days appends a date stamp.

### Recommended `.env` entry

`.env` files don't travel between machines or harnesses, so set `LAST30DAYS_MEMORY_DIR` explicitly in `~/.config/last30days/.env` once per host. The `/last30days` slash command works without it (the SKILL.md wrapper has its own default), but **bare engine invocations** — `python3 scripts/last30days.py ...` from cron jobs, scripts, or agents that bypass the wrapper — silently no-op the file save unless the engine sees the env var. Mirrors the `LAST30DAYS_STORE` env-or-flag convention.

```bash
# ~/.config/last30days/.env  (pick ONE — uncomment the line that matches your OS)
LAST30DAYS_MEMORY_DIR=~/Documents/Last30Days                      # POSIX — defaults to this path when unset
# LAST30DAYS_MEMORY_DIR=C:\Users\<user>\Documents\Last30Days      # Windows
# LAST30DAYS_LIBRARY_OWNER=Your Name                              # Optional Atom feed author
# LAST30DAYS_LIBRARY_CONTEXT=off                                  # Disable prior-run context (default: on)
```

The engine's `.env` reader doesn't expand `$HOME` — only the tilde, via `Path().expanduser()` downstream. Use `~/...` or an absolute path; **don't** write the literal string `$HOME/...` into your `.env` (it gets stored verbatim and breaks path resolution).

**Per-run overrides:**

- `--save-dir <path>` - one-off output location. **Flag wins over env var.** If neither flag nor env var is set, the engine does not write a file (DB persistence is independent — see `LAST30DAYS_STORE` below).
- `--output <file>` - write the rendered output to an exact file path, using the format selected by `--emit`.
- `--json-profile {agent,raw}` - select the research JSON shape used with `--emit=json`. `agent` is the default, versioned workflow contract; `raw` preserves the full internal `Report` dump for debugging and power users. See the [JSON export reference](docs/reference/json-export.md).
- `--register {default,exec,dev,creator,eli5}` - shape a standard single-topic Markdown or HTML research brief for its audience. `exec` is decisions-first with five core findings and numbers up top; `dev` gives GitHub, code, and technical signals more room; `creator` leads with hooks, Best Takes, community reactions, and virality metrics; `eli5` keeps the established evidence layout and asks the synthesizing agent for accessible language. Registers do not change retrieval, JSON exports, discovery, drill, library feed/search, or comparison output.
- `--discover [domain]` - trending discovery, two-stage: a river-listing sweep NOMINATES candidate topics, then each nomination gets a full research pass (Reddit with comments, X, GitHub, Digg, arXiv, and Epic Forums for relevant topics) before ranking. Bare `--discover` (no domain) is **global trending**: every feed's own hot list (r/all rising/top-week, Digg clusters when `digg-pp-cli` is on PATH) with no keyword gate; with a domain, the sweep is category-scoped and keyword-gated, and broad X activity joins when an X backend is authenticated. Every topic must clear a confidence floor (cross-source confirmation or a genuinely strong single-source spike); when nothing clears it the run reports "Nothing solid this window" instead of ranked noise. Run without a positional topic; it is mutually exclusive with `--drill`. `--emit=json` uses the separate versioned discovery contract (now with `outcome`, `weak_signal`, per-topic `top_comment` and `corroboration_count`) documented in the [JSON export reference](docs/reference/json-export.md).
- `--discover-shallow` - skip discovery's per-topic research passes and rank on listing evidence only. Faster and thinner; the confidence floor still applies. An explicit `--search` source list bounds both the sweep and the research passes. On a protocol run (below), adding it to the `--nominate-only` leg marks the bundle quick-tier so the resume leg uses the faster shallow research pass.
- `--nominate-only` - leg 1 of the three-command host-judged discovery protocol (agent hosts; SKILL.md drives it - one-shot `--discover` stays the scripting/cron form with deterministic topic names and no angles). With `--discover [domain]`: sweep the listings, write the nominations bundle (`discover-nominations.json` in the save dir, TTL one hour) for host judgment, print a judging digest, and stop - no enrichment, no queue writes. A zero-nomination sweep prints the nothing-solid brief directly.
- `--judgments <path>` - leg 2: resume from the nominations bundle, applying the host judgments file (`{"bundle_id": "...", "judgments": [{"id", "name", "junk", "worthiness"}, ...]}`, bound to the bundle by `bundle_id`). Runs the per-topic research passes (deep tier by default; budget tunable via `LAST30DAYS_ENRICH_BUDGET_SECONDS` below), writes the pending report (`discover-pending.json`), and prints per-topic angle inputs. Requires `--discover`.
- `--finalize` - leg 3: apply optional host angles to the pending report, render the final discovery brief, save artifacts, and record the topic queue (retries are idempotent - the pending file stays in place within its TTL). Offline; requires `--discover`.
- `--angles <path>` - optional host angles file for `--discover --finalize` (`{"bundle_id": "...", "angles": [{"id", "podcast", "x_article"}, ...]}`, sentences capped at 200 chars); omitting it ships the brief without angle lines. All three protocol legs must share one `--save-dir` (handoff files live there, else in `~/.config/last30days/`); contract failures (missing/stale/unbound handoff files) exit 2 with the remedy on stderr, and `--mock` protocol legs require `--save-dir` to stay side-effect-free.
- `--drill <target>` - deep follow-up over the fresh `~/.config/last30days/last-report.json` cache. Accepts a 1-based index (`--drill "cluster 3"` or `--drill "3"`) or a fuzzy cluster title/entity description. It re-fetches only sources that contributed to the matched cluster, enables their deep comment enrichment paths, merges/dedupes the evidence, and replaces the cache so drills can chain. Run it without a positional topic; if the cache is absent or expired, run a normal research pass first.
- `--verify-freshness` - opt into an act-time verification pass for conservatively extracted, source-grounded claims (GitHub star counts and explicit status assertions). With a topic, verification runs after research; without a topic, it re-verifies the fresh `last-report.json` cache without repeating research. Verdicts are `current`, `stale`, `contradicted`, or `unsupported` and include evidence timestamps. Set `LAST30DAYS_VERIFY_FRESHNESS=on` in `.env` to make the pass default for normal research runs.
- `--save-suffix <name>` - distinguish runs of the same topic (e.g. per client: `--save-suffix=acme`).
- `--search <list>` - comma-separated source subset for this run. Valid names: `reddit`, `x`, `github`, `digg`, `arxiv`, `epicforums`; `xquik` is accepted as an alias for `x`. Replace-not-add: list every source you still want. See `LAST30DAYS_DEFAULT_SEARCH` below for the persistent form.
- `--x-handle <handle>` / `--x-related <h1,h2>` - targeted X lanes (the entity's own posts, plus related voices at lower weight).
- `--subreddits <s1,s2>` / `--dedicated-subreddits <s1,s2>` - broad Reddit communities (relevance-floored) and entity-home subreddits (pulled in full, floor-exempt).
- `--github-user <user>` / `--github-repo <owner/repo,...>` - GitHub person-mode (PR velocity, top repos, releases) and project-mode (live stars, README, top issues).
- `--competitors-list "A,B"` / `--competitors-plan <json|path>` - comparison mode with named peers; the plan form carries per-entity `x_handle`, `x_related`, `subreddits`, `github_user`, `github_repos`, and `context`. The engine does not discover competitors itself.
- `--max-results N` / `--max-per-source N` / `--max-source-fetches N` - raise the ranked-pool, per-stream, and per-source fetch caps for high-volume topics.
- `--no-browser-cookies` - hard-disable browser-cookie extraction for this run, even when `FROM_BROWSER` is configured. MCP and folder-mode hosts use this for safe defaults.
- `--publish-html` - with `--emit=html`, publish the rendered HTML to `ht-ml.app` after local output/save-dir writes. This is explicit opt-in only; pages are public by default.
- `library feed` - scan `LAST30DAYS_MEMORY_DIR` plus `~/.local/share/last30days/briefs/`, then write a self-contained `index.html`, valid Atom `feed.xml`, and browser-ready pages under `briefs/`. The index is reverse-chronological and grouped by topic. For direct engine use: `python3 skills/last30days/scripts/last30days.py library feed`; use `--save-dir <path>` to scan and write another library directory.
- `library feed --publish` - publish each rendered brief and the HTML index through `ht-ml.app`. The generated `feed.xml` remains a first-class local artifact because this HTML host does not serve Atom with an XML content type. Host the output directory on any static host (for example, GitHub Pages) to make `feed.xml` subscribable. Publishing is explicit opt-in and pages are public by default; public pages may be crawled or indexed.
- `library search "<query>"` - incrementally sync `LAST30DAYS_MEMORY_DIR` and `~/.local/share/last30days/briefs/` through the shared library scanner, then run offline SQLite FTS5 across those briefs plus dated per-run sightings in `~/.local/share/last30days/research.db`. Results are grouped by topic run. The sibling search index lives at `~/.local/share/last30days/library.db`; hand edits, renames, and deletes are picked up on sync, and a corrupt index is rebuilt automatically.
- `LAST30DAYS_LIBRARY_OWNER=<name>` - optional feed-level Atom author. Defaults to `last30days research library`.
- `LAST30DAYS_LIBRARY_CONTEXT=on|off` - controls passive prior-run context on fresh research reports. It defaults to `on`; matching saved research appears in a short `From your library` section. Set `off` to skip the local index read and leave reports unchanged. Mock runs, eval replays, and internal fan-out subruns do not load library context, keeping fixtures deterministic.
- `--publish-password <password>` - optional shared password for `--publish-html` or `library feed --publish`. Prefer `LAST30DAYS_PUBLISH_PASSWORD=<password>` instead so the password is not visible in the process list or shell history. Use a unique non-personal password; never reuse the user's own password. The provider's update key is treated as secret and is not written to stdout, HTML, raw output, or `.publish.json` metadata.
- `--preflight` - print a human-readable permission preflight. It reports config source, project config trust/ignore state, browser-cookie plan, planned writes, optional commands, source availability, and endpoint overrides without reading browser cookies, writing setup/config/report files, or running research. Add `--emit=json` for the separate machine-readable preflight contract (`--json-profile` does not change it); use `--diagnose` when you need the full source diagnostic JSON.
- `--welcome` - print the first-run welcome text (engine-owned; the skill relays it verbatim on first run). Safe: prints and exits, no reads or writes.
- `--record-fixtures <dir>` - developer-only, hidden flag that records scrubbed source responses for the offline research-quality eval harness. It writes `<dir>/http.json`; see the [eval reference](docs/reference/eval.md) before recording or committing fixtures.
- `setup --github-start` / `setup --github-poll` - the two-command ScrapeCreators GitHub device-auth split. `--github-start` submits the device flow, copies the code to the clipboard, opens the browser, and returns the code immediately (foreground); `--github-poll` waits for you to authorize and persists the key. `setup --github` still runs both in one shot for back-compat.

The footer line `📎 Raw results saved to ${LAST30DAYS_MEMORY_DIR:-$HOME/Documents/Last30Days}/<slug>-raw.md` is the canonical pointer; if it shows backslashes on Windows update past v3.1.1.

Every completed research pass writes a structured `last-report.json` cache beside `last-run.json`. HTML follow-up renders use it so `--emit=html --synthesis-file` can reuse report metadata/footer without fetching sources again; `--drill <target>` uses it as the grounded starting point for targeted re-research; bare `--verify-freshness` updates only the cached report's claim verdicts. Reuse is intentionally short-lived: `LAST30DAYS_REPORT_CACHE_TTL_SECONDS` defaults to `3600` (one hour). Set it to another integer number of seconds to tune the window, or `0` to disable report-cache reuse and post-run follow-ups.

---

## First-run onboarding

On the very first `/last30days` run (no `~/.config/last30days/.env`, or `SETUP_COMPLETE` not set), the skill runs a consent-driven onboarding the model drives in chat. It takes one of two forms depending on the host:

- **Claude Code Modal Flow** - the restored v3.0.0 guided NUX, used on hosts with `AskUserQuestion` (Claude Code). A welcome message, then modals for Auto/Manual/Skip setup, cookie consent, the ScrapeCreators Reddit-backup offer, and a first-topic picker.
- **Non-Modal Prose Flow** - the same work done conversationally on hosts without modals (OpenClaw, Codex, Cursor, Gemini CLI, Grok, raw CLI).

Both share the same consent points:

1. **Browser cookies** - the model asks before reading anything. On yes it runs `setup --allow-browser-cookies`, which extracts x.com cookies (Chrome/Chromium family first via the Keychain, then Firefox and Safari) to unlock X/Twitter, and installs the keyless Digg and arXiv CLIs. On no it runs setup without `--allow-browser-cookies` (or with `FROM_BROWSER=off`), which skips all cookie reads and still installs the CLIs.
2. **Full Disk Access (macOS)** - if a Safari cookie read is permission-denied, the model surfaces the System Settings > Privacy & Security > Full Disk Access fix and offers one retry.
3. **ScrapeCreators GitHub signup** - offered on every first run (10,000 free calls). Reddit already works free; the key is the Reddit search **backup when the free path returns no items** (empty-only by default). On consent it runs `setup --github` (or the `--github-start` / `--github-poll` split), which opens a browser for GitHub device-auth and, on success, **persists `SCRAPECREATORS_API_KEY` automatically** (0o600, masked in output). Decline anytime; you can run it later by asking to set up ScrapeCreators. There is no source-tier opt-in step: the engine has no ScrapeCreators-only sources left.

**Two macOS gotchas the wizard cannot fix for you:**

- **Digg and arXiv CLIs need Go.** The Printing Press installer builds them from source; without Go it fails with "Go is required to install Printing Press CLIs". Run `brew install go`, then `npx -y @mvanhorn/printing-press-library@0.1.16 install digg --cli-only` and `... install arxiv --cli-only`. Both binaries land in `~/.local/bin`, which must be on the PATH the agent's subprocesses see.
- **Chromium browsers are found but not pinned.** Setup reports "X cookies found in chrome/brave/edge" but deliberately does not write `FROM_BROWSER` for Chromium browsers (to avoid surprise Keychain prompts on every run), so steady-state runs still skip X. Add `FROM_BROWSER=brave` (or `chrome`, `edge`, ...) to `~/.config/last30days/.env` yourself and choose "Always Allow" on the one-time Keychain dialog. Firefox and Safari are pinned automatically.

Re-run onboarding by deleting `~/.config/last30days/.env`. The mechanical work lives in `scripts/lib/setup_wizard.py`; the consent conversation and both host flows are specified in `skills/last30days/SKILL.md` Step 0. The original v3.0.0 wizard is captured at `docs/reference/old-nux-wizard-v3.0.0.md`.

---

## API keys (`.env`)

The skill reads keys from a `.env` file. Two locations are supported:

1. **`~/.config/last30days/.env`** at the user level (global default) - loaded by default.
2. **`.claude/last30days.env`** in the current project directory (project-scoped) - loaded only when trusted by setting `LAST30DAYS_TRUST_PROJECT_CONFIG=1` in the process environment or global config.

Override the global location with `LAST30DAYS_CONFIG_DIR=/path` (or `LAST30DAYS_CONFIG_DIR=""` for no-config mode). File permissions should be `600` on POSIX hosts - the engine warns on every run if they aren't.

The project-scoped file is useful for **intentional per-client setups**: drop a `.claude/last30days.env` into each client folder (`SCRAPECREATORS_API_KEY`, `EXCLUDE_SOURCES`, `LAST30DAYS_MEMORY_DIR`, `LAST30DAYS_X_BACKEND`, etc), then opt in with `LAST30DAYS_TRUST_PROJECT_CONFIG=1` from your shell or `~/.config/last30days/.env`. Folder-mode hosts such as Codex desktop do not trust hidden project config by default, and discovery stops at the git root so unrelated parent folders cannot silently influence runs. The SessionStart status hook (`hooks/scripts/check-config.sh`) uses the same trust gate — an untrusted repo's `.claude/last30days.env` is not read at session start.

**Source-by-source** - what each key unlocks:

| Source | Key(s) | Required for | Free tier |
|---|---|---|---|
| Reddit (public) | none (default free keyless path). With `SCRAPECREATORS_API_KEY`: empty-only search backup by default; `LAST30DAYS_REDDIT_SC_MIN_ITEMS=<N>` backfills thin free runs; `LAST30DAYS_REDDIT_BACKEND=scrapecreators` pins SC primary with free fallback. `LAST30DAYS_REDDIT_KEYLESS_RATE` paces unauthenticated reddit.com requests (default `1` req/sec) | always on; SC knobs require `SCRAPECREATORS_API_KEY` | yes |
| Reddit comments | none - top comments come free via shreddit | always on | yes |
| X / Twitter | one of: `AUTH_TOKEN` + `CT0` (browser cookies, Bird CLI), `FROM_BROWSER` (consented cookie-jar read), `XAI_API_KEY`, the `xurl` CLI (OAuth2), `XQUIK_API_KEY`, or a signed-in `grok` CLI (opt-in: `LAST30DAYS_X_BACKEND=grok`) | X items in results | cookie-jar / Bird = free; xAI / Xquik = key-based; grok = Grok plan, opt-in only |
| GitHub | none required. `gh` CLI auth or `GITHUB_TOKEN` raises the rate limit and deepens person-mode (`--github-user`) / project-mode (`--github-repo`) fetches | always on | yes |
| Digg | `digg-pp-cli` on PATH (auto-installed during first-run setup via `npx -y @mvanhorn/printing-press-library@0.1.16 install digg --cli-only`; binary defaults to `$HOME/.local/bin` — Hermes/OpenClaw agent subprocesses must inherit that dir on PATH for Digg to activate) | always on if `digg-pp-cli` on PATH | yes (free, keyless, read-only) |
| arXiv | `arxiv-pp-cli` on PATH (auto-installed during first-run setup via `npx -y @mvanhorn/printing-press-library@0.1.16 install arxiv --cli-only`) | always on if `arxiv-pp-cli` on PATH; fires on research/technical topics and stays quiet otherwise (relevance + recency gating) | yes (free, keyless) |
| Epic Forums | none; anonymous Discourse JSON | automatic for Epic/Unreal topics, or explicitly selected with `--search epicforums` | yes (free, keyless) |
| Planner / reranker (headless runs only) | one of `GOOGLE_API_KEY` / `GEMINI_API_KEY` / `GOOGLE_GENAI_API_KEY`, `OPENAI_API_KEY`, `XAI_API_KEY`; pin with `LAST30DAYS_REASONING_PROVIDER` | internal query planning + reranking when no hosting model passes `--plan` | provider pricing; unused when an agent host drives the skill |

That table is exhaustive. There is no YouTube, TikTok, Instagram, Hacker News, Polymarket, web-search, or Perplexity key to set, and `INCLUDE_SOURCES` / `EXCLUDE_SOURCES` only accept the six names above.

**Reddit keyless pacing.** Unauthenticated reddit.com requests (RSS, listing partials, shreddit) share one token bucket. The default is `1` request per second with a burst of 2, slow enough that engine fan-out does not trip HTTP 429 on a typical home IP. Set `LAST30DAYS_REDDIT_KEYLESS_RATE` to a float req/sec to trade wall-clock for coverage: higher finishes faster and loses more sub-requests to 429; lower is safer and slower. Invalid or non-positive values fall back to `1`. A 429'd RSS or listing sub-request is retried once after a short jittered pause, still through the limiter. Identical reddit.com requests within one command (subreddit listings, listing feeds, comment pages, which repeat across subqueries) are fetched once and memoized, so a typical four-subquery run issues roughly a quarter of the requests it used to. Comment enrichment covers 4 / 8 / 12 threads per subquery at quick / default / deep depth. This does not change ScrapeCreators routing (`LAST30DAYS_REDDIT_BACKEND` / `LAST30DAYS_REDDIT_SC_MIN_ITEMS`).

**X backend priority (bird first).** The default X backend chain is bird (browser cookies) → xai (API key) → xurl (OAuth2 CLI) → xquik (API key). Cookies beat `XAI_API_KEY` when both are present. A leftover grok login never steals the X lane; see below.

**Grok CLI (opt-in backup).** Install the Grok CLI (`curl -fsSL https://x.ai/cli/install.sh | bash`) and run `grok login`, and X can work with no X account, no browser cookies, and no `XAI_API_KEY`. However, grok is **opt-in only**: a leftover `~/.grok/auth.json` must never steal the X lane. Pin `LAST30DAYS_X_BACKEND=grok` to enable it. It is not "free" in the way the cookie path is: calls draw on your Grok plan, and depth costs several calls per run because the underlying tool caps each search at 10 posts. Results are validated before use — every returned post's ID is decoded to confirm it falls inside the requested date range, because the retrieval is performed by a language model and can otherwise return confident, well-formed posts that were never searched for.

**Epic Developer Community forums.** `/last30days Unreal Engine 5.8 path tracer glass` automatically includes the `epicforums` source. The relevance gate recognizes Unreal/UE4/UE5, Epic, Fortnite, UEFN, MetaHuman, Nanite, Lumen, Twinmotion, RealityCapture, and Megascans; unrelated topics do not query the forum. An explicit source selection overrides the topic gate.

- `LAST30DAYS_EPICFORUMS=off` disables the lane (default `on`). `EXCLUDE_SOURCES=epicforums` also excludes it.
- `LAST30DAYS_EPICFORUMS_BASE=https://forums.unrealengine.com` changes the Discourse host; HTTP(S) URLs without credentials, query, or fragment are accepted. For other communities, explicitly select `epicforums` (through the skill or `--search epicforums` in direct engine scripts).
- Requests are anonymous, at most one per second per host in the process, with backoff on HTTP 429. Quick/default/deep runs share hard caps of 5/9/15 requests across all subqueries, categories, retries, and enrichment; at most 1/3/5 top topic bodies are enriched. Categories are cached in-process. Search uses `after:` and `before:` and locally enforces the requested date window. Results may be partial when Discourse has more matches or the budget is exhausted.

**X on cookie-less hosts.** Bird (the free X source) scrapes X using your logged-in browser cookies (`AUTH_TOKEN`/`CT0`), which agent hosts like OpenClaw, CI, or headless runs often can't supply — and scraping carries some account risk. On those, set `XQUIK_API_KEY` (or `XAI_API_KEY`) for full, ranked X coverage from a single API key: the same engagement-based ranking, first-party authorship, and handle (from/mentions) lanes the native X source gets. `--diagnose` reports whether the key is working (and flags an unpaid key).

**Extra bird cookie lookups on Linux and Mac mini.** On a MacBook the X cookie path is unchanged (Firefox/Safari/Chrome extract, gated by `FROM_BROWSER`). On **extra hosts** the engine adds two more ways to hand bird a complete `auth_token`+`ct0` pair, tried in order (first COMPLETE pair wins; no half-pair merge; nothing is ever written to the `.env` and cookie values are never printed):

1. an explicit env `AUTH_TOKEN`+`CT0` (never overwritten);
2. the [`agentcookie`](https://github.com/) sidecar CLI — `agentcookie cookies --domain .x.com --json` — a soft dependency (absent = skipped; `AGENTCOOKIE=off` disables it) that delivers cookies on Linux, where the on-disk Chrome store can't be decrypted here;
3. a live signed-in Chrome/Chromium session over the DevTools Protocol (`Network.getAllCookies`);
4. the mainline browser extract, when `FROM_BROWSER` already lists a browser (on a Mac mini with a browser opted in, this native read runs *before* the CDP read).

A host counts as an "extra host" when ANY of these hold: `AGENTCOOKIE=on` (explicit opt-in, any OS); the platform is Linux; a Darwin **Mac mini** (`sysctl -n hw.model` prefix `Macmini`); or a Darwin **agentcookie sink** role. The host is never inferred from the home directory, PATH, or Hermes/OpenClaw env — only those signals. A plain MacBook does no agentcookie spawn and opens no CDP socket unless `AGENTCOOKIE=on`.

CDP endpoint resolution (extra hosts only, no port scan): `BROWSER_CDP_URL` if set, else port `18800` when it answers as Chrome, else `9222` + the X display number. Port `18800` is the last30days extras **NUX convention** — the agent launches a throwaway login Chrome with `SAND_CHROME_REMOTE_DEBUG_PORT=18800` (see SKILL.md's "X on Linux / Grok Bot / Mac mini"), so it is not confused with a daily Chrome profile on `9222`+display (box-chrome's own built-in default). `18800` is tried first but falls through when it yields no complete pair, so a logged-out Chrome there never shadows a logged-in profile; pin `BROWSER_CDP_URL` if a stale session answers there. A Node `--inspect` endpoint is rejected; a Chrome page target is required.

**Example `.env` skeleton** (placeholders only - replace with your own values):

```bash
# Reasoning + planning for HEADLESS runs only (one provider; see priority below).
# Not needed when Claude Code / Codex / Gemini drive the skill - the host is the planner.
# GOOGLE_API_KEY=<your-gemini-key>

# Reddit: free keyless path is the default. Optional backup lane:
# SCRAPECREATORS_API_KEY=<your-scrapecreators-key>   # used only when the free path returns no items
# LAST30DAYS_REDDIT_SC_MIN_ITEMS=5                    # or: backfill when the free path yields < N items
# LAST30DAYS_REDDIT_BACKEND=scrapecreators            # or: make ScrapeCreators primary, free path as fallback
# LAST30DAYS_REDDIT_KEYLESS_RATE=1  # keyless reddit.com req/sec; lower = fewer 429s, slower runs

# GitHub: works keyless. Optional token raises rate limits and deepens person/project mode.
# GITHUB_TOKEN=<your-github-token>

# X authentication (one option only)
AUTH_TOKEN=<your-auth-token>
CT0=<your-ct0-token>
# OR xAI API key (paid)
# XAI_API_KEY=<your-xai-key>
# OR Xquik key-based X search
# XQUIK_API_KEY=<your-xquik-key>
# OR cookie-jar (free; logs in via your browser session).
# Unset = no browser-cookie reads. FROM_BROWSER=auto tries Firefox/Safari and
# the Chromium family (Chrome, Brave, Edge, Vivaldi, Opera, Arc, Chromium); it
# only prompts for macOS Keychain access on the browser that actually holds your
# X cookies. Or name a single browser, e.g. brave/edge. On Windows only Firefox
# is supported.
# FROM_BROWSER=firefox
# OR pin a backend explicitly (bird / xai / xurl / xquik / grok)
# LAST30DAYS_X_BACKEND=xai

# Source set (six supported sources; Epic Forums is topic-gated; trim with either of these)
# EXCLUDE_SOURCES=arxiv
# LAST30DAYS_DEFAULT_SEARCH=reddit,x,github
```

After editing: `chmod 600 ~/.config/last30days/.env` (or `chmod 600 .claude/last30days.env` if using the project-scoped variant).

**Troubleshooting:** if a source you expected to see isn't appearing in results, run `python3 scripts/last30days.py --preflight` for a human permission summary or `python3 scripts/last30days.py --diagnose` for full JSON diagnostics. Both are safe: they report source availability, config source, browser-cookie plan, external command availability, write destinations, and ignored untrusted project config without reading browser cookies or running live provider probes.

### Encrypted credential sources (Keychain / pass)

If you'd rather not keep keys in a plaintext `.env`, the loader has two
encrypted sources that decrypt secrets transiently at call time (never written
to disk, never logged). Both are **lowest-priority and additive** — an explicit
`.env` or process-env value always overrides them, so you can mix and match. The
`pass` source is only consulted for keys still missing after the higher-priority
sources, so a box that merely has `pass` installed pays no decrypt cost when
everything is already in `.env`.

Effective credential priority is: process env > trusted project config
(`.claude/last30days.env`) > global config (`~/.config/last30days/.env`) >
macOS Keychain > `pass`(1). The SessionStart status hook also checks for
Keychain item **presence** under `last30days-<KEY>` without reading secret
values, so a Keychain-only setup is treated as configured instead of showing the
first-run welcome again.

| Platform | Source | Store keys with | Lookup convention |
|---|---|---|---|
| macOS | Keychain | `scripts/setup-keychain.sh` | service name `last30days-<KEY>` |
| Linux / Unix (anywhere `pass` exists, incl. macOS) | [`pass`(1)](https://www.passwordstore.org/) | `scripts/setup-pass.sh` | pass path `last30days/<KEY>` |

```bash
# macOS Keychain
./scripts/setup-keychain.sh                 # interactive; --list / --delete KEY

# pass(1) — Linux/Unix analog
./scripts/setup-pass.sh                      # interactive; --list / --delete KEY
./scripts/setup-pass.sh SCRAPECREATORS_API_KEY   # just one key
```

The `pass` source honors `PASSWORD_STORE_DIR`. If your store organizes secrets
under a different prefix, point the loader at it with `LAST30DAYS_PASS_PREFIX`
(works from your `.env` too, and must match where `setup-pass.sh` wrote them).
The prefix is used verbatim, so keep the trailing separator:

```bash
export LAST30DAYS_PASS_PREFIX="secrets/last30days/"   # default: last30days/
```

Both sources cover exactly these keys: `OPENAI_API_KEY`, `XAI_API_KEY`, `GOOGLE_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_GENAI_API_KEY`, `SCRAPECREATORS_API_KEY`, `AUTH_TOKEN`, `CT0`, `XQUIK_API_KEY`, and `GITHUB_TOKEN`. Anything else must live in `.env` or the process environment.

#### Reusing existing macOS Keychain items

If you already have keys stored under another Keychain naming convention, you
can reference them without copying the secret by setting non-secret alias
metadata in `LAST30DAYS_KEYCHAIN_ALIASES`. The loader still checks
`last30days-<KEY>` first; aliases are fallback lookups only.

```bash
# ~/.config/last30days/.env
LAST30DAYS_KEYCHAIN_ALIASES={"XAI_API_KEY":{"account":"keychain-user","service":"existing-xai-api-key"},"GITHUB_TOKEN":"existing-github-token"}
```

Each JSON key must be one of the supported env-var names (`XAI_API_KEY`,
`SCRAPECREATORS_API_KEY`, `GITHUB_TOKEN`, etc). A string value means "use this
service name with the current user account"; an object can specify both
`account` and `service`. Lists are allowed for fallback order:

```bash
LAST30DAYS_KEYCHAIN_ALIASES={"XAI_API_KEY":[{"account":"keychain-user","service":"existing-xai-api-key"},{"service":"last-resort-xai"}]}
```

The alias value contains no secret material; it is safe to keep in `.env` as
configuration. The secret itself remains in its original Keychain item and is
read directly by the engine process.

Write `LAST30DAYS_KEYCHAIN_ALIASES` as a single-line JSON value in `.env`.
Multiline JSON formatting is not supported because `.env` files are parsed
line-by-line.

#### Disabling the Keychain source

Set `LAST30DAYS_SKIP_KEYCHAIN=1` to switch the Keychain source off entirely,
making the loader a no-op on macOS as well:

```bash
LAST30DAYS_SKIP_KEYCHAIN=1 uv run pytest tests/test_footer_nudge_suppression.py
```

Scope it to the tests that need a sealed Keychain rather than the whole suite:
the full run should keep exercising the positive-path Keychain tests.

This exists mainly for tests and reproductions that assert on
"no credentials configured" behaviour. Clearing `os.environ` and pointing
`LAST30DAYS_CONFIG_DIR` at nothing is not sufficient on a machine with items
stored under `last30days-<KEY>`: Keychain is a third, independent source, so a
stored key can quietly satisfy a lookup the test expected to fail — and the
test then fails on a contributor's Mac while passing in Linux CI, where the
loader already no-ops.

Unlike `LAST30DAYS_KEYCHAIN_ALIASES`, this switch is read from the process
environment only and never from a `.env` file. It gates a credential source
consulted *while* the config is being assembled, so a file-sourced value would
be read too late to take effect.

### Default source set (`LAST30DAYS_DEFAULT_SEARCH`)

By default the engine decides the source set per query (everything available, minus `EXCLUDE_SOURCES`). To pin a **fixed** source set for every run without passing `--search` each time — and without patching `SKILL.md`, which a release would overwrite — set:

```bash
LAST30DAYS_DEFAULT_SEARCH=reddit,x,github
```

Accepts the same comma-separated names as `--search` (`reddit`, `x`, `github`, `digg`, `arxiv`); the only alias is `xquik` → `x`, because Xquik is a backend of the single X source rather than a source of its own. Precedence: an explicit `--search` on the command line always wins; `LAST30DAYS_DEFAULT_SEARCH` applies only when the flag is omitted; when neither is set, per-query behavior is unchanged. `INCLUDE_SOURCES` / `EXCLUDE_SOURCES` keep their existing additive/subtractive roles on whichever set is selected.

### Audience register (`LAST30DAYS_REGISTER`)

The default standard brief stays balanced and byte-compatible with prior releases. To keep a named audience preset across runs, set one of the supported values:

```bash
LAST30DAYS_REGISTER=exec  # default | exec | dev | creator | eli5
```

An explicit `--register` wins over `LAST30DAYS_REGISTER`; the environment/config value defaults to `default`. Presets are intentionally named and bounded - arbitrary prompt or template files are not accepted. Existing `ELI5_MODE=true` configurations continue to resolve to the `eli5` register when no explicit register is selected, but new configuration should use `LAST30DAYS_REGISTER=eli5`.

---

## Reasoning provider priority

`/last30days` needs one reasoning model for planning + reranking when you don't pass `--plan` yourself. Auto-detect priority (set `LAST30DAYS_REASONING_PROVIDER=<name>` to pin one):

1. **Gemini** - `GOOGLE_API_KEY` / `GEMINI_API_KEY` / `GOOGLE_GENAI_API_KEY`
2. **OpenAI** - `OPENAI_API_KEY` only. Codex ChatGPT auth at `~/.codex/auth.json` is intentionally not used as an OpenAI provider credential.
3. **xAI** - `XAI_API_KEY`
4. **Local / deterministic** - always available, lowest quality

When you invoke `/last30days` from Claude Code, Codex, or Gemini, the host model **is** the reasoning provider for plan + synthesis - you don't need any of the keys above unless you also run the script headlessly (cron, CI, watchlist).

---

## X backend priority

There is one X source with interchangeable backends, tried in order until one returns items. Unpinned: **bird** (browser cookies via `AUTH_TOKEN`/`CT0` or a consented `FROM_BROWSER` read) → **xai** (`XAI_API_KEY`) → **xurl** (official X API v2 via the `xurl` CLI, OAuth2) → **xquik** (`XQUIK_API_KEY`). The **grok** CLI is opt-in only and never auto-selected. Pin a single backend (no failover) with `LAST30DAYS_X_BACKEND=<bird|xai|xurl|xquik|grok>`. Details, including the cookie-less-host guidance and the Grok caveats, are in the "X backend priority (bird first)" and "Grok CLI (opt-in backup)" notes under [API keys](#api-keys-env).

General web search has no engine backend in this fork. Blog, news, and docs coverage comes from the hosting agent's own web-search tool (Claude Code, Codex, Gemini CLI, ...) during pre-research and Step 2 supplements; headless runs simply have no web layer.

---

## Health check (`doctor`)

One command answers "what could be on, what's turned on, what's working, and what isn't" — a four-state audit (WORKING / TURNED ON - UNVERIFIED / NOT WORKING / COULD BE ON), one line per source, with a CLI-health block for sources that need a downloaded binary, indented backup/comment sub-lanes, the backend the next run will use (for chained sources), and an exact fix on anything that isn't working:

```bash
python3 skills/last30days/scripts/last30days.py doctor              # four-state audit (text)
python3 skills/last30days/scripts/last30days.py doctor --json       # machine contract
python3 skills/last30days/scripts/last30days.py doctor --cached     # serve the cached report while fresh
python3 skills/last30days/scripts/last30days.py doctor --postmortem # what actually broke on the last run
python3 skills/last30days/scripts/last30days.py doctor --probe      # bounded live test (free/CLI sources)
```

Slash-command form: `/last30days doctor`. Reporting problems is a successful run — the exit code is always 0, no browser cookies are read, and no secret values appear anywhere (key presence is booleans only). Backends within a chained source are probed sequentially with a 5-second budget per binary probe, so a chained source's worst-case check time is additive across its backends (only reached when several binaries hang at once).

`doctor --postmortem` reads the last run's `last-report.json` (any age, labeled) and reports what actually happened per source — Failed / Partial / Succeeded / Skipped, with details and fix hints — so a run that returned less than expected can be diagnosed after the fact. It makes no network calls.

**Network note:** plain `doctor` with a fresh run, `--cached`, and `--json` make **no** network calls. `doctor --probe` — and a plain `doctor` when there is **no** fresh run to learn from — run a **bounded** live test to verify WORKING instead of guessing. The probe is scoped to free HTTP endpoints (Reddit, GitHub, Epic Forums) plus the keyless Digg and arXiv CLIs; credit- or auth-gated lanes (X backends, the ScrapeCreators Reddit backup) are never probed, so no ScrapeCreators credits are spent and no auth rate limits are tripped. Each source is probed concurrently under a per-source deadline so a slow source can never hang the command.

Every live run writes its JSON result to `~/.config/last30days/doctor-cache.json` (beside `last-run.json`; honors `LAST30DAYS_CONFIG_DIR`). `doctor --cached` returns that stored report when it is younger than the TTL, and falls through to a live run — rewriting the cache — when it is stale, absent, or corrupt. The cache also self-invalidates on configuration change: the payload carries a schema stamp plus a fingerprint of non-secret config signals (which credentials are present as booleans, the `LAST30DAYS_X_BACKEND` / `LAST30DAYS_REDDIT_BACKEND` pin values, and `INCLUDE_SOURCES`), so adding or removing a key, changing a pin, or toggling an opt-in source makes the next `--cached` call run live — no raw secret ever enters the fingerprint or the file. Every report also carries `from_cache` (true/false) and `generated_at` (when the report was built), in the `--json` top level and as a final `generated: … (cached|live)` text line, so you can always tell how old a cached answer is. A failed cache write is never fatal — doctor prints a one-line stderr warning and continues. An explicit `doctor` without `--cached` always runs live and refreshes the cache.

| Var | Effect |
| --- | --- |
| `LAST30DAYS_DOCTOR_TTL` | Freshness window for `doctor --cached`, in **seconds**. Defaults to `900` (15 minutes). `0` makes every `--cached` call run live. |
| `LAST30DAYS_DOCTOR_PROBE_TIMEOUT` | Per-source deadline (**seconds**) for `doctor --probe` live checks. Defaults to `10`. Caps each concurrent probe so a slow source cannot hang the command. |
| `LAST30DAYS_X_BACKEND` | Pins the X backend (`bird` / `xai` / `xurl` / `xquik` / `grok`); doctor renders the pin and predicts "will use" accordingly. The unpinned auto chain is bird → xai → xurl → xquik (grok is opt-in only). Pin `grok` to enable it; a leftover `~/.grok/auth.json` is never auto-selected. |
| `AGENTCOOKIE` | `on` opts any host (incl. a MacBook) into the extra bird cookie lookups (agentcookie sidecar + live Chrome CDP); `off` disables the agentcookie sidecar reader. Unset uses host detection (Linux / Mac mini / Darwin sink get the extras). See "Extra bird cookie lookups" above. |
| `BROWSER_CDP_URL` | Explicit Chrome DevTools endpoint (e.g. `http://127.0.0.1:18800`) for the extra-host CDP cookie lookup. Preferred over the `18800` / `9222`+`$DISPLAY` defaults. Extra hosts only. |
| `LAST30DAYS_REDDIT_BACKEND` | `scrapecreators` makes ScrapeCreators the primary Reddit backend; doctor renders Reddit's conditional routing with the pin applied. |
| `LAST30DAYS_REDDIT_SC_MIN_ITEMS` | Integer thinness floor for ScrapeCreators Reddit **search** backfill. Default `0` = empty-only (free path keeps any non-empty result; no credit spend). Set above `0` to backfill when free yield is below that count; merged results dedupe by post id. Requires `SCRAPECREATORS_API_KEY`. Ignored when `LAST30DAYS_REDDIT_BACKEND=scrapecreators` (SC is already primary). |

### Strict exit for degraded runs

By default a research run exits `0` even when a source failed mid-run (rate-limited, auth-failed, unreachable, timeout, schema-drift) — the report still renders, with the failure annotated in the per-source footer and a partial-coverage warning. Wrappers that need to distinguish degraded coverage from success (cron briefs, CI, downstream agents) can opt in:

| Var | Effect |
| --- | --- |
| `LAST30DAYS_STRICT_EXIT` | Truthy (`1`/`true`/`yes`/`on`): the engine exits `3` when any source outcome is neither `ok`, `no-results`, nor `skipped-unconfigured`. A one-line `strict-exit: degraded sources: ...` note goes to stderr. Default (unset): exit `0`, unchanged behavior. |

Exit codes with the flag on: `0` clean run, `3` completed-but-degraded (report was produced), non-zero others unchanged (hard failures). Same hybrid pattern as `LAST30DAYS_DEBUG` — works shell-exported or in `.env`.

---

## Debug mode (`--debug`)

Add `--debug` to any run to emit verbose `[DEBUG]` log lines to stderr from the source modules (X API, HTTP, etc.). Helpful for diagnosing API errors or unexpected behavior.

**Always-on alternative:** set `LAST30DAYS_DEBUG=true` in your `.env` or export it from your shell. The flag still works as before; the env var is purely additive — works whether shell-exported or set in `.env`.

---

## Trend monitoring (`--store` + watchlist + briefings)

The default behavior - one slug-named file per topic, overwritten on rerun - is the snapshot mode. For continuous monitoring, the repo ships three components most users miss:

### `--store` flag

Adding `--store` to any run persists every finding to a SQLite database (default at `~/.local/share/last30days/research.db`). Findings dedupe on the `source_url` column (UNIQUE constraint), so the same URL across runs updates the existing row instead of creating a duplicate. The markdown file still saves; the SQLite is the time-series substrate.

**Always-on alternative:** set `LAST30DAYS_STORE=1` in your `.env` instead of remembering `--store` on every invocation. The flag still works as before; the env var is purely additive. Same hybrid pattern as `LAST30DAYS_DEBUG` — works whether shell-exported or in `.env`.

Relevant tables: `topics`, `research_runs`, `findings`, `settings`. Schema: [`scripts/store.py`](skills/last30days/scripts/store.py).

### Discovery topic queue (`LAST30DAYS_DISCOVERY_QUEUE`)

`--discover` runs remember what they surfaced (table `discovery_topics` in the same research.db). Re-surfaced topics get a `**Pipeline:**` line on their card ("surfaced 2nd time", "marked covered") so the discovery brief doubles as a podcast / X-article content pipeline. On by default for real runs; `--mock` runs never write. With `--save-dir`, queue rows land in that directory's scoped `research.db`, never the global one.

| Var | Effect |
| --- | --- |
| `LAST30DAYS_DISCOVERY_QUEUE` | Set to `off` to disable queue writes and card annotations. Any other value (or unset) keeps the queue on. Works shell-exported or in `.env`. |
| `LAST30DAYS_ENRICH_BUDGET_SECONDS` | Wall-clock budget (seconds) for the deep-tier per-topic research batch on the discovery resume leg (`--discover --judgments <file>`). Default `450`; unset/invalid/non-positive values fall back to it. The one-shot `--discover` path keeps its fixed quick-tier 240s budget regardless. Works shell-exported or in `.env`. |

Manage the queue from the engine CLI:

```bash
# Uncovered surfaced topics (name, domain, surface_count, last_surfaced, status)
python3 skills/last30days/scripts/last30days.py queue list

# Mark a topic done after you record the episode / publish the article.
# Requires the exact topic name; unknown names exit 2 instead of no-opping.
python3 skills/last30days/scripts/last30days.py queue cover "Gemma 4 chat templates"
```

Both respect `--save-dir` scoping.

### `watchlist.py` - recurring topics

[`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py) manages topics that should be researched on a schedule. Subcommands: `add`, `remove`, `list`, `run-one`, `run-all`, `config`. Built-in delivery to Slack incoming webhooks (`hooks.slack.com/...`) or any HTTPS endpoint, fired only when new findings appear.

Two-step flow (the watchlist holds the topic; an external scheduler invokes the run):

```bash
# 1. Add the topic to the watchlist
#    Default schedule daily 8am; --weekly switches to Mondays 8am
python3 scripts/watchlist.py add "british airways middle east" --weekly

# 2. Configure delivery and budget (optional)
python3 scripts/watchlist.py config delivery "https://hooks.slack.com/services/..."
python3 scripts/watchlist.py config budget 5.00

# 3. Trigger via cron / Task Scheduler / GitHub Actions
python3 scripts/watchlist.py run-one "british airways middle east"
# or run every enabled topic, gated by daily_budget
python3 scripts/watchlist.py run-all
```

The schedule field stored on each topic is metadata - the actual cron / Task Scheduler invocation is your responsibility. Watchlist runs hardcode `--quick` and `--lookback-days 90` when spawning the underlying engine.

### `briefing.py` - daily / weekly digests

[`scripts/briefing.py`](skills/last30days/scripts/briefing.py) reads the SQLite store and emits structured data the agent then synthesizes into prose. Modes: `generate` (daily), `generate --weekly`, `show [--date DATE]` (display a saved briefing). Briefs save to `~/.local/share/last30days/briefs/`.

### Recommended cadence pattern

| Step | Cadence | Command |
|---|---|---|
| Baseline | one-time per topic | `/last30days "<topic>" --days=30 --store` |
| Add to watchlist | one-time per topic | `python3 scripts/watchlist.py add "<topic>" --weekly` |
| Recurring run | daily or weekly (external scheduler) | `python3 scripts/watchlist.py run-all` |
| Digest | weekly | `python3 scripts/briefing.py generate --weekly` |

---

## Per-client patterns

The skill is built to flex around different client environments. Four patterns that compose well:

**Codex note:** the repository includes `.codex-plugin/plugin.json` so Codex can treat the existing
`skills/last30days/SKILL.md` tree as plugin metadata without maintaining a separate Codex copy.
The Codex marketplace catalog points at the repository root URL: Codex clones the repo, reads the
root `.codex-plugin/plugin.json`, and loads skills from `./skills/`. The Agent Skills install
command documented in the README remains the broadest cross-host path.

**Grok note:** the repository includes `.grok-plugin/plugin.json` and `.grok-plugin/marketplace.json`
so xAI's Grok Build CLI (`grok`) can install last30days as a native plugin. Grok also reads the
Claude Code manifests for compatibility; the native pair is the first-class lane. The Grok
marketplace catalog uses a bare Git URL source (no commit pin) so `grok plugin marketplace add
mvanhorn/last30days-skill` tracks HEAD — the same pattern as the Codex catalog. `npx skills add`
remains a valid cross-host fallback.

### 1. Trusted per-client `.claude/last30days.env`

When each client has its own working directory, drop a `.claude/last30days.env` into the client folder and opt in with `LAST30DAYS_TRUST_PROJECT_CONFIG=1` from your shell or global `~/.config/last30days/.env`. The skill loads the project file only after that trust signal. Typical contents:

```bash
LAST30DAYS_MEMORY_DIR=C:\Users\<you>\Clients\acme\Research\Last30Days
SCRAPECREATORS_API_KEY=<acme-scoped-key-or-shared>
LAST30DAYS_REDDIT_SC_MIN_ITEMS=5
EXCLUDE_SOURCES=arxiv
```

`cd` into the client folder, run `/last30days <topic>` as normal, no wrappers. Combine with `--save-suffix=<client-slug>` per run if you also need to differentiate filenames within that folder.

### 2. Per-client save dir + suffix wrapper

For workflows where you don't `cd` into a client folder (running from anywhere, scripted batches), a tiny shell function isolates each client's research without engine changes.

PowerShell example:

```powershell
function Run-L30D-Client {
    param([string]$ClientSlug, [Parameter(ValueFromRemainingArguments=$true)]$Args)
    $env:LAST30DAYS_MEMORY_DIR = "C:\Users\$env:USERNAME\Clients\$ClientSlug\Research\Last30Days"
    /last30days @Args --save-suffix=$ClientSlug
}
# Usage: Run-L30D-Client acme "british airways middle east"
```

Bash example:

```bash
l30d-client() {
    local client=$1; shift
    LAST30DAYS_MEMORY_DIR="$HOME/Clients/$client/Research/Last30Days" \
        /last30days "$@" --save-suffix="$client"
}
# Usage: l30d-client acme "british airways middle east"
```

### 3. Custom category-peer subreddits

[`scripts/lib/categories.py`](skills/last30days/scripts/lib/categories.py) holds a table of `(category_id, trigger_keywords, peer_subreddits)`. If a client lives in a vertical that isn't covered (legal-tech, real-estate-tech, B2B HR SaaS), add a row. Pure data, no logic.

Section 2a of `SKILL.md` documents the merging rule the skill applies when your topic matches a category.

### 4. Pre-built `--competitors-plan` JSON

For competitor-vs-comparisons that recur, a pre-written JSON skeleton per client industry saves real time:

```json
{
  "Competitor B": {
    "x_handle": "competitor_b_handle",
    "subreddits": ["sub1", "sub2"],
    "github_user": "competitor-b-org",
    "context": "Founded 2019, focused on ..."
  },
  "Competitor C": { ... }
}
```

Pass as `--competitors-plan @client/competitors-plan.json` (or as a string). See `SKILL.md` section "If QUERY_TYPE = COMPARISON" for the full schema.

---

## Beta channel

Experimental customizations live on a private companion repo (`mvanhorn/last30days-skill-private`) installed as `/last30days-beta`. Never ship beta-only changes to the public marketplace without a review PR against the public repo. Workflow guide: `BETA.md` in the private repo.

This is the right home for client-specific changes you don't intend to upstream - custom category rows, internal subreddit lists, per-vertical plan templates.

---

## Cross-references

- The CLI flag surface: `python3 scripts/last30days.py --help`
- The skill contract (voice, LAWs, pre-flight protocol): [`skills/last30days/SKILL.md`](skills/last30days/SKILL.md)
- Shared package vocabulary and engine/harness terminology: [`CONCEPTS.md`](CONCEPTS.md)
- Contributor guidance: [`CONTRIBUTORS.md`](CONTRIBUTORS.md)
