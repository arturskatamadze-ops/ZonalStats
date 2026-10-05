"""
Smoke test: synthesize a 4-band (DJI M3M) GeoTIFF + a 2-polygon shapefile,
run the extraction engine, and check the band stats and index math.

Run:  .venv\\Scripts\\python.exe tests\\smoke_test.py
"""

import os
import sys
import tempfile

import numpy as np
import rasterio
from rasterio.transform import from_origin
import fiona
from fiona.crs import from_epsg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zonalstats.core import ExtractionConfig, run_extraction
from zonalstats.sensors import load_sensors
from zonalstats.indices import load_indices
from zonalstats.grid import (GridSpec, assign_pattern, build_plots,
                             infer_grid_positions, read_shapefile, write_shapefile)

# Constant reflectance per band (M3M order: Green, Red, RedEdge, NIR).
GREEN, RED, REDEDGE, NIR = 0.30, 0.20, 0.50, 0.80
EXPECTED = {
    "NDVI": (NIR - RED) / (NIR + RED),            # 0.6
    "NDRE": (NIR - REDEDGE) / (NIR + REDEDGE),    # 0.2308
    "GNDVI": (NIR - GREEN) / (NIR + GREEN),       # 0.4545
}


def build_raster(path):
    width = height = 20
    transform = from_origin(0, 200, 10, 10)  # 10 m pixels, extent x:0..200 y:0..200
    data = np.zeros((4, height, width), dtype="float32")
    data[0] = GREEN
    data[1] = RED
    data[2] = REDEDGE
    data[3] = NIR
    with rasterio.open(path, "w", driver="GTiff", height=height, width=width,
                       count=4, dtype="float32", crs="EPSG:32629",
                       transform=transform, nodata=-10000.0) as ds:
        ds.write(data)


def build_shapefile(path):
    schema = {"geometry": "Polygon",
              "properties": {"num": "int", "col": "int", "row": "int"}}
    polys = [
        ({"num": 1, "col": 1, "row": 1},
         [[(20, 120), (60, 120), (60, 160), (20, 160), (20, 120)]]),
        ({"num": 2, "col": 2, "row": 1},
         [[(100, 40), (150, 40), (150, 90), (100, 90), (100, 40)]]),
    ]
    with fiona.open(path, "w", driver="ESRI Shapefile", crs=from_epsg(32629),
                    schema=schema) as dst:
        for props, coords in polys:
            dst.write({"geometry": {"type": "Polygon", "coordinates": coords},
                       "properties": props})


def test_grid(tmp, rpath):
    """Designer path: build a grid, write it, and run extraction through it.

    The synthetic raster spans map x:0..200, y:0..200 (EPSG:32629). We lay a 2x2
    grid of 40x40 m plots with 20 m alleys anchored at (20, 180); all four plots
    fall inside the raster, so each must yield valid pixels.
    """
    # geometry sanity
    spec = GridSpec(origin=(100.0, 200.0), plot_w=2.0, plot_h=5.0,
                    gap_x=0.3, gap_y=0.5, n_cols=2, n_rows=3)
    plots = build_plots(spec)
    assert len(plots) == 6
    assert plots[0].corners[0] == (100.0, 200.0)
    assert abs(plots[0].corners[1][0] - 102.0) < 1e-9      # +plot_w east
    assert abs(plots[0].corners[2][1] - 195.0) < 1e-9      # -plot_h south
    assert abs(plots[1].corners[0][0] - 102.3) < 1e-9      # col pitch 2.3
    # 90-deg rotation pivots about origin: corner (2,0) -> (0,2)
    rp = build_plots(GridSpec(origin=(0, 0), plot_w=2, plot_h=1, angle_deg=90))[0]
    assert abs(rp.corners[1][0]) < 1e-9 and abs(rp.corners[1][1] - 2) < 1e-9

    # end-to-end through the extraction engine
    grid_spec = GridSpec(origin=(20.0, 180.0), plot_w=40.0, plot_h=40.0,
                         gap_x=20.0, gap_y=20.0, n_cols=2, n_rows=2)
    grid_plots = build_plots(grid_spec)
    crs_wkt = rasterio.open(rpath).crs.to_wkt()
    gpath = os.path.join(tmp, "grid_plots.shp")
    write_shapefile(grid_plots, gpath, crs_wkt)

    sensor = load_sensors()["DJI M3M"]
    cfg = ExtractionConfig(
        raster_paths=[rpath], shapefile=gpath,
        output_csv=os.path.join(tmp, "grid_results.csv"), sensor=sensor,
        bands=["NIR"], indices=[], stats=["mean", "count"],
        attributes=["num", "col", "row"], nodata=-10000.0, scale=1.0,
    )
    out = run_extraction(cfg)
    import pandas as pd
    gdf = pd.read_csv(out)
    assert len(gdf) == 4, f"expected 4 designed plots, got {len(gdf)}"
    assert (gdf["NIR_count"] > 0).all(), "a designed plot had no valid pixels"
    assert abs(gdf["NIR_mean"].iloc[0] - NIR) < 1e-4
    print("grid designer -> extraction: 4 plots, all with valid pixels OK")


