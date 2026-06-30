"""Unit tests for scripts/trip_utils.py — pure functions, no I/O required
except for read_gpx_meta which uses a tmp file."""

import pytest
import trip_utils
from trip_utils import (
    trip_id, trip_date, read_gpx_meta, trip_sport, gpx_sensors,
    NAME_RE, ID_RE, TITLE_RE, SPORT_CATEGORIES, SPORT_KEYWORDS,
)


# ---------------------------------------------------------------------------
# trip_id
# ---------------------------------------------------------------------------
class TestTripId:
    def test_returns_numeric_suffix(self):
        assert trip_id("2024-01-15_Morning-Run-12345.gpx") == "12345"

    def test_case_insensitive(self):
        assert trip_id("2024-01-15_ride-99.GPX") == "99"

    def test_none_when_no_id(self):
        assert trip_id("no-id-here.gpx") is None

    def test_none_for_empty(self):
        assert trip_id("") is None


# ---------------------------------------------------------------------------
# trip_date
# ---------------------------------------------------------------------------
class TestTripDate:
    def test_extracts_date(self):
        assert trip_date("2024-06-20_Hike-7890.gpx") == "2024-06-20"

    def test_empty_when_no_date(self):
        assert trip_date("nodatehere.gpx") == ""

    def test_empty_string(self):
        assert trip_date("") == ""


# ---------------------------------------------------------------------------
# read_gpx_meta
# ---------------------------------------------------------------------------
class TestReadGpxMeta:
    def test_reads_source_and_sport(self, tmp_path):
        f = tmp_path / "test.gpx"
        f.write_text("<!-- heatmap-source: garmin -->\n<!-- heatmap-sport: Bike -->\n<gpx/>",
                     encoding="utf-8")
        source, sport = read_gpx_meta(str(f))
        assert source == "garmin"
        assert sport == "Bike"

    def test_missing_file_returns_empty(self, tmp_path):
        source, sport = read_gpx_meta(str(tmp_path / "nonexistent.gpx"))
        assert source == ""
        assert sport == ""

    def test_no_comments_returns_empty(self, tmp_path):
        f = tmp_path / "plain.gpx"
        f.write_text("<gpx/>", encoding="utf-8")
        assert read_gpx_meta(str(f)) == ("", "")

    def test_only_source(self, tmp_path):
        f = tmp_path / "src.gpx"
        f.write_text("<!-- heatmap-source: komoot -->\n<gpx/>", encoding="utf-8")
        source, sport = read_gpx_meta(str(f))
        assert source == "komoot"
        assert sport == ""

    def test_reads_only_first_1kb(self, tmp_path):
        # Tags beyond 1 KB should not be found
        f = tmp_path / "big.gpx"
        padding = "x" * 1100
        f.write_text(f"<gpx/>{padding}<!-- heatmap-source: hidden -->", encoding="utf-8")
        source, _ = read_gpx_meta(str(f))
        assert source == ""


# ---------------------------------------------------------------------------
# trip_sport — filename keyword matching
# ---------------------------------------------------------------------------
class TestTripSport:
    @pytest.mark.parametrize("name,expected", [
        ("2024-01-01_Morning-Bike-Ride-1.gpx", "Bike"),
        ("2024-01-01_vélo-dimanche-1.gpx", "Bike"),
        ("2024-01-01_MTB-trail-1.gpx", "Bike"),
        ("2024-01-01_VTT-forêt-1.gpx", "Bike"),
        ("2024-01-01_Gravel-ride-1.gpx", "Bike"),
        ("2024-01-01_Morning-Run-1.gpx", "Run"),
        ("2024-01-01_course-matinale-1.gpx", "Run"),
        ("2024-01-01_randonnée-montagne-1.gpx", "Walking"),
        ("2024-01-01_hiking-trail-1.gpx", "Walking"),
        ("2024-01-01_ski-nordique-1.gpx", "Ski"),
        ("2024-01-01_snowboard-session-1.gpx", "Ski"),
        ("2024-01-01_natation-1.gpx", "Swimming"),
        ("2024-01-01_climbing-escalade-1.gpx", "Climbing"),
        ("2024-01-01_boulder-session-1.gpx", "Climbing"),
        ("2024-01-01_Random-Activity-1.gpx", "Unknown"),
    ])
    def test_keyword_detection(self, name, expected):
        assert trip_sport(name) == expected

    def test_embedded_sport_takes_priority(self, tmp_path):
        # File is named "run" but embedded tag says "Bike"
        f = tmp_path / "2024-01-01_morning-run-1.gpx"
        f.write_text("<!-- heatmap-sport: Bike -->\n<gpx/>", encoding="utf-8")
        assert trip_sport(f.name, str(f)) == "Bike"

    def test_falls_back_to_keyword_when_no_embedded(self, tmp_path):
        f = tmp_path / "2024-01-01_morning-run-1.gpx"
        f.write_text("<gpx/>", encoding="utf-8")
        assert trip_sport(f.name, str(f)) == "Run"

    def test_unknown_when_no_match_and_no_file(self):
        assert trip_sport("2024-01-01_random-activity-99.gpx") == "Unknown"


