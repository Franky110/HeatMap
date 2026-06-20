"""Shared GUI/logic for the "Import from <provider>" windows (Komoot, Strava,
Garmin, ...). Each provider module supplies a small subclass of
ImportWindowBase that implements authentication, fetching the activity list,
and downloading a single activity as GPX; this module provides the common
window layout (filters, activity table, download buttons, log) and the
download/filter logic shared by all of them.
"""

import os
import sys
import threading
import http.server
import urllib.parse
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox
from xml.sax.saxutils import escape

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPTS_DIR)  # project root

# komootgpx is bundled at the project root in releases; in development it lives
# as a sibling of the project root. Check project root first, then fall back.
if os.path.isdir(os.path.join(_PROJECT_DIR, "komootgpx")):
    if _PROJECT_DIR not in sys.path:
        sys.path.insert(0, _PROJECT_DIR)
else:
    _parent = os.path.dirname(_PROJECT_DIR)
    if _parent not in sys.path:
        sys.path.insert(0, _parent)

from komootgpx.utils import sanitize_filename

from trip_manager import list_trips, trip_id, SOURCE_DIR, load_config, save_config


# ---------------------------------------------------------------------------
# Sport type normalisation: map raw provider type strings → canonical category
# ---------------------------------------------------------------------------
SPORT_TYPE_MAP = {
    # Garmin typeKey values
    'running': 'Run', 'trail_running': 'Run', 'treadmill_running': 'Run',
    'virtual_running': 'Run', 'track_running': 'Run',
    'cycling': 'Bike', 'mountain_biking': 'Bike', 'indoor_cycling': 'Bike',
    'virtual_ride': 'Bike', 'gravel_cycling': 'Bike', 'road_cycling': 'Bike',
    'bmx': 'Bike', 'cyclocross': 'Bike', 'e_bike_fitness': 'Bike',
    'swimming': 'Swimming', 'open_water_swimming': 'Swimming', 'lap_swimming': 'Swimming',
    'hiking': 'Walking', 'walking': 'Walking', 'casual_walking': 'Walking',
    'speed_walking': 'Walking', 'indoor_walking': 'Walking',
    'skiing': 'Ski', 'resort_skiing_snowboarding': 'Ski', 'backcountry_skiing': 'Ski',
    'snowboarding': 'Ski', 'cross_country_skiing': 'Ski', 'skate_skiing': 'Ski',
    'rock_climbing': 'Climbing', 'bouldering': 'Climbing', 'indoor_climbing': 'Climbing',
    # Strava sport_type values
    'Run': 'Run', 'TrailRun': 'Run', 'VirtualRun': 'Run', 'Treadmill': 'Run',
    'Ride': 'Bike', 'VirtualRide': 'Bike', 'MountainBikeRide': 'Bike',
    'GravelRide': 'Bike', 'EBikeRide': 'Bike', 'EMountainBikeRide': 'Bike',
    'Velomobile': 'Bike', 'Handcycle': 'Bike',
    'Swim': 'Swimming',
    'Hike': 'Walking', 'Walk': 'Walking',
    'AlpineSki': 'Ski', 'BackcountrySki': 'Ski', 'NordicSki': 'Ski', 'Snowboard': 'Ski',
    'RockClimbing': 'Climbing',
}


def normalize_sport(activity_type):
    """Return a canonical sport category from a raw provider type string, or ''."""
    return SPORT_TYPE_MAP.get(activity_type, '')


def inject_gpx_metadata(gpx_content, provider_key, activity_type=''):
    """Prepend heatmap-source / heatmap-sport comments to a GPX string."""
    lines = [f'<!-- heatmap-source: {provider_key} -->']
    sport = normalize_sport(activity_type)
    if sport:
        lines.append(f'<!-- heatmap-sport: {sport} -->')
    return '\n'.join(lines) + '\n' + gpx_content


# ---------------------------------------------------------------------------
# Headless / auto-check helpers
# ---------------------------------------------------------------------------

class _FakeVar:
    """Drop-in for tk.BooleanVar / tk.StringVar in headless (no-GUI) mode."""
    def __init__(self, value): self._v = value
    def get(self): return self._v
    def set(self, v): self._v = v
    def trace_add(self, *a): pass


