# ZonalStats

A standalone Windows program that extracts **per-plot reflectance statistics and
vegetation indices** from a drone orthomosaic using polygons drawn in QGIS, and
writes the results to a CSV.

Workflow it fits into:

1. Build the orthomosaic in Agisoft Metashape.
2. Draw plot polygons in QGIS (shapefile with attributes such as `num`, `col`,
   `row`) — or draw them in ZonalStats' Plot Designer and give each plot its
   variant and replication number there.
3. **Run ZonalStats** -> pick the orthomosaic, the shapefile, an output folder,
   choose what you want in the CSV, and click *Run extraction*.

No QGIS or Python install is needed on the machine that runs the built `.exe` -
GDAL/PROJ are bundled inside it.

---

## Using the program

| Field | What to do |
|-------|------------|
| **Orthomosaic** | *Single file* for one `.tif`, or *Folder* to batch every `.tif` in a folder. |
| **Shapefile (.shp)** | Your QGIS plot polygons, *or* design them in-app with **Design / edit plots…** (see below). Its attribute fields appear on the **Attributes** tab. |
| **Output folder / file name** | Where the CSV is written. |
| **Sensor / camera** | Sets the band order. Auto-guessed from band count; override if needed. Use **Add / edit camera…** to define a new camera in-app. |

The window is laid out as the three questions a run actually asks — **1 · Data**,
**2 · What to extract**, **3 · Output** — and each of the four tabs under step 2
shapes a different part of the CSV:

- **Bands & statistics** - which raw bands to output, and which statistics
  (mean, median, std, min, max, count, range, IQR, percentiles, CV). **Hover** a
  statistic to see what it is good for. **All / None / Typical** shortcuts;
  "Typical" is mean, median, std and count. The chosen statistics apply to both
  bands and indices.
- **Indices** - a multi-column checklist of vegetation indices. Only indices
  whose required bands exist on the selected sensor are shown. **Hover** one to
  see its formula and description; **double-click** to edit it; **∗** marks
  custom indices. The **search box** filters by name, formula or description.
  **Add / edit index...** creates a new one (live validation).
- **Attributes** - which shapefile fields (e.g. `num`, `col`, `row`, `variant`,
  `rep`) to carry into the CSV. Those five are pre-selected when present.
- **Options** - NoData value and a reflectance scale factor (leave at `1.0` for
  0-1 reflectance orthomosaics).

### It tells you what it will do before it does it

- **Nothing is validated only at Run time.** Every input is checked as you type.
  A status dot beside each one shows `○` still to do, `✓` ready, `✕` there's a
  problem, and the **Run** button stays disabled with the next thing to fix
  written beside it.
- **The CSV's columns are previewed live** under step 3 — the exact count and
  the first few names — so you see the shape of the output before running.
- Picking the orthomosaic fills in the rest: the output folder defaults beside
  it, the file name becomes `<ortho>_stats.csv` until you type your own, and the
  camera is guessed from the band count.
- **Everything is remembered.** Camera, statistics, indices, attributes, folders,
  window size and recent files are saved to `settings.json` next to the `.exe`
  and restored next launch, so the next flight of the same trial is: pick the new
  orthomosaic, press Run. *File > Clear all inputs* starts fresh.

Keyboard: `Ctrl+O` orthomosaic · `Ctrl+P` plot polygons · `Ctrl+D` Plot Designer
· `F5` run · `Ctrl+L` log · `F1` quick guide.

### Plot Designer (drawing plots without QGIS)

Click **Design / edit plots…** next to the Shapefile field. This opens a viewer
where you lay out plots directly on the imagery — and where you assign each plot
its variant and replication. If a shapefile is already selected in the main
window it is loaded in, so the same window covers both "draw new plots" and
"annotate the plots I have".

- The orthomosaic loads as a downsampled RGB preview (true-colour when the
  sensor has Red/Green/Blue bands). **Mouse wheel** zooms, **drag empty space**
  pans, **Fit view** re-centres. A **scale bar** and a live **cursor
  coordinate / metres-per-pixel** readout (bottom strip) ground your spacing.
- Set **plot width/height**, **columns/rows**, **gaps**, and a **rotation**
  angle (fields are rarely axis-aligned), all in map units (metres for a UTM
  ortho). Click **Build / reset grid** — or just press **Enter** in any field to
  re-apply. The grid centres itself on the current view.
- A **Brightness** slider lightens or darkens the preview when the ortho is too
  dark (or blown out) to line plots up against. It affects the picture only —
  the pixel values that get extracted are untouched. **Reset** returns to 1.0×.
- Click **Set anchor**, then click the map to place the top-left of plot 1.
  Use the **rotation nudges** (±0.2° / ±1°) to line the grid up with the rows.
- **Click** a plot to select it, **drag** to move it, **arrow keys** to
  fine-nudge (Shift = ×10), **Delete** to remove a stray plot, and **Ctrl+Z** to
  undo any change.
- **Import shapefile…** pulls in plots you already have (e.g. digitised in
  QGIS). Missing `col`/`row` are inferred from where the plots actually sit —
  rotated layouts included — missing `num` is filled in reading order, and
  geometry in another CRS is reprojected onto the ortho. Existing
  `variant`/`rep` values are kept.
- **Export shapefile…** writes `num`/`col`/`row`/`variant`/`rep` polygons in the
  raster's CRS and offers to load them straight into the main window for
  extraction.

Plots are stored in real map coordinates, so the exported shapefile is exact
regardless of the preview's resolution. *(Classical-CV and AI auto-detection of
plots are planned follow-ups.)*

#### Plot IDs (the `num` column)

The ID is what joins each plot back to your own records, so it is yours to set —
the **Plot IDs** tab offers three ways in, and you can mix them:

