"""Pure utility functions for trip filename parsing and GPX metadata extraction.

No GUI, no network, no side effects at import time.  Imported by trip_manager,
combine_trips, data_wizard and the test suite.
"""

import os
import re

# ---------------------------------------------------------------------------
# Filename regexes
# ---------------------------------------------------------------------------
NAME_RE            = re.compile(r'^(\d{4}-\d{2}-\d{2})_')
ID_RE              = re.compile(r'-(\d+)\.gpx$', re.IGNORECASE)
TITLE_RE           = re.compile(r'^\d{4}-\d{2}-\d{2}_(.+)-\d+\.gpx$', re.IGNORECASE)
DATETIME_SUFFIX_RE = re.compile(r'\s+\d{2}\.\d{2}\.\d{4}\s+\d{3,4}$')

# ---------------------------------------------------------------------------
# Sport detection
# ---------------------------------------------------------------------------
SPORT_CATEGORIES = ["Bike", "Run", "Walking", "Ski", "Swimming", "Climbing", "Unknown"]

SPORT_KEYWORDS = [
    (re.compile(r'bike|bicycl|cycling|v[ée]lo|vtt|cyclisme|gravel|mtb|giro|cyclo', re.IGNORECASE), "Bike"),
    (re.compile(r'ski|snowboard', re.IGNORECASE), "Ski"),
    (re.compile(r'natation|nage|swim', re.IGNORECASE), "Swimming"),
    (re.compile(r'climb|escalad|boulder', re.IGNORECASE), "Climbing"),
    (re.compile(r'course|running|run\b', re.IGNORECASE), "Run"),
    (re.compile(r'randonn|marche|hiking|walk', re.IGNORECASE), "Walking"),
]

# GPX comment format written by inject_gpx_metadata()
_GPX_META_RE = re.compile(r'<!--\s*heatmap-(\w+):\s*(\S+)\s*-->')

# Sensor-data detection (same tag names used by combine_trips.py's _find_tag)
_HR_RE  = re.compile(r'<(?:[^:>\s]+:)?hr\b',    re.IGNORECASE)
_CAD_RE = re.compile(r'<(?:[^:>\s]+:)?cad\b',   re.IGNORECASE)
_PWR_RE = re.compile(r'<(?:[^:>\s]+:)?power\b',  re.IGNORECASE)
_TMP_RE = re.compile(r'<(?:[^:>\s]+:)?atemp\b', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def trip_id(filename: str):
    """Return the numeric ID suffix from a GPX filename, or None."""
    m = ID_RE.search(filename)
    return m.group(1) if m else None


def trip_date(filename: str) -> str:
    """Return the YYYY-MM-DD prefix of a GPX filename, or ''."""
    m = NAME_RE.match(filename)
    return m.group(1) if m else ""


def read_gpx_meta(filepath: str):
    """Return (source, sport) embedded as ``<!-- heatmap-*: -->`` comments.

    Only the first 1 KB of the file is read.  Returns ('', '') on any error.
    """
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as fh:
            head = fh.read(1024)
    except OSError:
        return '', ''
    tags = {m.group(1): m.group(2) for m in _GPX_META_RE.finditer(head)}
    return tags.get('source', ''), tags.get('sport', '')


def gpx_sensors(filepath: str) -> str:
    """Return a compact string listing sensor data present in a GPX file.

    Reads only the first 16 KB — enough to reach the initial trackpoints even
    in files with large metadata headers.  Returns e.g. ``"HR Cad Pwr"`` or
    ``""`` when no optional sensors are detected.
    """
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as fh:
            head = fh.read(16384)
    except OSError:
        return ''
    tags = []
    if _HR_RE.search(head):  tags.append('HR')
    if _CAD_RE.search(head): tags.append('Cad')
    if _PWR_RE.search(head): tags.append('Pwr')
    if _TMP_RE.search(head): tags.append('Tmp')
    return ' '.join(tags)


def trip_sport(filename: str, filepath: str = None) -> str:
    """Determine sport category for a GPX file.

    Priority order:
    1. ``<!-- heatmap-sport: X -->`` comment embedded by the import tools.
    2. Keywords matched against the activity title extracted from *filename*.
    3. ``"Unknown"`` as a safe default.
    """
    if filepath:
        _, embedded = read_gpx_meta(filepath)
        if embedded:
            return embedded
    m = TITLE_RE.match(filename)
    title = DATETIME_SUFFIX_RE.sub('', m.group(1)).strip() if m else ""
    for pattern, category in SPORT_KEYWORDS:
        if pattern.search(title):
            return category
    return "Unknown"
