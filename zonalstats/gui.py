"""
Tkinter GUI for the ZonalStats extractor.

The window is laid out as the three questions a run actually asks, in order:

  1. Data      — which orthomosaic, which plot polygons, which camera.
  2. Extract   — bands & statistics, vegetation indices, shapefile attributes,
                 and the nodata / scale options. One notebook, four tabs.
  3. Output    — folder, file name, and a live preview of the CSV's columns.

Below that sits a permanent readiness line: instead of letting the user press
Run and meet an error box, every input is validated as it is typed, the section
that still needs attention shows a status dot, and the Run button states what is
missing until nothing is. Everything the user picks is remembered in
settings.json and restored next launch, so the common case — same trial, next
flight — is pick the new orthomosaic and press Run.
"""

from __future__ import annotations

import glob
import os
import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk, filedialog, messagebox

from . import APP_NAME, __version__
from .core import ALL_STATS, ExtractionConfig, run_extraction
from .paths import app_dir
from .settings import existing_only, load_settings, remember_recent, save_settings
from .ui import ACCENT, BAD, FIELD, INK, OK, StatusDot, accent_header, apply_theme
from .formula import KNOWN_ROLES, FormulaError, validate_formula
from .indices import Index, add_or_update_index, builtin_indices, load_indices, save_indices
from .sensors import Sensor, is_builtin, load_sensors, save_sensors
from .tooltip import ScrollableFrame, ToolTip

DEFAULT_STATS = {"mean", "median", "std", "count"}
DEFAULT_INDICES = {"NDVI", "NDRE", "GNDVI"}
PREFERRED_ATTRS = ("num", "col", "row", "variant", "rep")
ROLE_LABELS = {"RedEdge": "Red Edge"}

# One-line explanations shown on hover. Statistics are the part of the app most
# likely to be picked by guesswork, so each says what it is *for*, not just what
# it computes.
STAT_HELP = {
    "mean": "Arithmetic mean of every valid pixel in the plot. The usual choice.",
    "median": "50th percentile. Robust to a few bright edge or weed pixels.",
    "std": "Standard deviation — how variable the plot is internally.",
    "min": "Smallest valid pixel value in the plot.",
    "max": "Largest valid pixel value in the plot.",
    "count": "How many valid pixels were used. 0 means the plot did not overlap "
             "the raster — a useful sanity check on every run.",
    "range": "max − min.",
    "iqr": "Interquartile range (p75 − p25). Spread, ignoring the tails.",
    "p5": "5th percentile.",
    "p10": "10th percentile.",
    "p25": "25th percentile (lower quartile).",
    "p75": "75th percentile (upper quartile).",
    "p90": "90th percentile.",
    "p95": "95th percentile.",
    "cv": "Coefficient of variation (std / mean). Unitless variability, handy "
          "for comparing plots with different overall brightness.",
}

ROLE_HELP = {
    "Blue": "Blue reflectance band.",
    "Green": "Green reflectance band.",
    "Red": "Red reflectance band.",
    "RedEdge": "Red-edge band — sensitive to chlorophyll and early stress.",
    "NIR": "Near-infrared band — the workhorse for biomass and vigour.",
    "LWIR": "Thermal (long-wave infrared) band, if the camera has one.",
}

