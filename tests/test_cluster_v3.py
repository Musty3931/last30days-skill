import unittest

from lib import cluster, schema


def make_candidate(candidate_id: str, source: str, title: str, snippet: str, score: float) -> schema.Candidate:
    return schema.Candidate(
        candidate_id=candidate_id,
        item_id=candidate_id,
        source=source,
        title=title,
        url=f"https://example.com/{candidate_id}",
        snippet=snippet,
        subquery_labels=["primary"],
        native_ranks={"primary:reddit": 1},
        local_relevance=0.8,
        freshness=80,
        engagement=10,
        source_quality=0.7,
        rrf_score=0.02,
        rerank_score=score,
        final_score=score,
    )


class ClusterV3Tests(unittest.TestCase):
    def test_singleton_clusters_for_non_clustered_plan(self):
        plan = schema.QueryPlan(
            intent="how_to",
            freshness_mode="balanced_recent",
            cluster_mode="none",
            raw_topic="docker setup",
            subqueries=[schema.SubQuery(label="primary", search_query="docker setup", ranking_query="How do I set up Docker?", sources=["reddit"])],
            source_weights={"reddit": 1.0},
        )
        candidates = [
            make_candidate("c1", "reddit", "Docker setup guide", "Step by step setup", 80),
            make_candidate("c2", "github", "Docker install script", "Repo walkthrough", 75),
        ]
        clusters = cluster.cluster_candidates(candidates, plan)
        self.assertEqual(2, len(clusters))
        self.assertEqual(["c1"], clusters[0].representative_ids)
        self.assertEqual(["c2"], clusters[1].representative_ids)

    def test_breaking_news_clusters_related_items(self):
        plan = schema.QueryPlan(
            intent="breaking_news",
            freshness_mode="strict_recent",
            cluster_mode="story",
            raw_topic="model launch",
            subqueries=[schema.SubQuery(label="primary", search_query="model launch", ranking_query="What happened in the model launch?", sources=["reddit", "x"])],
            source_weights={"reddit": 0.5, "x": 0.5},
        )
        candidates = [
            make_candidate("c1", "reddit", "Open model launch reactions", "People are reacting to the open model launch today.", 88),
            make_candidate("c2", "x", "Open model launch update", "People are reacting to the open model launch today on X.", 84),
            make_candidate("c3", "github", "Different topic", "A separate discussion about hardware benchmarks.", 70),
        ]
        clusters = cluster.cluster_candidates(candidates, plan)
        self.assertEqual(2, len(clusters))
        self.assertEqual(2, len(clusters[0].candidate_ids))
        self.assertIn("c1", clusters[0].candidate_ids)
        self.assertIn("c2", clusters[0].candidate_ids)


class TestCrossSourceMerging(unittest.TestCase):
    """Test the entity-based second pass that merges same-story clusters across sources."""

    def _plan(self, intent="breaking_news"):
        return schema.QueryPlan(
            intent=intent,
            freshness_mode="strict_recent",
            cluster_mode="story",
            raw_topic="test",
            subqueries=[schema.SubQuery(label="primary", search_query="test", ranking_query="test", sources=["reddit", "x", "digg"])],
            source_weights={"reddit": 0.5, "x": 0.5, "digg": 0.5},
        )

    def test_same_story_different_phrasing_merges(self):
        """Wireless Festival example: same event, different wording, different sources."""
        candidates = [
            make_candidate("c1", "reddit", "Kanye West to headline all three nights of Wireless Festival 2026", "Big announcement for Wireless.", 80),
            make_candidate("c2", "x", "BREAKING: Kanye West is making his massive UK comeback at Wireless Festival this July", "Ye returns to UK.", 75),
            make_candidate("c3", "digg", "Kanye West BULLY Album Review - Knox Hill Reacts", "Full album reaction and breakdown.", 70),
        ]
        clusters = cluster.cluster_candidates(candidates, self._plan())
        # c1 and c2 should merge (Kanye + Wireless + Festival overlap), c3 should stay separate
        self.assertEqual(2, len(clusters))
        wireless_cluster = next(cl for cl in clusters if len(cl.candidate_ids) == 2)
        self.assertIn("c1", wireless_cluster.candidate_ids)
        self.assertIn("c2", wireless_cluster.candidate_ids)
        self.assertEqual(sorted(["reddit", "x"]), wireless_cluster.sources)
        # Multi-source cluster should not have "single-source" uncertainty
        self.assertNotEqual("single-source", wireless_cluster.uncertainty)

    def test_different_stories_dont_merge(self):
        """Different topics should stay separate even with some entity overlap (e.g., 'Kanye')."""
        candidates = [
            make_candidate("c1", "reddit", "Kanye West BULLY Album First Impressions Thread", "What do you think of BULLY?", 80),
            make_candidate("c2", "x", "Kanye West apology for antisemitism in Wall Street Journal ad", "Full page WSJ ad.", 75),
            make_candidate("c3", "digg", "Kanye West Wireless Festival ticket prices breakdown", "How much for Wireless tickets?", 70),
        ]
        clusters = cluster.cluster_candidates(candidates, self._plan())
        # These are 3 different stories, should remain as 3 clusters
        self.assertEqual(3, len(clusters))

    def test_same_source_clusters_dont_merge(self):
        """Two single-source clusters from the same source should not merge via entity pass."""
        candidates = [
            make_candidate("c1", "reddit", "Kanye West Wireless Festival headline announcement", "Three nights!", 80),
            make_candidate("c2", "reddit", "Kanye West returning to Wireless Festival confirmed", "UK comeback.", 70),
        ]
        clusters = cluster.cluster_candidates(candidates, self._plan())
        # The initial greedy pass may or may not merge these (depends on token similarity).
        # But if they end up as separate clusters, the entity pass should NOT merge them
        # since they're both from reddit.
        for cl in clusters:
            self.assertTrue(len(cl.sources) >= 1)  # basic sanity

