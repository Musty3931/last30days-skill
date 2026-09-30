# YouTube fixtures

`metadata-bot-check.stderr` is unmodified stderr recorded on the Azure worker
on 2026-09-30 with yt-dlp 2026.08.19. An anonymous flat search for
`Unreal Engine 5.8 path tracer glass` returned 12 videos, but extracting metadata
for its first result (`MP3dVMCW3T8`) with `--skip-download
--ignore-no-formats-error --dump-single-json --no-playlist` exited **0** with a
bot-check warning and partial JSON. Global config and browser cookies were
disabled with `--ignore-config --no-cookies-from-browser`.

The regression tests use this recording directly and substitute explicit bot/429
wording variants to cover other yt-dlp versions; those variants are synthetic.
`search-flat.jsonl` contains the 12 flat entries from that same worker recording
(`/tmp/l30-mrg1-youtube-search.jsonl`), reduced to the original public `id`,
`title`, `url`, `channel`, and `view_count` fields. The recorded entries have no
upload dates. Tests replay these entries for both URL search and the `ytsearchN:`
fallback; per-video dates used to test window enforcement are synthetic.

`search-windows.json` records the owner's 2026-09-30 Mac observation of the
month-filter URL (`sp=EgIIBA%253D%253D`) and adds derived boundary cases for the
other windows. Those other cases are protocol expectations, not live recordings:
base64 decodes to protobuf bytes `12 02 08 <enum>` with hour/today/week/month/year
values 1/2/3/4/5. The day-granularity engine never selects hour. The tests verify
the bytes and double-encoded padding; live filtered results still require the
owner's Mac because this worker is bot-gated. The recording does not establish
that every recent video appears in YouTube's approximate date buckets.

All other video JSON used by the tests is synthetic. No cookies or credentials
are used. `captions.vtt` supplies caption parsing and transcript budget examples.
