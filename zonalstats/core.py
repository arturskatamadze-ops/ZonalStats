"""
Zonal statistics & vegetation-index extraction engine (rasterio + fiona).

For every polygon in the shapefile and every input raster, this masks the raster
to the polygon, computes the requested per-band statistics, evaluates each
selected vegetation index per-pixel, and writes one CSV row per (feature, raster).

This replaces the GDAL/OGR loop in the legacy scripts and fixes a latent bug in
them: median/IQR/percentile were computed over ``masked.data`` (which still
contains nodata and out-of-polygon pixels). Here every statistic is computed
only over valid in-polygon pixels (``masked`` compressed to finite values).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd
import fiona
import rasterio
from rasterio.crs import CRS
from rasterio.features import geometry_mask, geometry_window
from rasterio.warp import transform_geom
from rasterio.windows import Window, WindowError

from .formula import evaluate_formula
from .indices import Index
from .sensors import Sensor

# Statistics offered to the user. Each maps to a function over a 1-D float array
# of valid pixel values. Empty input yields NaN (or 0 for count).
ALL_STATS: list[str] = [
    "mean", "median", "std", "min", "max", "count",
    "range", "iqr", "p5", "p10", "p25", "p75", "p90", "p95", "cv",
]


def _stat(name: str, v: np.ndarray) -> float:
    if name == "count":
        return float(v.size)
    if v.size == 0:
        return float("nan")
    if name == "mean":
        return float(np.mean(v))
    if name == "median":
        return float(np.median(v))
    if name == "std":
        return float(np.std(v))
    if name == "min":
        return float(np.min(v))
    if name == "max":
        return float(np.max(v))
    if name == "range":
        return float(np.max(v) - np.min(v))
    if name == "iqr":
        return float(np.percentile(v, 75) - np.percentile(v, 25))
    if name == "cv":
        m = float(np.mean(v))
        return float(np.std(v) / m) if m != 0 else float("nan")
    if name.startswith("p"):
        return float(np.percentile(v, float(name[1:])))
    raise ValueError(f"Unknown statistic: {name}")


def compute_stats(values: np.ndarray, stats: list[str]) -> dict[str, float]:
    return {s: _stat(s, values) for s in stats}


@dataclass
class ExtractionConfig:
    raster_paths: list[str]
    shapefile: str
    output_csv: str
    sensor: Sensor
    bands: list[str]                 # band roles to output raw stats for
    indices: list[Index]             # indices to compute
    stats: list[str]                 # statistics applied to bands and indices
    attributes: list[str] = field(default_factory=list)  # shapefile fields to keep
    nodata: float | None = None      # override; None -> use sensor.default_nodata
    scale: float = 1.0               # reflectance scale (multiplies all bands)


ProgressCB = Callable[[float, str], None]


def _to_rio_crs(fiona_crs) -> CRS | None:
    if not fiona_crs:
        return None
    try:
        if hasattr(fiona_crs, "to_wkt"):
            return CRS.from_wkt(fiona_crs.to_wkt())
        return CRS.from_user_input(dict(fiona_crs))
    except Exception:
        try:
            return CRS.from_user_input(fiona_crs)
        except Exception:
            return None


def _load_features(shapefile: str):
    """Return (features, field_names, fiona_crs).

    features: list of (properties_dict, geometry_mapping).
    """
    feats = []
    with fiona.open(shapefile, "r") as src:
        crs = src.crs
        fields = list(src.schema["properties"].keys())
        for f in src:
            geom = f["geometry"]
            if geom is None:
                continue
            feats.append((dict(f["properties"]), dict(geom)))
    return feats, fields, crs


def _feature_pixels(ds, geom_mapping, needed_roles, sensor, nodata, scale):
    """Extract aligned valid-pixel arrays for *needed_roles* within one polygon.

    Returns dict role -> 1-D float array (same length for all roles), or None if
    the polygon does not overlap the raster / has no valid pixels.
    """
    try:
        win = geometry_window(ds, [geom_mapping])
    except (WindowError, ValueError):
        return None
    # Clip to the raster so the read window and its transform stay consistent
    # even when a polygon lies partly outside the orthomosaic.
    try:
        win = win.intersection(Window(0, 0, ds.width, ds.height))
    except (WindowError, ValueError):
        return None
    if win.width <= 0 or win.height <= 0:
        return None

    band_indexes = [sensor.bands[r] for r in needed_roles]
    arr = ds.read(indexes=band_indexes, window=win, masked=True,
                  boundless=False).astype(np.float64)
    if arr.size == 0:
        return None

    transform = ds.window_transform(win)
    inside = geometry_mask([geom_mapping], out_shape=(arr.shape[1], arr.shape[2]),
                           transform=transform, invert=True)

    # Combined validity: inside polygon, not masked in any band, finite, not nodata.
    band_mask = np.ma.getmaskarray(arr).any(axis=0)
    finite = np.isfinite(arr.data).all(axis=0)
    valid = inside & ~band_mask & finite
    if nodata is not None:
        valid &= (arr.data != nodata).all(axis=0)

    if not valid.any():
        return None

    out = {}
    for role, band_plane in zip(needed_roles, arr.data):
        out[role] = band_plane[valid] * scale
    return out


def run_extraction(cfg: ExtractionConfig, progress: ProgressCB | None = None) -> str:
    """Run the extraction and write a CSV. Returns the output CSV path."""

    def report(frac: float, msg: str) -> None:
        if progress:
            progress(frac, msg)

    sensor = cfg.sensor
    nodata = cfg.nodata if cfg.nodata is not None else sensor.default_nodata

    # Roles needed = selected raw bands plus every band any selected index uses.
    needed_roles: set[str] = set(cfg.bands)
    for idx in cfg.indices:
        needed_roles |= idx.dependencies()
    missing = needed_roles - sensor.role_set()
    if missing:
        raise ValueError(
            f"Sensor '{sensor.name}' has no band(s): {', '.join(sorted(missing))}. "
            "Check the selected sensor or remove indices/bands that need them."
        )
    needed_roles_ordered = [r for r in sensor.roles if r in needed_roles]

    report(0.02, f"Reading shapefile: {os.path.basename(cfg.shapefile)}")
    features, fields, shp_crs = _load_features(cfg.shapefile)
    if not features:
        raise ValueError("Shapefile contains no usable polygon features.")
    shp_crs_rio = _to_rio_crs(shp_crs)

    attrs = [a for a in cfg.attributes if a in fields]

    rows: list[dict] = []
    n_rasters = len(cfg.raster_paths)
    total_steps = max(1, n_rasters * len(features))
    step = 0

    for ri, rpath in enumerate(cfg.raster_paths):
        report(step / total_steps,
               f"Opening raster {ri + 1}/{n_rasters}: {os.path.basename(rpath)}")
        with rasterio.open(rpath) as ds:
            if ds.count < sensor.band_count:
                raise ValueError(
                    f"'{os.path.basename(rpath)}' has {ds.count} band(s) but sensor "
                    f"'{sensor.name}' expects {sensor.band_count}. Wrong sensor?"
                )
            # Reproject polygons to the raster CRS once per raster if needed.
            reproject = (shp_crs_rio is not None and ds.crs is not None
                         and shp_crs_rio != ds.crs)
            source_name = os.path.splitext(os.path.basename(rpath))[0]

            for props, geom in features:
                step += 1
                geom_r = transform_geom(shp_crs_rio, ds.crs, geom) if reproject else geom

                row: dict = {}
                for a in attrs:
                    row[a] = props.get(a)
                row["source_raster"] = source_name

                pix = _feature_pixels(ds, geom_r, needed_roles_ordered,
                                      sensor, nodata, cfg.scale)

                if pix is None:
                    # No overlap / no valid pixels: emit NaNs so the feature still
                    # appears in the output (count = 0).
                    for role in cfg.bands:
                        for s in cfg.stats:
                            row[f"{role}_{s}"] = 0.0 if s == "count" else float("nan")
                    for idx in cfg.indices:
                        for s in cfg.stats:
                            row[f"{idx.name}_{s}"] = 0.0 if s == "count" else float("nan")
                else:
                    for role in cfg.bands:
                        st = compute_stats(pix[role], cfg.stats)
                        for s in cfg.stats:
                            row[f"{role}_{s}"] = st[s]
                    for idx in cfg.indices:
                        vals = evaluate_formula(idx.formula, pix)
                        vals = vals[np.isfinite(vals)]
                        st = compute_stats(vals, cfg.stats)
                        for s in cfg.stats:
                            row[f"{idx.name}_{s}"] = st[s]

                rows.append(row)
                if step % 10 == 0 or step == total_steps:
                    report(step / total_steps,
                           f"Processed {step}/{total_steps} feature-rasters")

    report(0.98, "Writing CSV…")
    df = pd.DataFrame(rows)

    # Column order: attributes, source_raster, then bands, then indices.
    ordered = list(attrs) + ["source_raster"]
    for role in cfg.bands:
        ordered += [f"{role}_{s}" for s in cfg.stats]
    for idx in cfg.indices:
        ordered += [f"{idx.name}_{s}" for s in cfg.stats]
    ordered = [c for c in ordered if c in df.columns]
    df = df[ordered]

    os.makedirs(os.path.dirname(os.path.abspath(cfg.output_csv)), exist_ok=True)
    df.to_csv(cfg.output_csv, index=False)
    report(1.0, f"Done. Wrote {len(df)} rows to {cfg.output_csv}")
    return cfg.output_csv