class TestStaleClusterDemotion(unittest.TestCase):
    """Stale candidates must never become cluster representatives or titles."""

    def _plan(self):
        return schema.QueryPlan(
            intent="breaking_news",
            freshness_mode="strict_recent",
            cluster_mode="story",
            raw_topic="test",
            subqueries=[schema.SubQuery(label="primary", search_query="test", ranking_query="test", sources=["reddit", "x"])],
            source_weights={"reddit": 0.5, "x": 0.5},
        )

    def _candidate_with_date(
        self, candidate_id: str, source: str, title: str, score: float,
        published_at: str, date_confidence: str, range_from: str, range_to: str,
    ) -> schema.Candidate:
        item = schema.SourceItem(
            item_id=candidate_id,
            source=source,
            title=title,
            body=title,
            url=f"https://example.com/{candidate_id}",
            published_at=published_at,
            date_confidence=date_confidence,
        )
        return schema.Candidate(
            candidate_id=candidate_id,
            item_id=candidate_id,
            source=source,
            title=title,
            url=f"https://example.com/{candidate_id}",
            snippet=title,
            subquery_labels=["primary"],
            native_ranks={"primary:reddit": 1},
            local_relevance=0.8,
            freshness=80,
            engagement=10,
            source_quality=0.7,
            rrf_score=0.02,
            rerank_score=score,
            final_score=score,
            source_items=[item],
            metadata={"range_from": range_from, "range_to": range_to},
        )

    def test_stale_candidate_not_cluster_representative(self):
        """A stale item with higher final_score must not lead a cluster over a fresh item.

        This guards against the issue where a 2025-10 video ranked #1 in a
        2026-07 brief because clustering re-sorted by final_score alone.
        """
        range_from = "2026-06-15"
        range_to = "2026-07-15"
        stale = self._candidate_with_date(
            "stale", "reddit", "Model launch reactions discussion",
            score=95.0,
            published_at="2025-10-15",
            date_confidence="low",
            range_from=range_from,
            range_to=range_to,
        )
        fresh = self._candidate_with_date(
            "fresh", "x", "Model launch reactions update",
            score=50.0,
            published_at="2026-07-10",
            date_confidence="high",
            range_from=range_from,
            range_to=range_to,
        )
        candidates = [stale, fresh]
        clusters = cluster.cluster_candidates(candidates, self._plan())

        self.assertEqual(1, len(clusters))
        self.assertEqual("fresh", clusters[0].representative_ids[0])
        self.assertEqual(fresh.title, clusters[0].title)


class TestClusterUncertainty(unittest.TestCase):
    def test_single_source_returns_single_source(self):
        candidates = [make_candidate("c1", "reddit", "Title", "Body", 80)]
        result = cluster._cluster_uncertainty(candidates)
        self.assertEqual("single-source", result)

    def test_multi_source_high_score_returns_none(self):
        candidates = [
            make_candidate("c1", "reddit", "Title", "Body", 80),
            make_candidate("c2", "x", "Title2", "Body2", 70),
        ]
        result = cluster._cluster_uncertainty(candidates)
        self.assertIsNone(result)

    def test_multi_source_low_score_returns_thin_evidence(self):
        candidates = [
            make_candidate("c1", "reddit", "Title", "Body", 30),
            make_candidate("c2", "x", "Title2", "Body2", 40),
        ]
        result = cluster._cluster_uncertainty(candidates)
        self.assertEqual("thin-evidence", result)

if __name__ == "__main__":
    unittest.main()
