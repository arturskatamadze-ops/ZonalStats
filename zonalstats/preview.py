"""
Orthomosaic preview loader for the Plot Designer.

Orthomosaics are routinely several GB, so the designer never loads one at full
resolution. This module reads a *decimated* RGB preview (longest side capped)
together with the affine transform that maps preview-pixel coordinates to map
coordinates in the raster's CRS. The designer uses that affine to convert what
the user draws on screen into real georeferenced polygons, and back.

Returned image is a Pillow ``Image`` in mode "RGB" with a simple 2-98 percentile
contrast stretch per band so multispectral orthos (16-bit, odd band order) are
actually visible. No Tkinter here; the designer wraps the image in a PhotoImage.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Preview:
    image: "object"                 # PIL.Image.Image (RGB), decimated
    transform: "object"             # affine.Affine: preview-pixel (col,row) -> map (x,y)
    width: int                      # preview image width  (px)
    height: int                     # preview image height (px)
    full_width: int                 # source raster width  (px)
    full_height: int                # source raster height (px)
    crs_wkt: str | None             # source CRS as WKT (for the output shapefile)

    def pixel_to_map(self, px: float, py: float) -> tuple[float, float]:
        """Preview-pixel (col, row) -> map coordinate (x, y)."""
        x, y = self.transform * (px, py)
        return x, y

    def map_to_pixel(self, x: float, y: float) -> tuple[float, float]:
        """Map coordinate (x, y) -> preview-pixel (col, row)."""
        inv = ~self.transform
        px, py = inv * (x, y)
        return px, py


def _stretch(band: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """2-98 percentile contrast stretch of one band to uint8, honouring *mask*."""
    valid = band[mask] if mask is not None else band.ravel()
    valid = valid[np.isfinite(valid)]
    if valid.size == 0:
        return np.zeros(band.shape, dtype=np.uint8)
    lo, hi = np.percentile(valid, (2, 98))
    if hi <= lo:
        hi = lo + 1.0
    out = np.clip((band - lo) / (hi - lo), 0.0, 1.0)
    return (out * 255.0).astype(np.uint8)


def load_preview(path: str, rgb_bands: tuple[int, int, int] | None = None,
                 max_size: int = 1600) -> Preview:
    """Load a decimated RGB preview of *path*.

    ``rgb_bands`` are 1-based band numbers to map to (R, G, B); if ``None`` the
    first three bands are used. ``max_size`` caps the longest preview side.
    """
    import rasterio
    from affine import Affine
    from PIL import Image

    with rasterio.open(path) as ds:
        full_w, full_h = ds.width, ds.height
        scale = max(full_w, full_h) / float(max_size)
        scale = max(scale, 1.0)
        out_w = max(1, int(round(full_w / scale)))
        out_h = max(1, int(round(full_h / scale)))

        if rgb_bands is None:
            count = ds.count
            rgb_bands = (1, 2, 3) if count >= 3 else (1, 1, 1)
        rgb_bands = tuple(min(b, ds.count) for b in rgb_bands)

        arr = ds.read(indexes=list(rgb_bands), out_shape=(3, out_h, out_w),
                      masked=True).astype(np.float64)
        valid = ~np.ma.getmaskarray(arr).any(axis=0)

        chans = [_stretch(arr.data[i], valid) for i in range(3)]
        rgb = np.dstack(chans)
        # Render nodata/outside-footprint pixels black so the plot area is clear.
        rgb[~valid] = 0
        image = Image.fromarray(rgb, mode="RGB")

        # Transform for the decimated grid: source transform scaled by the actual
        # decimation factors (they differ slightly from `scale` after rounding).
        sx, sy = full_w / out_w, full_h / out_h
        transform = ds.transform * Affine.scale(sx, sy)
        crs_wkt = ds.crs.to_wkt() if ds.crs else None

    return Preview(image=image, transform=transform, width=out_w, height=out_h,
                   full_width=full_w, full_height=full_h, crs_wkt=crs_wkt)
