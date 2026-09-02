"""Tests for post-research quality score and upgrade nudge.

Reddit is always a core source (free public JSON). X remains supported when
active, but its absence is optional and must not lower the quality grade or
trigger an authentication nudge.
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_config(**overrides):
    """Return a minimal config dict."""
    config = {
        "AUTH_TOKEN": None,
        "CT0": None,
        "XAI_API_KEY": None,
        "XQUIK_API_KEY": None,
        "SCRAPECREATORS_API_KEY": None,
    }
    config.update(overrides)
    return config


def _base_results(**overrides):
    """Return a minimal research_results dict with no errors."""
    results = {
        "x_error": None,
        "reddit_error": None,
    }
    results.update(overrides)
    return results


def _compute(config_overrides=None, result_overrides=None):
    from lib.quality_nudge import compute_quality_score

    config = _base_config(**(config_overrides or {}))
    results = _base_results(**(result_overrides or {}))
    return compute_quality_score(config, results)

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBaseline:
    """Reddit active; X omitted (unconfigured) leaves the denominator."""

    def test_score_100(self):
        q = _compute()
        assert q["score_pct"] == 100

    def test_active_sources(self):
        q = _compute()
        assert q["core_active"] == ["reddit"]

    def test_nothing_missing(self):
        q = _compute()
        assert q["core_missing"] == []
        assert q["core_errored"] == []

    def test_nudge_is_none(self):
        q = _compute()
        assert q["nudge_text"] is None


class TestXCookies:
    """X cookie credentials make X an active core source."""

    def test_score_100(self):
        q = _compute(config_overrides={"AUTH_TOKEN": "tok123"})
        assert q["score_pct"] == 100
        assert q["core_active"] == ["reddit", "x"]

    def test_nudge_is_none(self):
        q = _compute(config_overrides={"AUTH_TOKEN": "tok123"})
        assert q["nudge_text"] is None


class TestXquikKey:
    """The XQUIK_API_KEY backend counts as X credentials."""

    def test_score_100(self):
        q = _compute(config_overrides={"XQUIK_API_KEY": "xq123"})
        assert q["score_pct"] == 100
        assert "x" in q["core_active"]


class TestXaiKey:
    """The XAI_API_KEY backend counts as X credentials."""

    def test_score_100(self):
        q = _compute(config_overrides={"XAI_API_KEY": "xai123"})
        assert q["score_pct"] == 100
        assert "x" in q["core_active"]


class TestActiveSourceX:
    """The runtime active-source list preserves X without legacy credentials."""

    def test_active_x_is_counted(self):
        q = _compute(result_overrides={"active_sources": ["reddit", "x"]})
        assert "x" in q["core_active"]
        assert q["score_pct"] == 100


class TestConfiguredXErrored:
    """A configured X that errored is a real outage: docked and surfaced,
    never disguised as an optional omission."""

    def test_errored_x_docks_the_score(self):
        q = _compute(
            config_overrides={"AUTH_TOKEN": "tok123"},
            result_overrides={"x_error": "401 unauthorized"},
        )
        assert q["score_pct"] == 50  # 1/2 - X stays in the denominator
        assert q["core_missing"] == ["x"]
        assert q["core_errored"] == ["x"]

    def test_errored_x_nudge_surfaces_the_repair(self):
        q = _compute(
            config_overrides={"AUTH_TOKEN": "tok123"},
            result_overrides={"x_error": "401 unauthorized"},
        )
        assert q["nudge_text"] is not None
        assert "Research quality: 1/2 core sources." in q["nudge_text"]
        assert "X/Twitter (errored this run)" in q["nudge_text"]
        assert "Free fixes:" in q["nudge_text"]
        assert "no affiliation" in q["nudge_text"]

    def test_runtime_active_x_that_errored_is_also_docked(self):
        q = _compute(
            result_overrides={
                "active_sources": ["reddit", "x"],
                "x_error": "429 rate limited",
            },
        )
        assert q["core_errored"] == ["x"]
        assert q["score_pct"] == 50


class TestSCDoesNotAffectCoreScore:
    """ScrapeCreators is a Reddit backup lane, not a core source."""

    def test_sc_alone_still_100(self):
        q = _compute(config_overrides={"SCRAPECREATORS_API_KEY": "sc123"})
        assert q["score_pct"] == 100
        assert q["core_active"] == ["reddit"]
        assert q["nudge_text"] is None


class TestRedditNeverInCoreErrored:
    """Reddit errors don't affect core score since it's always-active via public path."""

    def test_reddit_error_does_not_affect_score(self):
        q = _compute(
            config_overrides={"AUTH_TOKEN": "tok123"},
            result_overrides={"reddit_error": "429 Too Many Requests"},
        )
        # Reddit is always-active in core (public path), error doesn't demote it
        assert "reddit" in q["core_active"]
        assert "reddit" not in q["core_missing"]
        assert q["score_pct"] == 100
