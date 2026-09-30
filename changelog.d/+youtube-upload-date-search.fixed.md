YouTube searches now use an upload-date filter matched to the requested window
before verifying individual video dates, with a bounded `ytsearchN:` fallback
for empty or unsupported URL searches. Quick and default runs inspect more
candidate metadata, while bot checks and rate limits remain visible failures.
