# Eval fixtures

Empty on purpose. The upstream fixtures were recorded against web search
(`grounding`) and Hacker News, both removed from this fork, so nothing could
replay. Record new fixtures for the surviving sources (reddit, x, github, digg,
arxiv) with the hidden `--record-fixtures tests/eval/fixtures/<name>` flag and
add a `manifest.json` per docs/reference/eval.md; the tests in
`tests/eval/test_eval_harness.py` skip while this directory has no manifests.