- **Number the whole grid** — pick an order (*rows / columns*, straight or
  **snaking**), a **start** value and a **step**, then *Number them*. One click
  for `1…N`, or `101, 111, 121…`, or a serpentine walk that matches how the
  trial was drilled.
- **Paste IDs from Excel** — copy a column of IDs and press *Paste into the ID
  column* (or **Ctrl+V** in the table). They fill downwards from the first
  selected row; a second and third copied column land in `variant` and `rep`.
- **Click plots in your own order** — set *Next ID*, switch **Number by
  clicking** on, then click plots on the map in whatever order your trial uses.
  Each click stamps the next ID and counts on by *step*. This is the one-click-
  per-plot answer for a layout no pattern describes. **Ctrl+Z** undoes a
  mis-click.

You can also double-click any **ID** cell in the table and type it directly.

**Duplicate IDs are caught as you go.** Two plots sharing an ID would make two
rows of the results CSV look like the same plot, so clashing plots are outlined
red on the map, highlighted in the table, and counted in a warning line under
it — with a *Select the clashing plots* button that jumps straight to them.
Exporting with duplicates or blanks asks first. IDs left blank export as NULL
rather than `0`.

#### Variant & replication (the plot table)

The panel to the right of the map lists every plot — `num`, `col`, `row`,
`variant`, `rep` — and is the fastest way to get a trial design onto the
polygons. `variant` is the genotype / treatment, `rep` the replication or block.
Selecting a row highlights that plot on the map, and clicking a plot jumps to
its row. Three ways to fill them in, mix as you like:

- **Pattern** — the usual block layout in one click: *rep = block of N rows*
  (or columns), *variant = 1…N cycling per rep*. Tick only **set rep** to lay
  down the replication strips and paint the variants yourself. So a 6 × 9 field
  with `rep = block of 3 rows` gives three reps of 18 plots, each running
  variants 1–18.
- **Typing** — double-click a `variant`/`rep` cell (or press **Enter** on a
  row): **Enter** commits and drops down a row, **Tab** moves across, **Esc**
  cancels. Select rows and **Fill selected rows** to set them all at once, or
  **Ctrl+V** to paste a column of values straight from Excel (starting at the
  first selected row, into the column you last clicked; a second tab-separated
  column lands in `rep`).
- **Painting** — type the values, switch **Paint on map** on, then click plots
  to stamp them; drag across several to do a whole strip. An empty box leaves
  that column untouched.

Set **Map labels** to `variant`, `rep` or `variant / rep` and tick **colour by
variant** to check a randomisation at a glance. Assignments survive a grid
rebuild as long as the rows/columns don't change; when the numbering would shift
you're asked before they're cleared. Unassigned plots export as NULL rather than
`0`, so a gap stays visible instead of turning into a real variant.

### Output CSV

One row per polygon (per raster, in batch mode). Columns:

```
num, col, row, variant, rep, source_raster, Red_mean, Red_std, ..., NIR_mean, ..., NDVI_mean, NDVI_median, ...
```

Naming is `<Band>_<stat>` and `<Index>_<stat>`.

### Sensors (band order)

Built in:

| Sensor | Bands (in order) |
|--------|------------------|
| **MicaSense Altum** | 1 Blue, 2 Green, 3 Red, 4 Red Edge, 5 NIR, 6 LWIR |
| **DJI M3M** | 1 Green, 2 Red, 3 Red Edge, 4 NIR |

To **add a camera** (e.g. Agrocam NDVI once you confirm its band order), the
easy way is **Add / edit camera…** next to the sensor dropdown: give it a name,
set the number of bands, and pick a role (Blue, Green, Red, RedEdge, NIR, LWIR)
for each band. It's saved to `sensors.json` next to the `.exe` and its bands
become usable in index formulas immediately. You can also edit that JSON by hand:

```json
{
  "Agrocam NDVI": {
    "bands": { "1": "NIR", "2": "Green", "3": "Blue" },
    "default_nodata": -10000.0
  }
}
```

Band role tokens understood by formulas: `Blue, Green, Red, RedEdge, NIR, LWIR`.

### Indices

The built-in library (`indices.json`) includes NDVI, GNDVI, NDRE, SAVI, OSAVI,
MSAVI, EVI2, EVI, ARVI, SR, DVI, GCI, CIrededge, MCARI, TCARI, LCI, PSRI, NDWI,
GLI, VARI, ExG, TGI, RGBVI, NGRDI, GRVI, NDREI.

Formulas use the band tokens above plus `+ - * / **`, parentheses, and the
functions `sqrt, abs, exp, log, log10, min, max, clip`. Custom indices you add in
the app are saved to `indices.json` and reload on next launch.

Indices are computed **per pixel** inside each polygon and then aggregated, and
non-finite values (e.g. division by zero) are dropped before statistics.

---

## Building the .exe

Requires Python 3.12 (64-bit). From this folder:

```bat
build.bat
```

This creates `.venv`, installs dependencies, and produces `dist\ZonalStats.exe`.
Ship `ZonalStats.exe` together with `sensors.json` and `indices.json` if you want
those editable on the target machine (otherwise the built-in defaults are used).

### Run from source (for development)

```bat
.venv\Scripts\python.exe app.py
```

### Test the engine

```bat
.venv\Scripts\python.exe tests\smoke_test.py
```

---

## Notes / differences from the old scripts

- The legacy `vic\*.py` scripts computed median/IQR/percentiles over `masked.data`,
  which still contained nodata and out-of-polygon pixels. ZonalStats computes
  **every** statistic only over valid in-polygon pixels.
- Shapefiles in a different CRS than the raster are reprojected automatically.
