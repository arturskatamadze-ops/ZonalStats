# ZonalStats

**Draw your trial plots on a drone orthomosaic and get per-plot reflectance and
vegetation-index statistics in a CSV. One Windows program, no GIS software needed.**

You lay out the field trial's plot grid directly on the image, tell it which
variant and replication each plot is, pick the indices you want, and press Run.

## Download

Get **`ZonalStats.exe`** from the
[latest release](https://github.com/arturskatamadze-ops/ZonalStats/releases/latest).
Double-click to run. Nothing else to install, since Python, GDAL and PROJ are bundled inside.

## How it works

1. **Open your orthomosaic** (`.tif`, e.g. from Agisoft Metashape). The camera
   and band order are detected from the band count.
2. **Draw the plots in the app.** Click **Design / edit plots…**, set plot size,
   rows, columns, gaps and rotation, and drop the grid onto the field. Drag,
   nudge and delete plots until it matches what was sown.
3. **Fill in the trial layout.** Give each plot its ID, variant and replication,
   either with one click for a standard block design, by pasting from Excel, or by
   clicking plots in your own order.
4. **Choose what to extract** (bands, statistics, indices) and press **Run**.
   You get one CSV row per plot.

## Plot Designer: polygons and trial layout in one place

- **Plot grid on the image.** Plot width and height, rows, columns, gaps and
  rotation are all in metres. Place the top-left plot with a click, line it up
  with the rotation nudges, then fine-tune single plots with drag or arrow keys.
  Ctrl+Z undoes anything.
- **Plot IDs.** You can number the whole grid by rows or columns, straight or
  snaking, with any start and step. You can also paste a column of IDs from
  Excel, or click plots in the order your trial uses. Duplicate IDs are flagged
  in red before you export.
- **Variant and replication.**
  - *Pattern:* one click for the usual design, e.g. "rep = block of 3 rows,
    variants 1–18 per rep".
  - *Typing / pasting:* fill the plot table directly, or paste from Excel.
  - *Painting:* click or drag across plots on the map to stamp values.
  - Label the map by variant or rep, and colour by variant, to check the
    randomisation at a glance.
- **Brightness slider** for dark or washed-out imagery. It only changes the
  picture, never the extracted values.
- **Export** the plots as a shapefile (`num`, `col`, `row`, `variant`, `rep`) in
  the image's coordinate system. An existing shapefile can also be imported
  and re-labelled.

## What you get

One row per plot (per image, if you process a whole folder of flights at once):

```
num, col, row, variant, rep, source_raster, Red_mean, ..., NIR_mean, ..., NDVI_mean, NDVI_median, ...
```

- **Statistics:** mean, median, std, min, max, count, range, IQR, percentiles, CV,
  computed only over valid pixels inside each plot.
- **Indices:** NDVI, GNDVI, NDRE, SAVI, OSAVI, MSAVI, EVI, EVI2, ARVI, SR, DVI,
  GCI, CIrededge, MCARI, TCARI, LCI, PSRI, NDWI, GLI, VARI, ExG, TGI, RGBVI,
  NGRDI, GRVI, NDREI. You can add your own formula in the app.
- **Cameras:** MicaSense Altum and DJI M3M are built in. You can add another
  camera in the app by assigning a role (Blue, Green, Red, RedEdge, NIR, LWIR)
  to each band.

The app checks every input as you go, previews the CSV columns before you run,
and remembers your choices for the next flight. Press **F1** in the app for the
quick guide.

---

## For developers

Requires Python 3.12 (64-bit) on Windows.

```bat
build.bat
```

This creates `.venv`, installs `requirements.txt`, and builds `dist\ZonalStats.exe`.

Run from source, or run the engine smoke test:

```bat
.venv\Scripts\python.exe app.py
.venv\Scripts\python.exe tests\smoke_test.py
```

**Releasing:** push a version tag (e.g. `git tag v1.0.0 && git push origin v1.0.0`).
GitHub Actions then builds the `.exe` and attaches it to a release for that tag.

## License

[MIT](LICENSE). You are free to use, change and share ZonalStats; please keep the copyright notice.
