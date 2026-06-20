"""Data Wizard: inspect the raw GPX library and the processed data, and
clean up trips that are no longer wanted (raw file, processed data, or both).

Deleting "processed data" for a trip removes its entry from tripMeta/rawAll,
remaps the trip indices referenced by processedEdges/edges_cache.json, drops
edges left with no trips, and reindexes the corresponding
trip_details/trip_N.js files."""

import os
import json
import gzip
import tkinter as tk
from tkinter import ttk, messagebox

from trip_manager import SOURCE_DIR, list_trips, trip_date
from trip_utils import trip_sport, read_gpx_meta, SPORT_CATEGORIES, gpx_sensors


# ---------------------------------------------------------------------------
# processed/ data helpers
# ---------------------------------------------------------------------------
def load_js_data(path):
    """Parse a 'var name = <json>;' per line file (as written by
    combine_trips.py)."""
    data = {}
    if not os.path.exists(path):
        return data
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


def write_gzip_copy(path):
    with open(path, "rb") as src, gzip.open(path + ".gz", "wb") as dst:
        dst.write(src.read())


def raw_data_path(output_dir):
    return os.path.join(output_dir, "raw_data.js")


def processed_data_path(output_dir):
    return os.path.join(output_dir, "processed_data.js")


def edges_cache_path(output_dir):
    return os.path.join(output_dir, "edges_cache.json")


def trip_details_dir(output_dir):
    return os.path.join(output_dir, "trip_details")


def load_processed(output_dir):
    """Return a dict with rawAll, tripMeta, processedLabel, processedEdges,
    processedTotal and edges (edges_cache.json contents), or None if there is
    no processed data yet."""
    rdp = raw_data_path(output_dir)
    if not os.path.exists(rdp):
        return None

    raw = load_js_data(rdp)
    processed = load_js_data(processed_data_path(output_dir))

    edges = []
    ecp = edges_cache_path(output_dir)
    if os.path.exists(ecp):
        with open(ecp, encoding="utf-8") as fh:
            edges = json.load(fh)

    return {
        "rawAll": raw.get("rawAll", []),
        "tripMeta": raw.get("tripMeta", []),
        "processedLabel": processed.get("processedLabel", ""),
        "processedEdges": processed.get("processedEdges", []),
        "processedTotal": processed.get("processedTotal", 0),
        "edges": edges,
    }


def remove_trip_indices(data, indices_to_remove):
    """Mutate `data` (as returned by load_processed) to drop the given
    0-based trip indices from tripMeta/rawAll, remap the tripIdxs referenced
    by processedEdges/edges accordingly (dropping edges left with no trips),
    and recompute processedTotal/processedLabel.

    Returns old_to_new: a dict mapping each surviving old index to its new
    index (removed indices are absent from the dict)."""
    remove = set(indices_to_remove)

    old_to_new = {}
    new_idx = 0
    for old_idx in range(len(data["tripMeta"])):
        if old_idx in remove:
            continue
        old_to_new[old_idx] = new_idx
        new_idx += 1

    data["tripMeta"] = [m for i, m in enumerate(data["tripMeta"]) if i not in remove]
    data["rawAll"] = [r for i, r in enumerate(data["rawAll"]) if i not in remove]

    def remap_edges(edges):
        new_edges = []
        for entry in edges:
            geom, trip_idxs, *rest = entry
            new_trip_idxs = [old_to_new[i] for i in trip_idxs if i in old_to_new]
            if not new_trip_idxs:
                continue
            new_edges.append([geom, new_trip_idxs] + rest)
        return new_edges

    data["processedEdges"] = remap_edges(data["processedEdges"])
    data["edges"] = remap_edges(data["edges"])
    data["processedTotal"] = len(data["tripMeta"])
    data["processedLabel"] = f"Processed ({len(data['tripMeta'])} trips)"
    return old_to_new


