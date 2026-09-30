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
All video JSON used by the tests is synthetic. No cookies or credentials are used.

`captions.vtt` supplies caption parsing and transcript budget examples.
