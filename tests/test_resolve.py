import io
import unittest
from contextlib import redirect_stderr

from lib import resolve
from lib.resolve import MAX_SUBS, _merge_category_peers


class TestCanonicalizeGithubRepos(unittest.TestCase):
    def test_rewrites_integration_repo_to_canonical_product(self):
        repos = ["openai/codex", "anthropics/claude-code-action"]
        result = resolve.canonicalize_github_repos("claude code vs codex", repos, cap=None)
        self.assertEqual(result, ["openai/codex", "anthropics/claude-code"])

    def test_preserves_action_repo_when_topic_intends_action(self):
        repos = ["anthropics/claude-code-action", "openai/codex"]
        result = resolve.canonicalize_github_repos("claude code action setup", repos, cap=None)
        self.assertIn("anthropics/claude-code-action", result)
        self.assertNotIn("anthropics/claude-code", result)

    def test_dedupes_case_insensitive_after_canonicalization(self):
        repos = ["Anthropics/Claude-Code-Action", "anthropics/claude-code"]
        result = resolve.canonicalize_github_repos("claude code", repos, cap=None)
        self.assertEqual(result, ["Anthropics/Claude-Code"])


class MergeCategoryPeersHappyPath(unittest.TestCase):
    def test_image_gen_topic_appends_peers(self):
        merged, category = _merge_category_peers(
            "Prompting GPT Image 2",
            ["OpenAI", "ChatGPT", "singularity"],
        )
        self.assertEqual(category, "ai_image_generation")
        self.assertIn("OpenAI", merged)
        self.assertIn("ChatGPT", merged)
        self.assertIn("singularity", merged)
        self.assertIn("StableDiffusion", merged)
        self.assertIn("midjourney", merged)
        self.assertIn("dalle2", merged)

    def test_preserves_websearch_order_then_appends_peers(self):
        merged, _ = _merge_category_peers(
            "Prompting GPT Image 2",
            ["OpenAI", "ChatGPT"],
        )
        self.assertEqual(merged[0], "OpenAI")
        self.assertEqual(merged[1], "ChatGPT")
        self.assertEqual(merged[2], "StableDiffusion")

    def test_emits_stderr_log_when_peers_added(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            _merge_category_peers(
                "Prompting GPT Image 2",
                ["OpenAI", "ChatGPT"],
            )
        output = buf.getvalue()
        self.assertIn("Matched category=ai_image_generation", output)
        self.assertIn("StableDiffusion", output)


class MergeCategoryPeersDedupe(unittest.TestCase):
    def test_peer_already_in_websearch_not_duplicated(self):
        merged, _ = _merge_category_peers(
            "midjourney v7 prompts",
            ["midjourney", "aiArt"],
        )
        self.assertEqual(
            sum(1 for s in merged if s.lower() == "midjourney"),
            1,
        )

    def test_dedupe_is_case_insensitive(self):
        merged, _ = _merge_category_peers(
            "Prompting GPT Image 2",
            ["STABLEDIFFUSION"],
        )
        lower = [s.lower() for s in merged]
        self.assertEqual(lower.count("stablediffusion"), 1)

    def test_no_log_when_all_peers_already_present(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            _merge_category_peers(
                "Prompting GPT Image 2",
                [
                    "StableDiffusion",
                    "midjourney",
                    "dalle2",
                    "aiArt",
                    "PromptEngineering",
                    "MediaSynthesis",
                ],
            )
        self.assertNotIn("Matched category=", buf.getvalue())


class MergeCategoryPeersEdgeCases(unittest.TestCase):
    def test_topic_with_no_category_returns_unchanged(self):
        merged, category = _merge_category_peers(
            "Kanye West",
            ["Kanye", "hiphopheads"],
        )
        self.assertIsNone(category)
        self.assertEqual(merged, ["Kanye", "hiphopheads"])

    def test_empty_subreddit_list_with_category_still_adds_peers(self):
        merged, category = _merge_category_peers("Prompting GPT Image 2", [])
        self.assertEqual(category, "ai_image_generation")
        self.assertIn("StableDiffusion", merged)

    def test_empty_topic_returns_unchanged(self):
        merged, category = _merge_category_peers("", ["foo", "bar"])
        self.assertIsNone(category)
        self.assertEqual(merged, ["foo", "bar"])

    def test_none_topic_returns_unchanged(self):
        merged, category = _merge_category_peers(None, ["foo", "bar"])
        self.assertIsNone(category)
        self.assertEqual(merged, ["foo", "bar"])

    def test_no_log_when_topic_has_no_category(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            _merge_category_peers("Kanye West", ["Kanye"])
        self.assertNotIn("Matched category=", buf.getvalue())


class MergeCategoryPeersCap(unittest.TestCase):
    def test_cap_is_enforced_at_max_subs(self):
        websearch_subs = [f"Sub{i}" for i in range(9)]
        merged, _ = _merge_category_peers(
            "Prompting GPT Image 2",
            websearch_subs,
        )
        self.assertEqual(len(merged), MAX_SUBS)
        for s in websearch_subs:
            self.assertIn(s, merged)
        self.assertEqual(len(merged) - len(websearch_subs), 1)
        self.assertEqual(merged[9], "StableDiffusion")

    def test_cap_preserves_highest_priority_peer_when_trimming(self):
        websearch_subs = [f"Sub{i}" for i in range(8)]
        merged, _ = _merge_category_peers(
            "Prompting GPT Image 2",
            websearch_subs,
        )
        self.assertEqual(len(merged), MAX_SUBS)
        self.assertEqual(merged[8], "StableDiffusion")
        self.assertEqual(merged[9], "midjourney")


class MergeCategoryPeersClassificationFailure(unittest.TestCase):
    def test_classification_error_returns_unwidened_list_and_logs(self):
        original = resolve.categories.detect_category

        def boom(_topic):
            raise RuntimeError("synthetic classifier failure")

        resolve.categories.detect_category = boom
        try:
            buf = io.StringIO()
            with redirect_stderr(buf):
                merged, category = _merge_category_peers(
                    "Prompting GPT Image 2",
                    ["OpenAI"],
                )
            self.assertEqual(merged, ["OpenAI"])
            self.assertIsNone(category)
            self.assertIn("Category classification failed", buf.getvalue())
        finally:
            resolve.categories.detect_category = original


if __name__ == "__main__":
    unittest.main()
