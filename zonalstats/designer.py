"""
Plot Designer window.

A Tkinter ``Toplevel`` that lets the user lay out trial plots directly on the
orthomosaic instead of digitising them in QGIS:

  * load a decimated RGB preview of the ortho (``preview.load_preview``);
  * pan (drag empty space) and zoom (mouse wheel) the view, with a live scale
    bar and cursor-coordinate readout for spatial grounding;
  * place a regular array of plots with a grid tool — plot size, alley gaps,
    rows x cols, rotation (``grid.build_plots``);
  * click "Set anchor" then click the map to position the grid's top-left plot;
  * select a plot (click), drag to nudge it, fine-nudge with the arrow keys, or
    Delete to remove a stray one; Ctrl+Z undoes the last change;
  * give every plot its **ID** — the join key back to the user's own records —
    on the "Plot IDs" tab: renumber the whole grid in a chosen traversal
    (``grid.renumber``), paste a column of IDs from Excel, or click plots in an
    arbitrary order. Clashes are reported live (``grid.id_report``);
  * assign each plot a **variant** (genotype / treatment) and **replication** in
    the plot table beside the map — by pattern (``grid.assign_pattern``), by
    typing into the table, or by painting values onto plots with the mouse;
  * export to a shapefile in the raster's CRS (``grid.write_shapefile``) and
    hand the path back to the main window so it feeds straight into extraction.

Coordinate layers
-----------------
map  <->  preview-pixel        (via Preview.map_to_pixel / pixel_to_map)
preview-pixel  <->  canvas     (canvas = pixel * zoom + pan)

Plots are stored as map-coordinate corners (the export-truth), so zoom/pan never
changes them and a saved layout is exact regardless of preview decimation.

Rendering is split in two so interaction stays smooth: the (expensive) decimated
image is only re-rendered on zoom/pan/resize, while plot outlines and overlays
are redrawn cheaply on hover/select/edit.
"""

from __future__ import annotations

import copy
import math
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from . import APP_NAME
from .grid import (GridSpec, assign_pattern, build_plots, id_report,
                   read_shapefile, renumber, write_shapefile)
from .tooltip import ScrollableFrame, ToolTip
from .ui import ACCENT, BAD, CANVAS_BG, MUTED, OK, apply_theme

PLOT_OUTLINE = "#ffd400"
PLOT_HOVER = "#fff59d"
PLOT_SELECTED = "#ff5252"
PLOT_DUPLICATE = "#ff1744"       # a plot whose ID is shared with another
LABEL_COLOUR = "#fff8e1"

# Numbering orders, in the wording the user sees. Maps the label shown in the
# dropdown to the traversal key grid.renumber understands.
ORDER_LABELS = {
    "Rows, left to right": "rowwise",
    "Rows, snaking": "serpentine",
    "Columns, top to bottom": "colwise",
    "Columns, snaking": "col_serpentine",
}
UNDO_LIMIT = 60
NUDGE_STEP = 0.1                 # map units per arrow-key press (Shift = x10)

# Qualitative palette for "colour by variant" — distinct hues so a randomisation
# can be eyeballed for mistakes. Cycled when there are more variants than colours.
VARIANT_COLOURS = [
    "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#42d4f4",
    "#f032e6", "#bfef45", "#fabed4", "#469990", "#dcbeff", "#9a6324",
    "#fffac8", "#800000", "#aaffc3", "#808000", "#ffd8b1", "#000075",
    "#a9a9a9", "#ffe119",
]


