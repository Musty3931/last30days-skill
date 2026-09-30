# Epic Forums recordings

Captured anonymously on 2026-09-30 using `lib/http.py` with a descriptive User-Agent and at least one second between requests. Each file preserves the exact decoded response, request URL, and UTC capture time; no cookies or credentials were sent.

- `search_path_tracer_glass.json`: 2 topics, 2 matched posts, after 2026-08-31 and before 2026-10-01.
- `search_lumen_reflections.json`: 22 topics, 22 matched posts in the same window.
- `topic.json`: topic 2832702, “Pathtracer Substrate Transmission Issues”, including the August 21 Epic staff replies. These test authority detection with an explicit wider window; they are not current 30-day evidence.

These are single-page API responses, not exhaustive forum exports. Tests use the recorded window explicitly rather than treating the recordings as current forever. Category lookup and error/rate-limit cases use synthetic responses in tests.