def save_processed(output_dir, data, old_to_new=None):
    """Write raw_data.js/.gz, processed_data.js/.gz and edges_cache.json from
    `data`. If `old_to_new` is given, also reindex the trip_details/trip_N.js
    files to match the new indices."""
    rdp = raw_data_path(output_dir)
    pdp = processed_data_path(output_dir)
    ecp = edges_cache_path(output_dir)

    with open(rdp, "w", encoding="utf-8") as out:
        out.write("var rawAll = ")
        json.dump(data["rawAll"], out)
        out.write(";\nvar tripMeta = ")
        json.dump(data["tripMeta"], out)
        out.write(";")

    with open(pdp, "w", encoding="utf-8") as out:
        out.write("var processedLabel = ")
        json.dump(data["processedLabel"], out)
        out.write(";\nvar processedEdges = ")
        json.dump(data["processedEdges"], out)
        out.write(";\nvar processedTotal = ")
        json.dump(data["processedTotal"], out)
        out.write(";")

    with open(ecp, "w", encoding="utf-8") as out:
        json.dump(data["edges"], out)

    write_gzip_copy(rdp)
    write_gzip_copy(pdp)

    if old_to_new is not None:
        _reindex_trip_details(output_dir, old_to_new)


def _reindex_trip_details(output_dir, old_to_new):
    """Rename trip_details/trip_<old>.js to trip_<new>.js (rewriting the
    'var tripDetail_<old> = ' declaration to use <new>) for every surviving
    index in old_to_new, and remove the files of deleted trips. Uses a
    temporary name for each surviving file first, since old and new index
    ranges can overlap."""
    tdd = trip_details_dir(output_dir)
    if not os.path.isdir(tdd):
        return

    new_to_old = {new: old for old, new in old_to_new.items()}

    tmp_paths = {}
    for old_idx, new_idx in old_to_new.items():
        src = os.path.join(tdd, f"trip_{old_idx}.js")
        if not os.path.exists(src):
            continue
        tmp = os.path.join(tdd, f"trip_{old_idx}.js.tmp")
        os.replace(src, tmp)
        tmp_paths[new_idx] = tmp

    # Any trip_N.js left at this point belonged to a removed trip.
    for fname in os.listdir(tdd):
        if fname.startswith("trip_") and fname.endswith(".js"):
            try:
                os.remove(os.path.join(tdd, fname))
            except OSError:
                pass

    for new_idx, tmp in tmp_paths.items():
        old_idx = new_to_old[new_idx]
        with open(tmp, encoding="utf-8") as fh:
            content = fh.read()
        content = content.replace(f"var tripDetail_{old_idx} =", f"var tripDetail_{new_idx} =", 1)
        with open(os.path.join(tdd, f"trip_{new_idx}.js"), "w", encoding="utf-8") as fh:
            fh.write(content)
        os.remove(tmp)


