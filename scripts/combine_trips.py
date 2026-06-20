import os
import re
import json
import glob
import gzip
import math
import sys
import argparse
from datetime import datetime

if getattr(sys, 'frozen', False):
    DIR = os.path.dirname(sys.executable)
else:
    DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root


def resolve_dir(path):
    """Resolve a path argument relative to this script's folder unless absolute."""
    return path if os.path.isabs(path) else os.path.join(DIR, path)


# Decimate raw GPS tracks: keep every Nth point (plus the last) for rawAll /
# trip_details. Lower stride = smoother map lines, larger raw_data.js.
RAW_POINT_STRIDE = 3

# Minimum distance (metres) between consecutive points in the exported GPS
# track segments (rawAll). Below this the point is skipped as a near-duplicate.
GPS_MIN_STEP_M = 5.0

NAME_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})_')
TITLE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}_(.+)-\d+\.gpx$', re.IGNORECASE)
DATETIME_SUFFIX_RE = re.compile(r'\s+\d{2}\.\d{2}\.\d{4}\s+\d{3,4}$')

SPORT_KEYWORDS = [
    (re.compile(r'v[ée]lo|vtt|cyclisme|gravel|mtb|giro', re.IGNORECASE), "Bike"),
    (re.compile(r'ski', re.IGNORECASE), "Ski"),
    (re.compile(r'natation|nage|swim', re.IGNORECASE), "Swimming"),
    (re.compile(r'course|running', re.IGNORECASE), "Run"),
    (re.compile(r'randonn|marche|hiking|walk', re.IGNORECASE), "Walking"),
]


def trip_sport(filename):
    m = TITLE_RE.match(filename)
    title = DATETIME_SUFFIX_RE.sub('', m.group(1)).strip() if m else ""
    for pattern, category in SPORT_KEYWORDS:
        if pattern.search(title):
            return category
    return "Bike"


def load_js_data(path):
    """Parse a 'var name = <json>;' per-line file (as written by this script)."""
    data = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh.read().split(";\n"):
            line = line.strip()
            if not line.startswith("var "):
                continue
            name, _, value = line[len("var "):].partition("=")
            value = value.strip()
            if value.endswith(";"):
                value = value[:-1]
            data[name.strip()] = json.loads(value)
    return data


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def trip_distance_km(trip):
    d = 0.0
    for i in range(len(trip) - 1):
        d += haversine_m(trip[i][0], trip[i][1], trip[i + 1][0], trip[i + 1][1])
    return d / 1000.0


TRKPT_SPLIT_RE = re.compile(r'<trkpt\b')
LATLON_RE = re.compile(r'\blat="([\-0-9.]+)"\s+lon="([\-0-9.]+)"')
TZ_RE = re.compile(r'^(.*[+-]\d{2})(\d{2})$')


def _find_tag(block, tag):
    """Return the inner text of the first <*:tag> or <tag> element in block."""
    m = re.search(r'<(?:[^:>\s]+:)?' + re.escape(tag) + r'(?:\s[^>]*)?>([^<]+)<', block)
    return m.group(1).strip() if m else None


def parse_time(s):
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    m = TZ_RE.match(s)
    if m:
        s = m.group(1) + ":" + m.group(2)
    return datetime.fromisoformat(s)


def load_trip(path):
    """Parse a GPX file and return a list of
    (lat, lon, ele_or_None, datetime, extras_dict) tuples.
    extras_dict contains optional keys: hr, cad, pwr, tmp (all int/float or None)."""
    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read()
    trip = []
    for block in TRKPT_SPLIT_RE.split(content)[1:]:
        m = LATLON_RE.search(block[:120])
        if not m:
            continue
        try:
            lat, lon = float(m.group(1)), float(m.group(2))
            time_s = _find_tag(block, 'time')
            if not time_s:
                continue
            ele_s = _find_tag(block, 'ele')
            ele = float(ele_s) if ele_s else None
            hr_s  = _find_tag(block, 'hr')
            cad_s = _find_tag(block, 'cad')
            pwr_s = _find_tag(block, 'power')
            tmp_s = _find_tag(block, 'atemp')
            extras = {
                'hr':  int(round(float(hr_s)))  if hr_s  else None,
                'cad': int(round(float(cad_s))) if cad_s else None,
                'pwr': int(round(float(pwr_s))) if pwr_s else None,
                'tmp': round(float(tmp_s), 1)   if tmp_s else None,
            }
            trip.append((lat, lon, ele, parse_time(time_s), extras))
        except Exception:
            continue
    return trip


