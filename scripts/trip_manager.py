import functools
import json
import os
import re
import sys
import shutil
import subprocess
import threading
import webbrowser
import http.server
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

_FROZEN = getattr(sys, 'frozen', False)
_MEIPASS = getattr(sys, '_MEIPASS', None)

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
# When frozen, DIR = folder containing the .exe (used as config key per installation).
# When running from source, DIR = project root (parent of scripts/).
DIR = os.path.dirname(sys.executable) if _FROZEN else os.path.dirname(SCRIPTS_DIR)
# APP_DIR = where combined.html and help.html live at runtime.
APP_DIR = _MEIPASS if _FROZEN else DIR
HTML_PATH = os.path.join(APP_DIR, "combined.html")
# COMBINE_SCRIPT is only used when running from source; frozen mode re-invokes sys.executable.
COMBINE_SCRIPT = None if _FROZEN else os.path.join(SCRIPTS_DIR, "combine_trips.py")

# --- personal data folder --------------------------------------------------
# The program (this file, combine_trips.py, combined.html, ...) is meant to be
# shared without the user's personal data (GPX library, processed maps, Komoot
# credentials). That data lives in a separate folder chosen by each user,
# remembered in a per-user config file outside the program folder.
APP_NAME = "TripManager"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_config(config):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)


def resolve_data_dir():
    """Return the folder used to store the user's personal data (GPX
    library, processed output, Komoot credentials), prompting the user to
    choose or create one on first run.

    Set the ``TRIPMANAGER_DATA_DIR`` environment variable to bypass the GUI
    dialog entirely (used by the test suite and CI).

    Each program installation (identified by its directory path) remembers
    its own data folder so that two copies of Trip Manager installed in
    different locations can each manage a separate GPS library."""
    env_override = os.environ.get("TRIPMANAGER_DATA_DIR")
    if env_override:
        os.makedirs(os.path.join(env_override, "raw_gpx"), exist_ok=True)
        os.makedirs(os.path.join(env_override, "processed"), exist_ok=True)
        return env_override

    config = load_config()
    # Per-installation lookup: data_dirs is a dict keyed by program directory.
    data_dirs = config.get("data_dirs", {})
    data_dir = data_dirs.get(DIR)
    # Fall back to the legacy single-entry key used by older versions.
    if not data_dir:
        data_dir = config.get("data_dir")
    if data_dir and os.path.isdir(data_dir):
        os.makedirs(os.path.join(data_dir, "raw_gpx"), exist_ok=True)
        os.makedirs(os.path.join(data_dir, "processed"), exist_ok=True)
        return data_dir

    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo(
        "Choose a data folder",
        "Trip Manager keeps your personal data (GPX files, processed maps, "
        "Komoot credentials) in a folder separate from the program.\n\n"
        "Choose or create a folder where this data will be stored."
    )
    while True:
        chosen = filedialog.askdirectory(title="Choose or create your Trip Manager data folder")
        if chosen:
            break
        if not messagebox.askretrycancel(
            "Data folder required",
            "Trip Manager needs a data folder to continue. Try again?"
        ):
            sys.exit(0)
    root.destroy()

    os.makedirs(os.path.join(chosen, "raw_gpx"), exist_ok=True)
    os.makedirs(os.path.join(chosen, "processed"), exist_ok=True)
    data_dirs[DIR] = chosen
    config["data_dirs"] = data_dirs
    config.pop("data_dir", None)  # migrate away from legacy key
    save_config(config)
    return chosen


DATA_DIR = resolve_data_dir()
SOURCE_DIR = os.path.join(DATA_DIR, "raw_gpx")  # library of .gpx trip files
DEFAULT_OUTPUT_DIR = os.path.join(DATA_DIR, "processed")  # combine_trips.py output (editable in the UI)

if _FROZEN:
    # Frozen exe re-invokes itself with --_combine-trips to run combine_trips.main().
    PYTHON_FOR_COMBINE = sys.executable
else:
    # Prefer the venv Python so combine_trips gets the right packages.
    _VENV_PYTHON = os.path.join(DIR, "env", "Scripts", "python.exe")
    PYTHON_FOR_COMBINE = _VENV_PYTHON if os.path.exists(_VENV_PYTHON) else sys.executable

# Pure parsing utilities live in trip_utils (no GUI, safe to import anywhere)
from trip_utils import (
    NAME_RE, ID_RE, TITLE_RE, DATETIME_SUFFIX_RE, _GPX_META_RE,
    SPORT_CATEGORIES, SPORT_KEYWORDS,
    trip_id, trip_date, read_gpx_meta, trip_sport,
)