def test_variant_rep(tmp, rpath):
    """Trial-design attributes: pattern -> shapefile -> CSV, and back in again.

    A 4x6 grid with rep = a block of 2 rows gives 3 reps of 8 plots, each rep
    running variants 1..8. Those numbers have to survive the shapefile write,
    the extraction, and a re-import into the designer.
    """
    plots = build_plots(GridSpec(origin=(20.0, 190.0), plot_w=30.0, plot_h=25.0,
                                 gap_x=10.0, gap_y=5.0, n_cols=4, n_rows=6))
    assign_pattern(plots, rep_axis="row", block_size=2, n_variants=8)
    assert sorted({p.rep for p in plots}) == [1, 2, 3]
    assert [p.variant for p in plots[:9]] == [1, 2, 3, 4, 5, 6, 7, 8, 1]
    # rep 1 = rows 1-2 (plots 1-8), rep 2 starts at row 3, rep 3 at row 5
    assert (plots[7].rep, plots[8].rep, plots[16].rep) == (1, 2, 3)
    assert plots[8].variant == 1, "variant must restart with each rep"

    # variant only, leaving reps alone
    keep = [p.rep for p in plots]
    assign_pattern(plots, rep_axis="row", block_size=2, n_variants=4, set_rep=False)
    assert [p.rep for p in plots] == keep

    plots[0].variant = None                              # an unassigned plot
    crs_wkt = rasterio.open(rpath).crs.to_wkt()
    spath = os.path.join(tmp, "design_plots.shp")
    write_shapefile(plots, spath, crs_wkt)

    with fiona.open(spath) as src:
        assert list(src.schema["properties"]) == ["num", "col", "row", "variant", "rep"]
        recs = [dict(f["properties"]) for f in src]
    assert recs[0]["variant"] is None, "unassigned variant must stay NULL, not 0"
    assert recs[1]["variant"] == plots[1].variant and recs[1]["rep"] == plots[1].rep

    # designer re-import keeps identity and assignments
    back, notes = read_shapefile(spath, crs_wkt)
    assert notes == [], notes
    assert [(p.num, p.col, p.row, p.variant, p.rep) for p in back] == \
           [(p.num, p.col, p.row, p.variant, p.rep) for p in plots]

    # col/row inferred from geometry alone must match how the grid was built
    stripped = [type(p)(p.num, 0, 0, list(p.corners)) for p in plots]
    infer_grid_positions(stripped)
    assert [(p.col, p.row) for p in stripped] == [(p.col, p.row) for p in plots], \
        "inferred grid positions disagree with the built ones"

    # …and all the way through extraction into the CSV
    cfg = ExtractionConfig(
        raster_paths=[rpath], shapefile=spath,
        output_csv=os.path.join(tmp, "design_results.csv"),
        sensor=load_sensors()["DJI M3M"], bands=["NIR"], indices=[],
        stats=["mean", "count"], attributes=["num", "col", "row", "variant", "rep"],
        nodata=-10000.0, scale=1.0,
    )
    import pandas as pd
    df = pd.read_csv(run_extraction(cfg))
    assert list(df.columns)[:5] == ["num", "col", "row", "variant", "rep"]
    assert df["rep"].dropna().nunique() == 3
    assert df["variant"].isna().sum() == 1, "the unassigned plot should be blank"
    print("variant/rep: pattern -> shapefile -> CSV -> re-import OK "
          f"({len(df)} plots, 3 reps)")


def main():
    tmp = tempfile.mkdtemp(prefix="zonalstats_test_")
    rpath = os.path.join(tmp, "ortho.tif")
    spath = os.path.join(tmp, "plots.shp")
    opath = os.path.join(tmp, "results.csv")
    build_raster(rpath)
    build_shapefile(spath)

    sensor = load_sensors()["DJI M3M"]
    indices = [i for i in load_indices() if i.name in EXPECTED]

    cfg = ExtractionConfig(
        raster_paths=[rpath], shapefile=spath, output_csv=opath, sensor=sensor,
        bands=["Red", "NIR"], indices=indices,
        stats=["mean", "median", "std", "count"],
        attributes=["num", "col", "row"], nodata=-10000.0, scale=1.0,
    )

    out = run_extraction(cfg, progress=lambda f, m: print(f"  [{f:5.1%}] {m}"))

    import pandas as pd
    df = pd.read_csv(out)
    print("\nColumns:", list(df.columns))
    print(df.to_string(index=False))

    assert len(df) == 2, f"expected 2 rows, got {len(df)}"
    assert list(df["num"]) == [1, 2], "attribute 'num' not carried through correctly"
    assert (df["Red_count"] > 0).all(), "no valid pixels found in a polygon"

    tol = 1e-4
    for name, exp in EXPECTED.items():
        got = df[f"{name}_mean"].iloc[0]
        assert abs(got - exp) < tol, f"{name}: expected {exp:.4f}, got {got:.4f}"
    assert abs(df["NIR_mean"].iloc[0] - NIR) < tol
    assert abs(df["Red_mean"].iloc[0] - RED) < tol

    test_grid(tmp, rpath)
    test_variant_rep(tmp, rpath)

    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
