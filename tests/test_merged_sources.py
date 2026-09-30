"""The Epic Forums and YouTube lanes must coexist through the full pipeline."""
import pytest

from lib import doctor, pipeline, planner, render, schema, youtube_yt


@pytest.mark.parametrize("depth", ["quick", "default", "deep"])
def test_both_sources_survive_plan_normalization_ranking_and_footer(depth):
    report = pipeline.run(topic="Unreal Engine 5.8 path tracer glass", config={},
                          depth=depth, requested_sources=["epicforums", "youtube"], mock=True)
    assert set(report.items_by_source) == {"epicforums", "youtube"}
    assert all(report.items_by_source.values())
    assert all(item.source == source for source, items in report.items_by_source.items() for item in items)
    assert report.items_by_source["epicforums"][0].engagement["likes"] > 0
    assert report.items_by_source["youtube"][0].engagement["views"] > 0
    footer = "\n".join(render._build_source_footer_lines(report))
    assert "Epic Forums:" in footer and "likes" in footer
    assert "YouTube:" in footer and "with transcripts" in footer
    restored = schema.report_from_dict(schema.to_dict(report))
    assert set(restored.source_status) == {"epicforums", "youtube"}


def test_availability_and_doctor_register_both_sources(monkeypatch):
    monkeypatch.setattr(pipeline.env, "get_x_source", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.env, "x_pending_browser_auth", lambda *a, **k: False)
    monkeypatch.setattr(youtube_yt, "is_ytdlp_installed", lambda *a: True)
    available = pipeline.available_sources({}, local_only=True)
    assert {"epicforums", "youtube"} <= set(available)
    assert {"epicforums", "youtube"} <= set(doctor.SOURCE_ORDER)
    assert {"epicforums", "youtube"} <= set(doctor._SOURCE_BUILDERS)
    for priorities in (planner.SOURCE_PRIORITY, planner.QUICK_SOURCE_PRIORITY):
        assert all({"epicforums", "youtube"} <= set(order) for order in priorities.values())