def _apply_headless_filters(tours, defaults, type_choices):
    """Filter a {id: meta} dict using a saved-defaults dict."""
    type_choice    = type_choices.get(defaults.get('type', 'All'), 'all')
    start_date     = defaults.get('start_date', '')
    end_date       = defaults.get('end_date', '')
    excluded_sports = set(defaults.get('excluded_sports', []))
    try:
        min_dist = float(defaults.get('min_dist', 0))
    except (TypeError, ValueError):
        min_dist = 0.0

    result = {}
    for tid, t in tours.items():
        if type_choice != 'all' and t.get('type') != type_choice:
            continue
        if excluded_sports and t.get('sport') in excluded_sports:
            continue
        date = (t.get('date') or '')[:10]
        if start_date and date < start_date:
            continue
        if end_date and date > end_date:
            continue
        if (t.get('distance') or 0) / 1000.0 < min_dist:
            continue
        result[tid] = t
    return result


# ---------------------------------------------------------------------------
# OAuth2 "authorization code" helper: opens the system browser at
# authorize_url_fn(redirect_uri) and runs a one-shot local HTTP server to
# catch the "...?code=..." redirect. Used by providers (e.g. Strava) whose
# API requires a browser-based login.
# ---------------------------------------------------------------------------
class _OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        self.server.oauth_code = params.get("code", [None])[0]
        self.server.oauth_error = params.get("error", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        if self.server.oauth_error:
            msg = f"Authorization failed: {self.server.oauth_error}. You can close this tab."
        else:
            msg = "Authorization complete. You can close this tab and return to Trip Manager."
        self.wfile.write(f"<html><body style='font-family:sans-serif'>{msg}</body></html>".encode())

    def log_message(self, *args, **kwargs):
        pass


def run_oauth_flow(authorize_url_fn, port=8765, timeout=180):
    """Open the browser for an OAuth2 authorization-code flow and block until
    the provider redirects back to http://localhost:<port>/callback with
    either "code" or "error". Returns (code, redirect_uri)."""
    server = http.server.HTTPServer(("localhost", port), _OAuthCallbackHandler)
    server.oauth_code = None
    server.oauth_error = None
    server.timeout = timeout

    redirect_uri = f"http://localhost:{port}/callback"
    webbrowser.open(authorize_url_fn(redirect_uri))

    server.handle_request()
    server.server_close()

    if server.oauth_error:
        raise RuntimeError(f"Authorization was not granted ({server.oauth_error}).")
    if not server.oauth_code:
        raise RuntimeError("Timed out waiting for authorization in the browser.")
    return server.oauth_code, redirect_uri


# ---------------------------------------------------------------------------
# Minimal GPX writer, for providers whose API returns raw point streams
# (lat/lon/elevation/time) rather than a ready-made GPX file.
# ---------------------------------------------------------------------------
def build_gpx(name, points, start_time):
    """points: list of (lat, lon, ele_or_None, seconds_offset_or_None)
    or (lat, lon, ele_or_None, seconds_offset_or_None, extras_or_None).
    extras is a dict with optional keys: hr, cad, pwr, tmp."""
    from datetime import timedelta

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="TripManager"',
        '     xmlns="http://www.topografix.com/GPX/1/1"',
        '     xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">',
        '  <trk>',
        f'    <name>{escape(name)}</name>',
        '    <trkseg>',
    ]
    for pt in points:
        lat, lon, ele, offset_s = pt[0], pt[1], pt[2], pt[3]
        extras = pt[4] if len(pt) > 4 else None
        lines.append(f'      <trkpt lat="{lat}" lon="{lon}">')
        if ele is not None:
            lines.append(f'        <ele>{ele}</ele>')
        t = start_time + timedelta(seconds=offset_s or 0)
        lines.append(f'        <time>{t.strftime("%Y-%m-%dT%H:%M:%SZ")}</time>')
        if extras:
            ext_tags = []
            if extras.get('hr')  is not None: ext_tags.append(f'            <gpxtpx:hr>{int(extras["hr"])}</gpxtpx:hr>')
            if extras.get('cad') is not None: ext_tags.append(f'            <gpxtpx:cad>{int(extras["cad"])}</gpxtpx:cad>')
            if extras.get('pwr') is not None: ext_tags.append(f'            <gpxtpx:power>{int(extras["pwr"])}</gpxtpx:power>')
            if extras.get('tmp') is not None: ext_tags.append(f'            <gpxtpx:atemp>{extras["tmp"]}</gpxtpx:atemp>')
            if ext_tags:
                lines.append('        <extensions>')
                lines.append('          <gpxtpx:TrackPointExtension>')
                lines.extend(ext_tags)
                lines.append('          </gpxtpx:TrackPointExtension>')
                lines.append('        </extensions>')
        lines.append('      </trkpt>')
    lines += ['    </trkseg>', '  </trk>', '</gpx>']
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Common import window: filters, activity table, download buttons, log.
# Subclasses provide the credentials UI and the provider-specific calls.
# ---------------------------------------------------------------------------
class ImportWindowBase(tk.Toplevel):
    PROVIDER = "Provider"
    PROVIDER_KEY = ""  # config key; set in each subclass (e.g. "komoot")
    # label -> value used to filter tour["type"]; {"All": "all"} disables the filter.
    TYPE_CHOICES = {"All": "all"}

    def __init__(self, master, on_downloaded=None):
        super().__init__(master)
        self.title(f"Import from {self.PROVIDER}")
        self.geometry("820x600")
        self.on_downloaded = on_downloaded

        self.all_tours = {}   # id -> tour dict
        self.new_tours = {}   # id -> tour dict (not yet in library)
        self.existing_ids = set()  # tour ids already present as local GPX files

        self.build_auth_frame()
        self._build_data_options_frame()
        self._build_filters_frame()
        self._build_tree()
        self._build_download_controls()
        self._build_progress_and_log()

        # Pre-fill with saved defaults
        self._apply_defaults(load_config().get("import_defaults", {}).get(self.PROVIDER_KEY, {}))
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.update_idletasks()
        self.minsize(self.winfo_reqwidth(), self.winfo_reqheight())

    # ------------------------------------------------------------- to override
    def build_auth_frame(self):
        """Build the provider-specific credentials/connect UI. Must set
        self.login_btn to the button that starts login_and_fetch()."""
        raise NotImplementedError

    def authenticate(self):
        """Perform login / token refresh. Runs on a background thread; raise
        on failure."""
        raise NotImplementedError

    def fetch_tours(self):
        """Return {id: {"date", "name", "sport", "distance" (m), "type"}}."""
        raise NotImplementedError

    def download_activity(self, tid, tour_meta):
        """Return the GPX file content (str) for one activity."""
        raise NotImplementedError

    def pre_login_check(self):
        """Run on the UI thread before starting the login thread. Return
        False (after showing an error) to abort."""
        return True

    def _headless_init(self):
        """Load saved credentials into self.*_var without tkinter.
        Return True if credentials are available, False to abort auto-check."""
        return False

    # ------------------------------------------------- defaults load / save
    def _apply_defaults(self, d):
        if not d:
            return
        _bool = {'hr': self.import_hr_var, 'cad': self.import_cad_var,
                 'pwr': self.import_pwr_var, 'tmp': self.import_tmp_var}
        for key, var in _bool.items():
            if key in d:
                var.set(d[key])
        _str = {'tours_filter': self.tours_filter_var, 'type': self.type_var,
                'start_date': self.start_date_var, 'end_date': self.end_date_var,
                'min_dist': self.min_dist_var}
        for key, var in _str.items():
            if d.get(key) is not None:
                var.set(d[key])
        # Sport exclusions are applied later in _set_sports (sports aren't known yet)
        self._pending_excluded_sports = set(d.get('excluded_sports', []))

    def _collect_defaults(self):
        excluded = [s for s, v in self._sport_vars.items() if not v.get()]
        return {
            'hr':               self.import_hr_var.get(),
            'cad':              self.import_cad_var.get(),
            'pwr':              self.import_pwr_var.get(),
            'tmp':              self.import_tmp_var.get(),
            'tours_filter':     self.tours_filter_var.get(),
            'type':             self.type_var.get(),
            'start_date':       self.start_date_var.get(),
            'end_date':         self.end_date_var.get(),
            'min_dist':         self.min_dist_var.get(),
            'excluded_sports':  excluded,
        }

    def _save_defaults(self):
        if not self.PROVIDER_KEY:
            return
        cfg = load_config()
        cfg.setdefault("import_defaults", {})[self.PROVIDER_KEY] = self._collect_defaults()
        save_config(cfg)
        self._log(f"Settings saved as defaults for {self.PROVIDER}.")

    def _on_close(self):
        self._save_defaults()
        self.destroy()

    # ------------------------------------------------- headless / auto-check
    @classmethod
    def _run_headless(cls, log_fn, on_done, show_confirm=None):
        """Non-interactive auto-check: fetch new activities matching saved defaults.
        If show_confirm is provided, it is called with (provider_name, [(tid, meta), ...])
        and must return the subset the user approved; download is skipped if None."""
        try:
            defaults = load_config().get("import_defaults", {}).get(cls.PROVIDER_KEY, {})
            inst = object.__new__(cls)
            inst.import_hr_var  = _FakeVar(defaults.get('hr',  True))
            inst.import_cad_var = _FakeVar(defaults.get('cad', True))
            inst.import_pwr_var = _FakeVar(defaults.get('pwr', False))
            inst.import_tmp_var = _FakeVar(defaults.get('tmp', False))
            inst.remember_var   = _FakeVar(True)

            if not inst._headless_init():
                log_fn(f"{cls.PROVIDER} auto-check: no saved credentials — open the import window to connect.\n")
                if on_done: on_done(0)
                return

            log_fn(f"Auto-checking {cls.PROVIDER} for new activities…\n")
            inst.authenticate()
            all_tours = inst.fetch_tours()

            existing_ids = {tid for tid in (trip_id(f) for f in list_trips()) if tid}
            new_tours = {tid: t for tid, t in all_tours.items()
                         if str(tid) not in existing_ids}

            if not new_tours:
                log_fn(f"{cls.PROVIDER}: no new activities found.\n")
                if on_done: on_done(0)
                return

            filtered = _apply_headless_filters(new_tours, defaults, cls.TYPE_CHOICES)
            if not filtered:
                log_fn(f"{cls.PROVIDER}: no new activities match filters.\n")
                if on_done: on_done(0)
                return

            log_fn(f"{cls.PROVIDER}: {len(filtered)} new activity(ies) match filters.\n")

            if show_confirm is not None:
                approved = show_confirm(cls.PROVIDER, list(filtered.items()))
                if not approved:
                    log_fn(f"{cls.PROVIDER}: import skipped by user.\n")
                    if on_done: on_done(0)
                    return
                to_download = approved
            else:
                to_download = list(filtered.items())

            log_fn(f"{cls.PROVIDER}: downloading {len(to_download)} activity(ies)…\n")
            downloaded = 0
            for tid, meta in to_download:
                try:
                    gpx = inst.download_activity(tid, meta)
                    gpx = inject_gpx_metadata(gpx, cls.PROVIDER_KEY, meta.get('type', ''))
                    fname = inst.activity_filename(tid, meta)
                    with open(os.path.join(SOURCE_DIR, fname), 'w', encoding='utf-8') as fh:
                        fh.write(gpx)
                    downloaded += 1
                    log_fn(f"  Downloaded: {fname}\n")
                except Exception as e:
                    log_fn(f"  Error downloading {tid}: {e}\n")

            log_fn(f"{cls.PROVIDER} auto-check done: {downloaded} activity(ies) downloaded.\n")
            if on_done: on_done(downloaded)

        except Exception as e:
            log_fn(f"{cls.PROVIDER} auto-check failed: {e}\n")
            if on_done: on_done(0)

    def activity_filename(self, tid, tour_meta):
        date_str = (tour_meta.get("date") or "")[:10]
        name = sanitize_filename(tour_meta.get("name") or str(tid))
        return f"{date_str}_{name}-{tid}.gpx"

    # ----------------------------------------------------- data options
    def _build_data_options_frame(self):
        frame = ttk.LabelFrame(self, text="Data to import")
        frame.pack(fill="x", padx=10, pady=(0, 4))

        self.import_hr_var  = tk.BooleanVar(value=True)
        self.import_cad_var = tk.BooleanVar(value=True)
        self.import_pwr_var = tk.BooleanVar(value=False)
        self.import_tmp_var = tk.BooleanVar(value=False)

        ttk.Checkbutton(frame, text="Heart rate", variable=self.import_hr_var).pack(side="left", padx=8, pady=4)
        ttk.Checkbutton(frame, text="Cadence",    variable=self.import_cad_var).pack(side="left", padx=8, pady=4)
        ttk.Checkbutton(frame, text="Power",      variable=self.import_pwr_var).pack(side="left", padx=8, pady=4)
        ttk.Checkbutton(frame, text="Temperature",variable=self.import_tmp_var).pack(side="left", padx=8, pady=4)
        ttk.Label(frame, text="(Garmin: from GPX extensions already present; Strava: fetched on demand)",
                  foreground="#888", font=("", 9, "italic")).pack(side="left", padx=8)
        ttk.Button(frame, text="Save as defaults", command=self._save_defaults).pack(side="right", padx=8, pady=4)

    @property
    def selected_extras(self):
        return {
            'hr':  self.import_hr_var.get(),
            'cad': self.import_cad_var.get(),
            'pwr': self.import_pwr_var.get(),
            'tmp': self.import_tmp_var.get(),
        }

    # ------------------------------------------------------------------ UI
    def _build_filters_frame(self):
        filt = ttk.LabelFrame(self, text="Filters")
        filt.pack(fill="x", padx=10, pady=8)

        ttk.Label(filt, text="Tours:").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self.tours_filter_var = tk.StringVar(value="New tours")
        ttk.Combobox(filt, textvariable=self.tours_filter_var, values=["New tours", "All tours"],
                     state="readonly", width=12).grid(row=0, column=1, padx=4, pady=4, sticky="w")
        self.tours_filter_var.trace_add("write", lambda *a: self.apply_filters())

        ttk.Label(filt, text="Type:").grid(row=0, column=2, padx=4, pady=4, sticky="e")
        self.type_var = tk.StringVar(value="All")
        ttk.Combobox(filt, textvariable=self.type_var, values=list(self.TYPE_CHOICES.keys()),
                     state="readonly", width=12).grid(row=0, column=3, padx=4, pady=4, sticky="w")

        ttk.Label(filt, text="Sports:").grid(row=0, column=4, padx=4, pady=4, sticky="e")
        self._sport_btn = ttk.Button(filt, text="All ▾", width=18, command=self._toggle_sport_popup)
        self._sport_btn.grid(row=0, column=5, padx=4, pady=4, sticky="w")
        self._sport_vars = {}    # sport name -> tk.BooleanVar
        self._sport_popup = None
        self._pending_excluded_sports = set()  # populated by _apply_defaults, consumed by _set_sports

        ttk.Label(filt, text="Start date (YYYY-MM-DD):").grid(row=1, column=0, padx=4, pady=4, sticky="e")
        self.start_date_var = tk.StringVar()
        ttk.Entry(filt, textvariable=self.start_date_var, width=12).grid(row=1, column=1, padx=4, pady=4, sticky="w")

        ttk.Label(filt, text="End date (YYYY-MM-DD):").grid(row=1, column=2, padx=4, pady=4, sticky="e")
        self.end_date_var = tk.StringVar()
        ttk.Entry(filt, textvariable=self.end_date_var, width=12).grid(row=1, column=3, padx=4, pady=4, sticky="w")

        ttk.Label(filt, text="Min distance (km):").grid(row=1, column=4, padx=4, pady=4, sticky="e")
        self.min_dist_var = tk.StringVar(value="0")
        ttk.Entry(filt, textvariable=self.min_dist_var, width=8).grid(row=1, column=5, padx=4, pady=4, sticky="w")

        ttk.Button(filt, text="Apply filters", command=self.apply_filters).grid(row=1, column=6, padx=4, pady=4, sticky="e")

        self.filter_count_var = tk.StringVar(value="")
        ttk.Label(filt, textvariable=self.filter_count_var).grid(row=2, column=0, columnspan=7, padx=4, pady=(0, 4), sticky="w")

    # ---------------------------------------------------------- sport popup
    def _set_sports(self, sports):
        """Populate the sport multi-select with the given list, preserving
        any existing selections for sports that are still present."""
        old = {s: v.get() for s, v in self._sport_vars.items()}
        pending = getattr(self, '_pending_excluded_sports', set())
        self._sport_vars = {
            s: tk.BooleanVar(value=old.get(s, s not in pending))
            for s in sports
        }
        self._pending_excluded_sports = set()  # consumed
        self._update_sport_btn()
        if self._sport_popup and self._sport_popup.winfo_exists():
            self._close_sport_popup()

    def _selected_sports(self):
        """Return selected sport names, or None when all (or none defined) are selected."""
        if not self._sport_vars:
            return None
        selected = {s for s, v in self._sport_vars.items() if v.get()}
        return None if selected == set(self._sport_vars) else selected

    def _update_sport_btn(self):
        sel = self._selected_sports()
        if sel is None:
            label = "All ▾"
        elif not sel:
            label = "None ▾"
        elif len(sel) <= 2:
            label = ", ".join(sorted(sel)) + " ▾"
        else:
            label = f"{len(sel)} sports ▾"
        self._sport_btn.config(text=label)

    def _toggle_sport_popup(self):
        if self._sport_popup and self._sport_popup.winfo_exists():
            self._close_sport_popup()
        else:
            self._open_sport_popup()

    def _open_sport_popup(self):
        if not self._sport_vars:
            return
        popup = tk.Toplevel(self)
        popup.wm_overrideredirect(True)
        popup.wm_attributes("-topmost", True)
        self._sport_popup = popup

        self.update_idletasks()
        bx = self._sport_btn.winfo_rootx()
        by = self._sport_btn.winfo_rooty() + self._sport_btn.winfo_height()
        popup.geometry(f"+{bx}+{by}")

        outer = ttk.Frame(popup, borderwidth=1, relief="solid")
        outer.pack()

        btn_row = ttk.Frame(outer)
        btn_row.pack(fill="x", padx=4, pady=(4, 2))
        ttk.Button(btn_row, text="All",  width=6,
                   command=lambda: self._sport_select_all(True)).pack(side="left")
        ttk.Button(btn_row, text="None", width=6,
                   command=lambda: self._sport_select_all(False)).pack(side="left", padx=(4, 0))
        ttk.Separator(outer, orient="horizontal").pack(fill="x", pady=2)
        for sport in sorted(self._sport_vars):
            ttk.Checkbutton(outer, text=sport, variable=self._sport_vars[sport],
                            command=self._sport_changed).pack(anchor="w", padx=8, pady=1)
        ttk.Separator(outer, orient="horizontal").pack(fill="x", pady=2)
        ttk.Button(outer, text="Apply", command=self._close_sport_popup).pack(pady=(0, 4))

        popup.bind("<Escape>", lambda _e: self._close_sport_popup())
        popup.bind("<FocusOut>", self._sport_popup_focusout)
        popup.focus_set()

    def _sport_popup_focusout(self, event):
        # Delay check so internal focus transfers (e.g. between checkboxes) don't close the popup
        self.after(100, self._sport_popup_check_close)

    def _sport_popup_check_close(self):
        if not (self._sport_popup and self._sport_popup.winfo_exists()):
            return
        try:
            focused = self._sport_popup.focus_get()
            if focused is not None:
                return  # focus is still inside popup
        except Exception:
            pass
        self._close_sport_popup()

    def _close_sport_popup(self):
        if self._sport_popup and self._sport_popup.winfo_exists():
            self._sport_popup.destroy()
        self._sport_popup = None
        self._update_sport_btn()
        self.apply_filters()

    def _sport_select_all(self, state):
        for v in self._sport_vars.values():
            v.set(state)
        self._update_sport_btn()

    def _sport_changed(self):
        self._update_sport_btn()

    def _build_tree(self):
        list_frame = ttk.Frame(self)
        list_frame.pack(fill="both", expand=True, padx=10)

        columns = ("date", "name", "sport", "distance", "type", "id", "status")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="extended")
        headers = {"date": "Date", "name": "Name", "sport": "Sport", "distance": "Distance (km)",
                   "type": "Type", "id": "ID", "status": "Status"}
        for col, label in headers.items():
            self.tree.heading(col, text=label, command=lambda c=col: self.sort_by_column(c))
        self.tree.column("date", width=90, anchor="w")
        self.tree.column("name", width=250, anchor="w")
        self.tree.column("sport", width=110, anchor="w")
        self.tree.column("distance", width=90, anchor="e")
        self.tree.column("type", width=90, anchor="w")
        self.tree.column("id", width=100, anchor="w")
        self.tree.column("status", width=90, anchor="w")
        self.sort_state = {}

        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

    def _build_download_controls(self):
        dlbtns = ttk.Frame(self)
        dlbtns.pack(fill="x", padx=10, pady=6)
        self.status_var = tk.StringVar(value="Not logged in.")
        ttk.Label(dlbtns, textvariable=self.status_var).pack(side="left")
        self.download_sel_btn = ttk.Button(dlbtns, text="Download selected", command=self.download_selected, state="disabled")
        self.download_sel_btn.pack(side="right", padx=(6, 0))
        self.download_all_btn = ttk.Button(dlbtns, text="Download all (filtered)", command=self.download_all, state="disabled")
        self.download_all_btn.pack(side="right")

    def _build_progress_and_log(self):
        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", padx=10, pady=(0, 6))

        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.pack(fill="both", expand=False, padx=10, pady=(0, 10))
        self.log_text = tk.Text(log_frame, height=6, state="disabled", wrap="word")
        log_vsb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_vsb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_vsb.pack(side="right", fill="y")

    # ------------------------------------------------------------------ utils
    def _log(self, text):
        self.after(0, self._log_append, text)

    def _log_append(self, text):
        self.log_text.config(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def sort_by_column(self, col):
        reverse = self.sort_state.get(col, False)
        items = [(self.tree.set(i, col), i) for i in self.tree.get_children("")]

        def key(t):
            val = t[0]
            if col == "distance":
                try:
                    return float(val)
                except ValueError:
                    return 0.0
            return val

        items.sort(key=key, reverse=reverse)
        for index, (_, i) in enumerate(items):
            self.tree.move(i, "", index)
        self.sort_state[col] = not reverse

    # --------------------------------------------------------------- login/fetch
    def login_and_fetch(self):
        if not self.pre_login_check():
            return

        self.login_btn.config(state="disabled")
        self.progress.config(mode="indeterminate")
        self.progress.start(10)
        self.status_var.set("Logging in...")

        thread = threading.Thread(target=self._login_and_fetch_thread, daemon=True)
        thread.start()

    def _login_and_fetch_thread(self):
        try:
            self.authenticate()
            self.all_tours = self.fetch_tours()
            self.after(0, self._on_fetched)
        except Exception as e:
            self._log(f"Error: {e}")
            self.after(0, lambda: self.status_var.set("Login failed."))
        finally:
            self.after(0, self._stop_progress)
            self.after(0, lambda: self.login_btn.config(state="normal"))

    def _stop_progress(self):
        self.progress.stop()

    def _on_fetched(self):
        self.existing_ids = {tid for tid in (trip_id(f) for f in list_trips()) if tid}
        self.new_tours = {
            tid: tour for tid, tour in self.all_tours.items()
            if str(tid) not in self.existing_ids
        }

        sports = sorted({tour.get("sport", "") for tour in self.all_tours.values() if tour.get("sport")})
        self._set_sports(sports)

        self.status_var.set(
            f"Fetched {len(self.all_tours)} activities from {self.PROVIDER}, "
            f"{len(self.new_tours)} not yet downloaded."
        )
        self.download_sel_btn.config(state="normal")
        self.download_all_btn.config(state="normal")
        self._log(f"Fetched {len(self.all_tours)} activities, {len(self.new_tours)} new.")
        self.apply_filters()

    # --------------------------------------------------------------------- filters
    def apply_filters(self):
        self.tree.delete(*self.tree.get_children())

        tours_source = self.all_tours if self.tours_filter_var.get() == "All tours" else self.new_tours
        type_choice = self.TYPE_CHOICES.get(self.type_var.get(), "all")
        selected_sports = self._selected_sports()
        start_date = self.start_date_var.get().strip()
        end_date = self.end_date_var.get().strip()
        try:
            min_dist = float(self.min_dist_var.get())
        except ValueError:
            min_dist = 0.0

        shown = 0
        for tid, tour in tours_source.items():
            if type_choice != "all" and tour.get("type") != type_choice:
                continue
            if selected_sports is not None and tour.get("sport") not in selected_sports:
                continue

            date = (tour.get("date") or "")[:10]
            if start_date and date < start_date:
                continue
            if end_date and date > end_date:
                continue

            dist_km = round((tour.get("distance") or 0) / 1000.0, 1)
            if dist_km < min_dist:
                continue

            status = "Downloaded" if str(tid) in self.existing_ids else "New"
            self.tree.insert("", "end", iid=str(tid), values=(
                date, tour.get("name", ""), tour.get("sport", ""), f"{dist_km:.1f}",
                tour.get("type", ""), tid, status,
            ))
            shown += 1

        total = len(tours_source)
        if shown == total:
            self.filter_count_var.set(f"Showing all {total} tour(s).")
        else:
            self.filter_count_var.set(f"Showing {shown} of {total} tour(s) ({total - shown} filtered out).")

    # ------------------------------------------------------------------- download
    def download_selected(self):
        ids = self.tree.selection()
        if not ids:
            messagebox.showinfo("Import", "No tours selected.")
            return
        self._start_download([int(i) for i in ids])

    def download_all(self):
        ids = [int(i) for i in self.tree.get_children("")]
        if not ids:
            messagebox.showinfo("Import", "No tours to download.")
            return
        self._start_download(ids)

    def _start_download(self, ids):
        existing = [tid for tid in ids if str(tid) in self.existing_ids]
        if existing:
            if not messagebox.askyesno(
                "Import",
                f"{len(existing)} of the selected tour(s) already exist locally.\n"
                f"Replace the existing GPX file(s) with a fresh download?"
            ):
                ids = [tid for tid in ids if tid not in existing]
                if not ids:
                    messagebox.showinfo("Import", "Nothing to download.")
                    return

        self.download_sel_btn.config(state="disabled")
        self.download_all_btn.config(state="disabled")
        self.login_btn.config(state="disabled")
        self.progress.config(mode="determinate", maximum=len(ids), value=0)

        thread = threading.Thread(target=self._download_thread, args=(ids,), daemon=True)
        thread.start()

    def _download_thread(self, ids):
        downloaded = 0
        existing_by_id = {trip_id(f): f for f in list_trips() if trip_id(f)}
        for tid in ids:
            tour_meta = self.new_tours.get(tid) or self.all_tours.get(tid)
            try:
                old_file = existing_by_id.get(str(tid))
                if old_file:
                    self._log(f"Removing existing file {old_file}...")
                    os.remove(os.path.join(SOURCE_DIR, old_file))

                self._log(f"Downloading activity {tid} ({tour_meta.get('name', '')})...")
                gpx_content = self.download_activity(tid, tour_meta)
                gpx_content = inject_gpx_metadata(gpx_content, self.PROVIDER_KEY,
                                                   tour_meta.get('type', ''))

                path = os.path.join(SOURCE_DIR, self.activity_filename(tid, tour_meta))
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(gpx_content)

                downloaded += 1
                self._log(f"  -> wrote {os.path.basename(path)}")
            except Exception as e:
                self._log(f"  Error downloading {tid}: {e}")
            finally:
                self.after(0, self.progress.step, 1)

        self._log(f"Done: {downloaded}/{len(ids)} activity(ies) downloaded.")
        self.after(0, self._download_done, ids)

    def _download_done(self, ids):
        for tid in ids:
            self.new_tours.pop(tid, None)
        self.existing_ids = {tid for tid in (trip_id(f) for f in list_trips()) if tid}
        self.apply_filters()
        self.status_var.set(f"{len(self.new_tours)} new tour(s) remaining.")
        self.download_sel_btn.config(state="normal")
        self.download_all_btn.config(state="normal")
        self.login_btn.config(state="normal")
        if self.on_downloaded:
            self.on_downloaded()
