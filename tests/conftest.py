"""Shared pytest fixtures for the HeatMapCreator test suite.

Key concern: trip_manager.py calls resolve_data_dir() at module import time.
We set TRIPMANAGER_DATA_DIR *before* importing anything from scripts/ so that
no GUI dialog is triggered.
"""

import os
import sys
import pytest

# Ensure scripts/ is on sys.path for all tests
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Minimal GPX strings used as fixtures
# ---------------------------------------------------------------------------
GPX_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
{comments}<gpx version="1.1" creator="test">
  <trk><trkseg>
    <trkpt lat="{lat0}" lon="{lon0}"><ele>100</ele><time>2024-01-15T08:00:00Z</time></trkpt>
    <trkpt lat="{lat1}" lon="{lon1}"><ele>110</ele><time>2024-01-15T08:10:00Z</time></trkpt>
    <trkpt lat="{lat2}" lon="{lon2}"><ele>105</ele><time>2024-01-15T08:20:00Z</time></trkpt>
  </trkseg></trk>
</gpx>
"""

def _make_gpx(source=None, sport=None,
              lat0=48.85, lon0=2.35,
              lat1=48.86, lon1=2.36,
              lat2=48.87, lon2=2.37):
    comments = ""
    if source:
        comments += f"<!-- heatmap-source: {source} -->\n"
    if sport:
        comments += f"<!-- heatmap-sport: {sport} -->\n"
    return GPX_TEMPLATE.format(
        comments=comments,
        lat0=lat0, lon0=lon0,
        lat1=lat1, lon1=lon1,
        lat2=lat2, lon2=lon2,
    )


@pytest.fixture
def gpx_plain():
    """GPX with no heatmap metadata."""
    return _make_gpx()


@pytest.fixture
def gpx_garmin_bike():
    return _make_gpx(source="garmin", sport="Bike")


@pytest.fixture
def gpx_komoot_run():
    return _make_gpx(source="komoot", sport="Run")


@pytest.fixture
def gpx_strava_climbing():
    return _make_gpx(source="strava", sport="Climbing")


@pytest.fixture
def make_gpx():
    """Factory fixture: make_gpx(source=..., sport=...)."""
    return _make_gpx


# ---------------------------------------------------------------------------
# tmp data directory — set env var BEFORE any script imports
# ---------------------------------------------------------------------------
@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Create a temporary data directory tree and expose it via env-var.

    Sets TRIPMANAGER_DATA_DIR so that resolve_data_dir() never opens a GUI
    dialog.  Also creates raw_gpx/ and processed/ subdirectories.
    """
    raw = tmp_path / "raw_gpx"
    raw.mkdir()
    (tmp_path / "processed").mkdir()
    monkeypatch.setenv("TRIPMANAGER_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def raw_gpx_dir(data_dir):
    return data_dir / "raw_gpx"


@pytest.fixture
def processed_dir(data_dir):
    return data_dir / "processed"


# ---------------------------------------------------------------------------
# Helper: write a named GPX file into raw_gpx/
# ---------------------------------------------------------------------------
@pytest.fixture
def write_gpx(raw_gpx_dir, make_gpx):
    """Return a callable write_gpx(name, source=None, sport=None) -> Path."""
    def _write(name, source=None, sport=None, content=None):
        path = raw_gpx_dir / name
        path.write_text(content if content is not None else make_gpx(source=source, sport=sport),
                        encoding="utf-8")
        return path
    return _write
