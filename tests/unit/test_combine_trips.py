"""Unit tests for scripts/combine_trips.py — pure parsing and math functions."""

import math
import pytest
from datetime import datetime, timezone

import combine_trips as ct


# ---------------------------------------------------------------------------
# haversine_m
# ---------------------------------------------------------------------------
class TestHaversineM:
    def test_zero_distance(self):
        assert ct.haversine_m(48.85, 2.35, 48.85, 2.35) == pytest.approx(0.0)

    def test_known_distance(self):
        # Paris to London approximation ~340 km
        d = ct.haversine_m(48.8566, 2.3522, 51.5074, -0.1278)
        assert 330_000 < d < 350_000

    def test_symmetrical(self):
        d1 = ct.haversine_m(48.0, 2.0, 49.0, 3.0)
        d2 = ct.haversine_m(49.0, 3.0, 48.0, 2.0)
        assert d1 == pytest.approx(d2)


# ---------------------------------------------------------------------------
# parse_time
# ---------------------------------------------------------------------------
class TestParseTime:
    def test_utc_z(self):
        dt = ct.parse_time("2024-01-15T08:00:00Z")
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 15

    def test_with_offset(self):
        dt = ct.parse_time("2024-01-15T09:00:00+0100")
        assert dt.utcoffset().total_seconds() == 3600

    def test_with_colon_offset(self):
        dt = ct.parse_time("2024-01-15T09:00:00+01:00")
        assert dt.utcoffset().total_seconds() == 3600


# ---------------------------------------------------------------------------
# load_trip
# ---------------------------------------------------------------------------
GPX_TWO_POINTS = """\
<?xml version="1.0"?>
<gpx><trk><trkseg>
  <trkpt lat="48.85" lon="2.35"><ele>100</ele><time>2024-01-15T08:00:00Z</time></trkpt>
  <trkpt lat="48.86" lon="2.36"><ele>110</ele><time>2024-01-15T08:10:00Z</time></trkpt>
</trkseg></trk></gpx>
"""

GPX_WITH_HR = """\
<?xml version="1.0"?>
<gpx><trk><trkseg>
  <trkpt lat="48.85" lon="2.35"><ele>100</ele><time>2024-01-15T08:00:00Z</time>
    <extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>150</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions>
  </trkpt>
  <trkpt lat="48.86" lon="2.36"><ele>110</ele><time>2024-01-15T08:10:00Z</time>
    <extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>160</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions>
  </trkpt>
</trkseg></trk></gpx>
"""

GPX_WITH_POWER = """\
<?xml version="1.0"?>
<gpx><trk><trkseg>
  <trkpt lat="48.85" lon="2.35"><ele>100</ele><time>2024-01-15T08:00:00Z</time>
    <extensions><power>200</power></extensions>
  </trkpt>
  <trkpt lat="48.86" lon="2.36"><ele>110</ele><time>2024-01-15T08:10:00Z</time>
    <extensions><power>210</power></extensions>
  </trkpt>
</trkseg></trk></gpx>
"""

GPX_WITH_NAMESPACED_POWER = """\
<?xml version="1.0"?>
<gpx xmlns:ns3="http://www.garmin.com/xmlschemas/ActivityExtension/v2"><trk><trkseg>
  <trkpt lat="48.85" lon="2.35"><ele>100</ele><time>2024-01-15T08:00:00Z</time>
    <extensions><ns3:TPX><ns3:power>250</ns3:power></ns3:TPX></extensions>
  </trkpt>
  <trkpt lat="48.86" lon="2.36"><ele>110</ele><time>2024-01-15T08:10:00Z</time>
    <extensions><ns3:TPX><ns3:power>260</ns3:power></ns3:TPX></extensions>
  </trkpt>
</trkseg></trk></gpx>
"""