GUIDE_TEXT = """\
ZonalStats turns a drone orthomosaic plus plot polygons into one CSV row per plot.

──────────────────────────────────────────────
1 · DATA
──────────────────────────────────────────────
Orthomosaic
    Single file  — one .tif.
    Folder       — every .tif in the folder is processed in one run, and the
                   CSV gains a 'source_raster' column telling the rows apart.
                   Use this for a whole season of flights at once.

Plot polygons (.shp)
    Draw them right here with "Design / edit plots…": lay a plot grid over
    the orthomosaic, then give each plot its ID, variant (genotype /
    treatment) and rep (replication). An existing shapefile works too, and
    the Designer can import and re-label it.

Sensor / camera
    Sets which raster band is Red, NIR, and so on. It is guessed from the band
    count when you pick the orthomosaic — check it, and override if the guess is
    wrong. "Add / edit camera…" defines a new one; it is saved and offered
    every launch afterwards.

──────────────────────────────────────────────
2 · WHAT TO EXTRACT
──────────────────────────────────────────────
Bands & statistics
    Which raw bands to report, and which statistics to compute. The statistics
    apply to both the raw bands and every index — pick "mean" and you get
    Red_mean and NDVI_mean alike. Hover any statistic for what it is good for.

Indices
    Vegetation indices. Only those computable from the selected camera's bands
    are listed, so an index never silently fails. Hover for the formula and
    description, double-click to edit, and use the search box to find one by
    name or formula. "*" marks an index you added yourself.

Attributes
    Which columns of the shapefile (num, col, row, variant, rep …) to carry
    through into the CSV. Those five are pre-selected when present.

Options
    NoData value and a reflectance scale factor. Scale matters for the
    non-normalised indices (SAVI, EVI…): leave it at 1.0 for 0–1 reflectance
    orthomosaics, or set 1/32768 for 16-bit digital numbers.

──────────────────────────────────────────────
3 · OUTPUT
──────────────────────────────────────────────
Pick a folder and a file name. The line underneath shows the exact columns the
CSV will have, and how many, before you run anything.

──────────────────────────────────────────────
RUNNING
──────────────────────────────────────────────
The Run button stays disabled until everything needed is present, and says what
is still missing. The status dot beside each input shows the same thing at a
glance: circle = still to do, tick = ready, cross = there is a problem.

Everything you pick is remembered and restored next launch, so the next flight
of the same trial is: choose the new orthomosaic, press Run.

Keyboard
    Ctrl+O    choose orthomosaic          F5 / Ctrl+R   run extraction
    Ctrl+P    choose plot polygons        Ctrl+L        show / hide the log
    Ctrl+D    open the Plot Designer      F1            this guide
"""


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        apply_theme(self)
        self.title(f"{APP_NAME} {__version__} — Zonal statistics & indices")
        self.minsize(760, 600)

        self.settings = load_settings()
        self.sensors = load_sensors()
        self.all_indices: list[Index] = load_indices()
        self._builtin_names = {i.name for i in builtin_indices()}

        # Tk variables
        self.input_mode = tk.StringVar(value="file")
        self.raster_var = tk.StringVar()
        self.shape_var = tk.StringVar()
        self.outdir_var = tk.StringVar()
        self.outname_var = tk.StringVar(value="results.csv")
        self.sensor_var = tk.StringVar(value=next(iter(self.sensors)))
        self.nodata_var = tk.StringVar(value="-10000")
        self.scale_var = tk.StringVar(value="1.0")
        self.index_filter = tk.StringVar()
        self.open_when_done = tk.BooleanVar(value=True)
        self.log_visible = tk.BooleanVar(value=False)

        self.band_vars: dict[str, tk.BooleanVar] = {}
        self.stat_vars: dict[str, tk.BooleanVar] = {
            s: tk.BooleanVar(value=s in DEFAULT_STATS) for s in ALL_STATS
        }
        self.index_vars: dict[str, tk.BooleanVar] = {}
        self.attr_vars: dict[str, tk.BooleanVar] = {}
        self._shape_fields: list[str] = []
        self._shape_count = 0            # polygons in the chosen shapefile
        self._outname_touched = False    # has the user typed their own name?
        self._running = False
        self._raster_cache: tuple[str, str, list[str]] | None = None
        self.recent_rasters: list[str] = existing_only(
            self.settings.get("recent_rasters"))
        self.recent_shapes: list[str] = existing_only(
            self.settings.get("recent_shapefiles"))

        self._build_menu()
        self._build_ui()
        self._restore_settings()
        self._bind_shortcuts()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_ready()
        self._apply_geometry()

    # ---------- window geometry ----------
    def _apply_geometry(self):
        """Reopen at the last size/position, else at the size the content needs.

        A saved geometry from a second monitor that is no longer attached would
        put the window off-screen, so anything that does not land inside the
        current desktop is discarded in favour of a fresh fit.
        """
        saved = self.settings.get("geometry")
        if saved and self._geometry_fits(saved):
            try:
                self.geometry(saved)
                return
            except tk.TclError:
                pass
        self.update_idletasks()
        w = max(self.winfo_reqwidth(), 860)
        h = self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(w, int(sw * 0.95)), min(h, int(sh * 0.92))
        self.geometry(f"{w}x{h}+{max((sw - w) // 2, 0)}+{max((sh - h) // 3, 0)}")

    def _geometry_fits(self, geom: str) -> bool:
        """True if 'WxH+X+Y' lands inside the current desktop at a usable size."""
        try:
            parts = geom.replace("+-", "+ -").split("+")
            w, h = (int(v) for v in parts[0].split("x"))
            x, y = int(parts[1]), int(parts[2])
        except (ValueError, IndexError, AttributeError):
            return False
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        return 400 <= w <= sw and 300 <= h <= sh and -50 <= x < sw and -50 <= y < sh

    # ---------- menu & shortcuts ----------
    def _build_menu(self):
        kw = dict(tearoff=0, bg=FIELD, fg=INK, activebackground=ACCENT,
                  activeforeground="#ffffff", borderwidth=0)
        bar = tk.Menu(self, **kw)

        self.file_menu = tk.Menu(bar, **kw)
        self.file_menu.add_command(label="Open orthomosaic…", accelerator="Ctrl+O",
                                   command=self._browse_raster)
        self.file_menu.add_command(label="Open plot polygons…", accelerator="Ctrl+P",
                                   command=self._browse_shape)
        self.file_menu.add_command(label="Set output folder…", command=self._browse_outdir)
        self.file_menu.add_separator()
        self.recent_menu = tk.Menu(self.file_menu, **kw)
        self.file_menu.add_cascade(label="Recent", menu=self.recent_menu)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Clear all inputs", command=self._clear_all)
        self.file_menu.add_command(label="Exit", command=self._on_close)
        bar.add_cascade(label="File", menu=self.file_menu)

        tools = tk.Menu(bar, **kw)
        tools.add_command(label="Plot Designer…", accelerator="Ctrl+D",
                          command=self._open_designer)
        tools.add_separator()
        tools.add_command(label="Add / edit camera…",
                          command=lambda: self._sensor_dialog(None))
        tools.add_command(label="Add / edit index…",
                          command=lambda: self._index_dialog(None))
        tools.add_separator()
        tools.add_command(label="Open config folder",
                          command=lambda: self._open_path(app_dir()))
        bar.add_cascade(label="Tools", menu=tools)

        helpm = tk.Menu(bar, **kw)
        helpm.add_command(label="Quick guide", accelerator="F1", command=self._show_guide)
        helpm.add_command(label="About", command=self._show_about)
        bar.add_cascade(label="Help", menu=helpm)

        self.config(menu=bar)
        self._refresh_recent_menu()

    def _refresh_recent_menu(self):
        self.recent_menu.delete(0, "end")
        if not self.recent_rasters and not self.recent_shapes:
            self.recent_menu.add_command(label="(nothing yet)", state="disabled")
            return
        for p in self.recent_rasters:
            self.recent_menu.add_command(
                label=f"Ortho:  {os.path.basename(p)}",
                command=lambda q=p: self._use_raster(q, autodetect=True))
        if self.recent_rasters and self.recent_shapes:
            self.recent_menu.add_separator()
        for p in self.recent_shapes:
            self.recent_menu.add_command(
                label=f"Plots:  {os.path.basename(p)}",
                command=lambda q=p: self._use_shapefile(q))

    def _bind_shortcuts(self):
        self.bind("<Control-o>", lambda e: self._browse_raster())
        self.bind("<Control-p>", lambda e: self._browse_shape())
        self.bind("<Control-d>", lambda e: self._open_designer())
        self.bind("<Control-r>", lambda e: self._run_clicked())
        self.bind("<F5>", lambda e: self._run_clicked())
        self.bind("<Control-l>", lambda e: self._toggle_log())
        self.bind("<F1>", lambda e: self._show_guide())

    # ---------- UI construction ----------
    def _build_ui(self):
        accent_header(self, APP_NAME, "Per-plot reflectance & vegetation indices",
                      f"v{__version__}")
        self._build_bottom()          # packed first so it is always visible

        body = ttk.Frame(self, padding=(12, 10, 12, 4))
        body.pack(fill="both", expand=True)
        self._build_step_data(body)
        self._build_step_extract(body)
        self._build_step_output(body)

        # Live validation: any change to a path or number re-checks readiness.
        for var in (self.raster_var, self.shape_var, self.outdir_var,
                    self.outname_var, self.nodata_var, self.scale_var):
            var.trace_add("write", lambda *_: self._refresh_ready())
        for var in self.stat_vars.values():
            var.trace_add("write", lambda *_: self._refresh_ready())

    # --- step 1: data -----------------------------------------------------
    def _build_step_data(self, parent):
        box = ttk.LabelFrame(parent, text="1 · Data", style="Step.TLabelframe")
        box.pack(fill="x")
        box.columnconfigure(2, weight=1)

        # Orthomosaic
        self.dot_raster = StatusDot(box)
        self.dot_raster.grid(row=0, column=0, sticky="w")
        ttk.Label(box, text="Orthomosaic").grid(row=0, column=1, sticky="w", padx=(0, 8))
        self.raster_entry = ttk.Entry(box, textvariable=self.raster_var)
        self.raster_entry.grid(row=0, column=2, sticky="ew")
        b = ttk.Button(box, text="Browse…", command=self._browse_raster)
        b.grid(row=0, column=3, padx=(6, 0))
        ToolTip(b, "Choose the orthomosaic .tif (or a folder of them).  Ctrl+O")

        sub = ttk.Frame(box)
        sub.grid(row=1, column=2, columnspan=2, sticky="w", pady=(2, 8))
        ttk.Radiobutton(sub, text="Single file", value="file",
                        variable=self.input_mode,
                        command=self._on_mode_change).pack(side="left")
        ttk.Radiobutton(sub, text="Folder — batch every .tif", value="folder",
                        variable=self.input_mode,
                        command=self._on_mode_change).pack(side="left", padx=(10, 0))
        self.raster_hint = ttk.Label(sub, text="", style="Hint.TLabel")
        self.raster_hint.pack(side="left", padx=(12, 0))

        # Plot polygons
        self.dot_shape = StatusDot(box)
        self.dot_shape.grid(row=2, column=0, sticky="w")
        ttk.Label(box, text="Plot polygons").grid(row=2, column=1, sticky="w", padx=(0, 8))
        self.shape_entry = ttk.Entry(box, textvariable=self.shape_var)
        self.shape_entry.grid(row=2, column=2, sticky="ew")
        b = ttk.Button(box, text="Browse…", command=self._browse_shape)
        b.grid(row=2, column=3, padx=(6, 0))
        ToolTip(b, "Choose the .shp holding your plot polygons.  Ctrl+P")

        sub = ttk.Frame(box)
        sub.grid(row=3, column=2, columnspan=2, sticky="w", pady=(2, 8))
        b = ttk.Button(sub, text="Design / edit plots…", command=self._open_designer)
        b.pack(side="left")
        ToolTip(b, "Draw plots straight onto the imagery, or import the ones you\n"
                   "have to give them variant and rep numbers.  Ctrl+D")
        self.shape_hint = ttk.Label(sub, text="No shapefile chosen yet.",
                                    style="Hint.TLabel")
        self.shape_hint.pack(side="left", padx=(12, 0))

        # Sensor
        self.dot_sensor = StatusDot(box)
        self.dot_sensor.grid(row=4, column=0, sticky="w")
        ttk.Label(box, text="Sensor / camera").grid(row=4, column=1, sticky="w", padx=(0, 8))
        sf = ttk.Frame(box)
        sf.grid(row=4, column=2, columnspan=2, sticky="ew")
        self.sensor_combo = ttk.Combobox(sf, textvariable=self.sensor_var,
                                         state="readonly", values=list(self.sensors),
                                         width=24)
        self.sensor_combo.pack(side="left")
        self.sensor_combo.bind("<<ComboboxSelected>>", lambda e: self._on_sensor_change())
        ToolTip(self.sensor_combo,
                "Decides which raster band is Red, NIR, … — guessed from the\n"
                "band count when you pick the orthomosaic. Override if wrong.")
        b = ttk.Button(sf, text="Add / edit camera…",
                       command=lambda: self._sensor_dialog(None))
        b.pack(side="left", padx=(6, 0))
        ToolTip(b, "Define a new camera's band order, or correct an existing one.")

        self.band_order_lbl = ttk.Label(box, text="", style="Mono.TLabel",
                                        wraplength=640, justify="left")
        self.band_order_lbl.grid(row=5, column=2, columnspan=2, sticky="w", pady=(3, 0))

    # --- step 2: what to extract -----------------------------------------
    def _build_step_extract(self, parent):
        box = ttk.LabelFrame(parent, text="2 · What to extract",
                             style="Step.TLabelframe")
        box.pack(fill="both", expand=True, pady=(10, 0))

        nb = ttk.Notebook(box)
        nb.pack(fill="both", expand=True)
        self.tab_bands = ttk.Frame(nb)
        self.tab_indices = ttk.Frame(nb)
        self.tab_attrs = ttk.Frame(nb)
        self.tab_opts = ttk.Frame(nb)
        nb.add(self.tab_bands, text="Bands & statistics")
        nb.add(self.tab_indices, text="Indices")
        nb.add(self.tab_attrs, text="Attributes")
        nb.add(self.tab_opts, text="Options")
        self._build_bands_tab()
        self._build_indices_tab()
        self._build_attrs_tab()
        self._build_opts_tab()

    def _build_bands_tab(self):
        wrap = ttk.Frame(self.tab_bands, padding=(8, 8))
        wrap.pack(fill="both", expand=True)
        wrap.columnconfigure(0, weight=1, uniform="bt")
        wrap.columnconfigure(1, weight=2, uniform="bt")
        wrap.rowconfigure(0, weight=1)

        # Raw bands — rebuilt whenever the camera changes.
        bf = ttk.LabelFrame(wrap, text="Raw bands")
        bf.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.bands_holder = ttk.Frame(bf)
        self.bands_holder.pack(fill="both", expand=True)
        row = ttk.Frame(bf)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="All", width=5, style="Compact.TButton",
                   command=lambda: self._set_all_bands(True)).pack(side="left")
        ttk.Button(row, text="None", width=6, style="Compact.TButton",
                   command=lambda: self._set_all_bands(False)).pack(side="left", padx=4)
        self.bands_count = ttk.Label(row, text="", style="Hint.TLabel")
        self.bands_count.pack(side="right")

        # Statistics — the same set applies to bands and indices alike.
        sf = ttk.LabelFrame(wrap, text="Statistics  ·  applied to every band and index")
        sf.grid(row=0, column=1, sticky="nsew")
        grid = ttk.Frame(sf)
        grid.pack(fill="both", expand=True)
        for i, s in enumerate(ALL_STATS):
            cb = ttk.Checkbutton(grid, text=s, variable=self.stat_vars[s], width=9)
            cb.grid(row=i // 5, column=i % 5, sticky="w", padx=4, pady=1)
            ToolTip(cb, STAT_HELP.get(s, s))
        for c in range(5):
            grid.columnconfigure(c, weight=1)
        row = ttk.Frame(sf)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="All", width=5, style="Compact.TButton",
                   command=lambda: self._set_all_stats(True)).pack(side="left")
        ttk.Button(row, text="None", width=6, style="Compact.TButton",
                   command=lambda: self._set_all_stats(False)).pack(side="left", padx=4)
        ttk.Button(row, text="Typical", style="Compact.TButton",
                   command=self._set_typical_stats).pack(side="left")
        self.stats_count = ttk.Label(row, text="", style="Hint.TLabel")
        self.stats_count.pack(side="right")
        ttk.Label(sf, style="Hint.TLabel", justify="left", wraplength=430,
                  text="Hover a statistic for what it is good for. Every one you tick "
                       "becomes a column for each band and each index, so the CSV "
                       "grows quickly — 'Typical' is mean, median, std and count."
                  ).pack(anchor="w", pady=(4, 0))

    def _build_indices_tab(self):
        bar = ttk.Frame(self.tab_indices, padding=(8, 8, 8, 0))
        bar.pack(fill="x")
        b = ttk.Button(bar, text="Add / edit index…",
                       command=lambda: self._index_dialog(None))
        b.pack(side="left")
        ToolTip(b, "Create your own index from a formula, with live validation.")
        ttk.Button(bar, text="All", width=5, style="Compact.TButton",
                   command=lambda: self._set_all_indices(True)).pack(side="left", padx=(6, 2))
        ttk.Button(bar, text="None", width=6, style="Compact.TButton",
                   command=lambda: self._set_all_indices(False)).pack(side="left")
        ttk.Label(bar, text="Search:").pack(side="left", padx=(14, 4))
        e = ttk.Entry(bar, textvariable=self.index_filter, width=16)
        e.pack(side="left")
        ToolTip(e, "Filter by name, formula or description.")
        self.index_filter.trace_add("write", lambda *_: self._refresh_indices_list())
        self.indices_count = ttk.Label(bar, text="", style="Hint.TLabel")
        self.indices_count.pack(side="right")

        ttk.Label(self.tab_indices, style="Hint.TLabel",
                  text="Only indices your camera has the bands for are listed  ·  "
                       "hover for the formula  ·  double-click to edit  ·  * = your own"
                  ).pack(anchor="w", padx=10, pady=(4, 0))
        self.idx_scroll = ScrollableFrame(self.tab_indices, height=150)
        self.idx_scroll.pack(fill="both", expand=True, padx=8, pady=(4, 8))

    def _build_attrs_tab(self):
        bar = ttk.Frame(self.tab_attrs, padding=(8, 8, 8, 0))
        bar.pack(fill="x")
        ttk.Button(bar, text="All", width=5, style="Compact.TButton",
                   command=lambda: self._set_all_attrs(True)).pack(side="left")
        ttk.Button(bar, text="None", width=6, style="Compact.TButton",
                   command=lambda: self._set_all_attrs(False)).pack(side="left", padx=4)
        self.attrs_count = ttk.Label(bar, text="", style="Hint.TLabel")
        self.attrs_count.pack(side="right")
        ttk.Label(self.tab_attrs, style="Hint.TLabel", justify="left", wraplength=680,
                  text="Columns from the shapefile to carry into the CSV. num, col, row, "
                       "variant and rep are pre-selected when the shapefile has them."
                  ).pack(anchor="w", padx=10, pady=(4, 0))
        self.attrs_scroll = ScrollableFrame(self.tab_attrs, height=140)
        self.attrs_scroll.pack(fill="both", expand=True, padx=8, pady=(4, 8))

    def _build_opts_tab(self):
        f = ttk.Frame(self.tab_opts, padding=(12, 12))
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="NoData value").grid(row=0, column=0, sticky="w", pady=5)
        e = ttk.Entry(f, textvariable=self.nodata_var, width=16)
        e.grid(row=0, column=1, sticky="w", padx=10)
        ToolTip(e, "Pixels equal to this are excluded. Blank = use the camera's\n"
                   "default. Leave as-is unless your ortho uses something unusual.")
        ttk.Label(f, text="Blank uses the camera's default.",
                  style="Hint.TLabel").grid(row=0, column=2, sticky="w")

        ttk.Label(f, text="Reflectance scale").grid(row=1, column=0, sticky="w", pady=5)
        e = ttk.Entry(f, textvariable=self.scale_var, width=16)
        e.grid(row=1, column=1, sticky="w", padx=10)
        ToolTip(e, "Multiplies every band before the indices are evaluated.")
        ttk.Label(f, text="1.0 = no scaling.",
                  style="Hint.TLabel").grid(row=1, column=2, sticky="w")

        ttk.Label(f, style="Hint.TLabel", justify="left", wraplength=640,
                  text="Scale only matters for the non-normalised indices (SAVI, EVI, "
                       "MCARI…). Ratio indices such as NDVI cancel it out. For 0–1 "
                       "reflectance orthomosaics leave it at 1.0; for 16-bit digital "
                       "numbers set 1/32768 which is about 0.0000305."
                  ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(12, 0))

        ttk.Button(f, text="Reset options to defaults",
                   command=self._reset_options).grid(row=3, column=0, sticky="w",
                                                     pady=(14, 0))

    # --- step 3: output ---------------------------------------------------
    def _build_step_output(self, parent):
        box = ttk.LabelFrame(parent, text="3 · Output", style="Step.TLabelframe")
        box.pack(fill="x", pady=(10, 0))
        box.columnconfigure(2, weight=1)

        self.dot_out = StatusDot(box)
        self.dot_out.grid(row=0, column=0, sticky="w")
        ttk.Label(box, text="Folder").grid(row=0, column=1, sticky="w", padx=(0, 8))
        self.outdir_entry = ttk.Entry(box, textvariable=self.outdir_var)
        self.outdir_entry.grid(row=0, column=2, sticky="ew")
        ttk.Button(box, text="Browse…", command=self._browse_outdir).grid(
            row=0, column=3, padx=(6, 0))

        ttk.Label(box, text="File name").grid(row=1, column=1, sticky="w",
                                              padx=(0, 8), pady=(6, 0))
        nm = ttk.Frame(box)
        nm.grid(row=1, column=2, columnspan=2, sticky="ew", pady=(6, 0))
        ent = ttk.Entry(nm, textvariable=self.outname_var, width=32)
        ent.pack(side="left")
        ent.bind("<KeyRelease>", lambda e: setattr(self, "_outname_touched", True))
        ToolTip(ent, "'.csv' is added automatically if you leave it off.\n"
                     "Named after the orthomosaic until you type your own.")
        cb = ttk.Checkbutton(nm, text="Open the folder when finished",
                             variable=self.open_when_done)
        cb.pack(side="left", padx=(14, 0))
        ToolTip(cb, "Pops the output folder open as soon as the CSV is written.")

        self.cols_lbl = ttk.Label(box, text="", style="Hint.TLabel",
                                  wraplength=760, justify="left")
        self.cols_lbl.grid(row=2, column=1, columnspan=3, sticky="w", pady=(8, 0))

    # --- bottom: readiness, run, log --------------------------------------
    def _build_bottom(self):
        bottom = ttk.Frame(self)
        bottom.pack(side="bottom", fill="x")

        run = ttk.Frame(bottom, padding=(12, 8, 12, 4))
        run.pack(fill="x")
        self.run_btn = ttk.Button(run, text="▶  Run extraction", style="Accent.TButton",
                                  command=self._run_clicked)
        self.run_btn.pack(side="left")
        ToolTip(self.run_btn, "Start the extraction.  F5")
        self.log_btn = ttk.Button(run, text="Log ▾", style="Link.TButton",
                                  command=self._toggle_log)
        self.log_btn.pack(side="right")
        ToolTip(self.log_btn, "Show or hide the detailed run log.  Ctrl+L")

        bar = ttk.Frame(run)
        bar.pack(side="left", fill="x", expand=True, padx=10)
        self.ready_lbl = ttk.Label(bar, text="", style="Muted.TLabel",
                                   anchor="w", wraplength=560, justify="left")
        self.ready_lbl.pack(fill="x")
        self.progress = ttk.Progressbar(bar, mode="determinate", maximum=1.0,
                                        style="Accent.Horizontal.TProgressbar")

        self.logf = ttk.LabelFrame(bottom, text="Log")
        self.log = tk.Text(self.logf, height=7, wrap="word", state="disabled",
                           font=("Consolas", 9), relief="flat", bg=FIELD,
                           highlightthickness=1, highlightbackground="#dce1e6")
        vsb = ttk.Scrollbar(self.logf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True, padx=4, pady=4)

    def _toggle_log(self):
        self.log_visible.set(not self.log_visible.get())
        self._sync_log_visibility()

    def _sync_log_visibility(self):
        if self.log_visible.get():
            self.logf.pack(fill="both", expand=True, padx=12, pady=(0, 10))
            self.log_btn.config(text="Log ▴")
        else:
            self.logf.pack_forget()
            self.log_btn.config(text="Log ▾")

    # ---------- readiness / live validation ----------
    def _raster_paths(self) -> list[str]:
        """Rasters the current inputs resolve to, cached per (mode, path).

        The folder branch globs the directory, which is why the result is cached:
        this runs on every keystroke in the path box.
        """
        p = self.raster_var.get().strip()
        mode = self.input_mode.get()
        if not p:
            return []
        if self._raster_cache and self._raster_cache[0] == mode \
                and self._raster_cache[1] == p:
            return self._raster_cache[2]
        if mode == "folder":
            found = sorted(glob.glob(os.path.join(p, "*.tif")) +
                           glob.glob(os.path.join(p, "*.tiff"))) if os.path.isdir(p) else []
        else:
            found = [p] if os.path.isfile(p) else []
        self._raster_cache = (mode, p, found)
        return found

    def _selected_bands(self) -> list[str]:
        return [r for r in self.current_sensor.roles
                if self.band_vars.get(r) and self.band_vars[r].get()]

    def _selected_stats(self) -> list[str]:
        return [s for s in ALL_STATS if self.stat_vars[s].get()]

    def _selected_indices(self) -> list[Index]:
        roles = self.current_sensor.role_set()
        return [i for i in self.all_indices
                if i.available_for(roles) and self.index_vars.get(i.name)
                and self.index_vars[i.name].get()]

    def _selected_attrs(self) -> list[str]:
        return [f for f in self._shape_fields
                if self.attr_vars.get(f) and self.attr_vars[f].get()]

    def _csv_columns(self) -> list[str]:
        """Exactly the columns run_extraction will write, in its order.

        Mirrors the ordering in core.run_extraction so the preview cannot drift
        from the file the user actually gets.
        """
        stats = self._selected_stats()
        cols = list(self._selected_attrs()) + ["source_raster"]
        for role in self._selected_bands():
            cols += [f"{role}_{s}" for s in stats]
        for idx in self._selected_indices():
            cols += [f"{idx.name}_{s}" for s in stats]
        return cols

    def _problems(self) -> list[str]:
        """Everything standing between the current state and a valid run."""
        out = []
        raster = self.raster_var.get().strip()
        if not raster:
            out.append("Choose an orthomosaic" +
                       (" folder" if self.input_mode.get() == "folder" else " file"))
        elif not os.path.exists(raster):
            out.append("That orthomosaic path does not exist")
        elif not self._raster_paths():
            out.append("No .tif files in that folder"
                       if self.input_mode.get() == "folder" else
                       "That path is a folder — switch to Folder mode, or pick a .tif")

        shp = self.shape_var.get().strip()
        if not shp:
            out.append("Choose the plot polygons, or draw them in the Plot Designer")
        elif not os.path.exists(shp):
            out.append("That shapefile does not exist")

        if not self.outdir_var.get().strip():
            out.append("Choose an output folder")

        if not self._selected_bands() and not self._selected_indices():
            out.append("Pick at least one band or index on the "
                       "'Bands & statistics' or 'Indices' tab")
        if not self._selected_stats():
            out.append("Pick at least one statistic")

        txt = self.nodata_var.get().strip()
        if txt:
            try:
                float(txt)
            except ValueError:
                out.append("NoData must be a number, or blank")
        try:
            float(self.scale_var.get().strip() or "1.0")
        except ValueError:
            out.append("Reflectance scale must be a number")
        return out

    def _refresh_ready(self):
        """Repaint every live indicator: dots, hints, counts, preview, Run."""
        if self._running:
            return
        raster, shp = self.raster_var.get().strip(), self.shape_var.get().strip()

        # Orthomosaic
        found = self._raster_paths()
        if not raster:
            self.dot_raster.set_state("todo")
            self.raster_hint.config(text="", style="Hint.TLabel")
        elif found:
            self.dot_raster.set_state("ok")
            self.raster_hint.config(
                text=f"{len(found)} .tif files found" if len(found) > 1 else "",
                style="Hint.TLabel")
        else:
            self.dot_raster.set_state("bad")
            self.raster_hint.config(text="not found", style="Bad.TLabel")
        self.raster_entry.config(
            style="Invalid.TEntry" if (raster and not found) else "TEntry")

        # Plot polygons
        if not shp:
            self.dot_shape.set_state("todo")
        elif os.path.exists(shp):
            self.dot_shape.set_state("ok")
        else:
            self.dot_shape.set_state("bad")
        self.shape_entry.config(
            style="Invalid.TEntry" if (shp and not os.path.exists(shp)) else "TEntry")

        self.dot_sensor.set_state("ok")
        self.dot_out.set_state("ok" if self.outdir_var.get().strip() else "todo")

        # Selection counters
        bands, stats = self._selected_bands(), self._selected_stats()
        indices, attrs = self._selected_indices(), self._selected_attrs()
        n_avail = sum(1 for i in self.all_indices
                      if i.available_for(self.current_sensor.role_set()))
        self.bands_count.config(text=f"{len(bands)} of {len(self.current_sensor.roles)}")
        self.stats_count.config(text=f"{len(stats)} of {len(ALL_STATS)}")
        self.indices_count.config(text=f"{len(indices)} of {n_avail} selected")
        self.attrs_count.config(text=f"{len(attrs)} of {len(self._shape_fields)}")

        # CSV column preview — what the file will actually look like.
        cols = self._csv_columns()
        shown = ", ".join(cols[:8])
        more = f"  … +{len(cols) - 8} more" if len(cols) > 8 else ""
        rows = self._shape_count * max(len(found), 1) if self._shape_count else 0
        rowtxt = f"{rows} rows × " if rows else ""
        self.cols_lbl.config(
            text=f"CSV: {rowtxt}{len(cols)} columns  ·  {shown}{more}")

        # Run button + the one thing to fix next.
        problems = self._problems()
        if problems:
            self.run_btn.config(state="disabled")
            extra = f"   (+{len(problems) - 1} more)" if len(problems) > 1 else ""
            self.ready_lbl.config(text=f"{problems[0]}{extra}", style="Muted.TLabel")
        else:
            self.run_btn.config(state="normal")
            n_r = len(found)
            src = f"{n_r} orthomosaics" if n_r > 1 else "1 orthomosaic"
            self.ready_lbl.config(
                text=f"Ready — {src}, {len(bands)} bands, {len(indices)} indices, "
                     f"{len(stats)} statistics.", style="OK.TLabel")

    # ---------- sensor / dynamic widgets ----------
    @property
    def current_sensor(self):
        return self.sensors[self.sensor_var.get()]

    def _on_mode_change(self):
        self.raster_var.set("")
        self._raster_cache = None
        self._refresh_ready()

    def _on_sensor_change(self):
        s = self.current_sensor
        order = "   ".join(f"{s.bands[r]}·{ROLE_LABELS.get(r, r)}" for r in s.roles)
        self.band_order_lbl.config(text=f"band order:   {order}")
        self._rebuild_band_boxes()
        self._refresh_indices_list()
        self._refresh_ready()

    def _rebuild_band_boxes(self):
        """Rebuild the raw-band tick list for the current camera's roles."""
        for w in self.bands_holder.winfo_children():
            w.destroy()
        new_vars: dict[str, tk.BooleanVar] = {}
        for r in self.current_sensor.roles:
            var = self.band_vars.get(r)
            if var is None:
                var = tk.BooleanVar(value=True)
                var.trace_add("write", lambda *_: self._refresh_ready())
            new_vars[r] = var
            cb = ttk.Checkbutton(self.bands_holder, text=ROLE_LABELS.get(r, r),
                                 variable=var)
            cb.pack(anchor="w", pady=1)
            ToolTip(cb, ROLE_HELP.get(r, f"{r} band."))
        self.band_vars = new_vars

    def _set_all_bands(self, value: bool):
        for v in self.band_vars.values():
            v.set(value)

    def _set_all_stats(self, value: bool):
        for v in self.stat_vars.values():
            v.set(value)

    def _set_typical_stats(self):
        for s, v in self.stat_vars.items():
            v.set(s in DEFAULT_STATS)

    def _refresh_indices_list(self):
        """Repaint the index checklist for the camera and the search box."""
        roles = self.current_sensor.role_set()
        needle = self.index_filter.get().strip().lower()
        self.idx_scroll.clear()
        body = self.idx_scroll.body
        cols = 3
        r = c = 0
        shown = 0
        for idx in self.all_indices:
            if not idx.available_for(roles):
                continue
            if needle and needle not in (
                    f"{idx.name} {idx.formula} {idx.description}").lower():
                continue
            var = self.index_vars.get(idx.name)
            if var is None:
                var = tk.BooleanVar(value=idx.name in DEFAULT_INDICES)
                var.trace_add("write", lambda *_: self._refresh_ready())
                self.index_vars[idx.name] = var

            tag = "" if idx.name in self._builtin_names else " *"
            cb = ttk.Checkbutton(body, text=idx.name + tag, variable=var, width=16)
            cb.grid(row=r, column=c, sticky="w", padx=6, pady=1)
            ToolTip(cb, lambda i=idx: f"{i.name}\nFormula: {i.formula}\n\n{i.description}")
            cb.bind("<Double-Button-1>", lambda e, i=idx: self._index_dialog(i))
            shown += 1
            c += 1
            if c >= cols:
                c, r = 0, r + 1

        if shown == 0:
            msg = ("No index matches that search." if needle else
                   "No indices are computable with this camera's bands.")
            ttk.Label(body, text=msg, style="Bad.TLabel").grid(
                row=0, column=0, padx=8, pady=8, sticky="w")

    def _set_all_indices(self, value: bool):
        roles = self.current_sensor.role_set()
        for idx in self.all_indices:
            if idx.available_for(roles) and idx.name in self.index_vars:
                self.index_vars[idx.name].set(value)

    def _set_all_attrs(self, value: bool):
        for v in self.attr_vars.values():
            v.set(value)

    def _populate_attrs(self, fields: list[str], keep: set[str] | None = None):
        """Rebuild the attribute list. *keep* overrides the default selection."""
        self._shape_fields = fields
        self.attrs_scroll.clear()
        self.attr_vars = {}
        has_pref = any(f in fields for f in PREFERRED_ATTRS)
        for f in fields:
            if keep is not None:
                default = f in keep
            else:
                default = (f in PREFERRED_ATTRS) if has_pref else True
            var = tk.BooleanVar(value=default)
            var.trace_add("write", lambda *_: self._refresh_ready())
            self.attr_vars[f] = var
            ttk.Checkbutton(self.attrs_scroll.body, text=f,
                            variable=var).pack(anchor="w", padx=8, pady=1)
        if not fields:
            ttk.Label(self.attrs_scroll.body,
                      text="(this shapefile has no attribute fields)",
                      style="Warn.TLabel").pack(anchor="w", padx=8)

    def _reset_options(self):
        self.nodata_var.set("-10000")
        self.scale_var.set("1.0")

    # ---------- browse handlers ----------
    def _browse_raster(self):
        if self.input_mode.get() == "file":
            p = filedialog.askopenfilename(
                title="Select orthomosaic GeoTIFF",
                filetypes=[("GeoTIFF", "*.tif *.tiff"), ("All files", "*.*")])
        else:
            p = filedialog.askdirectory(title="Select folder containing .tif orthomosaics")
        if p:
            self._use_raster(p, autodetect=True)

    def _use_raster(self, p: str, autodetect: bool = False):
        """Adopt *p* as the orthomosaic and fill in what follows from it.

        Choosing imagery is the one action that can sensibly imply the rest, so
        this is where the output folder, the file name and the camera get their
        defaults — leaving the user with nothing else to type in the common case.
        """
        self._raster_cache = None
        if os.path.isdir(p):
            self.input_mode.set("folder")
        self.raster_var.set(p)
        base = p if os.path.isdir(p) else os.path.dirname(p)
        if not self.outdir_var.get().strip():
            self.outdir_var.set(base)
        if not self._outname_touched:
            stem = os.path.basename(os.path.normpath(p))
            if not os.path.isdir(p):
                stem = os.path.splitext(stem)[0]
            self.outname_var.set(f"{stem}_stats.csv" if stem else "results.csv")
        self.recent_rasters = remember_recent(self.recent_rasters, p)
        self._refresh_recent_menu()
        if autodetect:
            self._maybe_autodetect_sensor(p)
        self._refresh_ready()

    def _maybe_autodetect_sensor(self, path):
        """Guess the camera from the band count of the (first) raster.

        Reported in the band-order line rather than only the log, because the
        wrong camera silently produces wrong indices and is the single most
        expensive mistake this app allows.
        """
        try:
            import rasterio
            tifs = self._raster_paths()
            if not tifs:
                return
            with rasterio.open(tifs[0]) as ds:
                count = ds.count
            for name, s in self.sensors.items():
                if s.band_count == count:
                    self.sensor_var.set(name)
                    self._on_sensor_change()
                    self._log(f"Auto-selected camera '{name}' ({count} bands).")
                    self.band_order_lbl.config(
                        text=f"{self.band_order_lbl.cget('text')}      "
                             f"← matched {count} raster bands")
                    return
            self._log(f"No camera matches {count} bands — check the sensor dropdown.")
        except Exception:
            pass

    def _browse_shape(self):
        p = filedialog.askopenfilename(title="Select plot polygon shapefile",
                                       filetypes=[("Shapefile", "*.shp"),
                                                  ("All files", "*.*")])
        if p:
            self._use_shapefile(p)

    def _use_shapefile(self, p: str, keep_attrs: set[str] | None = None):
        """Set the shapefile path and load its fields + polygon count."""
        self.shape_var.set(p)
        try:
            import fiona
            with fiona.open(p, "r") as src:
                fields = list(src.schema["properties"].keys())
                self._shape_count = len(src)
            self._populate_attrs(fields, keep=keep_attrs)
            self.shape_hint.config(
                text=f"{self._shape_count} plots  ·  "
                     f"{', '.join(fields) if fields else 'no attributes'}",
                style="Hint.TLabel")
            self._log(f"Shapefile: {self._shape_count} plots, fields: "
                      f"{', '.join(fields) if fields else '(none)'}")
            self.recent_shapes = remember_recent(self.recent_shapes, p)
            self._refresh_recent_menu()
        except Exception as exc:
            self._shape_count = 0
            self.shape_hint.config(text="could not be read", style="Bad.TLabel")
            messagebox.showerror(APP_NAME, f"Could not read shapefile attributes:\n{exc}")
        self._refresh_ready()

    def _open_designer(self):
        """Open the Plot Designer on the selected orthomosaic (or one the user picks).

        A shapefile already chosen here is loaded into the designer, so the button
        doubles as "edit the plots I have" — e.g. to give QGIS-drawn plots their
        variant and replication numbers.
        """
        rasters = self._raster_paths()
        ortho = rasters[0] if rasters else filedialog.askopenfilename(
            title="Select orthomosaic to design plots on",
            filetypes=[("GeoTIFF", "*.tif *.tiff"), ("All files", "*.*")])
        if not ortho:
            return
        if not os.path.exists(ortho):
            messagebox.showerror(APP_NAME, f"Orthomosaic not found:\n{ortho}")
            return
        existing = self.shape_var.get().strip()
        from .designer import PlotDesigner
        PlotDesigner(self, ortho, sensor=self.current_sensor,
                     on_export=self._use_shapefile,
                     shapefile=existing if os.path.exists(existing) else None)

    def _browse_outdir(self):
        p = filedialog.askdirectory(title="Select output folder")
        if p:
            self.outdir_var.set(p)

    def _clear_all(self):
        for v in (self.raster_var, self.shape_var, self.outdir_var):
            v.set("")
        self.outname_var.set("results.csv")
        self._outname_touched = False
        self._shape_count = 0
        self._raster_cache = None
        self._populate_attrs([])
        self.shape_hint.config(text="No shapefile chosen yet.", style="Hint.TLabel")
        self._refresh_ready()

    def _open_path(self, path: str):
        try:
            os.startfile(path)
        except Exception:
            webbrowser.open(path)

    # ---------- help ----------
    def _show_guide(self):
        dlg = tk.Toplevel(self)
        dlg.title(f"{APP_NAME} — Quick guide")
        dlg.transient(self)
        dlg.geometry("740x640")
        frm = ttk.Frame(dlg, padding=10)
        frm.pack(fill="both", expand=True)
        txt = tk.Text(frm, wrap="word", relief="flat", bg=FIELD, fg=INK,
                      font=("Consolas", 9), padx=10, pady=8)
        vsb = ttk.Scrollbar(frm, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", GUIDE_TEXT)
        txt.config(state="disabled")
        ttk.Button(dlg, text="Close", command=dlg.destroy).pack(pady=(0, 10))

    def _show_about(self):
        messagebox.showinfo(
            APP_NAME,
            f"{APP_NAME} {__version__}\n\n"
            "Per-plot reflectance statistics and vegetation indices from drone "
            "orthomosaics.\n\n"
            f"Cameras, indices and your saved settings live in:\n{app_dir()}")

    # ---------- add / edit camera (sensor) ----------
    def _sensor_dialog(self, template: Sensor | None):
        """Add or edit a camera: name + a band role for each raster band.

        Opens pre-filled from the currently selected sensor as a starting
        template. Saving under the same name overrides that sensor; saving under
        a new name creates a new one. Roles map to the band tokens formulas
        understand, so a new camera's bands are immediately usable in indices.
        """
        template = template or self.current_sensor
        dlg = tk.Toplevel(self)
        dlg.title("Add / edit camera")
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(False, False)

        ROLE_NONE = "(unused)"
        role_labels = [ROLE_NONE] + [ROLE_LABELS.get(r, r) for r in KNOWN_ROLES]
        label_to_role = {ROLE_LABELS.get(r, r): r for r in KNOWN_ROLES}
        role_to_label = {r: ROLE_LABELS.get(r, r) for r in KNOWN_ROLES}
        existing_role_by_band = {num: role for role, num in template.bands.items()}

        frm = ttk.Frame(dlg, padding=14)
        frm.pack(fill="both", expand=True)

        name_v = tk.StringVar(value=template.name)
        nodata_v = tk.StringVar(value=f"{template.default_nodata:g}")
        count_v = tk.StringVar(value=str(template.band_count))

        ttk.Label(frm, text="Camera name:").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(frm, textvariable=name_v, width=28).grid(
            row=0, column=1, columnspan=2, sticky="ew", pady=3)
        ttk.Label(frm, text="Number of bands:").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Spinbox(frm, from_=1, to=12, textvariable=count_v, width=6).grid(
            row=1, column=1, sticky="w", pady=3)

        ttk.Label(frm, text="Assign a role to each band (top = band 1, the GeoTIFF's "
                            "band order):", style="Muted.TLabel", wraplength=330,
                  justify="left").grid(row=2, column=0, columnspan=3, sticky="w",
                                       pady=(8, 2))
        rows_holder = ttk.Frame(frm)
        rows_holder.grid(row=3, column=0, columnspan=3, sticky="ew")

        row_vars: list[tk.StringVar] = []

        def rebuild_rows(*_):
            try:
                n = max(1, min(12, int(float(count_v.get()))))
            except (ValueError, tk.TclError):
                return
            prev = [v.get() for v in row_vars]
            for w in rows_holder.winfo_children():
                w.destroy()
            row_vars.clear()
            for i in range(n):
                band_no = i + 1
                if i < len(prev):
                    init = prev[i]
                elif band_no in existing_role_by_band:
                    init = role_to_label[existing_role_by_band[band_no]]
                else:
                    init = role_labels[band_no] if band_no < len(role_labels) else ROLE_NONE
                ttk.Label(rows_holder, text=f"Band {band_no}").grid(
                    row=i, column=0, sticky="w", pady=2, padx=(0, 8))
                var = tk.StringVar(value=init)
                ttk.Combobox(rows_holder, textvariable=var, values=role_labels,
                             state="readonly", width=14).grid(row=i, column=1,
                                                              sticky="w", pady=2)
                row_vars.append(var)

        rebuild_rows()
        count_v.trace_add("write", rebuild_rows)

        ttk.Label(frm, text="NoData value:").grid(row=4, column=0, sticky="w", pady=(10, 3))
        ttk.Entry(frm, textvariable=nodata_v, width=12).grid(row=4, column=1, sticky="w",
                                                             pady=(10, 3))
        status = ttk.Label(frm, text="", style="Bad.TLabel", wraplength=330,
                           justify="left")
        status.grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))

        def save():
            name = name_v.get().strip()
            if not name:
                status.config(text="Camera name is required.")
                return
            bands, seen = {}, set()
            for i, var in enumerate(row_vars):
                role = label_to_role.get(var.get())
                if role is None:
                    continue
                if role in seen:
                    status.config(text=f"'{var.get()}' is assigned to more than one band.")
                    return
                seen.add(role)
                bands[role] = i + 1
            if not bands:
                status.config(text="Assign a role to at least one band.")
                return
            try:
                nodata = float(nodata_v.get().strip() or "-10000")
            except ValueError:
                status.config(text="NoData must be a number.")
                return
            self.sensors[name] = Sensor(name=name, bands=bands, default_nodata=nodata)
            try:
                save_sensors(self.sensors)
            except OSError as e:
                messagebox.showwarning(APP_NAME,
                                       f"Camera saved in memory but not to disk:\n{e}",
                                       parent=dlg)
            self.sensor_combo["values"] = list(self.sensors)
            self.sensor_var.set(name)
            self._on_sensor_change()
            self._log(f"Saved camera '{name}' ({len(bands)} bands: "
                      f"{', '.join(f'{bands[r]}={r}' for r in sorted(bands, key=bands.get))}).")
            dlg.destroy()

        def delete():
            name = name_v.get().strip()
            if is_builtin(name):
                messagebox.showinfo(APP_NAME, "Built-in cameras can't be deleted "
                                              "(you can edit their band order instead).",
                                    parent=dlg)
                return
            if name not in self.sensors:
                dlg.destroy()
                return
            self.sensors.pop(name, None)
            try:
                save_sensors(self.sensors)
            except OSError:
                pass
            self.sensor_combo["values"] = list(self.sensors)
            self.sensor_var.set(next(iter(self.sensors)))
            self._on_sensor_change()
            dlg.destroy()

        btns = ttk.Frame(frm)
        btns.grid(row=6, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Save", style="Accent.TButton",
                   command=save).pack(side="right")
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="right", padx=6)
        if not is_builtin(template.name):
            ttk.Button(btns, text="Delete", command=delete).pack(side="left")
        frm.columnconfigure(1, weight=1)

    # ---------- add / edit index ----------
    def _index_dialog(self, existing: Index | None):
        dlg = tk.Toplevel(self)
        dlg.title("Add index" if existing is None else f"Edit index — {existing.name}")
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(False, False)

        name_v = tk.StringVar(value=existing.name if existing else "")
        cat_v = tk.StringVar(value=existing.category if existing else "Custom")
        formula_v = tk.StringVar(value=existing.formula if existing else "")
        desc_v = tk.StringVar(value=existing.description if existing else "")

        frm = ttk.Frame(dlg, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Name:").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(frm, textvariable=name_v, width=36).grid(row=0, column=1,
                                                           sticky="ew", pady=3)
        ttk.Label(frm, text="Category:").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(frm, textvariable=cat_v, width=36).grid(row=1, column=1,
                                                          sticky="ew", pady=3)
        ttk.Label(frm, text="Formula:").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(frm, textvariable=formula_v, width=36).grid(row=2, column=1,
                                                              sticky="ew", pady=3)
        ttk.Label(frm, text="Description:").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Entry(frm, textvariable=desc_v, width=36).grid(row=3, column=1,
                                                           sticky="ew", pady=3)

        ttk.Label(frm, text="Bands you can use: " + ", ".join(KNOWN_ROLES),
                  style="Muted.TLabel", wraplength=360, justify="left").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(frm, text="Functions: sqrt, abs, exp, log, log10, min, max, clip",
                  style="Muted.TLabel").grid(row=5, column=0, columnspan=2, sticky="w")
        status = ttk.Label(frm, text="", style="Bad.TLabel", wraplength=360,
                           justify="left")
        status.grid(row=6, column=0, columnspan=2, sticky="w", pady=(6, 0))

        def validate_live(*_):
            try:
                deps = validate_formula(formula_v.get())
                avail = deps.issubset(self.current_sensor.role_set())
                msg = "✓ Valid. Uses: " + ", ".join(sorted(deps))
                if not avail:
                    msg += "  (note: current camera lacks some of these bands)"
                status.config(text=msg, foreground=OK)
            except FormulaError as e:
                status.config(text=str(e), foreground=BAD)
        formula_v.trace_add("write", validate_live)
        validate_live()

        btns = ttk.Frame(frm)
        btns.grid(row=7, column=0, columnspan=2, sticky="e", pady=(12, 0))

        def save():
            idx = Index(name_v.get().strip(), formula_v.get().strip(),
                        desc_v.get().strip(), cat_v.get().strip() or "Custom")
            if not idx.name:
                status.config(text="Name is required.", foreground=BAD)
                return
            try:
                self.all_indices = add_or_update_index(self.all_indices, idx)
            except FormulaError as e:
                status.config(text=str(e), foreground=BAD)
                return
            # New custom index defaults to selected.
            if idx.name not in self.index_vars:
                var = tk.BooleanVar(value=True)
                var.trace_add("write", lambda *_: self._refresh_ready())
                self.index_vars[idx.name] = var
            try:
                save_indices(self.all_indices)
            except OSError as e:
                messagebox.showwarning(APP_NAME,
                                       f"Index saved in memory but not to disk:\n{e}")
            self._refresh_indices_list()
            self._refresh_ready()
            dlg.destroy()

        def delete():
            if existing is None:
                return
            if existing.name in self._builtin_names:
                messagebox.showinfo(APP_NAME, "Built-in indices cannot be deleted "
                                              "(you can edit their formula instead).")
                return
            self.all_indices = [i for i in self.all_indices if i.name != existing.name]
            self.index_vars.pop(existing.name, None)
            try:
                save_indices(self.all_indices)
            except OSError:
                pass
            self._refresh_indices_list()
            self._refresh_ready()
            dlg.destroy()

        ttk.Button(btns, text="Save", style="Accent.TButton",
                   command=save).pack(side="right")
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="right", padx=6)
        if existing is not None and existing.name not in self._builtin_names:
            ttk.Button(btns, text="Delete", command=delete).pack(side="left")
        frm.columnconfigure(1, weight=1)

    # ---------- run ----------
    def _run_clicked(self):
        if self._running or self.run_btn.instate(["disabled"]):
            return
        try:
            cfg = self._build_config()
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        self._running = True
        self.run_btn.config(state="disabled", text="Running…")
        self.progress.pack(fill="x", pady=(4, 0))
        self.progress["value"] = 0
        self.ready_lbl.config(text="Starting…", style="Muted.TLabel")
        self._log("-- Starting extraction --")
        self._q: queue.Queue = queue.Queue()
        t = threading.Thread(target=self._worker, args=(cfg, self._q), daemon=True)
        t.start()
        self.after(100, self._poll_queue)

    def _build_config(self) -> ExtractionConfig:
        rasters = self._raster_paths()
        if not rasters:
            raise ValueError("Select an orthomosaic file or a folder containing .tif files.")
        shp = self.shape_var.get().strip()
        if not shp or not os.path.exists(shp):
            raise ValueError("Select a valid shapefile (.shp).")
        outdir = self.outdir_var.get().strip()
        if not outdir:
            raise ValueError("Select an output folder.")
        name = self.outname_var.get().strip() or "results.csv"
        if not name.lower().endswith(".csv"):
            name += ".csv"

        bands, stats = self._selected_bands(), self._selected_stats()
        indices, attrs = self._selected_indices(), self._selected_attrs()
        if not bands and not indices:
            raise ValueError("Select at least one band or one index to output.")
        if not stats:
            raise ValueError("Select at least one statistic.")

        nodata_txt = self.nodata_var.get().strip()
        try:
            nodata = float(nodata_txt) if nodata_txt else None
        except ValueError:
            raise ValueError(f"NoData must be a number or blank, got '{nodata_txt}'.")
        try:
            scale = float(self.scale_var.get().strip() or "1.0")
        except ValueError:
            raise ValueError("Reflectance scale must be a number.")

        return ExtractionConfig(
            raster_paths=rasters, shapefile=shp,
            output_csv=os.path.join(outdir, name), sensor=self.current_sensor,
            bands=bands, indices=indices, stats=stats, attributes=attrs,
            nodata=nodata, scale=scale,
        )

    def _worker(self, cfg, q):
        try:
            out = run_extraction(cfg, progress=lambda f, m: q.put(("progress", f, m)))
            q.put(("done", out))
        except Exception as exc:  # surface any failure to the UI
            q.put(("error", str(exc)))

    def _finish_run(self):
        self._running = False
        self.run_btn.config(text="▶  Run extraction")
        self.progress.pack_forget()

    def _poll_queue(self):
        try:
            while True:
                item = self._q.get_nowait()
                kind = item[0]
                if kind == "progress":
                    _, frac, msg = item
                    self.progress["value"] = frac
                    self.ready_lbl.config(text=msg, style="Muted.TLabel")
                    self._log(msg)
                elif kind == "done":
                    self.progress["value"] = 1.0
                    out = item[1]
                    self._log(f"Finished: {out}")
                    self._finish_run()
                    self.ready_lbl.config(
                        text=f"Done — wrote {os.path.basename(out)}", style="OK.TLabel")
                    self._save_settings()
                    if self.open_when_done.get():
                        self._open_path(os.path.dirname(out))
                    self._refresh_ready_soon()
                    return
                elif kind == "error":
                    self._log(f"ERROR: {item[1]}")
                    self._finish_run()
                    self.ready_lbl.config(text="Extraction failed — see the log.",
                                          style="Bad.TLabel")
                    self.log_visible.set(True)
                    self._sync_log_visibility()
                    messagebox.showerror(APP_NAME, f"Extraction failed:\n\n{item[1]}")
                    self._refresh_ready_soon()
                    return
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _refresh_ready_soon(self):
        """Restore the readiness line a moment after the run result is shown."""
        self.after(4000, self._refresh_ready)

    def _log(self, msg: str):
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    # ---------- settings ----------
    def _restore_settings(self):
        """Reapply the last session, skipping anything no longer valid."""
        s = self.settings
        if s.get("sensor") in self.sensors:
            self.sensor_var.set(s["sensor"])
        if s.get("input_mode") in ("file", "folder"):
            self.input_mode.set(s["input_mode"])
        for key, var in (("outname", self.outname_var), ("nodata", self.nodata_var),
                         ("scale", self.scale_var)):
            if isinstance(s.get(key), str) and s[key]:
                var.set(s[key])
        self._outname_touched = bool(s.get("outname_touched"))
        self.open_when_done.set(bool(s.get("open_when_done", True)))
        self.log_visible.set(bool(s.get("log_visible", False)))
        self._sync_log_visibility()

        # Paths only if they still exist, so a moved drive never shows an error
        # on a window the user has not touched.
        raster = s.get("raster")
        if raster and os.path.exists(raster):
            self._raster_cache = None
            self.raster_var.set(raster)
        outdir = s.get("outdir")
        if outdir and os.path.isdir(outdir):
            self.outdir_var.set(outdir)

        # Band / index / stat ticks are restored after the camera is applied,
        # because that is what creates their variables.
        self._on_sensor_change()
        saved_bands = s.get("bands")
        if isinstance(saved_bands, list):
            for role, var in self.band_vars.items():
                var.set(role in saved_bands)
        saved_stats = s.get("stats")
        if isinstance(saved_stats, list) and saved_stats:
            for st, var in self.stat_vars.items():
                var.set(st in saved_stats)
        saved_idx = s.get("indices")
        if isinstance(saved_idx, list):
            for name, var in self.index_vars.items():
                var.set(name in saved_idx)

        shp = s.get("shapefile")
        if shp and os.path.exists(shp):
            keep = set(s["attrs"]) if isinstance(s.get("attrs"), list) else None
            self._use_shapefile(shp, keep_attrs=keep)

    def _collect_settings(self) -> dict:
        return {
            "geometry": self.winfo_geometry(),
            "input_mode": self.input_mode.get(),
            "raster": self.raster_var.get().strip(),
            "shapefile": self.shape_var.get().strip(),
            "outdir": self.outdir_var.get().strip(),
            "outname": self.outname_var.get().strip(),
            "outname_touched": self._outname_touched,
            "sensor": self.sensor_var.get(),
            "nodata": self.nodata_var.get(),
            "scale": self.scale_var.get(),
            "bands": self._selected_bands(),
            "stats": self._selected_stats(),
            "indices": [i.name for i in self._selected_indices()],
            "attrs": self._selected_attrs(),
            "open_when_done": bool(self.open_when_done.get()),
            "log_visible": bool(self.log_visible.get()),
            "recent_rasters": self.recent_rasters,
            "recent_shapefiles": self.recent_shapes,
        }

    def _save_settings(self):
        save_settings(self._collect_settings())

    def _on_close(self):
        self._save_settings()
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
