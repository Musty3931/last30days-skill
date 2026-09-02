import math
import unittest

from lib import schema, signals


class SignalsV3Tests(unittest.TestCase):
    def test_reddit_engagement_uses_source_specific_formula(self):
        item = schema.SourceItem(
            item_id="r1",
            source="reddit",
            title="Title",
            body="Body",
            url="https://example.com",
            engagement={"score": 99, "num_comments": 20, "upvote_ratio": 0.8},
            metadata={"top_comments": [{"score": 10}]},
        )
        expected = (
            0.50 * math.log1p(99)
            + 0.35 * math.log1p(20)
            + 0.05 * (0.8 * 10.0)
            + 0.10 * math.log1p(10)
        )
        self.assertAlmostEqual(expected, signals.engagement_raw(item))

    def test_source_without_weights_uses_generic_fallback(self):
        item = schema.SourceItem(
            item_id="g1",
            source="github",
            title="Title",
            body="Body",
            url="https://github.com/owner/repo",
            engagement={"stars": 10, "forks": 100},
        )
        expected = (math.log1p(10) + math.log1p(100)) / 2
        self.assertAlmostEqual(expected, signals.engagement_raw(item))

    def test_digg_engagement_uses_cluster_fields(self):
        item = schema.SourceItem(
            item_id="d1",
            source="digg",
            title="Title",
            body="Body",
            url="https://digg.com/ai/cluster/1",
            engagement={"postCount": 12, "uniqueAuthors": 8, "rank_score": 3},
        )
        expected = (
            0.40 * math.log1p(12)
            + 0.30 * math.log1p(8)
            + 0.30 * math.log1p(3)
        )
        self.assertAlmostEqual(expected, signals.engagement_raw(item))

    def test_source_quality_covers_surviving_sources(self):
        self.assertEqual(set(signals.SOURCE_QUALITY), {"reddit", "x", "digg", "arxiv"})
        self.assertEqual(signals.source_quality("github"), 0.6)

    def test_annotate_stream_sorts_by_source_specific_reddit_engagement(self):
        higher = schema.SourceItem(
            item_id="r-high",
            source="reddit",
            title="High signal",
            body="claude code skill",
            url="https://example.com/high",
            published_at="2026-03-15",
            engagement={"score": 120, "num_comments": 40, "upvote_ratio": 0.9},
            metadata={"top_comments": [{"score": 15}]},
        )
        lower = schema.SourceItem(
            item_id="r-low",
            source="reddit",
            title="Lower signal",
            body="claude code skill",
            url="https://example.com/low",
            published_at="2026-03-15",
            engagement={"score": 4, "num_comments": 1, "upvote_ratio": 0.5},
            metadata={"top_comments": [{"score": 1}]},
        )
        ranked = signals.annotate_stream(
            [lower, higher],
            ranking_query="What recent evidence matters for claude code skill?",
            freshness_mode="balanced_recent",
        )
        self.assertEqual(["r-high", "r-low"], [item.item_id for item in ranked])

    def test_local_relevance_dominates_over_high_engagement_noise(self):
        relevant = schema.SourceItem(
            item_id="relevant",
            source="reddit",
            title="Deploy to Fly.io with MCP in 60 seconds",
            body="Deploy to Fly.io guide with concrete steps.",
            url="https://example.com/relevant",
            published_at="2026-03-15",
            engagement={"score": 2, "num_comments": 0, "upvote_ratio": 0.8},
            metadata={"top_comments": []},
        )
        noisy = schema.SourceItem(
            item_id="noisy",
            source="reddit",
            title="BATTLEFIELD 6 GAME UPDATE 1.2.2.0",
            body="Patch notes and gameplay discussion.",
            url="https://example.com/noisy",
            published_at="2026-03-15",
            engagement={"score": 5000, "num_comments": 1200, "upvote_ratio": 0.95},
            metadata={"top_comments": [{"score": 400}]},
        )
        ranked = signals.annotate_stream(
            [noisy, relevant],
            ranking_query="How do I deploy on Fly.io?",
            freshness_mode="evergreen_ok",
        )
        self.assertEqual("relevant", ranked[0].item_id)

    def test_prune_low_relevance_keeps_stronger_matches(self):
        strong = schema.SourceItem(
            item_id="strong",
            source="reddit",
            title="Deploy to Fly.io",
            body="Step-by-step Fly.io deploy guide.",
            url="https://example.com/strong",
            local_relevance=0.3,
        )
        weak = schema.SourceItem(
            item_id="weak",
            source="reddit",
            title="Battlefield update",
            body="Patch notes.",
            url="https://example.com/weak",
            local_relevance=0.0,
        )
        pruned = signals.prune_low_relevance([strong, weak], minimum=0.1)
        self.assertEqual(["strong"], [item.item_id for item in pruned])

    def test_prune_low_relevance_falls_back_when_all_are_weak(self):
        weak = schema.SourceItem(
            item_id="weak",
            source="reddit",
            title="Generic post",
            body="Generic body.",
            url="https://example.com/weak",
            metadata={"local_relevance": 0.02},
        )
        pruned = signals.prune_low_relevance([weak], minimum=0.1)
        self.assertEqual(["weak"], [item.item_id for item in pruned])

    # -- Iteration 4: Missing engagement formula tests --

    def test_x_engagement_dominant_weight(self):
        """X: likes at 0.55 should dominate over quotes at 0.05."""
        item = schema.SourceItem(
            item_id="x1", source="x", title="T", body="B",
            url="https://example.com",
            engagement={"likes": 100, "reposts": 100, "replies": 100, "quotes": 100},
        )
        result = signals.engagement_raw(item)
        self.assertIsNotNone(result)
        expected = (
            0.55 * math.log1p(100)
            + 0.25 * math.log1p(100)
            + 0.15 * math.log1p(100)
            + 0.05 * math.log1p(100)
        )
        self.assertAlmostEqual(expected, result)

    def test_x_engagement_all_zero_returns_none(self):
        item = schema.SourceItem(
            item_id="x2", source="x", title="T", body="B",
            url="https://example.com",
            engagement={"likes": 0, "reposts": 0, "replies": 0, "quotes": 0},
        )
        self.assertIsNone(signals.engagement_raw(item))

    def test_x_engagement_missing_fields(self):
        """Missing fields default to 0, no crash."""
        item = schema.SourceItem(
            item_id="x3", source="x", title="T", body="B",
            url="https://example.com",
            engagement={"likes": 50},
        )
        result = signals.engagement_raw(item)
        self.assertIsNotNone(result)
        expected = 0.55 * math.log1p(50)
        self.assertAlmostEqual(expected, result)

    # -- Fix 5: Rebalance engagement weight --

    def test_engagement_weight_meaningful_for_social_ranking(self):
        """Engagement must have enough weight to differentiate otherwise-equal items."""
        high_engagement = schema.SourceItem(
            item_id="viral",
            source="x",
            title="Trending topic discussion",
            body="Popular social post",
            url="https://example.com/viral",
            published_at="2026-03-15",
            engagement={"likes": 50000, "reposts": 5000, "replies": 2000, "quotes": 500},
        )
        low_engagement = schema.SourceItem(
            item_id="quiet",
            source="x",
            title="Trending topic discussion",
            body="Popular social post",
            url="https://example.com/quiet",
            published_at="2026-03-15",
            engagement={"likes": 10, "reposts": 1, "replies": 0, "quotes": 0},
        )
        ranked = signals.annotate_stream(
            [low_engagement, high_engagement],
            ranking_query="trending topic discussion",
            freshness_mode="balanced_recent",
        )
        high_score = ranked[0].local_rank_score
        low_score = ranked[1].local_rank_score
        gap = high_score - low_score
        # With 10% engagement weight, the gap should be >= 0.06
        # With 5% weight, gap would be ~0.04
        self.assertGreaterEqual(gap, 0.06,
                                f"Engagement gap should be >= 0.06 with 10% weight, got {gap:.4f}")

    # -- Fix 4: Lower prune threshold for social media --

    def test_prune_keeps_social_items_above_003(self):
        """Social media items with low but non-trivial relevance should survive pruning."""
        social = schema.SourceItem(
            item_id="social",
            source="x",
            title="Viral tweet about topic",
            body="Short social post",
            url="https://example.com/social",
            metadata={"local_relevance": 0.05},
        )
        strong = schema.SourceItem(
            item_id="strong",
            source="github",
            title="Detailed article about topic",
            body="In-depth analysis",
            url="https://example.com/strong",
            metadata={"local_relevance": 0.4},
        )
        pruned = signals.prune_low_relevance([strong, social])
        ids = [item.item_id for item in pruned]
        self.assertIn("social", ids, "Item with relevance 0.05 should survive pruning")
        self.assertIn("strong", ids)

    # -- Engagement never manufactures relevance --

    def test_high_engagement_does_not_create_relevance_floor(self):
        """High engagement never floors relevance for an off-topic item."""
        item = schema.SourceItem(
            item_id="reddit-viral",
            source="reddit",
            title="Completely unrelated post",
            body="Nothing about the topic",
            url="https://reddit.com/r/test",
            engagement={"score": 50000, "num_comments": 3000},
        )
        rel = signals.local_relevance(item, "kanye west")
        self.assertLess(rel, 0.3, f"Off-topic item should not get a relevance floor, got {rel}")

    # -- Low-engagement social items survive on relevance alone --

    def test_low_engagement_social_item_with_relevance_survives(self):
        """An X post with weak engagement but solid relevance is kept."""
        low_eng_x = schema.SourceItem(
            item_id="x-low", source="x", title="Tweet", body="Topic discussion",
            url="https://x.com/low",
            local_relevance=0.5, engagement={"likes": 2, "reposts": 0},
            engagement_score=5,
        )
        other = schema.SourceItem(
            item_id="r-other", source="reddit", title="Post", body="Topic",
            url="https://reddit.com/other",
            local_relevance=0.5, engagement_score=50,
        )
        pruned = signals.prune_low_relevance([other, low_eng_x])
        ids = [item.item_id for item in pruned]
        self.assertIn("x-low", ids, "Relevant X items survive weak engagement")

    # -- Snippets do not exempt items from relevance pruning --

    def test_snippet_does_not_exempt_low_relevance_items(self):
        """Items with low relevance are still pruned even if they have a
        non-empty snippet."""
        reddit_with_snippet = schema.SourceItem(
            item_id="reddit-snippet",
            source="reddit",
            title="Short title",
            body="Short body",
            url="https://reddit.com/r/test",
            snippet="Some snippet content",
            local_relevance=0.05,
        )
        strong = schema.SourceItem(
            item_id="strong",
            source="reddit",
            title="Strong post",
            body="Detailed analysis of the topic",
            url="https://reddit.com/r/strong",
            local_relevance=0.5,
        )
        pruned = signals.prune_low_relevance([strong, reddit_with_snippet], minimum=0.15)
        ids = [item.item_id for item in pruned]
        self.assertIn("strong", ids, "Strong item should survive")
        self.assertNotIn("reddit-snippet", ids,
                         "Items should still be pruned by relevance threshold")


if __name__ == "__main__":
    unittest.main()
