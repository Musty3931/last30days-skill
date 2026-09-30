# last30days (seven-source fork) - install and setup

You are Claude Code, helping a teammate install Shane Barnard's fork of the `last30days` research skill. Walk through every step below in order, run the commands yourself where a command is shown, and confirm each check before moving on. Stop and ask the user only where a step says so.

## What this is

`/last30days <topic>` researches what people said about a topic in the last 30 days across exactly seven sources: Reddit, X, GitHub, Digg, arXiv, Epic Forums, and YouTube. It is a stripped-down fork of `mvanhorn/last30days-skill`; every other source (Hacker News, web search, Perplexity, and about twenty more) has been removed on purpose. Do not try to enable them.

- Repo: https://github.com/Musty3931/last30days-skill
- Plugin: `last30days@last30days-skill`
- Free, no keys: Reddit and GitHub work immediately; Epic Forums works for Unreal/Epic topics. Digg and arXiv need two small CLIs; YouTube needs `yt-dlp`. X needs a browser login or an API key.

## Step 1 - prerequisites (macOS)

Check each and install what is missing. Run:

```bash
python3 -c 'import sys; print(sys.version.split()[0])'   # need 3.12+
node --version                                             # any recent Node; the X client is Node
go version                                                 # needed to build the Digg/arXiv CLIs
which gh && gh auth status 2>&1 | head -2                  # optional, raises GitHub rate limits
```

Install anything missing with Homebrew:

```bash
brew install python@3.12   # only if python3 is below 3.12
brew install node          # only if missing
brew install go            # required for Step 4
brew install gh            # optional
brew install yt-dlp        # enables YouTube search and captions
```

Also confirm `~/.local/bin` is on the user's PATH, because the Digg and arXiv binaries land there:

```bash
echo "$PATH" | tr ':' '\n' | grep -c '\.local/bin'   # must print 1 or more
```

If it prints 0, add `export PATH="$HOME/.local/bin:$PATH"` to the user's shell profile (`~/.zshrc`) and tell them to open a new terminal afterwards.

## Step 2 - install the plugin

Tell the user to run these two slash commands in Claude Code (they are typed by the user, not run in Bash):

```
/plugin marketplace add Musty3931/last30days-skill
/plugin install last30days@last30days-skill
```

Then they must start a new Claude Code session. Plugins load at startup; the skill is not available in the session that installed it. Verify from the new session:

```bash
claude plugin list 2>&1 | grep -A3 last30days
```

Expected: `last30days@last30days-skill`, `Status: enabled`.

## Step 3 - first run and the setup wizard

Tell the user to type `/last30days Claude Code` (any topic works). The first run opens a short setup wizard inside Claude Code. Guide them to these answers:

1. **How would you like to set up?** Choose **Auto setup**.
2. **Include X?** Choose **Yes - X cookies + all CLIs** if they are logged into x.com in Chrome, Brave, Edge, Firefox, or Safari on this Mac. macOS will show a Keychain prompt for the browser's cookie store once; they should click **Always Allow**. Cookies are read live and never written to disk.
3. **ScrapeCreators Reddit backup?** Optional. Reddit already works free. Skip unless they want a paid backup lane for empty Reddit results.

The wizard writes `~/.config/last30days/.env` and installs the Digg and arXiv CLIs. If Go was missing when it ran, the CLI installs fail with "Go is required"; fix that in Step 4.

## Step 4 - Digg and arXiv CLIs (if the wizard could not install them)

```bash
npx -y @mvanhorn/printing-press-library@0.1.16 install digg --cli-only
npx -y @mvanhorn/printing-press-library@0.1.16 install arxiv --cli-only
which digg-pp-cli arxiv-pp-cli
```

Both paths should resolve under `~/.local/bin`.

## Step 5 - make X stick (Chrome, Brave, or Edge users)

The wizard finds cookies in Chromium browsers but deliberately does not pin them, so later runs skip X. Fix it once. Ask the user which browser holds their x.com login, then append the pin (use `chrome`, `brave`, or `edge`):

```bash
printf 'FROM_BROWSER=brave\nBROWSER_CONSENT=true\n' >> ~/.config/last30days/.env
```

Firefox and Safari users skip this step; the wizard pins those automatically.

No browser login? Alternatives, any one of them added to `~/.config/last30days/.env`:

- `XAI_API_KEY=...` from api.x.ai (paid)
- `XQUIK_API_KEY=...` (paid)
- `AUTH_TOKEN=...` and `CT0=...` copied from x.com cookies by hand

## Step 6 - verify all seven sources

Run the engine's diagnose from the installed plugin:

```bash
SKILL_DIR=$(find "$HOME/.claude/plugins/cache/last30days-skill/last30days" -maxdepth 1 -mindepth 1 -type d | sort -V | tail -1)/skills/last30days
python3 "$SKILL_DIR/scripts/last30days.py" --diagnose 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['available_sources'])"
```

Expected with all dependencies available: `['reddit', 'x', 'github', 'epicforums', 'digg', 'arxiv', 'youtube']`. If `x` is missing, redo Step 5. If `digg` or `arxiv` is missing, redo Step 4 and re-check PATH. If `youtube` is missing, check that `yt-dlp` resolves on the agent subprocess PATH, or set `LAST30DAYS_YTDLP`. Epic Forums needs no install and is selected automatically for Epic/Unreal topics; `LAST30DAYS_EPICFORUMS=off` disables it.

Then run a real query to prove the live path:

```bash
python3 "$SKILL_DIR/scripts/last30days.py" "Claude Code" --quick --emit=compact 2>&1 | grep -E "Research complete|Sources:"
```

Expected: a `Research complete` line with non-zero counts for Reddit and X.

## Step 7 - how to use it well

- Type `/last30days <topic>` and let the skill run. It plans queries itself, resolves X handles, subreddits, and GitHub repos with web search, then synthesizes.
- Give it a specific topic. Ambiguous names ("Cura AI", "Loom") collide with unrelated words on X and Reddit; add the company or domain ("Loom screen recording").
- Person topics work best when the skill can find their X handle and GitHub user. Product topics work best with a GitHub repo.
- `/last30days trending` or `/last30days what's hot in AI agents` runs discovery mode across Reddit, Digg, and X.
- "A vs B" topics run a comparison. The skill names the peers itself; there is no automatic competitor discovery.
- `LAST30DAYS_MEMORY_DIR` defaults to `~/Documents/Last30Days`; set it to change where results save. Ask "search my library for X" to query past runs.

## Known limits of this fork

- No general web search inside the engine. Blog and news context comes from Claude's own web search during the run.
- arXiv only returns papers submitted inside the 30-day window, so many topics legitimately show zero papers.
- YouTube can refuse requests from datacenter IPs even when `yt-dlp` is installed. A bot-check or rate-limit status means coverage is unavailable; it is not evidence that no videos exist. Captions require no cookies or audio downloads.
- X depends on the browser session. If the user logs out of x.com, X silently drops out of runs until they log back in.
- Updates: `claude plugin update last30days@last30days-skill`. The fork does not track upstream automatically.

## Where to look when something breaks

- `python3 "$SKILL_DIR/scripts/last30days.py" doctor` prints a per-source health audit with fix hints.
- `~/.config/last30days/.env` holds all configuration. Never overwrite it; append.
- Full configuration reference: `CONFIGURATION.md` in the repo. Skill behavior: `skills/last30days/SKILL.md`.