class PlotDesigner(tk.Toplevel):
    def __init__(self, master, ortho_path: str, sensor=None, on_export=None,
                 shapefile: str | None = None):
        super().__init__(master)
        apply_theme(self)
        self.title(f"{APP_NAME} — Plot Designer")
        # Map + controls + plot table need width; clamp to the screen so the
        # window never opens larger than the display it lands on.
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1460, int(sw * 0.94))}x{min(900, int(sh * 0.90))}")
        self.minsize(min(1040, sw - 40), min(640, sh - 80))

        self.ortho_path = ortho_path
        self.sensor = sensor
        self.on_export = on_export

        self.preview = None          # preview.Preview
        self.zoom = 1.0              # canvas px per preview px
        self.pan_x = 0.0             # canvas x where preview pixel 0 is drawn
        self.pan_y = 0.0
        self._photo = None           # keep a ref so Tk doesn't GC the image
        self._fitted = False
        self.plots: list = []        # current editable plots (map-coord corners)
        self._edited = False         # any manual edit since last grid build?
        self._undo: list = []        # snapshots of self.plots for Ctrl+Z
        self._pending_import = shapefile   # loaded once the preview is ready
        self._imported_path = None   # source of the current plots, if imported

        # interaction state
        self._anchor_mode = False
        self._selected = None        # selected plot index, or None
        self._hover = None           # hovered plot index, or None
        self._drag_plot = None       # index being dragged, or None
        self._drag_last = None       # last (cx, cy) during a drag
        self._moved = False          # did the current drag actually move?
        self._panning = False
        self._layout_key = None      # (n_cols, n_rows) the current plots were built at

        # plot-table / assignment state
        self.tree = None             # ttk.Treeview once built
        self._editing = None         # (item, field, Entry) while a cell is open
        self._syncing = False        # guard: table <-> map selection echo
        self._last_col = "#4"        # last clicked table column (paste target)
        self._paint_mode = False
        self._paint_stroke = None    # plot indices stamped in the current stroke

        # plot-ID state
        self._number_mode = False    # click-a-plot-to-give-it-the-next-ID active
        self._dup_ids: set = set()   # IDs currently shared by more than one plot

        self._brightness = 1.0       # preview display gain, 1.0 = as loaded

        self._build_ui()
        self.after(50, self._load_preview)

    # ---------- UI ----------
    def _section(self, parent, title):
        ttk.Label(parent, text=title, style="Header.TLabel").pack(
            anchor="w", pady=(12, 2))
        box = ttk.Frame(parent)
        box.pack(fill="x")
        return box

    def _build_ui(self):
        # The control rail scrolls: on a 768-tall laptop the full stack of
        # sections is taller than the window, and silently clipping "Fit view"
        # or the status line off the bottom is worse than a scrollbar.
        rail = ScrollableFrame(self, height=560)
        rail.configure(width=280)
        rail.pack(side="left", fill="y")
        rail.pack_propagate(False)
        controls = ttk.Frame(rail.body, padding=(12, 8))
        controls.pack(fill="both", expand=True)

        # right-hand stage: map | plot table, over a status strip
        stage = ttk.Frame(self)
        stage.pack(side="right", fill="both", expand=True)

        strip = ttk.Frame(stage, padding=(8, 3))
        strip.pack(side="bottom", fill="x")
        self.lbl_coord = ttk.Label(strip, text="—", style="Muted.TLabel", width=26)
        self.lbl_coord.pack(side="left")
        self.lbl_scale = ttk.Label(strip, text="", style="Muted.TLabel")
        self.lbl_scale.pack(side="left", padx=16)
        self.show_table = tk.BooleanVar(value=True)
        ttk.Checkbutton(strip, text="Plot table", variable=self.show_table,
                        command=self._toggle_table).pack(side="right", padx=(14, 0))
        self.lbl_count = ttk.Label(strip, text="0 plots", style="Muted.TLabel")
        self.lbl_count.pack(side="right")

        self.panes = ttk.PanedWindow(stage, orient="horizontal")
        self.panes.pack(side="top", fill="both", expand=True)
        map_holder = ttk.Frame(self.panes)
        self.canvas = tk.Canvas(map_holder, bg=CANVAS_BG, highlightthickness=0,
                                cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.panes.add(map_holder, weight=4)
        self.table_holder = ttk.Frame(self.panes, width=340)
        self._build_table_panel(self.table_holder)
        self.panes.add(self.table_holder, weight=1)

        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_hover)
        self.canvas.bind("<Leave>", lambda e: self._clear_readout())
        self.canvas.bind("<MouseWheel>", self._on_wheel)             # Windows / macOS
        self.canvas.bind("<Button-4>", lambda e: self._wheel(e, 1))  # Linux up
        self.canvas.bind("<Button-5>", lambda e: self._wheel(e, -1)) # Linux down
        for key in ("Left", "Right", "Up", "Down"):
            self.canvas.bind(f"<{key}>", self._on_arrow)
            self.canvas.bind(f"<Shift-{key}>", self._on_arrow)
        self.bind("<Delete>", self._on_delete)
        self.bind("<Control-z>", lambda e: self._undo_last())

        # --- 1. Plot size ---
        box = self._section(controls, "1 · Plot size")
        self.vars = {
            "plot_w": tk.StringVar(value="2.0"),
            "plot_h": tk.StringVar(value="5.0"),
            "n_cols": tk.StringVar(value="6"),
            "n_rows": tk.StringVar(value="10"),
            "gap_x": tk.StringVar(value="0.3"),
            "gap_y": tk.StringVar(value="0.5"),
            "angle": tk.StringVar(value="0.0"),
        }
        self._field(box, 0, "Plot width (m)", "plot_w")
        self._field(box, 1, "Plot height (m)", "plot_h")

        # --- 2. Layout ---
        box = self._section(controls, "2 · Layout")
        self._field(box, 0, "Columns", "n_cols")
        self._field(box, 1, "Rows", "n_rows")
        self._field(box, 2, "Gap between cols (m)", "gap_x")
        self._field(box, 3, "Gap between rows (m)", "gap_y")
        self._field(box, 4, "Rotation (°)", "angle")
        # Numbering lives entirely on the 'Plot IDs' tab — a fresh grid is always
        # numbered plainly along rows, then renumbered however the trial needs.

        ttk.Button(controls, text="Build / reset grid", style="Accent.TButton",
                   command=lambda: self._build_grid(recenter=True)).pack(
            fill="x", pady=(12, 2))
        ttk.Label(controls, text="Tip: press Enter in any field to re-apply.",
                  style="Muted.TLabel", wraplength=224, justify="left").pack(anchor="w")

        # --- 3. Place & adjust ---
        self._section(controls, "3 · Place & adjust")
        self.anchor_btn = ttk.Button(controls, text="Set anchor (click map)",
                                     command=self._toggle_anchor)
        self.anchor_btn.pack(fill="x", pady=2)
        rot = ttk.Frame(controls)
        rot.pack(fill="x", pady=2)
        ttk.Label(rot, text="Rotate:").pack(side="left")
        for label, delta in (("−1°", -1), ("−0.2°", -0.2), ("+0.2°", 0.2), ("+1°", 1)):
            ttk.Button(rot, text=label, width=5, style="Compact.TButton",
                       command=lambda d=delta: self._bump_angle(d)).pack(
                side="left", padx=1, fill="x", expand=True)
        ttk.Label(controls, style="Muted.TLabel", justify="left", wraplength=234,
                  text="Wheel = zoom · drag empty = pan.\n"
                       "Click a plot to select; drag to move; arrow keys nudge "
                       "(Shift = ×10); Delete removes it; Ctrl+Z undoes.").pack(
            anchor="w", pady=(4, 0))

        # --- image brightness (display only, never touches the data) ---
        box = self._section(controls, "4 · Image")
        head = ttk.Frame(box)
        head.pack(fill="x")
        ttk.Label(head, text="Brightness").pack(side="left")
        self.lbl_bright = ttk.Label(head, text="1.0×", style="Muted.TLabel")
        self.lbl_bright.pack(side="left", padx=(6, 0))
        ttk.Button(head, text="Reset", width=6, style="Compact.TButton",
                   command=self._reset_brightness).pack(side="right")
        self.bright_scale = ttk.Scale(box, from_=0.2, to=3.0, orient="horizontal",
                                      command=self._on_brightness)
        self.bright_scale.set(1.0)
        self.bright_scale.pack(fill="x", pady=(2, 0))
        ttk.Label(box, style="Muted.TLabel", justify="left", wraplength=234,
                  text="Lightens or darkens the preview only — the extracted "
                       "pixel values are unaffected.").pack(anchor="w")

        # --- import / export / view ---
        self._section(controls, "5 · Import / export")
        ttk.Button(controls, text="Import shapefile…",
                   command=self._import_shapefile).pack(fill="x", pady=2)
        ttk.Button(controls, text="Export shapefile…", style="Accent.TButton",
                   command=self._export).pack(fill="x", pady=2)
        view = ttk.Frame(controls)
        view.pack(fill="x", pady=2)
        ttk.Button(view, text="Fit view", command=self._fit).pack(side="left",
                                                                   fill="x", expand=True)
        ttk.Button(view, text="Undo", command=self._undo_last).pack(
            side="left", fill="x", expand=True, padx=(4, 0))

        self.status = ttk.Label(controls, text="Loading orthomosaic…",
                                style="Muted.TLabel", wraplength=224, justify="left")
        self.status.pack(anchor="w", pady=(12, 0), side="bottom")

    def _field(self, parent, row, label, key):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        e = ttk.Entry(parent, textvariable=self.vars[key], width=8, justify="right")
        e.grid(row=row, column=1, sticky="e", padx=(6, 0), pady=2)
        e.bind("<Return>", lambda ev: self._rebuild_in_place())
        parent.columnconfigure(0, weight=1)

    # ---------- preview brightness (display only) ----------
    def _on_brightness(self, value):
        """Brightness slider moved: re-render the visible crop, nothing else."""
        self._brightness = float(value)
        self.lbl_bright.config(text=f"{self._brightness:.1f}×")
        self._render_image()
        self._render_overlay()

    def _reset_brightness(self):
        self.bright_scale.set(1.0)

    # ---------- plot table (variant / replication) ----------
    def _build_table_panel(self, parent):
        self.pat_axis = tk.StringVar(value="row")
        self.pat_block = tk.StringVar(value="1")
        self.pat_nvar = tk.StringVar(value="10")
        self.pat_set_rep = tk.BooleanVar(value=True)
        self.pat_set_var = tk.BooleanVar(value=True)
        self.val_variant = tk.StringVar()
        self.val_rep = tk.StringVar()
        self.label_by = tk.StringVar(value="num")
        self.colour_variant = tk.BooleanVar(value=False)

        # plot-ID vars
        self.id_order = tk.StringVar(value=next(iter(ORDER_LABELS)))
        self.id_start = tk.StringVar(value="1")
        self.id_step = tk.StringVar(value="1")
        self.id_next = tk.StringVar(value="1")

        pad = ttk.Frame(parent, padding=(10, 8))
        pad.pack(fill="both", expand=True)
        ttk.Label(pad, text="Plot table", style="Header.TLabel").pack(anchor="w")
        ttk.Label(pad, style="Muted.TLabel", wraplength=300, justify="left",
                  text="The ID ties each plot back to your own records."
                  ).pack(anchor="w", pady=(0, 6))

        # Two jobs, two tabs — giving plots their IDs, and giving them their
        # trial design. Keeping them apart is what makes each one obvious.
        tools = ttk.Notebook(pad)
        tools.pack(fill="x")
        tab_ids = ttk.Frame(tools)
        tab_design = ttk.Frame(tools)
        tools.add(tab_ids, text="Plot IDs")
        tools.add(tab_design, text="Variant & rep")
        self._build_id_tools(tab_ids)
        self._build_design_tools(tab_design)
        self._build_table_body(pad)      # shared by both tabs, always visible

    def _build_id_tools(self, parent):
        """Three ways to get the company's own plot IDs onto the plots.

        Numbering is the slowest job in the app, so each route is a visible
        control rather than something to discover: number the whole grid in a
        chosen order, paste the IDs straight out of Excel, or click the plots in
        whatever order the trial actually uses.
        """
        f = ttk.Frame(parent, padding=(8, 8))
        f.pack(fill="both", expand=True)

        auto = ttk.LabelFrame(f, text="1 · Number the whole grid")
        auto.pack(fill="x")
        r = ttk.Frame(auto)
        r.pack(fill="x", pady=1)
        ttk.Label(r, text="Order").pack(side="left")
        cb = ttk.Combobox(r, textvariable=self.id_order, state="readonly", width=20,
                          values=list(ORDER_LABELS))
        cb.pack(side="left", padx=4)
        ToolTip(cb, "How the numbering walks the field.\n"
                    "'Snaking' reverses every other row or column — the way a\n"
                    "plot harvester or a person walking the trial actually goes.")
        r = ttk.Frame(auto)
        r.pack(fill="x", pady=1)
        ttk.Label(r, text="Start at").pack(side="left")
        e = ttk.Entry(r, textvariable=self.id_start, width=6, justify="right")
        e.pack(side="left", padx=4)
        ToolTip(e, "First ID. Trials numbered from 101 or 1001 are common.")
        ttk.Label(r, text="Step").pack(side="left", padx=(10, 0))
        e = ttk.Entry(r, textvariable=self.id_step, width=5, justify="right")
        e.pack(side="left", padx=4)
        ToolTip(e, "How much each ID goes up by. Use 10 for 10, 20, 30…")
        ttk.Button(auto, text="Number them", style="Accent.TButton",
                   command=self._apply_numbering).pack(fill="x", pady=(4, 2))

        paste = ttk.LabelFrame(f, text="2 · Paste IDs from Excel")
        paste.pack(fill="x", pady=(8, 0))
        b = ttk.Button(paste, text="Paste into the ID column",
                       command=self._paste_ids)
        b.pack(fill="x", pady=2)
        ToolTip(b, "Copy one column of IDs in Excel, then press this (or Ctrl+V\n"
                   "in the table). They fill downwards from the first selected\n"
                   "row — select nothing to start at the top. A second and third\n"
                   "copied column land in variant and rep.")
        ttk.Label(paste, style="Muted.TLabel",
                  text="Fills down from the selected row.").pack(anchor="w")

        click = ttk.LabelFrame(f, text="3 · Click plots in your own order")
        click.pack(fill="x", pady=(8, 0))
        r = ttk.Frame(click)
        r.pack(fill="x")
        ttk.Label(r, text="Next ID").pack(side="left")
        e = ttk.Entry(r, textvariable=self.id_next, width=6, justify="right")
        e.pack(side="left", padx=4)
        ToolTip(e, "The ID the next click will stamp. Counts on by 'Step'.")
        self.number_btn = ttk.Button(click, text="Number by clicking: off",
                                     command=self._toggle_number_mode)
        self.number_btn.pack(fill="x", pady=(4, 2))
        ToolTip(self.number_btn,
                "Click plots on the map in the order your trial numbers them —\n"
                "each click stamps 'Next ID' and counts on by 'Step'.\n"
                "For a layout no pattern describes. Ctrl+Z undoes a mis-click.")
        ttk.Label(click, style="Muted.TLabel",
                  text="For an order no pattern describes.").pack(anchor="w")

    def _build_design_tools(self, parent):
        pad = ttk.Frame(parent, padding=(8, 8))
        pad.pack(fill="both", expand=True)

        # --- pattern autofill ---
        pat = ttk.LabelFrame(pad, text="Pattern")
        pat.pack(fill="x")
        r0 = ttk.Frame(pat)
        r0.pack(fill="x", pady=1)
        ttk.Label(r0, text="rep = block of").pack(side="left")
        ttk.Entry(r0, textvariable=self.pat_block, width=3, justify="right").pack(
            side="left", padx=4)
        ttk.Radiobutton(r0, text="rows", value="row", variable=self.pat_axis).pack(side="left")
        ttk.Radiobutton(r0, text="cols", value="col", variable=self.pat_axis).pack(side="left")
        r1 = ttk.Frame(pat)
        r1.pack(fill="x", pady=1)
        ttk.Label(r1, text="variant = 1…").pack(side="left")
        ttk.Entry(r1, textvariable=self.pat_nvar, width=3, justify="right").pack(
            side="left", padx=4)
        ttk.Label(r1, text="cycling per rep").pack(side="left")
        r2 = ttk.Frame(pat)
        r2.pack(fill="x", pady=(2, 0))
        ttk.Checkbutton(r2, text="set rep", variable=self.pat_set_rep).pack(side="left")
        ttk.Checkbutton(r2, text="set variant", variable=self.pat_set_var).pack(
            side="left", padx=(10, 0))
        ttk.Button(pat, text="Apply pattern", command=self._apply_pattern).pack(
            fill="x", pady=(4, 2))

        # --- explicit values: fill the selection, or paint onto the map ---
        val = ttk.LabelFrame(pad, text="Set values")
        val.pack(fill="x", pady=(8, 0))
        vr = ttk.Frame(val)
        vr.pack(fill="x")
        ttk.Label(vr, text="variant").pack(side="left")
        ttk.Entry(vr, textvariable=self.val_variant, width=5, justify="right").pack(
            side="left", padx=(4, 12))
        ttk.Label(vr, text="rep").pack(side="left")
        ttk.Entry(vr, textvariable=self.val_rep, width=5, justify="right").pack(
            side="left", padx=4)
        br = ttk.Frame(val)
        br.pack(fill="x", pady=(4, 2))
        ttk.Button(br, text="Fill selected rows", command=self._fill_selected).pack(
            side="left", fill="x", expand=True)
        ttk.Button(br, text="Clear", width=6, command=self._clear_selected).pack(
            side="left", padx=(4, 0))
        self.paint_btn = ttk.Button(val, text="Paint on map: off",
                                    command=self._toggle_paint)
        self.paint_btn.pack(fill="x", pady=(0, 2))
        ttk.Label(val, style="Muted.TLabel", wraplength=290, justify="left",
                  text="Painting stamps these values onto plots you click — drag "
                       "across several to stamp a strip. An empty box leaves that "
                       "column untouched.").pack(anchor="w")

    def _build_table_body(self, pad):
        """The plot list itself, plus the ID health line and map-label controls.

        Lives below the tool tabs rather than inside one, because it is the thing
        the user checks their work against whichever job they are doing.
        """
        # Everything that sits below the table is packed first, bottom-up, so a
        # short window shrinks the table instead of pushing the ID warning —
        # the one thing that must be seen — off the bottom edge.
        hint = ttk.Label(pad, style="Muted.TLabel", wraplength=300, justify="left",
                         text="Double-click a cell to type · Ctrl+V pastes a column")
        hint.pack(side="bottom", anchor="w", pady=(6, 0))
        ToolTip(hint, "Double-click any ID, variant or rep cell — or press Enter on\n"
                      "a row — to type. Enter commits and drops down a row, Tab\n"
                      "moves across, Esc cancels. Ctrl+V pastes a column from Excel\n"
                      "into whichever column you last clicked.")

        # --- how the map draws them ---
        disp = ttk.Frame(pad)
        disp.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Label(disp, text="Map labels:").pack(side="left")
        cb = ttk.Combobox(disp, textvariable=self.label_by, state="readonly", width=11,
                          values=["num", "variant", "rep", "variant / rep"])
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self._render_overlay())
        ToolTip(cb, "Which value is drawn on each plot. 'num' is the plot ID.")
        ttk.Checkbutton(disp, text="colour by variant", variable=self.colour_variant,
                        command=self._render_overlay).pack(side="left", padx=(8, 0))

        # --- ID health: the one thing that must be right before exporting ---
        idbox = ttk.Frame(pad)
        idbox.pack(side="bottom", fill="x", pady=(6, 0))
        self.id_status = ttk.Label(idbox, text="", style="Muted.TLabel",
                                   wraplength=290, justify="left")
        self.id_status.pack(anchor="w", fill="x")
        self.id_fix_btn = ttk.Button(idbox, text="Select the clashing plots",
                                     command=self._select_duplicates)
        # packed only while there is something to fix (see _refresh_id_status)

        # --- the table ---
        tf = ttk.Frame(pad)
        tf.pack(fill="both", expand=True, pady=(8, 0))
        cols = ("num", "col", "row", "variant", "rep")
        headings = {"num": "ID", "col": "col", "row": "row",
                    "variant": "variant", "rep": "rep"}
        self.tree = ttk.Treeview(tf, columns=cols, show="headings",
                                 selectmode="extended", height=9)
        widths = {"num": 56, "col": 36, "row": 36, "variant": 60, "rep": 44}
        for c in cols:
            self.tree.heading(c, text=headings[c])
            self.tree.column(c, width=widths[c], anchor="center",
                             stretch=c in ("variant", "rep"))
        # A shared ID silently corrupts the join back to the trial records, so
        # the offending rows are coloured the moment they appear.
        self.tree.tag_configure("dup", background="#ffe3e3", foreground=BAD)
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Button-1>", self._on_tree_click, add="+")
        self.tree.bind("<Double-Button-1>", self._on_tree_double)
        self.tree.bind("<Return>", self._on_tree_return)
        self.tree.bind("<Control-v>", self._paste_column)
        self.tree.bind("<Control-V>", self._paste_column)

    def _toggle_table(self):
        if self.show_table.get():
            self.panes.insert("end", self.table_holder, weight=1)
        else:
            self._end_edit(commit=True)
            self.panes.forget(self.table_holder)

    # --- table <-> plots ---
    @staticmethod
    def _cell(value) -> str:
        return "" if value is None else str(value)

    @staticmethod
    def _parse_value(text: str):
        """'' -> None (unassigned); '7' -> 7. Raises ValueError otherwise."""
        t = text.strip()
        if not t:
            return None
        return int(round(float(t.replace(",", "."))))

    def _refresh_table(self):
        """Rebuild every row. Called when plots are added / removed / renumbered."""
        if self.tree is None:
            return
        self._end_edit(commit=False)
        self.tree.delete(*self.tree.get_children())
        for i, p in enumerate(self.plots):
            self.tree.insert("", "end", iid=str(i),
                             values=(self._cell(p.num), p.col + 1, p.row + 1,
                                     self._cell(p.variant), self._cell(p.rep)))
        self._refresh_id_status()
        self._sync_table_selection()

    def _update_row(self, idx: int):
        """Push one plot's ID / variant / rep into its row (no full rebuild)."""
        if self.tree is None:
            return
        iid = str(idx)
        if self.tree.exists(iid):
            p = self.plots[idx]
            self.tree.set(iid, "num", self._cell(p.num))
            self.tree.set(iid, "variant", self._cell(p.variant))
            self.tree.set(iid, "rep", self._cell(p.rep))

    # --- plot IDs ---
    def _refresh_id_status(self):
        """Recheck IDs and report duplicates / blanks in one line.

        Called after anything that can change an ID, so the user is told the
        moment a clash appears rather than when the shapefile is written.
        """
        if self.tree is None:
            return
        dups, missing = id_report(self.plots)
        self._dup_ids = set(dups)
        for i, p in enumerate(self.plots):
            iid = str(i)
            if self.tree.exists(iid):
                self.tree.item(iid, tags=("dup",) if p.num in self._dup_ids else ())
        if not self.plots:
            self.id_status.config(text="", style="Muted.TLabel")
            self.id_fix_btn.pack_forget()
            return
        parts = []
        if dups:
            clashing = sum(dups.values())
            parts.append(f"{clashing} plots share {len(dups)} duplicated ID"
                         f"{'s' if len(dups) > 1 else ''}")
        if missing:
            parts.append(f"{missing} without an ID")
        if parts:
            self.id_status.config(text="⚠ " + ", ".join(parts) + ".",
                                  style="Bad.TLabel")
            self.id_fix_btn.pack(fill="x", pady=(2, 0))
        else:
            self.id_status.config(text=f"✓ {len(self.plots)} plots, every ID unique.",
                                  style="OK.TLabel")
            self.id_fix_btn.pack_forget()

    def _select_duplicates(self):
        """Select and scroll to every plot whose ID is shared, so it can be fixed."""
        iids = [str(i) for i, p in enumerate(self.plots) if p.num in self._dup_ids]
        if not iids:
            return
        # Deliberately not routed through _sync_table_selection: that mirrors the
        # map's single selected plot and would collapse this back to one row.
        self.tree.selection_set(iids)
        self.tree.see(iids[0])
        self.tree.focus(iids[0])
        self._render_overlay()
        self.status.config(text=f"Selected {len(iids)} plots with a clashing ID.")

    def _apply_numbering(self):
        """Renumber the whole grid in the chosen order, start and step."""
        if not self.plots:
            self.status.config(text="Build a grid first.")
            return
        try:
            start = int(float(self.id_start.get()))
            step = int(float(self.id_step.get()))
        except ValueError:
            self.status.config(text="Start and step must be whole numbers.")
            return
        order = ORDER_LABELS.get(self.id_order.get(), "rowwise")
        self._snapshot()
        try:
            renumber(self.plots, order=order, start=start, step=step)
        except ValueError as exc:
            self._undo.pop()
            self.status.config(text=str(exc))
            return
        self.id_next.set(str(start + step * len(self.plots)))
        self._refresh_table()
        self._render_overlay()
        self.status.config(text=f"Numbered {len(self.plots)} plots "
                                f"({self.id_order.get().lower()}, from {start}).")

    def _toggle_number_mode(self):
        """Turn click-to-number on/off. Mutually exclusive with painting."""
        self._number_mode = not self._number_mode
        self.number_btn.config(
            text=f"Number by clicking: "
                 f"{'ON — click plots' if self._number_mode else 'off'}")
        if self._number_mode:
            self._paint_mode = False
            self.paint_btn.config(text="Paint on map: off")
            self._anchor_mode = False
            self.anchor_btn.config(text="Set anchor (click map)")
            self.label_by.set("num")        # show what you are assigning
            self._render_overlay()
            self.status.config(text="Click plots in your numbering order. "
                                    "Click the button again to stop.")
        self.canvas.config(cursor=self._cursor())

    def _stamp_id(self, idx: int) -> bool:
        """Give plot *idx* the next ID and advance the counter."""
        try:
            nxt = int(float(self.id_next.get()))
            step = int(float(self.id_step.get()))
        except ValueError:
            self.status.config(text="'Next ID' and 'Step' must be whole numbers.")
            return False
        self.plots[idx].num = nxt
        self.id_next.set(str(nxt + step))
        self._update_row(idx)
        self._refresh_id_status()
        return True

    def _paste_ids(self):
        """Paste clipboard IDs into the ID column from the first selected row."""
        self._last_col = "#1"
        self._paste_column()

    def _sync_table_selection(self):
        """Mirror the map's selected plot into the table."""
        if self.tree is None or self._syncing:
            return
        self._syncing = True
        try:
            if self._selected is None:
                sel = self.tree.selection()
                if sel:
                    self.tree.selection_remove(*sel)
            else:
                iid = str(self._selected)
                if self.tree.exists(iid) and self.tree.selection() != (iid,):
                    self.tree.selection_set(iid)
                    self.tree.focus(iid)
                    self.tree.see(iid)
        finally:
            self._syncing = False

    def _on_tree_select(self, _event=None):
        if self._syncing:
            return
        sel = self.tree.selection()
        idx = int(sel[0]) if len(sel) == 1 else None
        if idx != self._selected:
            self._selected = idx
            self._render_overlay()

    def _on_tree_click(self, event):
        col = self.tree.identify_column(event.x)
        if col in self._EDIT_COLUMNS:
            self._last_col = col
        self._end_edit(commit=True)

    def _on_tree_double(self, event):
        item = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if item and col in self._EDIT_COLUMNS:
            self._begin_edit(item, col)
        return "break"

    def _on_tree_return(self, _event):
        item = self.tree.focus()
        if item:
            self._begin_edit(item, self._last_col)
        return "break"

    # --- in-place cell editing ---
    # The columns the user may type into: ID, variant, rep. col/row are derived
    # from where the plot physically sits, so they are never hand-edited.
    _EDIT_COLUMNS = ("#1", "#4", "#5")
    _COLUMN_FIELD = {"#1": "num", "#4": "variant", "#5": "rep"}
    _FIELD_COLUMN = {"num": "#1", "variant": "#4", "rep": "#5"}

    def _begin_edit(self, item, column):
        self._end_edit(commit=True)
        if column not in self._EDIT_COLUMNS:
            return
        self.tree.see(item)
        bbox = self.tree.bbox(item, column)
        if not bbox:
            return
        x, y, w, h = bbox
        field = self._COLUMN_FIELD[column]
        entry = tk.Entry(self.tree, justify="center", relief="solid", borderwidth=1)
        entry.insert(0, self.tree.set(item, field))
        entry.select_range(0, "end")
        entry.place(x=x, y=y, width=w, height=h)
        entry.focus_set()
        self._editing = (item, field, entry)
        entry.bind("<Return>", lambda e: self._end_edit(True, "down"))
        entry.bind("<Tab>", lambda e: self._end_edit(True, "right"))
        entry.bind("<Escape>", lambda e: self._end_edit(False))
        entry.bind("<FocusOut>", lambda e: self._end_edit(True))

    def _end_edit(self, commit: bool = True, move: str | None = None):
        if not self._editing:
            return "break"
        item, field, entry = self._editing
        text = entry.get()
        self._editing = None          # cleared first: destroy() re-fires FocusOut
        entry.destroy()
        if commit and not self._set_cell(item, field, text):
            return "break"
        if move:
            self._move_edit(item, field, move)
        return "break"

    def _set_cell(self, item, field, text) -> bool:
        try:
            value = self._parse_value(text)
        except ValueError:
            self.status.config(text=f"'{text.strip()}' is not a whole number.")
            return False
        idx = int(item)
        plot = self.plots[idx]
        if getattr(plot, field) == value:
            return True
        self._snapshot()
        setattr(plot, field, value)
        self._update_row(idx)
        if field == "num":
            self._refresh_id_status()
        self._render_overlay()
        return True

    def _move_edit(self, item, field, direction):
        """Tab moves across the editable columns; Enter drops down the same one."""
        column = self._FIELD_COLUMN[field]
        if direction == "right":
            i = self._EDIT_COLUMNS.index(column)
            if i + 1 < len(self._EDIT_COLUMNS):
                self._begin_edit(item, self._EDIT_COLUMNS[i + 1])
                return
            column = self._EDIT_COLUMNS[0]     # wrap onto the next row
        nxt = self.tree.next(item)
        if nxt:
            self._begin_edit(nxt, column)

    # --- bulk assignment ---
    def _selected_indices(self) -> list[int]:
        return sorted(int(i) for i in self.tree.selection())

    def _read_values_pair(self):
        """(variant, rep) from the 'Set values' boxes; None means 'leave alone'."""
        return (self._parse_value(self.val_variant.get()),
                self._parse_value(self.val_rep.get()))

    def _fill_selected(self):
        idxs = self._selected_indices()
        if not idxs:
            self.status.config(text="Select one or more rows in the table first.")
            return
        try:
            variant, rep = self._read_values_pair()
        except ValueError:
            self.status.config(text="Variant / rep must be whole numbers.")
            return
        if variant is None and rep is None:
            self.status.config(text="Type a variant and/or a rep value first.")
            return
        self._snapshot()
        for i in idxs:
            if variant is not None:
                self.plots[i].variant = variant
            if rep is not None:
                self.plots[i].rep = rep
            self._update_row(i)
        self._render_overlay()
        self.status.config(text=f"Filled {len(idxs)} plot(s).")

    def _clear_selected(self):
        idxs = self._selected_indices()
        if not idxs:
            self.status.config(text="Select one or more rows in the table first.")
            return
        self._snapshot()
        for i in idxs:
            self.plots[i].variant = self.plots[i].rep = None
            self._update_row(i)
        self._render_overlay()
        self.status.config(text=f"Cleared {len(idxs)} plot(s).")

    def _apply_pattern(self):
        if not self.plots:
            self.status.config(text="Build a grid first.")
            return
        if not (self.pat_set_rep.get() or self.pat_set_var.get()):
            self.status.config(text="Tick 'set rep' and/or 'set variant' first.")
            return
        try:
            block = int(float(self.pat_block.get()))
            n_var = int(float(self.pat_nvar.get() or 0))
        except ValueError:
            self.status.config(text="Block size and variant count must be whole numbers.")
            return
        try:
            self._snapshot()
            assign_pattern(self.plots, rep_axis=self.pat_axis.get(), block_size=block,
                           n_variants=n_var, set_rep=self.pat_set_rep.get(),
                           set_variant=self.pat_set_var.get())
        except ValueError as exc:
            self._undo.pop()
            self.status.config(text=str(exc))
            return
        for i in range(len(self.plots)):
            self._update_row(i)
        self._render_overlay()
        reps = len({p.rep for p in self.plots if p.rep is not None})
        self.status.config(text=f"Pattern applied: {reps} rep(s) across "
                                f"{len(self.plots)} plots.")

    def _paste_column(self, _event=None):
        """Paste a column of values (one per line) from Excel into the table."""
        try:
            text = self.clipboard_get()
        except tk.TclError:
            self.status.config(text="Nothing on the clipboard.")
            return "break"
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            return "break"

        rows = list(self.tree.get_children())
        sel = self.tree.selection()
        start = self.tree.index(sel[0]) if sel else 0
        targets = rows[start:]
        # Paste lands in the column last clicked and spills right from there, so
        # a two-column copy out of Excel fills both in one go.
        fields = {"#1": ["num", "variant", "rep"],
                  "#4": ["variant", "rep"],
                  "#5": ["rep"]}.get(self._last_col, ["variant", "rep"])

        self._snapshot()
        pasted = bad = 0
        for item, line in zip(targets, lines):
            idx = int(item)
            for field, cell in zip(fields, line.split("\t")):
                try:
                    setattr(self.plots[idx], field, self._parse_value(cell))
                except ValueError:
                    bad += 1
                    continue
            self._update_row(idx)
            pasted += 1
        if not pasted:
            self._undo.pop()
        self._refresh_id_status()
        self._render_overlay()
        note = f", {bad} value(s) skipped (not numbers)" if bad else ""
        extra = (f", {len(lines) - pasted} line(s) had no plot to land on"
                 if len(lines) > pasted else "")
        self.status.config(text=f"Pasted {fields[0]} into {pasted} plot(s)"
                                f"{note}{extra}.")
        return "break"

    # --- painting values onto the map ---
    def _toggle_paint(self):
        self._paint_mode = not self._paint_mode
        self.paint_btn.config(
            text=f"Paint on map: {'ON — click plots' if self._paint_mode else 'off'}")
        self.canvas.config(cursor=self._cursor())
        if self._paint_mode:
            self._anchor_mode = False
            self.anchor_btn.config(text="Set anchor (click map)")
            self._number_mode = False       # the two click modes are exclusive
            self.number_btn.config(text="Number by clicking: off")

    def _cursor(self) -> str:
        if self._anchor_mode:
            return "tcross"
        if self._number_mode:
            return "hand2"
        return "dotbox" if self._paint_mode else "crosshair"

    def _stamp(self, idx: int) -> bool:
        try:
            variant, rep = self._read_values_pair()
        except ValueError:
            self.status.config(text="Variant / rep must be whole numbers.")
            return False
        if variant is None and rep is None:
            self.status.config(text="Type a variant and/or a rep value to paint with.")
            return False
        plot = self.plots[idx]
        if variant is not None:
            plot.variant = variant
        if rep is not None:
            plot.rep = rep
        self._update_row(idx)
        return True

    def _variant_colour(self, variant: int) -> str:
        return VARIANT_COLOURS[(variant - 1) % len(VARIANT_COLOURS)]

    def _plot_label(self, plot) -> str:
        mode = self.label_by.get()
        if mode == "num":
            return self._cell(plot.num) or "·"
        if mode == "variant":
            return self._cell(plot.variant) or "·"
        if mode == "rep":
            return self._cell(plot.rep) or "·"
        return f"{self._cell(plot.variant) or '·'}/{self._cell(plot.rep) or '·'}"

    def _assignment_summary(self) -> str:
        missing_v = sum(1 for p in self.plots if p.variant is None)
        missing_r = sum(1 for p in self.plots if p.rep is None)
        if not (missing_v or missing_r):
            return ""
        parts = []
        if missing_v:
            parts.append(f"{missing_v} without a variant")
        if missing_r:
            parts.append(f"{missing_r} without a rep")
        return " and ".join(parts)

    # ---------- preview load ----------
    def _rgb_bands(self):
        """Pick (R, G, B) 1-based band numbers from the sensor if it has them."""
        s = self.sensor
        if s is not None and {"Red", "Green", "Blue"}.issubset(s.role_set()):
            return (s.bands["Red"], s.bands["Green"], s.bands["Blue"])
        return None

    def _load_preview(self):
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            from .preview import load_preview
            self.preview = load_preview(self.ortho_path, rgb_bands=self._rgb_bands())
        except Exception as exc:
            self.config(cursor="")
            messagebox.showerror(APP_NAME, f"Could not load orthomosaic:\n{exc}",
                                 parent=self)
            self.status.config(text="Failed to load orthomosaic.")
            return
        self.config(cursor="")
        if not self.preview.crs_wkt:
            self.status.config(
                text="⚠ Raster has no CRS — exported plots will be unreferenced.")
        else:
            self.status.config(text="Ready. Build a grid and click the map to place "
                                    "it, or import an existing plot shapefile.")
        self._fit()
        pending, self._pending_import = self._pending_import, None
        if pending and os.path.exists(pending):
            self._import_shapefile(pending)

    # ---------- coordinate transforms ----------
    def map_to_canvas(self, x: float, y: float) -> tuple[float, float]:
        px, py = self.preview.map_to_pixel(x, y)
        return px * self.zoom + self.pan_x, py * self.zoom + self.pan_y

    def canvas_to_map(self, cx: float, cy: float) -> tuple[float, float]:
        px = (cx - self.pan_x) / self.zoom
        py = (cy - self.pan_y) / self.zoom
        return self.preview.pixel_to_map(px, py)

    def _map_per_canvas_px(self) -> float:
        t = self.preview.transform
        return math.hypot(t.a, t.b) / self.zoom

    # ---------- view ----------
    def _on_configure(self, _event):
        if self.preview is None:
            return
        if not self._fitted and self.canvas.winfo_width() > 1:
            self._fit()
        else:
            self._render_image()
            self._render_overlay()

    def _fit(self):
        if self.preview is None:
            return
        cw = max(self.canvas.winfo_width(), 1)
        ch = max(self.canvas.winfo_height(), 1)
        self.zoom = min(cw / self.preview.width, ch / self.preview.height)
        self.pan_x = (cw - self.preview.width * self.zoom) / 2
        self.pan_y = (ch - self.preview.height * self.zoom) / 2
        self._fitted = True
        self._render_image()
        self._render_overlay()

    def _on_wheel(self, event):
        self._wheel(event, 1 if event.delta > 0 else -1)

    def _wheel(self, event, direction):
        if self.preview is None:
            return
        factor = 1.15 if direction > 0 else 1 / 1.15
        new_zoom = max(0.02, min(self.zoom * factor, 60.0))
        px = (event.x - self.pan_x) / self.zoom      # keep cursor point fixed
        py = (event.y - self.pan_y) / self.zoom
        self.zoom = new_zoom
        self.pan_x = event.x - px * self.zoom
        self.pan_y = event.y - py * self.zoom
        self._render_image()
        self._render_overlay()

    # ---------- rendering (split: image is costly, overlay is cheap) ----------
    def _render_image(self):
        from PIL import Image, ImageEnhance, ImageTk
        c = self.canvas
        c.delete("img")
        if self.preview is None:
            return
        cw, ch = max(c.winfo_width(), 1), max(c.winfo_height(), 1)
        pw, ph = self.preview.width, self.preview.height
        x0 = max(0, (0 - self.pan_x) / self.zoom)
        y0 = max(0, (0 - self.pan_y) / self.zoom)
        x1 = min(pw, (cw - self.pan_x) / self.zoom)
        y1 = min(ph, (ch - self.pan_y) / self.zoom)
        if x1 <= x0 or y1 <= y0:
            return
        box = (int(x0), int(y0), int(x1) + 1, int(y1) + 1)
        crop = self.preview.image.crop(box)
        out_w = max(1, int(round((box[2] - box[0]) * self.zoom)))
        out_h = max(1, int(round((box[3] - box[1]) * self.zoom)))
        resample = Image.NEAREST if self.zoom > 1 else Image.BILINEAR
        crop = crop.resize((out_w, out_h), resample)
        # Brightness is applied after the resize, so it only costs what is
        # actually on screen, and only to the copy being displayed.
        if abs(self._brightness - 1.0) > 0.01:
            crop = ImageEnhance.Brightness(crop).enhance(self._brightness)
        self._photo = ImageTk.PhotoImage(crop)
        c.create_image(self.pan_x + box[0] * self.zoom,
                       self.pan_y + box[1] * self.zoom,
                       anchor="nw", image=self._photo, tags="img")
        c.tag_lower("img")
        self._update_view_readout()

    def _render_overlay(self):
        c = self.canvas
        c.delete("overlay")
        if self.preview is None:
            return
        show_labels = len(self.plots) <= 400 and self.zoom > 0.12
        colour_by_variant = self.colour_variant.get()
        for i, p in enumerate(self.plots):
            pts = []
            for (x, y) in p.corners:
                cx, cy = self.map_to_canvas(x, y)
                pts.extend((cx, cy))
            if i == self._selected:
                outline, width, stip, fill = PLOT_SELECTED, 3, "gray25", PLOT_SELECTED
            elif i == self._hover:
                outline, width, stip, fill = PLOT_HOVER, 2, "gray12", PLOT_HOVER
            elif p.num in self._dup_ids:
                # A clashing ID is worth shouting about wherever the eye is.
                outline, width = PLOT_DUPLICATE, 3
                stip, fill = "gray25", PLOT_DUPLICATE
            elif colour_by_variant and p.variant is not None:
                outline, width = PLOT_OUTLINE, 2
                stip, fill = "gray50", self._variant_colour(p.variant)
            else:
                outline, width, stip, fill = PLOT_OUTLINE, 2, None, ""
            c.create_polygon(*pts, outline=outline, width=width, fill=fill,
                             tags=("overlay", "plot", f"plot{i}"),
                             **({"stipple": stip} if stip else {}))
            if show_labels:
                n = len(pts) // 2                 # imported rings aren't always 4 corners
                mx = sum(pts[0::2]) / n
                my = sum(pts[1::2]) / n
                text = self._plot_label(p)
                c.create_text(mx + 1, my + 1, text=text, fill="#1a1a1a",
                              font=("Segoe UI", 9, "bold"), tags=("overlay", "label"))
                c.create_text(mx, my, text=text, fill=LABEL_COLOUR,
                              font=("Segoe UI", 9, "bold"), tags=("overlay", "label"))
        self._draw_scale_bar()
        self.lbl_count.config(text=f"{len(self.plots)} plots"
                                   + ("  (edited)" if self._edited else ""))

    def _draw_scale_bar(self):
        c = self.canvas
        ch = max(c.winfo_height(), 1)
        m_per_px = self._map_per_canvas_px()
        if not math.isfinite(m_per_px) or m_per_px <= 0:
            return
        nice = self._nice_length(m_per_px * 110)
        bar_px = nice / m_per_px
        x, y = 18, ch - 20
        c.create_line(x, y, x + bar_px, y, fill="#ffffff", width=2, tags="overlay")
        for xx in (x, x + bar_px):
            c.create_line(xx, y - 4, xx, y + 4, fill="#ffffff", width=2, tags="overlay")
        label = f"{nice:g} m" if nice >= 1 else f"{nice * 100:g} cm"
        c.create_text(x + bar_px / 2, y - 9, text=label, fill="#ffffff",
                      font=("Segoe UI", 8), tags="overlay")

    @staticmethod
    def _nice_length(raw: float) -> float:
        """Largest 1/2/5 ×10ⁿ value not exceeding *raw* (min sensible floor)."""
        if raw <= 0:
            return 1.0
        exp = math.floor(math.log10(raw))
        base = 10 ** exp
        for step in (5, 2, 1):
            if step * base <= raw:
                return step * base
        return base

    def _update_view_readout(self):
        if self.preview is None:
            return
        m_per_px = self._map_per_canvas_px()
        self.lbl_scale.config(text=f"{m_per_px:.3g} m/px")

    def _update_readout(self, cx, cy):
        if self.preview is None:
            return
        mx, my = self.canvas_to_map(cx, cy)
        self.lbl_coord.config(text=f"E {mx:,.1f}   N {my:,.1f}")

    def _clear_readout(self):
        self.lbl_coord.config(text="—")
        if self._hover is not None:
            self._hover = None
            self._render_overlay()

    # ---------- grid building ----------
    def _read_float(self, key, name, positive=False):
        try:
            v = float(self.vars[key].get())
        except ValueError:
            raise ValueError(f"{name} must be a number.")
        if positive and v <= 0:
            raise ValueError(f"{name} must be greater than 0.")
        return v

    def _read_int(self, key, name):
        try:
            v = int(float(self.vars[key].get()))
        except ValueError:
            raise ValueError(f"{name} must be a whole number.")
        if v <= 0:
            raise ValueError(f"{name} must be at least 1.")
        return v

    def _read_values(self) -> dict:
        return dict(
            plot_w=self._read_float("plot_w", "Plot width", positive=True),
            plot_h=self._read_float("plot_h", "Plot height", positive=True),
            gap_x=self._read_float("gap_x", "Gap between cols"),
            gap_y=self._read_float("gap_y", "Gap between rows"),
            n_cols=self._read_int("n_cols", "Columns"),
            n_rows=self._read_int("n_rows", "Rows"),
            angle_deg=self._read_float("angle", "Rotation"),
        )

    def _centered_origin(self, v: dict) -> tuple[float, float]:
        """Origin that centres the (unrotated) grid in the current view."""
        pitch_x = v["plot_w"] + v["gap_x"]
        pitch_y = v["plot_h"] + v["gap_y"]
        grid_w = (v["n_cols"] - 1) * pitch_x + v["plot_w"]
        grid_h = (v["n_rows"] - 1) * pitch_y + v["plot_h"]
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        cx, cy = self.canvas_to_map(cw / 2, ch / 2)
        return (cx - grid_w / 2, cy + grid_h / 2)

    def _build_grid(self, origin=None, recenter=False):
        if self.preview is None:
            return
        if self._edited and self.plots and not messagebox.askyesno(
                APP_NAME, "Rebuilding the grid discards manual plot edits "
                          "(Ctrl+Z can undo). Continue?", parent=self):
            return
        try:
            v = self._read_values()
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e), parent=self)
            return

        # IDs and variant/rep survive a rebuild as long as the grid keeps its
        # shape. They are carried by (col, row) — where the plot actually sits —
        # rather than by num, so IDs the user set by hand or pasted from Excel
        # are not thrown away just because the plot size was nudged. A different
        # number of rows or columns makes the mapping meaningless, so it goes.
        key = (v["n_cols"], v["n_rows"])
        keep = {}
        if self.plots and self._layout_key == key:
            keep = {(p.col, p.row): (p.num, p.variant, p.rep) for p in self.plots}
        elif self.plots and any(
                p.num is not None or p.variant is not None or p.rep is not None
                for p in self.plots):
            if not messagebox.askyesno(
                    APP_NAME, "The new layout has a different number of plots, so "
                              "the IDs and variant / rep assignments can't be "
                              "carried over and will be cleared (Ctrl+Z can undo). "
                              "Continue?", parent=self):
                return

        if origin is None:
            origin = self._centered_origin(v) if (recenter or not self.plots) \
                else self.plots[0].corners[0]
        self._snapshot()
        self.plots = build_plots(GridSpec(origin=origin, **v))
        for p in self.plots:
            saved = keep.get((p.col, p.row))
            if saved is not None:
                p.num, p.variant, p.rep = saved
        self._layout_key = key
        self._edited = False
        self._selected = self._hover = None
        self.status.config(text=f"{len(self.plots)} plots placed "
                                f"({v['n_cols']}×{v['n_rows']}). Drag or nudge to "
                                "fine-tune, then Export.")
        self._refresh_table()
        self._render_overlay()

    def _rebuild_in_place(self):
        """Re-apply current parameters, keeping the existing anchor if any."""
        if self.plots:
            self._build_grid(origin=self.plots[0].corners[0])
        else:
            self._build_grid(recenter=True)

    def _bump_angle(self, delta):
        try:
            self.vars["angle"].set(f"{float(self.vars['angle'].get()) + delta:g}")
        except ValueError:
            self.vars["angle"].set(f"{delta:g}")
        if self.plots:
            self._build_grid(origin=self.plots[0].corners[0])

    def _toggle_anchor(self):
        self._anchor_mode = not self._anchor_mode
        self.anchor_btn.config(
            text="Click map to place…" if self._anchor_mode else "Set anchor (click map)")
        self.canvas.config(cursor=self._cursor())

    # ---------- undo ----------
    def _snapshot(self):
        self._undo.append([copy.copy(p) for p in self._clone_plots()])
        if len(self._undo) > UNDO_LIMIT:
            self._undo.pop(0)

    def _clone_plots(self):
        from .grid import Plot
        return [Plot(p.num, p.col, p.row, list(p.corners), p.variant, p.rep)
                for p in self.plots]

    def _undo_last(self):
        if not self._undo:
            self.status.config(text="Nothing to undo.")
            return
        self._end_edit(commit=False)
        self.plots = self._undo.pop()
        self._selected = self._hover = None
        self._edited = True
        self.status.config(text="Undid last change.")
        self._refresh_table()
        self._render_overlay()

    # ---------- mouse: anchor / select / drag / pan ----------
    def _plot_at(self, cx, cy):
        for item in reversed(self.canvas.find_overlapping(cx, cy, cx, cy)):
            for tag in self.canvas.gettags(item):
                if tag.startswith("plot") and tag != "plot":
                    return int(tag[4:])
        return None

    def _on_press(self, event):
        if self.preview is None:
            return
        self.canvas.focus_set()
        self._end_edit(commit=True)
        if self._anchor_mode:
            origin = self.canvas_to_map(event.x, event.y)
            self._anchor_mode = False
            self.anchor_btn.config(text="Set anchor (click map)")
            self.canvas.config(cursor=self._cursor())
            self._build_grid(origin=origin)
            return
        idx = self._plot_at(event.x, event.y)
        if self._number_mode and idx is not None:
            # One click = one ID. Each click is its own undo step, because the
            # mistake to recover from here is a single mis-clicked plot.
            self._snapshot()
            if not self._stamp_id(idx):
                self._undo.pop()
            self._selected = idx
            self._sync_table_selection()
            self._render_overlay()
            return
        if self._paint_mode and idx is not None:
            # Start a paint stroke: one snapshot covers everything it stamps.
            self._snapshot()
            self._paint_stroke = set()
            if self._stamp(idx):
                self._paint_stroke.add(idx)
            else:
                self._undo.pop()
                self._paint_stroke = None
            self._selected = idx
            self._drag_last = (event.x, event.y)
            self._sync_table_selection()
            self._render_overlay()
            return
        if idx is not None:
            self._selected = idx
            self._snapshot()                 # in case this becomes a move
            self._drag_plot = idx
            self._drag_last = (event.x, event.y)
            self._moved = False
        else:
            self._selected = None
            self._panning = True
            self._drag_last = (event.x, event.y)
        self._sync_table_selection()
        self._render_overlay()

    def _on_drag(self, event):
        if self._drag_last is None:
            return
        lx, ly = self._drag_last
        self._drag_last = (event.x, event.y)
        if self._paint_stroke is not None:
            idx = self._plot_at(event.x, event.y)
            if idx is not None and idx not in self._paint_stroke and self._stamp(idx):
                self._paint_stroke.add(idx)
                self._render_overlay()
        elif self._drag_plot is not None:
            mx0, my0 = self.canvas_to_map(lx, ly)
            mx1, my1 = self.canvas_to_map(event.x, event.y)
            self._translate_plot(self._drag_plot, mx1 - mx0, my1 - my0)
            self._moved = True
            self._edited = True
            self._render_overlay()
        elif self._panning:
            self.pan_x += event.x - lx
            self.pan_y += event.y - ly
            self._render_image()
            self._render_overlay()

    def _on_release(self, _event):
        # If a press on a plot didn't actually move it, drop the redundant undo.
        if self._drag_plot is not None and not self._moved and self._undo:
            self._undo.pop()
        if self._paint_stroke:
            self.status.config(text=f"Painted {len(self._paint_stroke)} plot(s).")
        self._paint_stroke = None
        self._drag_plot = None
        self._panning = False
        self._drag_last = None

    def _translate_plot(self, idx, ddx, ddy):
        p = self.plots[idx]
        p.corners = [(x + ddx, y + ddy) for (x, y) in p.corners]

    def _on_hover(self, event):
        if self.preview is None or self._anchor_mode:
            return
        self._update_readout(event.x, event.y)
        idx = self._plot_at(event.x, event.y)
        if idx != self._hover:
            self._hover = idx
            self.canvas.config(cursor="hand2" if idx is not None and not self._paint_mode
                               else self._cursor())
            self._render_overlay()

    def _on_arrow(self, event):
        if self._selected is None:
            return
        step = NUDGE_STEP * (10 if event.state & 0x0001 else 1)  # Shift = ×10
        dx = {"Left": -step, "Right": step}.get(event.keysym, 0.0)
        dy = {"Up": step, "Down": -step}.get(event.keysym, 0.0)  # north = +y
        if dx == 0.0 and dy == 0.0:
            return
        self._snapshot()
        self._translate_plot(self._selected, dx, dy)
        self._edited = True
        self._render_overlay()

    def _on_delete(self, _event):
        if self._selected is None:
            return
        self._snapshot()
        del self.plots[self._selected]
        self._selected = self._hover = None
        self._edited = True
        self.status.config(text="Plot deleted (Ctrl+Z to restore).")
        self._refresh_table()
        self._render_overlay()

    # ---------- import ----------
    def _import_shapefile(self, path: str | None = None):
        """Load plots from an existing shapefile instead of building a grid."""
        if self.preview is None:
            messagebox.showinfo(APP_NAME, "Wait for the orthomosaic to finish loading.",
                                parent=self)
            return
        if path is None:
            path = filedialog.askopenfilename(
                parent=self, title="Import plot shapefile",
                filetypes=[("Shapefile", "*.shp"), ("All files", "*.*")])
            if not path:
                return
            if self.plots and not messagebox.askyesno(
                    APP_NAME, f"Replace the {len(self.plots)} plots on the map with "
                              "the ones from this shapefile? (Ctrl+Z can undo.)",
                    parent=self):
                return
        try:
            plots, notes = read_shapefile(path, self.preview.crs_wkt)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not read the shapefile:\n{exc}",
                                 parent=self)
            return

        self._snapshot()
        self.plots = plots
        self._imported_path = path
        self._edited = True          # geometry no longer matches the grid fields
        self._layout_key = None      # ... so a rebuild must warn before clearing
        self._selected = self._hover = None
        # Point the grid/pattern fields at what actually came in.
        self.vars["n_cols"].set(str(max(p.col for p in plots) + 1))
        self.vars["n_rows"].set(str(max(p.row for p in plots) + 1))
        self._refresh_table()
        self._zoom_to_plots()
        assigned = sum(1 for p in plots if p.variant is not None or p.rep is not None)
        msg = f"Imported {len(plots)} plots from {os.path.basename(path)}."
        if assigned:
            msg += f" {assigned} already carry a variant/rep."
        for note in notes:
            msg += f" Note: {note}."
        self.status.config(text=msg)

    def _zoom_to_plots(self):
        """Frame the current plots in the view (used after an import)."""
        if not self.plots or self.preview is None:
            self._render_overlay()
            return
        pts = [self.preview.map_to_pixel(x, y)
               for p in self.plots for (x, y) in p.corners]
        x0 = min(px for px, _ in pts)
        x1 = max(px for px, _ in pts)
        y0 = min(py for _, py in pts)
        y1 = max(py for _, py in pts)
        cw = max(self.canvas.winfo_width(), 1)
        ch = max(self.canvas.winfo_height(), 1)
        span_x, span_y = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
        self.zoom = max(0.02, min(cw / span_x, ch / span_y, 60.0) * 0.9)
        self.pan_x = cw / 2 - (x0 + x1) / 2 * self.zoom
        self.pan_y = ch / 2 - (y0 + y1) / 2 * self.zoom
        self._render_image()
        self._render_overlay()

    # ---------- export ----------
    def _export(self):
        if not self.plots:
            messagebox.showinfo(APP_NAME, "Build a grid first.", parent=self)
            return
        self._end_edit(commit=True)
        # Plot IDs are the join key back to the trial's own records, so a clash
        # is worth stopping for even though the shapefile would write happily.
        dups, missing_ids = id_report(self.plots)
        if dups or missing_ids:
            parts = []
            if dups:
                listed = ", ".join(str(k) for k in sorted(dups)[:6])
                parts.append(f"{sum(dups.values())} plots share {len(dups)} "
                             f"duplicated ID(s): {listed}"
                             f"{'…' if len(dups) > 6 else ''}")
            if missing_ids:
                parts.append(f"{missing_ids} plots have no ID")
            if not messagebox.askyesno(
                    APP_NAME,
                    " and ".join(parts) + ".\n\nPlot IDs join these results back "
                    "to your own records — duplicates make two plots look like "
                    "one.\n\nExport anyway?", parent=self):
                return
        gaps = self._assignment_summary()
        if gaps and not messagebox.askyesno(
                APP_NAME, f"{len(self.plots)} plots, {gaps}.\n\nThose plots get an "
                          "empty variant / rep column in the shapefile. Export anyway?",
                parent=self):
            return
        # Default to a new name beside an imported file rather than over it.
        if self._imported_path:
            stem = os.path.splitext(os.path.basename(self._imported_path))[0]
            suggested = f"{stem}_assigned.shp"
        else:
            suggested = f"{os.path.splitext(os.path.basename(self.ortho_path))[0]}_plots.shp"
        path = filedialog.asksaveasfilename(
            parent=self, title="Save plot shapefile",
            defaultextension=".shp", initialfile=suggested,
            initialdir=os.path.dirname(self._imported_path or self.ortho_path),
            filetypes=[("Shapefile", "*.shp")])
        if not path:
            return
        try:
            write_shapefile(self.plots, path, self.preview.crs_wkt)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not write shapefile:\n{exc}",
                                 parent=self)
            return
        self.status.config(text=f"Exported {len(self.plots)} plots → "
                                f"{os.path.basename(path)}")
        if self.on_export:
            self.on_export(path)
        if messagebox.askyesno(APP_NAME,
                               f"Saved {len(self.plots)} plots to:\n{path}\n\n"
                               "Use this shapefile in the main window now?",
                               parent=self):
            self.destroy()
