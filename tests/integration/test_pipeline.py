"""Integration tests: write real GPX files → combine_trips → verify JS output."""

import os
import json
import sys
import pytest

import combine_trips as ct


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _gpx(source=None, sport=None,
         lat0=48.85, lon0=2.35,
         lat1=48.86, lon1=2.36,
         lat2=48.87, lon2=2.37,
         time_base="2024-01-15T08"):
    comments = ""
    if source:
        comments += f"<!-- heatmap-source: {source} -->\n"
    if sport:
        comments += f"<!-- heatmap-sport: {sport} -->\n"
    return (
        f'<?xml version="1.0"?>\n{comments}'
        f'<gpx version="1.1"><trk><trkseg>'
        f'<trkpt lat="{lat0}" lon="{lon0}"><ele>100</ele><time>{time_base}:00:00Z</time></trkpt>'
        f'<trkpt lat="{lat1}" lon="{lon1}"><ele>110</ele><time>{time_base}:10:00Z</time></trkpt>'
        f'<trkpt lat="{lat2}" lon="{lon2}"><ele>105</ele><time>{time_base}:20:00Z</time></trkpt>'
        f'</trkseg></trk></gpx>'
    )


def _gpx_with_power(source="garmin", sport="Bike", namespace=False):
    """GPX content with power data — bare <power> or Garmin-style <ns3:power>."""
    ns_attr = ' xmlns:ns3="http://www.garmin.com/xmlschemas/ActivityExtension/v2"' if namespace else ''
    pwr_tag = 'ns3:power' if namespace else 'power'
    comments = f"<!-- heatmap-source: {source} -->\n<!-- heatmap-sport: {sport} -->\n"
    def pt(lat, lon, ele, time, pwr):
        return (
            f'<trkpt lat="{lat}" lon="{lon}"><ele>{ele}</ele>'
            f'<time>2024-01-15T08:{time}:00Z</time>'
            f'<extensions><{pwr_tag}>{pwr}</{pwr_tag}></extensions></trkpt>'
        )
    return (
        f'<?xml version="1.0"?>\n{comments}'
        f'<gpx version="1.1"{ns_attr}><trk><trkseg>'
        + pt(48.85, 2.35, 100, "00", 200)
        + pt(48.86, 2.36, 110, "10", 220)
        + pt(48.87, 2.37, 105, "20", 210)
        + '</trkseg></trk></gpx>'
    )


def _run_combine(source_dir, output_dir, extra_args=None):
    """Run combine_trips.main() with test arguments (no subprocess)."""
    old_argv = sys.argv[:]
    try:
        sys.argv = [
            "combine_trips.py",
            "--source-dir", str(source_dir),
            "--output-dir", str(output_dir),
            "--limit", "0",
        ] + (extra_args or [])
        ct.main()
    finally:
        sys.argv = old_argv