# Regexes for parsing combine_trips.py's stderr/stdout progress lines
PROCESSING_COUNT_RE = re.compile(r'^Processing (\d+) files')
PER_FILE_RE = re.compile(r'^  .+ points -> ')


def list_trips():
    files = [f for f in os.listdir(SOURCE_DIR) if f.lower().endswith(".gpx")]
    return sorted(files)


class GzipAwareRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Serve files normally, but transparently serve a precompressed
    "<path>.gz" sibling (with Content-Encoding: gzip) when the browser
    accepts gzip and one exists -- combine_trips.py writes .gz copies of
    the large data files alongside the plain .js ones.

    "/processed/..." is served from the user's data folder (DATA_DIR),
    separate from the program folder (this handler's `directory`), since
    combined.html requests its data from a "processed/" relative path."""

    def translate_path(self, path):
        url_path = path.split("?", 1)[0].split("#", 1)[0]
        if url_path == "/processed" or url_path.startswith("/processed/"):
            rest = "/" + url_path[len("/processed"):].lstrip("/")
            original_directory = self.directory
            self.directory = os.path.join(DATA_DIR, "processed")
            try:
                return super().translate_path(rest)
            finally:
                self.directory = original_directory
        return super().translate_path(path)

    def send_head(self):
        path = self.translate_path(self.path)
        if os.path.isdir(path) or "gzip" not in self.headers.get("Accept-Encoding", ""):
            return super().send_head()

        gz_path = path + ".gz"
        if not os.path.isfile(gz_path):
            return super().send_head()

        try:
            f = open(gz_path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return None

        fs = os.fstat(f.fileno())
        self.send_response(200)
        self.send_header("Content-type", self.guess_type(path))
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(fs.st_size))
        self.send_header("Last-Modified", self.date_time_string(fs.st_mtime))
        self.end_headers()
        return f

    def log_message(self, *args):
        pass


def load_processed_meta(raw_data_path):
    """Return a dict mapping trip file name -> tripMeta entry (with
    distanceKm/sport/etc.) for trips already present in raw_data.js's
    tripMeta, or an empty dict if not available."""
    if not os.path.exists(raw_data_path):
        return {}
    try:
        with open(raw_data_path, encoding="utf-8") as fh:
            for line in fh.read().split(";\n"):
                line = line.strip()
                if not line.startswith("var tripMeta"):
                    continue
                _, _, value = line.partition("=")
                value = value.strip()
                if value.endswith(";"):
                    value = value[:-1]
                meta = json.loads(value)
                return {entry["name"]: entry for entry in meta if entry.get("name")}
    except (OSError, ValueError):
        pass
    return {}


class AutoImportConfirmDialog(tk.Toplevel):
    """Shown after auto-check finds new trips; lets the user pick which to download."""

    def __init__(self, master, provider_name, trips, result_list, done_event):
        super().__init__(master)
        self.title(f"New trips found — {provider_name}")
        self.resizable(True, True)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._skip)
        self._result_list = result_list
        self._done_event  = done_event
        self._vars = []

        ttk.Label(self, text=f"{len(trips)} new trip(s) found on {provider_name}:",
                  font=("", 10, "bold")).pack(padx=12, pady=(10, 4), anchor="w")

        # Scrollable checklist
        list_frame = ttk.Frame(self)
        list_frame.pack(fill="both", expand=True, padx=12)
        vsb = ttk.Scrollbar(list_frame, orient="vertical")
        canvas = tk.Canvas(list_frame, yscrollcommand=vsb.set, bd=0, highlightthickness=0, height=320)
        vsb.configure(command=canvas.yview)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        # Header row
        hdr = ttk.Frame(inner)
        hdr.pack(fill="x", padx=4, pady=(0, 2))
        tk.Label(hdr, text="", width=3).pack(side="left")
        tk.Label(hdr, text="Date",     width=11, anchor="w", font=("", 9, "bold")).pack(side="left")
        tk.Label(hdr, text="Name",     anchor="w",           font=("", 9, "bold")).pack(side="left", expand=True, fill="x")
        tk.Label(hdr, text="Dist (km)",width=9,  anchor="e", font=("", 9, "bold")).pack(side="left")
        tk.Label(hdr, text="Sport",    width=12, anchor="w", font=("", 9, "bold")).pack(side="left")
        ttk.Separator(inner, orient="horizontal").pack(fill="x")

        for tid, meta in trips:
            var = tk.BooleanVar(value=True)
            self._vars.append((tid, meta, var))
            row = ttk.Frame(inner)
            row.pack(fill="x", padx=4, pady=1)
            ttk.Checkbutton(row, variable=var).pack(side="left")
            date    = (meta.get("date") or "")[:10]
            dist_km = round((meta.get("distance") or 0) / 1000.0, 1)
            sport   = meta.get("sport", "")
            tk.Label(row, text=date,                   width=11, anchor="w").pack(side="left")
            tk.Label(row, text=meta.get("name", ""),   anchor="w").pack(side="left", expand=True, fill="x")
            tk.Label(row, text=f"{dist_km}",           width=9,  anchor="e").pack(side="left")
            tk.Label(row, text=sport,                  width=12, anchor="w").pack(side="left")

        ttk.Separator(inner, orient="horizontal").pack(fill="x", pady=(2, 0))

        # Button bar
        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=12, pady=8)
        ttk.Button(btns, text="Select all",      command=self._select_all ).pack(side="left")
        ttk.Button(btns, text="Select none",     command=self._select_none).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="Skip all",        command=self._skip       ).pack(side="right")
        ttk.Button(btns, text="Import selected", command=self._confirm    ).pack(side="right", padx=(0, 6))

        self.update_idletasks()
        self.minsize(580, 300)

    def _select_all(self):
        for _, _, v in self._vars: v.set(True)

    def _select_none(self):
        for _, _, v in self._vars: v.set(False)

    def _confirm(self):
        self._result_list.extend((tid, meta) for tid, meta, v in self._vars if v.get())
        self._done_event.set()
        self.destroy()

    def _skip(self):
        self._done_event.set()
        self.destroy()


class TripManager(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Trip Manager")
        self.geometry("820x640")

        # --- Top: trip count -------------------------------------------------
        self.count_var = tk.StringVar()
        self.count_label = ttk.Label(self, textvariable=self.count_var, font=("", 11, "bold"))
        self.count_label.pack(anchor="w", padx=10, pady=(10, 4))

        # --- Import (above the trip list) --------------------------------------
        self.import_frame = import_frame = ttk.LabelFrame(self, text="Import")
        import_frame.pack(fill="x", padx=10, pady=(0, 6))

        _import_providers = [
            ("Komoot",         "komoot", self.import_from_komoot),
            ("Strava",         "strava", self.import_from_strava),
            ("Garmin Connect", "garmin", self.import_from_garmin),
        ]
        self._auto_check_vars = {}
        _auto_cfg = load_config().get("auto_check", {})
        for col, (label, key, cmd) in enumerate(_import_providers):
            cell = ttk.Frame(import_frame)
            cell.grid(row=0, column=col, padx=(0 if col == 0 else 12, 0), pady=4, sticky="w")
            ttk.Button(cell, text=f"Import from {label}…", command=cmd).pack(anchor="w")
            var = tk.BooleanVar(value=_auto_cfg.get(key, False))
            self._auto_check_vars[key] = var
            def _make_toggle(k, v):
                def toggle():
                    cfg = load_config(); cfg.setdefault("auto_check", {})[k] = v.get(); save_config(cfg)
                return toggle
            ttk.Checkbutton(cell, text="Auto-check on startup", variable=var,
                            command=_make_toggle(key, var)).pack(anchor="w")
        ttk.Button(import_frame, text="Data Wizard…", command=self.open_data_wizard).grid(
            row=0, column=len(_import_providers), padx=(24, 0), pady=4, sticky="w"
        )

        # Trigger auto-checks shortly after startup (credentials may not be loaded yet)
        self.after(1000, self._run_startup_auto_checks)

        # --- Trip list ---------------------------------------------------------
        self.list_frame = list_frame = ttk.Frame(self)
        list_frame.pack(fill="both", expand=True, padx=10)

        columns = ("date", "name", "id", "sport", "source", "distance", "status")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="extended", height=4)
        self.sort_state = {}  # column -> last sort was reverse?
        self.column_labels = {
            "date": "Date", "name": "File name", "id": "ID",
            "sport": "Sport", "source": "Source", "distance": "Distance (km)", "status": "Status",
        }
        for col, label in self.column_labels.items():
            self.tree.heading(col, text=label, command=lambda c=col: self.sort_by_column(c))
        self.tree.column("date", width=90, anchor="w")
        self.tree.column("name", width=330, anchor="w")
        self.tree.column("id", width=80, anchor="w")
        self.tree.column("sport", width=80, anchor="w")
        self.tree.column("source", width=70, anchor="w")
        self.tree.column("distance", width=90, anchor="e")
        self.tree.column("status", width=80, anchor="w")

        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # --- Add / remove buttons -----------------------------------------------
        self.top_btns = btns = ttk.Frame(self)
        btns.pack(fill="x", padx=10, pady=6)

        ttk.Button(btns, text="Add files...", command=self.add_files).pack(side="left")
        ttk.Button(btns, text="Add folder...", command=self.add_folder).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="Remove selected", command=self.remove_selected).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="Remove all", command=self.remove_all).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="Refresh", command=self.refresh).pack(side="left", padx=(6, 0))

        self.show_log_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            btns, text="Show log", variable=self.show_log_var, command=self.toggle_log
        ).pack(side="right")

        # --- Processing options ----------------------------------------------
        self.opts_frame = opts = ttk.LabelFrame(self, text="Processing options")
        opts.pack(fill="x", padx=10, pady=6)

        # Row 0: date / limit
        ttk.Label(opts, text="Limit (0 = all):").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self.limit_var = tk.StringVar(value="0")
        ttk.Entry(opts, textvariable=self.limit_var, width=8).grid(row=0, column=1, padx=4, pady=4, sticky="w")

        ttk.Label(opts, text="Start date (YYYY-MM-DD):").grid(row=0, column=2, padx=4, pady=4, sticky="e")
        self.start_date_var = tk.StringVar()
        ttk.Entry(opts, textvariable=self.start_date_var, width=12).grid(row=0, column=3, padx=4, pady=4, sticky="w")

        ttk.Label(opts, text="End date (YYYY-MM-DD):").grid(row=0, column=4, padx=4, pady=4, sticky="e")
        self.end_date_var = tk.StringVar()
        ttk.Entry(opts, textvariable=self.end_date_var, width=12).grid(row=0, column=5, padx=4, pady=4, sticky="w")


        # Row 1: distance / sport / only-new / preview
        ttk.Label(opts, text="Min distance (km):").grid(row=1, column=0, padx=4, pady=4, sticky="e")
        self.min_dist_var = tk.StringVar(value="0")
        ttk.Entry(opts, textvariable=self.min_dist_var, width=8).grid(row=1, column=1, padx=4, pady=4, sticky="w")

        ttk.Label(opts, text="Sport:").grid(row=1, column=2, padx=4, pady=4, sticky="e")
        self.sport_var = tk.StringVar(value="All")
        self.sport_combo = ttk.Combobox(opts, textvariable=self.sport_var, state="readonly", width=22)
        self.sport_combo["values"] = ("All",)
        self.sport_combo.grid(row=1, column=3, padx=4, pady=4, sticky="w")

        self.only_new_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opts, text="Only process new trips", variable=self.only_new_var
        ).grid(row=1, column=4, columnspan=2, padx=4, pady=4, sticky="w")

        self.preview_btn = ttk.Button(opts, text="Preview filter...", command=self.preview_filter)
        self.preview_btn.grid(row=1, column=6, columnspan=2, padx=4, pady=4, sticky="e")

        # Row 2: output folder
        ttk.Label(opts, text="Output folder:").grid(row=2, column=0, padx=4, pady=4, sticky="e")
        self.output_dir_var = tk.StringVar(value=DEFAULT_OUTPUT_DIR)
        ttk.Entry(opts, textvariable=self.output_dir_var, width=50).grid(
            row=2, column=1, columnspan=6, padx=4, pady=4, sticky="we"
        )
        ttk.Button(opts, text="Browse...", command=self.choose_output_dir).grid(
            row=2, column=7, padx=4, pady=4, sticky="e"
        )

        # Row 3: selected / filtered trip count
        self.selection_count_var = tk.StringVar(value="")
        ttk.Label(opts, textvariable=self.selection_count_var, font=("", 9, "italic"), foreground="#555").grid(
            row=3, column=0, columnspan=6, padx=4, pady=(2, 4), sticky="w"
        )

        # Row 3 right: Run + Cancel
        run_style = ttk.Style(self)
        run_style.configure("Run.TButton", font=("", 11, "bold"), padding=(12, 8))
        self.run_btn = ttk.Button(opts, text="Run processing", command=self.run_processing, style="Run.TButton")
        self.run_btn.grid(row=3, column=6, padx=4, pady=(2, 4), sticky="e")

        self.cancel_btn = ttk.Button(opts, text="Cancel", command=self.cancel_processing, state="disabled")
        self.cancel_btn.grid(row=3, column=7, padx=4, pady=(2, 4), sticky="e")

        # --- Progress bar ----------------------------------------------------------
        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", padx=10, pady=(0, 6))

        # --- Bottom buttons --------------------------------------------------------
        self.bottom_frame = bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=10, pady=(0, 6))
        self.export_btn = ttk.Button(bottom, text="Export raw data...", command=self.export_processed)
        self.export_btn.pack(side="left")
        ttk.Button(bottom, text="Open visualization", command=self.open_html).pack(side="left", padx=(6, 0))
        ttk.Button(bottom, text="Help", command=self.open_help).pack(side="right")

        # --- Log (pushed to the very bottom) --------------------------------------
        self.log_frame = ttk.LabelFrame(self, text="Log")
        self.log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.log_text = tk.Text(self.log_frame, height=4, state="disabled", wrap="word")
        log_vsb = ttk.Scrollbar(self.log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_vsb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_vsb.pack(side="right", fill="y")

        self._proc = None
        self._cancelled = False

        # Update the "N trips selected/filtered" label whenever the tree
        # selection or any filter field changes.
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_selection_count())
        for var in (self.limit_var, self.start_date_var, self.end_date_var, self.min_dist_var):
            var.trace_add("write", lambda *_: self._update_selection_count())
        self.sport_var.trace_add("write", lambda *_: self._update_selection_count())
        self.only_new_var.trace_add("write", lambda *_: self._update_selection_count())

        self.refresh()
        self._set_minsize()

    def _set_minsize(self):
        """Compute the smallest window size at which every fixed-size row
        (top buttons, processing options, progress bar, bottom buttons)
        stays visible, leaving a bit of room for the trip list + log even
        when those are fully collapsed. The window can be resized down to
        this size but no further."""
        self.update_idletasks()
        fixed_height = (
            self.count_label.winfo_reqheight()
            + self.import_frame.winfo_reqheight()
            + self.top_btns.winfo_reqheight()
            + self.opts_frame.winfo_reqheight()
            + self.progress.winfo_reqheight()
            + self.bottom_frame.winfo_reqheight()
            + 60  # padding/margins around the fixed-size rows above
        )
        # Leave room for the trip list + log even when shrunk, based on their
        # actual minimum heights (driven by the tree/log_text "height" options)
        # so the bottom buttons can't get pushed off-window by widgets that
        # refuse to shrink below their natural size.
        min_list_log_height = self.list_frame.winfo_reqheight() + self.log_frame.winfo_reqheight()
        min_width = max(
            self.opts_frame.winfo_reqwidth(),
            self.bottom_frame.winfo_reqwidth(),
            self.top_btns.winfo_reqwidth(),
            self.list_frame.winfo_reqwidth(),  # keep the trip-list scrollbar visible
        ) + 20
        self.minsize(min_width, fixed_height + min_list_log_height)

    # ----------------------------------------------------------------- paths
    def raw_data_path(self):
        return os.path.join(self.output_dir_var.get(), "raw_data.js")

    def processed_data_path(self):
        return os.path.join(self.output_dir_var.get(), "raw_data.js")

    def choose_output_dir(self):
        folder = filedialog.askdirectory(
            title="Select output folder for processed data",
            initialdir=self.output_dir_var.get() or DIR,
        )
        if folder:
            self.output_dir_var.set(folder)
            self.refresh()

    # ------------------------------------------------------------------ trips
    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        files = list_trips()
        processed = load_processed_meta(self.raw_data_path())
        new_count = 0
        for f in files:
            meta = processed.get(f)
            status = "Processed" if meta else "New"
            if status == "New":
                new_count += 1
            fpath = os.path.join(SOURCE_DIR, f)
            src_meta, sport_meta = read_gpx_meta(fpath)
            sport = (meta.get("sport") if meta else None) or sport_meta or trip_sport(f)
            source = (meta.get("source") if meta else None) or src_meta
            distance = f"{meta['distanceKm']:.1f}" if meta and meta.get("distanceKm") is not None else ""
            self.tree.insert("", "end", values=(trip_date(f), f, trip_id(f) or "", sport, source, distance, status))
        self.count_var.set(
            f"Trips recorded: {len(files)} ({len(files) - new_count} processed, {new_count} new)"
        )

        self.sport_combo["values"] = ("All",) + tuple(SPORT_CATEGORIES)
        self._update_export_state()
        self._update_selection_count()

    def _update_selection_count(self):
        """Refresh the 'N trips will be processed' label below the output
        folder, reflecting either the current tree selection or the active
        filter set (whichever run_processing would use)."""
        selected = self.tree.selection()
        if selected:
            n = len(selected)
            self.selection_count_var.set(f"{n} trip{'s' if n != 1 else ''} selected — filters ignored")
        else:
            try:
                filtered = self._filtered_files()
                n = len(filtered)
                self.selection_count_var.set(
                    f"{n} trip{'s' if n != 1 else ''} will be processed (by filter)"
                )
            except (ValueError, OSError):
                self.selection_count_var.set("")

    def _update_export_state(self):
        state = "normal" if os.path.exists(self.processed_data_path()) else "disabled"
        self.export_btn.config(state=state)

    def toggle_log(self):
        if self.show_log_var.get():
            self.log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        else:
            self.log_frame.pack_forget()

    def sort_by_column(self, col):
        reverse = self.sort_state.get(col, False)
        if col == "distance":
            key = lambda t: float(t[0]) if t[0] else -1.0
        else:
            key = lambda t: t[0]
        items = [(self.tree.set(i, col), i) for i in self.tree.get_children("")]
        items.sort(key=key, reverse=reverse)
        for index, (_, i) in enumerate(items):
            self.tree.move(i, "", index)
        self.sort_state[col] = not reverse

        for c, label in self.column_labels.items():
            if c == col:
                label += " ▼" if reverse else " ▲"
            self.tree.heading(c, text=label, command=lambda c=c: self.sort_by_column(c))

    def _import_window(self, module_name, class_name, **kwargs):
        try:
            mod = __import__(module_name)
            cls = getattr(mod, class_name)
            cls(self, **kwargs)
        except ImportError as exc:
            messagebox.showerror(
                "Import error",
                f"Could not load {module_name}.py:\n{exc}\n\n"
                "Make sure setup.bat has been run in the Trip Manager folder.",
            )

    def import_from_komoot(self):
        self._import_window("komoot_import", "KomootImportWindow", on_downloaded=self.refresh)

    def import_from_strava(self):
        self._import_window("strava_import", "StravaImportWindow", on_downloaded=self.refresh)

    def import_from_garmin(self):
        self._import_window("garmin_import", "GarminImportWindow", on_downloaded=self.refresh)

    def _run_startup_auto_checks(self):
        cfg = load_config()
        auto_cfg = cfg.get("auto_check", {})
        providers = [
            ("komoot", "komoot_import", "KomootImportWindow"),
            ("strava", "strava_import", "StravaImportWindow"),
            ("garmin", "garmin_import", "GarminImportWindow"),
        ]
        for key, module_name, class_name in providers:
            if not auto_cfg.get(key, False):
                continue
            try:
                mod = __import__(module_name)
                cls = getattr(mod, class_name)
                def _done(n, k=key):
                    if n > 0:
                        self.after(0, self.refresh)

                def _show_confirm(provider_name, trips, _master=self):
                    result = []
                    event  = threading.Event()
                    _master.after(0, lambda: AutoImportConfirmDialog(
                        _master, provider_name, trips, result, event))
                    event.wait()
                    return result

                thread = threading.Thread(
                    target=cls._run_headless,
                    args=(self._log, _done, _show_confirm),
                    daemon=True,
                )
                thread.start()
            except Exception as e:
                self._log(f"Auto-check {key} could not start: {e}\n")

    def open_data_wizard(self):
        try:
            from data_wizard import DataWizardWindow
            DataWizardWindow(self, self.output_dir_var, on_changed=self.refresh)
        except ImportError as exc:
            messagebox.showerror("Import error", f"Could not load data_wizard.py:\n{exc}")

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="Select GPX files to add",
            filetypes=[("GPX files", "*.gpx"), ("All files", "*.*")],
        )
        if paths:
            self._copy_trips(paths)

    def add_folder(self):
        folder = filedialog.askdirectory(title="Select a folder containing GPX files")
        if not folder:
            return
        paths = [
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if f.lower().endswith(".gpx")
        ]
        if not paths:
            messagebox.showinfo("Add folder", "No .gpx files found in that folder.")
            return
        self._copy_trips(paths)

    def _copy_trips(self, paths):
        existing_ids = {tid for tid in (trip_id(f) for f in list_trips()) if tid}
        existing_names = set(list_trips())

        added, skipped = 0, 0
        for src in paths:
            name = os.path.basename(src)
            tid = trip_id(name)
            if name in existing_names or (tid and tid in existing_ids):
                skipped += 1
                continue
            dst = os.path.join(SOURCE_DIR, name)
            shutil.copy2(src, dst)
            existing_names.add(name)
            if tid:
                existing_ids.add(tid)
            added += 1

        self.refresh()
        messagebox.showinfo("Add trips", f"Added {added} trip(s), skipped {skipped} already-existing trip(s).")

    def remove_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        names = [self.tree.item(i, "values")[1] for i in sel]
        if not messagebox.askyesno(
            "Remove trips",
            f"Delete {len(names)} trip file(s) from the library? This cannot be undone."
        ):
            return
        for name in names:
            path = os.path.join(SOURCE_DIR, name)
            try:
                os.remove(path)
            except OSError as e:
                messagebox.showerror("Remove trips", f"Could not delete {name}:\n{e}")
        self.refresh()

    def remove_all(self):
        files = list_trips()
        if not files:
            return
        if not messagebox.askyesno(
            "Remove all trips",
            f"Delete ALL {len(files)} trip file(s) from the library? This cannot be undone."
        ):
            return
        for name in files:
            path = os.path.join(SOURCE_DIR, name)
            try:
                os.remove(path)
            except OSError as e:
                messagebox.showerror("Remove all trips", f"Could not delete {name}:\n{e}")
        self.refresh()

    # ------------------------------------------------------------------ filter
    def _filtered_files(self):
        """Return the list of trip file names that match the current date
        range / sport / limit / only-new filters, in the same order
        combine_trips.py would apply them. Does NOT apply the min-distance
        filter, since that requires parsing each GPX file."""
        files = list_trips()
        start = self.start_date_var.get().strip()
        end = self.end_date_var.get().strip()
        sport = self.sport_var.get()

        filtered = []
        for f in files:
            date = trip_date(f)
            if start and date < start:
                continue
            if end and date > end:
                continue
            if sport and sport != "All" and trip_sport(f) != sport:
                continue
            filtered.append(f)

        limit = int(self.limit_var.get())
        if limit > 0:
            filtered = filtered[-limit:]

        if self.only_new_var.get():
            processed = load_processed_meta(self.raw_data_path())
            filtered = [f for f in filtered if f not in processed]

        return filtered

    def preview_filter(self):
        try:
            int(self.limit_var.get())
        except ValueError:
            messagebox.showerror("Preview filter", "Limit must be a number.")
            return

        try:
            filtered = self._filtered_files()
        except ValueError as e:
            messagebox.showerror("Preview filter", str(e))
            return

        win = tk.Toplevel(self)
        win.title("Trips that will be processed")
        win.transient(self)

        min_dist = self.min_dist_var.get().strip()
        note = ""
        if min_dist and min_dist != "0":
            note = (f"\n(The min-distance ({min_dist} km) filter is applied during "
                    f"processing and is not reflected in this preview.)")
        ttk.Label(
            win, text=f"{len(filtered)} of {len(list_trips())} trip(s) will be processed.{note}",
            padding=(12, 12, 12, 6),
        ).pack(anchor="w")

        list_frame = ttk.Frame(win)
        list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        listbox = tk.Listbox(list_frame, width=70, height=18)
        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=vsb.set)
        listbox.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        for f in filtered:
            listbox.insert("end", f)

        ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 12))
        win.grab_set()

    # --------------------------------------------------------------- processing
    def run_processing(self):
        try:
            limit = int(self.limit_var.get())
            min_dist = float(self.min_dist_var.get())
        except ValueError:
            messagebox.showerror("Run processing", "Limit and min distance must be numbers.")
            return

        if _FROZEN:
            # Re-invoke the frozen exe as a combine_trips worker.
            # combine_trips uses flush=True so output is not buffered.
            args = [PYTHON_FOR_COMBINE, "--_combine-trips"]
        else:
            # -u: unbuffered stdout/stderr so progress lines reach the GUI immediately.
            args = [PYTHON_FOR_COMBINE, "-u", COMBINE_SCRIPT]
        args += [
            "--source-dir", SOURCE_DIR,
            "--output-dir", self.output_dir_var.get(),
            "--min-distance", str(min_dist),
        ]

        # If the user has selected specific rows in the trip list, process
        # exactly those files and ignore the date/sport/limit filters.
        selected = self.tree.selection()
        if selected:
            filenames = [self.tree.item(i, "values")[1] for i in selected]
            args += ["--files"] + filenames
        else:
            args += ["--limit", str(limit)]
            if self.start_date_var.get().strip():
                args += ["--start-date", self.start_date_var.get().strip()]
            if self.end_date_var.get().strip():
                args += ["--end-date", self.end_date_var.get().strip()]
            if self.sport_var.get() and self.sport_var.get() != "All":
                args += ["--sport", self.sport_var.get()]

        if self.only_new_var.get():
            args += ["--only-new"]

        self.run_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self._cancelled = False
        self._log_clear()
        self._log(f"Running: {' '.join(args)}\n")
        self.progress.config(mode="indeterminate")
        self.progress.start(10)

        thread = threading.Thread(target=self._run_processing_thread, args=(args,), daemon=True)
        thread.start()

    def cancel_processing(self):
        if not self._proc or self._proc.poll() is not None:
            return
        if not messagebox.askyesno("Cancel processing", "Stop the current processing run?"):
            return
        self._cancelled = True
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(self._proc.pid)],
                capture_output=True,
            )
        except Exception as e:
            self._log(f"\nFailed to cancel: {e}\n")
        self.cancel_btn.config(state="disabled")

    def _run_processing_thread(self, args):
        ok = False
        try:
            proc = subprocess.Popen(
                args, cwd=DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
            self._proc = proc
            for line in proc.stdout:
                self._log(line)

                m = PROCESSING_COUNT_RE.match(line)
                if m:
                    self.after(0, self._progress_set_total, int(m.group(1)))
                elif PER_FILE_RE.match(line):
                    self.after(0, self._progress_step)

            proc.wait()
            if self._cancelled:
                self._log("\nProcess cancelled by user.\n")
            else:
                self._log(f"\nProcess finished with exit code {proc.returncode}\n")
                ok = proc.returncode == 0
        except Exception as e:
            self._log(f"\nError: {e}\n")
        finally:
            self._proc = None
            self.after(0, self._processing_done, ok)

    def _progress_set_total(self, total):
        self.progress.stop()
        self.progress.config(mode="determinate", maximum=max(total, 1), value=0)

    def _progress_step(self):
        self.progress.step(1)

    def _processing_done(self, ok):
        self.progress.stop()
        self.run_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self.refresh()
        if ok:
            self._log("\nProcessing finished successfully.\n")

    def _log(self, text):
        self.after(0, self._log_append, text)

    def _log_append(self, text):
        self.log_text.config(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _log_clear(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    # ------------------------------------------------------------------- export
    def export_processed(self):
        raw_data_path = self.processed_data_path()
        if not os.path.exists(raw_data_path):
            messagebox.showwarning("Export", "No processed data found yet. Run processing first.")
            return
        dst = filedialog.asksaveasfilename(
            title="Export raw trip data",
            defaultextension=".js",
            initialfile="raw_data.js",
            filetypes=[("JavaScript data", "*.js"), ("All files", "*.*")],
        )
        if not dst:
            return
        shutil.copy2(raw_data_path, dst)
        messagebox.showinfo("Export", f"Exported to:\n{dst}")

    # --------------------------------------------------------------------- html
    _httpd = None
    _http_port = None

    def _ensure_server(self):
        """Start (once) a local HTTP server serving this folder, so the
        visualization can use gzip-compressed data files and fetch()
        without the restrictions of file:// pages."""
        if TripManager._httpd is None:
            handler = functools.partial(GzipAwareRequestHandler, directory=APP_DIR)
            httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            TripManager._httpd = httpd
            TripManager._http_port = httpd.server_address[1]
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
        return TripManager._http_port

    def open_html(self):
        if not os.path.exists(HTML_PATH):
            messagebox.showwarning("Open visualization", "combined.html not found.")
            return
        port = self._ensure_server()
        webbrowser.open(f"http://127.0.0.1:{port}/combined.html")

    def open_help(self):
        help_path = os.path.join(DIR, "help.html")
        if not os.path.exists(help_path):
            messagebox.showwarning("Help", "help.html not found.")
            return
        port = self._ensure_server()
        webbrowser.open(f"http://127.0.0.1:{port}/help.html")


def main():
    app = TripManager()
    app.mainloop()


if __name__ == "__main__":
    main()
