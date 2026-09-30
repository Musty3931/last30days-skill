# MRG1 integration validation

Branch: `l30-MRG1`.

Merged Epic Forums (`l30-EF1`, `721ced2`) first, then YouTube (`l30-YT1`,
`98deba3`). Resolved all 15 conflicted files by retaining both lanes: source
registries, availability and dispatch, strict date normalization, engagement
fields, doctor records, planner priorities, render footer entries, docs, and
onboarding contracts. The original Epic Forums and three YouTube test files
are unchanged from their lane commits. New integration tests exercise both
sources together at quick/default/deep depths and through serialization.

Audited non-conflicting source lists too: the planner runtime instructions,
welcome prose/modal, configuration source filters, plugin descriptions, and
session hook now include both sources. The hook reuses engine gates for these
lanes without running yt-dlp or reading cookies. No versions were bumped and
no coverage threshold was changed.

## YouTube failure diagnosis

A direct anonymous yt-dlp search returned 12 entries on this Azure worker.
Metadata extraction with `--ignore-no-formats-error` exited **0** while warning
`Sign in to confirm you’re not a bot` and returning partial metadata. This was
being treated as a successful fetch, and old/undated rows then yielded an
incorrect clean-empty outcome.

The adapter now checks stderr independently of exit status, preserves the
refusal before help links/trailing warnings can truncate it, and propagates
bot/429 refusals as `rate-limited` and other extraction failures as errors.
Partial valid results are retained with degraded coverage. Clean empty search
responses remain `no-results`; refused caption fetches do not become absent
caption tracks. Tests include the recorded Azure stderr, explicit synthetic
wording variants, nonzero/blank errors, partial metadata, and the final report's
source status and synthesis warning.

## Live smoke (2026-09-30 UTC)

One real engine invocation, development/fallback mode:

```bash
LAST30DAYS_CONFIG_DIR='' \
LAST30DAYS_YTDLP='/tmp/yt1-tools/bin/uvx yt-dlp' \
AGENTCOOKIE=off FROM_BROWSER=off \
/tmp/ef1-uv/bin/uv run python skills/last30days/scripts/last30days.py \
  'Unreal Engine 5.8 path tracer glass' --search epicforums,youtube \
  --emit compact --save-dir /tmp/l30-mrg1-smoke
```

Completed in 18.1 seconds, engine exit status 0 (default non-strict mode),
using the 2026-08-31 through 2026-09-30 date window. The two-source filter
isolates the lanes under integration. No cookies or API keys were used.

Exact footer:

```text
├─ 🎮 Epic Forums: 4 topics │ 1 likes │ 75 views
```

Exact YouTube coverage line:

```text
- YouTube: 0 items (rate-limited: WARNING: [youtube] Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies for the authentication. See https://githu... (run doctor for fixes))
```

The report also states: "Do not interpret a failed source as no discussion on
that source." Three returned Epic item titles:

1. [UE 5.8.3][macOS ARM64][MetaHuman] Body DNA export crashes — Pair != nullptr assertion in Map.h.inl:638; Head DNA succeeds
2. Incorrect Rendering results with Negative Scale When Using Packaged Level Actor / FastGeo ISMTransformer
3. [Free][Beta] WCW Memory Monitor — live RAM / VRAM / LLM / TextureGroup HUD for UE 5.6–5.8

The four retained Epic topics skew toward broader UE 5.8 discussions rather
than specifically path-traced glass; the smoke demonstrates transport,
normalization and integration, not the relevance of every match.

Raw engine output: `/tmp/l30-mrg1-smoke.stdout` and
`/tmp/l30-mrg1-smoke.stderr`; saved raw brief:
`/tmp/l30-mrg1-smoke/unreal-engine-5-8-path-tracer-glass-raw.md`.
Recorded yt-dlp stderr is committed at
`tests/fixtures/youtube/metadata-bot-check.stderr`.

## Validation

Final full-suite result (exit 0): **3,175 passed, 11 skipped, 44 subtests
passed**, in 300.68 seconds. Coverage: **89.67%**, above the unchanged **84%**
required floor.

The final run uses `uv run pytest --cov` (the repository's runner and CI
coverage gate). Local uv executable: `/tmp/ef1-uv/bin/uv`.
Full log: `/tmp/l30-mrg1-tests-final.log`.

Focused lane checks passed (155 tests before subsequent integration additions).
Onboarding/plugin/hook/config contracts passed (71 tests); merged-source and
hook tests passed (15 tests). `git diff --check` passes.

The Grok CLI is unavailable on this worker, so native `grok plugin validate .`
could not run; the repository's plugin-contract tests pass in the full suite.
