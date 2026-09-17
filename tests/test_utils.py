from youtube_pipeline.utils import date_windows, parse_iso8601_duration_seconds, stable_id


def test_duration_seconds():
    assert parse_iso8601_duration_seconds("PT1H2M3S") == 3723
    assert parse_iso8601_duration_seconds("PT45S") == 45
    assert parse_iso8601_duration_seconds(None) is None


def test_date_windows_end_date_is_inclusive():
    windows = date_windows("2026-01-01", "2026-01-02", 30)
    assert windows == [("2026-01-01T00:00:00Z", "2026-01-03T00:00:00Z")]


def test_stable_id_is_stable():
    assert stable_id("a", 1) == stable_id("a", 1)
    assert stable_id("a", 1) != stable_id("a", 2)