# ---------------------------------------------------------------------------
# Duplicate detection (pure, no GUI — testable in isolation)
# ---------------------------------------------------------------------------
def find_duplicate_pairs(rows):
    """Return (dup_names: set, pairs: list of (row_a, row_b)).

    Two trips are considered likely duplicates when they share the same date
    and their distances are both known and within 5 % of each other.
    """
    by_date = {}
    for row in rows:
        by_date.setdefault(row["date"] or "", []).append(row)

    dup_names = set()
    groups = []
    for date_rows in by_date.values():
        if len(date_rows) < 2:
            continue
        for i in range(len(date_rows)):
            for j in range(i + 1, len(date_rows)):
                a, b = date_rows[i], date_rows[j]
                da, db = a["distance"], b["distance"]
                if da is None or db is None:
                    similar = False
                else:
                    ref = max(da, db)
                    similar = ref == 0 or abs(da - db) / ref <= 0.05
                if similar:
                    dup_names.add(a["name"])
                    dup_names.add(b["name"])
                    groups.append((a, b))

    return dup_names, groups


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class DataWizardWindow(tk.Toplevel):
    STATUS_CHOICES = ["All", "Raw only", "Processed only", "Both"]

    def __init__(self, master, output_dir_var, on_changed=None):
        super().__init__(master)
        self.title("Data Wizard")
        self.geometry("900x640")
        self.output_dir_var = output_dir_var
        self.on_changed = on_changed
        self.all_rows = []

        self._build_filters_frame()
        self._build_tree()
        self._build_buttons()

        self.refresh()

    def output_dir(self):
        return self.output_dir_var.get()

    # ------------------------------------------------------------------ UI
    def _build_filters_frame(self):
        filt = ttk.LabelFrame(self, text="Filters")
        filt.pack(fill="x", padx=10, pady=8)

        ttk.Label(filt, text="Status:").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self.status_var = tk.StringVar(value="All")
        status_combo = ttk.Combobox(filt, textvariable=self.status_var, values=self.STATUS_CHOICES,
                                     state="readonly", width=14)
        status_combo.grid(row=0, column=1, padx=4, pady=4, sticky="w")

        ttk.Label(filt, text="Sport:").grid(row=0, column=2, padx=4, pady=4, sticky="e")
        self.sport_var = tk.StringVar(value="All")
        self.sport_combo = ttk.Combobox(filt, textvariable=self.sport_var, values=["All"],
                                         state="readonly", width=16)
        self.sport_combo.grid(row=0, column=3, padx=4, pady=4, sticky="w")

        ttk.Label(filt, text="Search name:").grid(row=0, column=4, padx=4, pady=4, sticky="e")
        self.search_var = tk.StringVar()
        ttk.Entry(filt, textvariable=self.search_var, width=24).grid(row=0, column=5, padx=4, pady=4, sticky="w")

        ttk.Label(filt, text="Start date (YYYY-MM-DD):").grid(row=1, column=0, padx=4, pady=4, sticky="e")
        self.start_date_var = tk.StringVar()
        ttk.Entry(filt, textvariable=self.start_date_var, width=12).grid(row=1, column=1, padx=4, pady=4, sticky="w")

        ttk.Label(filt, text="End date (YYYY-MM-DD):").grid(row=1, column=2, padx=4, pady=4, sticky="e")
        self.end_date_var = tk.StringVar()
        ttk.Entry(filt, textvariable=self.end_date_var, width=12).grid(row=1, column=3, padx=4, pady=4, sticky="w")

        ttk.Label(filt, text="Min distance (km):").grid(row=1, column=4, padx=4, pady=4, sticky="e")
        self.min_dist_var = tk.StringVar(value="0")
        ttk.Entry(filt, textvariable=self.min_dist_var, width=8).grid(row=1, column=5, padx=4, pady=4, sticky="w")

        ttk.Button(filt, text="Apply filters", command=self.apply_filters).grid(
            row=1, column=6, padx=4, pady=4, sticky="e"
        )

        self.filter_count_var = tk.StringVar(value="")
        ttk.Label(filt, textvariable=self.filter_count_var).grid(
            row=2, column=0, columnspan=7, padx=4, pady=(0, 4), sticky="w"
        )

        self.status_var.trace_add("write", lambda *a: self.apply_filters())
        self.sport_var.trace_add("write", lambda *a: self.apply_filters())

    def _build_tree(self):
        list_frame = ttk.Frame(self)
        list_frame.pack(fill="both", expand=True, padx=10)

        columns = ("date", "name", "sport", "source", "sensors", "distance", "status")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="extended")
        headers = {"date": "Date", "name": "File name", "sport": "Sport", "source": "Source",
                   "sensors": "Sensors", "distance": "Dist (km)", "status": "Status"}
        for col, label in headers.items():
            self.tree.heading(col, text=label, command=lambda c=col: self.sort_by_column(c))
        self.tree.column("date",     width=90,  anchor="w")
        self.tree.column("name",     width=310, anchor="w")
        self.tree.column("sport",    width=75,  anchor="w")
        self.tree.column("source",   width=62,  anchor="w")
        self.tree.column("sensors",  width=85,  anchor="w")
        self.tree.column("distance", width=75,  anchor="e")
        self.tree.column("status",   width=100, anchor="w")
        self.sort_state = {}

        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

    def _build_buttons(self):
        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=10, pady=6)

        ttk.Button(btns, text="Delete raw GPX", command=self.delete_raw).pack(side="left")
        ttk.Button(btns, text="Delete processed data", command=self.delete_processed).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="Delete both", command=self.delete_both).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="Find duplicates", command=self.find_duplicates).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="Refresh", command=self.refresh).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="Close", command=self.destroy).pack(side="right")

    def sort_by_column(self, col):
        reverse = self.sort_state.get(col, False)
        items = [(self.tree.set(i, col), i) for i in self.tree.get_children("")]

        def key(t):
            val = t[0]
            if col == "distance":
                try:
                    return float(val)
                except ValueError:
                    return -1.0
            return val

        items.sort(key=key, reverse=reverse)
        for index, (_, i) in enumerate(items):
            self.tree.move(i, "", index)
        self.sort_state[col] = not reverse

    # ------------------------------------------------------------- data/filters
    def refresh(self):
        raw_names = set(list_trips())
        data = load_processed(self.output_dir()) or {"tripMeta": []}

        processed_by_name = {}
        for idx, m in enumerate(data["tripMeta"]):
            name = m.get("name")
            if name:
                processed_by_name[name] = (idx, m)

        rows = []
        for name in sorted(raw_names | set(processed_by_name.keys())):
            in_raw = name in raw_names
            proc = processed_by_name.get(name)
            if in_raw and proc:
                status = "Both"
            elif in_raw:
                status = "Raw only"
            else:
                status = "Processed only"

            if proc:
                _idx, meta = proc
                date = meta.get("date") or trip_date(name)
                sport = meta.get("sport") or trip_sport(name)
                distance = meta.get("distanceKm")
            else:
                date = trip_date(name)
                sport = trip_sport(name)
                distance = None

            source = meta.get("source", "") if proc else ""
            fpath = os.path.join(SOURCE_DIR, name)
            sensors = ""
            if os.path.exists(fpath):
                src_meta, sport_meta = read_gpx_meta(fpath)
                if not source:
                    source = src_meta
                if not sport and sport_meta:
                    sport = sport_meta
                sensors = gpx_sensors(fpath)
            rows.append({"name": name, "date": date, "sport": sport, "distance": distance,
                         "status": status, "source": source, "sensors": sensors})

        self.all_rows = rows
        self.sport_combo["values"] = ("All",) + tuple(SPORT_CATEGORIES)
        self.apply_filters()

    def apply_filters(self):
        self.tree.delete(*self.tree.get_children())

        status_choice = self.status_var.get()
        sport_choice = self.sport_var.get()
        start_date = self.start_date_var.get().strip()
        end_date = self.end_date_var.get().strip()
        search = self.search_var.get().strip().lower()
        try:
            min_dist = float(self.min_dist_var.get())
        except ValueError:
            min_dist = 0.0

        shown = 0
        for row in self.all_rows:
            if status_choice != "All" and row["status"] != status_choice:
                continue
            if sport_choice != "All" and row["sport"] != sport_choice:
                continue

            date = row["date"] or ""
            if start_date and date < start_date:
                continue
            if end_date and date > end_date:
                continue

            dist = row["distance"]
            if min_dist and (dist is None or dist < min_dist):
                continue

            if search and search not in row["name"].lower():
                continue

            dist_str = f"{dist:.1f}" if dist is not None else ""
            self.tree.insert("", "end", iid=row["name"], values=(
                row["date"], row["name"], row["sport"], row.get("source", ""),
                row.get("sensors", ""), dist_str, row["status"],
            ))
            shown += 1

        total = len(self.all_rows)
        if shown == total:
            self.filter_count_var.set(f"Showing all {total} trip(s).")
        else:
            self.filter_count_var.set(f"Showing {shown} of {total} trip(s) ({total - shown} filtered out).")

    # ------------------------------------------------------------------ delete
    def _row_status(self, name):
        for row in self.all_rows:
            if row["name"] == name:
                return row["status"]
        return ""

    def _notify_changed(self):
        if self.on_changed:
            self.on_changed()

    # ---------------------------------------------------------------- duplicates
    _PAIR_COLORS = [
        '#FFF9C4', '#C8E6C9', '#BBDEFB', '#F8BBD0',
        '#FFE0B2', '#E1BEE7', '#B2EBF2', '#DCEDC8',
    ]

    def find_duplicates(self):
        """Highlight likely duplicate trips in the tree, one color per pair."""
        dup_names, groups = find_duplicate_pairs(self.all_rows)

        if not groups:
            messagebox.showinfo("Find duplicates", "No likely duplicates found.")
            return

        visible = set(self.tree.get_children(""))
        for pair_idx, (a, b) in enumerate(groups):
            tag = f'dup_pair_{pair_idx}'
            color = self._PAIR_COLORS[pair_idx % len(self._PAIR_COLORS)]
            self.tree.tag_configure(tag, background=color)
            for name in (a["name"], b["name"]):
                if name in visible:
                    existing = self.tree.item(name, "tags")
                    self.tree.item(name, tags=tuple(existing) + (tag,))

        lines = [f"{len(groups)} likely duplicate pair(s) found ({len(dup_names)} trip(s)):\n"]
        for a, b in groups[:20]:
            da = f"{a['distance']:.1f} km" if a["distance"] is not None else "?"
            db = f"{b['distance']:.1f} km" if b["distance"] is not None else "?"
            lines.append(f"  {a['date']}  {da}  ←→  {db}  [{a['source'] or '?'} / {b['source'] or '?'}]")
        if len(groups) > 20:
            lines.append(f"  ... and {len(groups) - 20} more")
        lines.append("\nPairs are color-coded in the list. Use Delete buttons to remove them.")
        messagebox.showinfo("Find duplicates", "\n".join(lines))

    def delete_raw(self):
        names = [n for n in self.tree.selection() if self._row_status(n) in ("Raw only", "Both")]
        if not names:
            messagebox.showinfo("Data Wizard", "No selected trip has a raw GPX file to delete.")
            return
        if not messagebox.askyesno(
            "Delete raw GPX",
            f"Delete the raw .gpx file for {len(names)} trip(s)? This cannot be undone."
        ):
            return
        self._delete_raw_files(names)
        self.refresh()
        self._notify_changed()

    def delete_processed(self):
        names = [n for n in self.tree.selection() if self._row_status(n) in ("Processed only", "Both")]
        if not names:
            messagebox.showinfo("Data Wizard", "No selected trip has processed data to delete.")
            return
        if not messagebox.askyesno(
            "Delete processed data",
            f"Delete processed data for {len(names)} trip(s)? This cannot be undone."
        ):
            return
        self._delete_processed_names(names)
        self.refresh()
        self._notify_changed()

    def delete_both(self):
        sel = list(self.tree.selection())
        if not sel:
            return
        if not messagebox.askyesno(
            "Delete both",
            f"Delete raw GPX files and processed data for {len(sel)} trip(s)? This cannot be undone."
        ):
            return

        raw_names = [n for n in sel if self._row_status(n) in ("Raw only", "Both")]
        processed_names = [n for n in sel if self._row_status(n) in ("Processed only", "Both")]

        self._delete_raw_files(raw_names)
        self._delete_processed_names(processed_names)

        self.refresh()
        self._notify_changed()

    def _delete_raw_files(self, names):
        for name in names:
            path = os.path.join(SOURCE_DIR, name)
            try:
                os.remove(path)
            except OSError as e:
                messagebox.showerror("Data Wizard", f"Could not delete {name}:\n{e}")

    def _delete_processed_names(self, names):
        if not names:
            return
        data = load_processed(self.output_dir())
        if not data:
            return

        name_to_idx = {m.get("name"): i for i, m in enumerate(data["tripMeta"])}
        indices = {name_to_idx[n] for n in names if n in name_to_idx}
        if not indices:
            return

        old_to_new = remove_trip_indices(data, indices)
        save_processed(self.output_dir(), data, old_to_new)