# ---------------------------------------------------------------------------
# gpx_sensors
# ---------------------------------------------------------------------------
class TestGpxSensors:
    def _write(self, tmp_path, content):
        f = tmp_path / "trip.gpx"
        f.write_text(content, encoding="utf-8")
        return str(f)

    def test_no_sensors(self, tmp_path):
        f = self._write(tmp_path, "<gpx><trk><trkseg><trkpt lat='1' lon='1'/></trkseg></trk></gpx>")
        assert gpx_sensors(f) == ""

    def test_hr_only(self, tmp_path):
        f = self._write(tmp_path, "<gpx><trkpt><extensions><gpxtpx:hr>150</gpxtpx:hr></extensions></trkpt></gpx>")
        assert gpx_sensors(f) == "HR"

    def test_cad_only(self, tmp_path):
        f = self._write(tmp_path, "<gpx><trkpt><extensions><gpxtpx:cad>90</gpxtpx:cad></extensions></trkpt></gpx>")
        assert gpx_sensors(f) == "Cad"

    def test_power_only(self, tmp_path):
        f = self._write(tmp_path, "<gpx><trkpt><extensions><power>250</power></extensions></trkpt></gpx>")
        assert gpx_sensors(f) == "Pwr"

    def test_power_namespaced(self, tmp_path):
        # Garmin exports power as <ns3:power>
        f = self._write(tmp_path, "<gpx><trkpt><extensions><ns3:power>250</ns3:power></extensions></trkpt></gpx>")
        assert gpx_sensors(f) == "Pwr"

    def test_temp_only(self, tmp_path):
        f = self._write(tmp_path, "<gpx><trkpt><extensions><gpxtpx:atemp>18</gpxtpx:atemp></extensions></trkpt></gpx>")
        assert gpx_sensors(f) == "Tmp"

    def test_all_sensors(self, tmp_path):
        content = ("<gpx><trkpt><extensions>"
                   "<gpxtpx:hr>155</gpxtpx:hr>"
                   "<gpxtpx:cad>85</gpxtpx:cad>"
                   "<power>200</power>"
                   "<gpxtpx:atemp>15</gpxtpx:atemp>"
                   "</extensions></trkpt></gpx>")
        f = self._write(tmp_path, content)
        assert gpx_sensors(f) == "HR Cad Pwr Tmp"

    def test_missing_file(self, tmp_path):
        assert gpx_sensors(str(tmp_path / "nonexistent.gpx")) == ""

    def test_only_reads_first_16kb(self, tmp_path):
        # Tags placed beyond 16 KB should not be found
        f = tmp_path / "big.gpx"
        padding = "x" * 17000
        f.write_text(f"<gpx/>{padding}<gpxtpx:hr>150</gpxtpx:hr>", encoding="utf-8")
        assert gpx_sensors(str(f)) == ""


# ---------------------------------------------------------------------------
# SPORT_CATEGORIES completeness
# ---------------------------------------------------------------------------
def test_sport_categories_contains_expected():
    for cat in ("Bike", "Run", "Walking", "Ski", "Swimming", "Climbing", "Unknown"):
        assert cat in SPORT_CATEGORIES
