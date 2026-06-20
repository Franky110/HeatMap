"""Unit tests for pure functions in scripts/activity_import_base.py."""

import pytest


# We test only the pure functions that have no GUI/network side effects.
# Import them directly after ensuring scripts/ is on sys.path (done by conftest).

def _import():
    from activity_import_base import normalize_sport, inject_gpx_metadata, SPORT_TYPE_MAP, _apply_headless_filters
    return normalize_sport, inject_gpx_metadata, SPORT_TYPE_MAP, _apply_headless_filters


# ---------------------------------------------------------------------------
# normalize_sport
# ---------------------------------------------------------------------------
class TestNormalizeSport:
    def setup_method(self):
        self.normalize_sport, *_ = _import()

    @pytest.mark.parametrize("raw,expected", [
        # Garmin
        ("running", "Run"),
        ("trail_running", "Run"),
        ("cycling", "Bike"),
        ("mountain_biking", "Bike"),
        ("gravel_cycling", "Bike"),
        ("swimming", "Swimming"),
        ("open_water_swimming", "Swimming"),
        ("hiking", "Walking"),
        ("walking", "Walking"),
        ("skiing", "Ski"),
        ("snowboarding", "Ski"),
        ("rock_climbing", "Climbing"),
        ("bouldering", "Climbing"),
        ("indoor_climbing", "Climbing"),
        # Strava
        ("Run", "Run"),
        ("TrailRun", "Run"),
        ("Ride", "Bike"),
        ("MountainBikeRide", "Bike"),
        ("Swim", "Swimming"),
        ("Hike", "Walking"),
        ("Walk", "Walking"),
        ("AlpineSki", "Ski"),
        ("RockClimbing", "Climbing"),
        # Unknown
        ("yoga", ""),
        ("", ""),
    ])
    def test_mapping(self, raw, expected):
        assert self.normalize_sport(raw) == expected


# ---------------------------------------------------------------------------
# inject_gpx_metadata
# ---------------------------------------------------------------------------
class TestInjectGpxMetadata:
    def setup_method(self):
        _, self.inject_gpx_metadata, *_ = _import()

    def test_source_comment_prepended(self):
        result = self.inject_gpx_metadata("<gpx/>", "garmin")
        assert result.startswith("<!-- heatmap-source: garmin -->")

    def test_sport_comment_when_known_type(self):
        result = self.inject_gpx_metadata("<gpx/>", "garmin", "cycling")
        assert "<!-- heatmap-sport: Bike -->" in result

    def test_no_sport_comment_for_unknown_type(self):
        result = self.inject_gpx_metadata("<gpx/>", "garmin", "yoga")
        assert "heatmap-sport" not in result

    def test_original_content_preserved(self):
        result = self.inject_gpx_metadata("<gpx>data</gpx>", "strava", "Run")
        assert "<gpx>data</gpx>" in result

    def test_source_always_present(self):
        result = self.inject_gpx_metadata("<gpx/>", "komoot")
        assert "heatmap-source: komoot" in result


# ---------------------------------------------------------------------------
# _apply_headless_filters
# ---------------------------------------------------------------------------
class TestApplyHeadlessFilters:
    def setup_method(self):
        *_, self._apply = _import()

    def _tours(self):
        return {
            "t1": {"type": "tour", "date": "2024-03-01", "distance": 30000, "sport": "Bike"},
            "t2": {"type": "tour", "date": "2024-03-02", "distance": 5000,  "sport": "Run"},
            "t3": {"type": "tour", "date": "2024-03-03", "distance": 80000, "sport": "Bike"},
            "t4": {"type": "planned", "date": "2024-03-01", "distance": 20000, "sport": "Walking"},
        }

    def test_no_filters_returns_all(self):
        tours = self._tours()
        result = self._apply(tours, {}, {"All": "all"})
        assert set(result.keys()) == set(tours.keys())

    def test_type_filter_recorded_only(self):
        tours = self._tours()
        result = self._apply(tours, {"type": "Recorded"}, {"All": "all", "Recorded": "tour"})
        assert "t4" not in result
        assert "t1" in result

    def test_min_distance_filter(self):
        tours = self._tours()
        result = self._apply(tours, {"min_dist": 20}, {"All": "all"})
        # t2 has 5 km, should be excluded
        assert "t2" not in result
        assert "t1" in result
        assert "t3" in result

    def test_date_range_filter(self):
        tours = self._tours()
        result = self._apply(tours, {"start_date": "2024-03-02", "end_date": "2024-03-02"}, {"All": "all"})
        assert set(result.keys()) == {"t2"}

    def test_excluded_sports(self):
        tours = self._tours()
        result = self._apply(tours, {"excluded_sports": ["Bike"]}, {"All": "all"})
        assert "t1" not in result
        assert "t3" not in result
        assert "t2" in result
        assert "t4" in result