class TestLoadTrip:
    def test_parses_two_points(self, tmp_path):
        f = tmp_path / "trip.gpx"
        f.write_text(GPX_TWO_POINTS, encoding="utf-8")
        trip = ct.load_trip(str(f))
        assert len(trip) == 2
        assert trip[0][0] == pytest.approx(48.85)
        assert trip[0][1] == pytest.approx(2.35)
        assert trip[0][2] == pytest.approx(100.0)

    def test_parses_hr(self, tmp_path):
        f = tmp_path / "hr.gpx"
        f.write_text(GPX_WITH_HR, encoding="utf-8")
        trip = ct.load_trip(str(f))
        assert len(trip) == 2
        assert trip[0][4].get("hr") == 150
        assert trip[1][4].get("hr") == 160

    def test_parses_power(self, tmp_path):
        f = tmp_path / "pwr.gpx"
        f.write_text(GPX_WITH_POWER, encoding="utf-8")
        trip = ct.load_trip(str(f))
        assert len(trip) == 2
        assert trip[0][4].get("pwr") == 200
        assert trip[1][4].get("pwr") == 210

    def test_parses_namespaced_power(self, tmp_path):
        # Garmin exports power as <ns3:power> — must be detected via namespace-aware regex
        f = tmp_path / "garmin_pwr.gpx"
        f.write_text(GPX_WITH_NAMESPACED_POWER, encoding="utf-8")
        trip = ct.load_trip(str(f))
        assert len(trip) == 2
        assert trip[0][4].get("pwr") == 250
        assert trip[1][4].get("pwr") == 260

    def test_empty_file_returns_empty(self, tmp_path):
        f = tmp_path / "empty.gpx"
        f.write_text("<gpx/>", encoding="utf-8")
        assert ct.load_trip(str(f)) == []


# ---------------------------------------------------------------------------
# trip_distance_km
# ---------------------------------------------------------------------------
class TestTripDistanceKm:
    def test_short_distance(self, tmp_path):
        f = tmp_path / "trip.gpx"
        f.write_text(GPX_TWO_POINTS, encoding="utf-8")
        trip = ct.load_trip(str(f))
        dist = ct.trip_distance_km(trip)
        # ~1.3 km between the two points
        assert 1.0 < dist < 2.0

    def test_single_point_is_zero(self):
        fake_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        trip = [(48.85, 2.35, 100.0, fake_time, {})]
        assert ct.trip_distance_km(trip) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# decimate_trip
# ---------------------------------------------------------------------------
class TestDecimateTrip:
    def _make_trip(self, n):
        fake_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        return [(48.0 + i * 0.001, 2.0 + i * 0.001, 100.0, fake_time, {}) for i in range(n)]

    def test_short_trip_unchanged(self):
        trip = self._make_trip(2)
        assert ct.decimate_trip(trip) == trip

    def test_longer_trip_reduces_points(self):
        trip = self._make_trip(30)
        decimated = ct.decimate_trip(trip)
        assert len(decimated) < len(trip)

    def test_last_point_preserved(self):
        trip = self._make_trip(20)
        decimated = ct.decimate_trip(trip)
        assert decimated[-1] == trip[-1]


# ---------------------------------------------------------------------------
# trip_detail
# ---------------------------------------------------------------------------
class TestTripDetail:
    def test_returns_expected_keys(self, tmp_path):
        f = tmp_path / "trip.gpx"
        f.write_text(GPX_TWO_POINTS, encoding="utf-8")
        trip = ct.load_trip(str(f))
        detail = ct.trip_detail(trip)
        assert "d" in detail
        assert "t" in detail
        assert "e" in detail
        assert "s" in detail

    def test_distance_starts_at_zero(self, tmp_path):
        f = tmp_path / "trip.gpx"
        f.write_text(GPX_TWO_POINTS, encoding="utf-8")
        trip = ct.load_trip(str(f))
        detail = ct.trip_detail(trip)
        assert detail["d"][0] == 0.0

    def test_hr_included_when_present(self, tmp_path):
        f = tmp_path / "hr.gpx"
        f.write_text(GPX_WITH_HR, encoding="utf-8")
        trip = ct.load_trip(str(f))
        detail = ct.trip_detail(trip)
        assert "hr" in detail
        assert detail["hr"][0] == 150

    def test_pwr_included_when_present(self, tmp_path):
        f = tmp_path / "pwr.gpx"
        f.write_text(GPX_WITH_POWER, encoding="utf-8")
        trip = ct.load_trip(str(f))
        detail = ct.trip_detail(trip)
        assert "pwr" in detail
        assert detail["pwr"][0] == 200

    def test_pwr_namespaced_included_when_present(self, tmp_path):
        f = tmp_path / "garmin_pwr.gpx"
        f.write_text(GPX_WITH_NAMESPACED_POWER, encoding="utf-8")
        trip = ct.load_trip(str(f))
        detail = ct.trip_detail(trip)
        assert "pwr" in detail
        assert detail["pwr"][0] == 250

    def test_no_pwr_key_when_absent(self, tmp_path):
        f = tmp_path / "no_pwr.gpx"
        f.write_text(GPX_TWO_POINTS, encoding="utf-8")
        trip = ct.load_trip(str(f))
        detail = ct.trip_detail(trip)
        assert "pwr" not in detail