def decimate_trip(trip):
    """Keep every RAW_POINT_STRIDE-th point plus the last, then remove points
    closer than GPS_MIN_STEP_M to the previous kept point."""
    if len(trip) <= 2:
        return trip
    strided = trip[::RAW_POINT_STRIDE]
    if strided[-1] != trip[-1]:
        strided = strided + [trip[-1]]
    pts = [strided[0]]
    for pt in strided[1:]:
        if haversine_m(pts[-1][0], pts[-1][1], pt[0], pt[1]) >= GPS_MIN_STEP_M:
            pts.append(pt)
        elif pt == strided[-1]:
            pts.append(pt)
    return pts


# Maximum plausible GPS speed (km/h) per sport — spikes above this are clamped.
SPORT_MAX_SPEED_KMH = {
    "Bike":     120.0,
    "Run":       45.0,
    "Walking":   20.0,
    "Ski":      200.0,
    "Swimming":  12.0,
}


def _median5(vals, i):
    """Median of up to 5 values centred on index i."""
    lo, hi = max(0, i - 2), min(len(vals), i + 3)
    window = sorted(vals[lo:hi])
    return window[len(window) // 2]


def trip_detail(trip, sport="Unknown"):
    """Per-point distance (m), elapsed time (s), elevation (m), speed (km/h),
    and optional sensor data (hr, cad, pwr), aligned 1:1 with the decimated
    rawAll coordinates for this trip."""
    d = [0.0]
    t = [0.0]
    e = [round(trip[0][2], 1) if trip[0][2] is not None else None]
    raw_s = [0.0]
    t0 = trip[0][3]

    ex0 = trip[0][4] if len(trip[0]) > 4 else {}
    hr_raw  = [ex0.get('hr')]
    cad_raw = [ex0.get('cad')]
    pwr_raw = [ex0.get('pwr')]

    for i in range(1, len(trip)):
        pt0, pt1 = trip[i - 1], trip[i]
        dd = haversine_m(pt0[0], pt0[1], pt1[0], pt1[1])
        dt = (pt1[3] - pt0[3]).total_seconds()
        d.append(round(d[-1] + dd, 1))
        t.append(round((pt1[3] - t0).total_seconds(), 1))
        e.append(round(pt1[2], 1) if pt1[2] is not None else None)
        raw_s.append((dd / dt) * 3.6 if dt > 0 else 0.0)
        ex1 = pt1[4] if len(pt1) > 4 else {}
        hr_raw.append(ex1.get('hr'))
        cad_raw.append(ex1.get('cad'))
        pwr_raw.append(ex1.get('pwr'))

    max_speed = SPORT_MAX_SPEED_KMH.get(sport, 200.0)
    s = [round(min(_median5(raw_s, i), max_speed), 1) for i in range(len(raw_s))]
    result = {"d": d, "t": t, "e": e, "s": s}
    if any(v is not None for v in hr_raw):
        result["hr"] = hr_raw
    if any(v is not None for v in cad_raw):
        result["cad"] = cad_raw
    if any(v is not None for v in pwr_raw):
        result["pwr"] = pwr_raw
    return result


def write_gzip_copy(path):
    """Write a precompressed "<path>.gz" sibling for servers that support
    Content-Encoding: gzip."""
    with open(path, "rb") as src, gzip.open(path + ".gz", "wb") as dst:
        dst.write(src.read())


def main():
    parser = argparse.ArgumentParser(description="Combine GPS trips into a heatmap.")
    parser.add_argument("--source-dir", type=str, default="raw_gpx",
                        help="Folder containing raw .gpx files. Default: raw_gpx.")
    parser.add_argument("--output-dir", type=str, default="processed",
                        help="Folder to write raw_data.js and trip_details/ into. Default: processed.")
    parser.add_argument("--limit", type=int, default=100,
                        help="Process only the N most recent files. 0 = all. Default: 100.")
    parser.add_argument("--start-date", type=str, default=None,
                        help="Only include trips on or after this date (YYYY-MM-DD).")
    parser.add_argument("--end-date", type=str, default=None,
                        help="Only include trips on or before this date (YYYY-MM-DD).")
    parser.add_argument("--min-distance", type=float, default=0.0,
                        help="Only include trips with at least this distance in km. Default: 0.")
    parser.add_argument("--sport", type=str, default=None,
                        help="Only include trips matching this sport type.")
    parser.add_argument("--only-new", action="store_true",
                        help="Only process trips not already in <output-dir>/raw_data.js, "
                             "merging the results into the existing data.")
    parser.add_argument("--files", nargs="+", default=None,
                        help="Explicit list of .gpx basenames to process (ignores date/limit/sport filters).")
    args = parser.parse_args()

    SOURCE_DIR = resolve_dir(args.source_dir)
    OUTPUT_DIR = resolve_dir(args.output_dir)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    ALL_FILES = sorted(
        os.path.basename(f) for f in glob.glob(os.path.join(SOURCE_DIR, "*.gpx"))
    )

    if args.files:
        all_set = set(ALL_FILES)
        SAMPLE_FILES = [f for f in args.files if f in all_set]
    else:
        SAMPLE_FILES = []
        for f in ALL_FILES:
            m = NAME_RE.match(f)
            date = m.group(1) if m else ""
            if args.start_date and date < args.start_date:
                continue
            if args.end_date and date > args.end_date:
                continue
            if args.sport and trip_sport(f) != args.sport:
                continue
            SAMPLE_FILES.append(f)
        if args.limit > 0:
            SAMPLE_FILES = SAMPLE_FILES[-args.limit:]

    RAW_DATA_PATH = os.path.join(OUTPUT_DIR, "raw_data.js")
    TRIP_DETAILS_DIR = os.path.join(OUTPUT_DIR, "trip_details")
    os.makedirs(TRIP_DETAILS_DIR, exist_ok=True)

    existing_trips_meta = []
    existing_raw_all = []

    if args.only_new:
        if os.path.exists(RAW_DATA_PATH):
            raw_existing = load_js_data(RAW_DATA_PATH)
            existing_trips_meta = raw_existing.get("tripMeta", [])
            existing_raw_all = raw_existing.get("rawAll", [])

        existing_names = {t["name"] for t in existing_trips_meta}
        SAMPLE_FILES = [f for f in SAMPLE_FILES if f not in existing_names]
        print(f"Only-new mode: {len(existing_trips_meta)} trip(s) already processed, "
              f"{len(SAMPLE_FILES)} new candidate file(s)", file=sys.stderr, flush=True)

    print(f"Considering {len(SAMPLE_FILES)} files from {SOURCE_DIR} "
          f"(limit={args.limit or 'all'}, start_date={args.start_date}, "
          f"end_date={args.end_date}, min_distance={args.min_distance} km, "
          f"sport={args.sport})", file=sys.stderr, flush=True)

    trip_idx_offset = len(existing_trips_meta)
    trips_meta = []
    raw_all = []
    n_too_short = 0

    for f in SAMPLE_FILES:
        trip = load_trip(os.path.join(SOURCE_DIR, f))
        if len(trip) < 2:
            continue
        dist_km = round(trip_distance_km(trip), 1)
        if dist_km < args.min_distance:
            n_too_short += 1
            continue
        m = NAME_RE.match(f)
        trip_idx = trip_idx_offset + len(trips_meta)
        trips_meta.append({
            "name": f,
            "date": m.group(1) if m else "",
            "distanceKm": dist_km,
            "sport": trip_sport(f),
        })
        raw_trip = decimate_trip(trip)
        raw_all.append([[round(pt[0], 6), round(pt[1], 6)] for pt in raw_trip])

        detail = trip_detail(raw_trip, sport=trip_sport(f))
        with open(os.path.join(TRIP_DETAILS_DIR, f"trip_{trip_idx}.js"), "w", encoding="utf-8") as out:
            out.write(f"var tripDetail_{trip_idx} = ")
            json.dump(detail, out)
            out.write(";")

        print(f"  {f}: {len(trip)} points -> {len(raw_trip)} kept", file=sys.stderr, flush=True)

    print(f"Processing {len(raw_all)} files "
          f"({n_too_short} skipped for being shorter than {args.min_distance} km)",
          file=sys.stderr, flush=True)

    if args.only_new:
        trips_meta = existing_trips_meta + trips_meta
        raw_all = existing_raw_all + raw_all

    with open(RAW_DATA_PATH, "w", encoding="utf-8") as out:
        out.write("var rawAll = ")
        json.dump(raw_all, out)
        out.write(";\nvar tripMeta = ")
        json.dump(trips_meta, out)
        out.write(";")

    write_gzip_copy(RAW_DATA_PATH)
    print(f"Wrote {RAW_DATA_PATH} ({len(trips_meta)} trips total)", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