def _load_output(output_dir):
    raw_data = os.path.join(str(output_dir), "raw_data.js")
    assert os.path.exists(raw_data), "raw_data.js not created"
    return ct.load_js_data(raw_data)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestCombineTripsIntegration:
    def test_single_trip_appears_in_output(self, tmp_path):
        src = tmp_path / "raw_gpx"
        src.mkdir()
        out = tmp_path / "processed"
        out.mkdir()
        (src / "2024-01-15_Morning-Run-1.gpx").write_text(_gpx(source="garmin", sport="Run"), encoding="utf-8")

        _run_combine(src, out)

        data = _load_output(out)
        assert len(data["tripMeta"]) == 1
        meta = data["tripMeta"][0]
        assert meta["name"] == "2024-01-15_Morning-Run-1.gpx"
        assert meta["date"] == "2024-01-15"
        assert meta["sport"] == "Run"
        assert meta.get("source") == "garmin"

    def test_multiple_trips(self, tmp_path):
        src = tmp_path / "raw_gpx"
        src.mkdir()
        out = tmp_path / "processed"
        out.mkdir()
        trips = [
            ("2024-01-10_Bike-Ride-1.gpx", "garmin", "Bike"),
            ("2024-01-11_Hiking-Trail-2.gpx", "komoot", "Walking"),
            ("2024-01-12_Run-Session-3.gpx", "strava", "Run"),
        ]
        for name, source, sport in trips:
            (src / name).write_text(_gpx(source=source, sport=sport), encoding="utf-8")

        _run_combine(src, out)

        data = _load_output(out)
        assert len(data["tripMeta"]) == 3
        names = {t["name"] for t in data["tripMeta"]}
        assert names == {n for n, _, _ in trips}

    def test_sport_filter(self, tmp_path):
        src = tmp_path / "raw_gpx"
        src.mkdir()
        out = tmp_path / "processed"
        out.mkdir()
        (src / "2024-01-10_Bike-Ride-1.gpx").write_text(_gpx(source="garmin", sport="Bike"), encoding="utf-8")
        (src / "2024-01-11_Morning-Run-2.gpx").write_text(_gpx(source="garmin", sport="Run"), encoding="utf-8")

        _run_combine(src, out, extra_args=["--sport", "Run"])

        data = _load_output(out)
        assert len(data["tripMeta"]) == 1
        assert data["tripMeta"][0]["sport"] == "Run"

    def test_only_new_merges_with_existing(self, tmp_path):
        src = tmp_path / "raw_gpx"
        src.mkdir()
        out = tmp_path / "processed"
        out.mkdir()

        # First pass: one trip
        (src / "2024-01-10_Bike-Ride-1.gpx").write_text(_gpx(source="garmin", sport="Bike"), encoding="utf-8")
        _run_combine(src, out)

        # Second pass: add a new trip with --only-new
        (src / "2024-01-11_Run-Session-2.gpx").write_text(_gpx(source="strava", sport="Run"), encoding="utf-8")
        _run_combine(src, out, extra_args=["--only-new"])

        data = _load_output(out)
        assert len(data["tripMeta"]) == 2

    def test_trip_detail_file_created(self, tmp_path):
        src = tmp_path / "raw_gpx"
        src.mkdir()
        out = tmp_path / "processed"
        out.mkdir()
        (src / "2024-01-15_Morning-Run-1.gpx").write_text(_gpx(), encoding="utf-8")

        _run_combine(src, out)

        detail_dir = out / "trip_details"
        assert detail_dir.exists()
        detail_files = list(detail_dir.glob("trip_*.js"))
        assert len(detail_files) == 1

    def test_gzip_copy_created(self, tmp_path):
        src = tmp_path / "raw_gpx"
        src.mkdir()
        out = tmp_path / "processed"
        out.mkdir()
        (src / "2024-01-15_Morning-Run-1.gpx").write_text(_gpx(), encoding="utf-8")

        _run_combine(src, out)

        assert (out / "raw_data.js.gz").exists()

    def test_sport_detected_from_filename_when_no_comment(self, tmp_path):
        src = tmp_path / "raw_gpx"
        src.mkdir()
        out = tmp_path / "processed"
        out.mkdir()
        # No embedded sport comment; sport should be inferred from filename
        (src / "2024-01-15_Vélo-du-dimanche-1.gpx").write_text(_gpx(), encoding="utf-8")

        _run_combine(src, out)

        data = _load_output(out)
        assert data["tripMeta"][0]["sport"] == "Bike"

    def test_power_in_trip_detail(self, tmp_path):
        src = tmp_path / "raw_gpx"
        src.mkdir()
        out = tmp_path / "processed"
        out.mkdir()
        (src / "2024-01-15_Bike-Ride-1.gpx").write_text(
            _gpx_with_power(namespace=False), encoding="utf-8"
        )

        _run_combine(src, out)

        detail_files = list((out / "trip_details").glob("trip_*.js"))
        assert len(detail_files) == 1
        raw = detail_files[0].read_text(encoding="utf-8")
        detail_data = ct.load_js_data(detail_files[0])
        key = [k for k in detail_data if k.startswith("tripDetail_")][0]
        assert "pwr" in detail_data[key], "power array missing from trip_detail"

    def test_garmin_namespaced_power_in_trip_detail(self, tmp_path):
        # Garmin exports <ns3:power> — must reach trip_detail["pwr"]
        src = tmp_path / "raw_gpx"
        src.mkdir()
        out = tmp_path / "processed"
        out.mkdir()
        (src / "2024-01-15_Bike-Ride-1.gpx").write_text(
            _gpx_with_power(namespace=True), encoding="utf-8"
        )

        _run_combine(src, out)

        detail_files = list((out / "trip_details").glob("trip_*.js"))
        detail_data = ct.load_js_data(detail_files[0])
        key = [k for k in detail_data if k.startswith("tripDetail_")][0]
        pwr = detail_data[key].get("pwr")
        assert pwr is not None, "namespaced <ns3:power> not extracted into trip_detail"
        assert pwr[0] == 200
