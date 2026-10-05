"""
Plot-grid geometry for the Plot Designer.

A field trial is almost always a regular array of rectangular plots, possibly
rotated to match the planting direction, with alley gaps between plots. This
module turns a small set of parameters into georeferenced plot polygons and
writes them to a shapefile that the extraction engine (``core.run_extraction``)
can consume directly.

Everything here is pure geometry in the raster's CRS (map units, usually
metres). The UI in ``designer.py`` is responsible for letting the user pick the
parameters interactively; this module has no Tkinter dependency so it can be
unit-tested on its own.

Coordinate convention
---------------------
* ``origin`` is the map-coordinate (x, y) of the top-left corner of plot
  (col 0, row 0) *before* rotation.
* Columns increase toward the east (local +x); rows increase toward the south
  (local -y), which matches how a field reads top-to-bottom on screen.
* ``angle_deg`` rotates the whole grid counter-clockwise about ``origin``.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

from shapely.geometry import Polygon, mapping


@dataclass
class GridSpec:
    """Parameters that define a regular array of plots, in map units."""

    origin: tuple[float, float]      # (x, y) of plot (0,0) top-left, pre-rotation
    plot_w: float                    # plot width  (east extent of one plot)
    plot_h: float                    # plot height (north-south extent of one plot)
    gap_x: float = 0.0               # alley gap between columns
    gap_y: float = 0.0               # alley gap between rows
    n_cols: int = 1
    n_rows: int = 1
    angle_deg: float = 0.0           # CCW rotation about origin

    @property
    def pitch_x(self) -> float:
        return self.plot_w + self.gap_x

    @property
    def pitch_y(self) -> float:
        return self.plot_h + self.gap_y


@dataclass
class Plot:
    """One plot: identity attributes plus its polygon corners in map units.

    ``variant`` (genotype / treatment) and ``rep`` (replication / block) are the
    trial-design attributes. They start unassigned (``None``) and are filled in
    from the designer's plot table — by pattern, by typing, or by painting on the
    map — then written to the shapefile alongside num/col/row.
    """

    num: int
    col: int                         # 0-based column (west->east)
    row: int                         # 0-based row (north->south)
    corners: list[tuple[float, float]]  # 4 corners, in map units
    variant: int | None = None       # genotype / treatment number
    rep: int | None = None           # replication / block number

    def polygon(self) -> Polygon:
        return Polygon(self.corners)


def _rotate(x: float, y: float, cos_t: float, sin_t: float,
            ox: float, oy: float) -> tuple[float, float]:
    """Rotate (x, y) about (ox, oy) by the angle whose cos/sin are given."""
    dx, dy = x - ox, y - oy
    return (ox + dx * cos_t - dy * sin_t,
            oy + dx * sin_t + dy * cos_t)


def build_plots(spec: GridSpec) -> list[Plot]:
    """Materialise ``spec`` into a list of :class:`Plot` (row-major order).

    ``num`` is a plain 1-based reading-order placeholder: the real plot IDs are
    the user's own, applied afterwards with :func:`renumber` or typed/pasted in
    the designer. ``col``/``row`` are always the true 0-based grid position
    whatever the IDs end up being, so they stay meaningful for downstream joins.
    """
    ox, oy = spec.origin
    theta = math.radians(spec.angle_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)

    plots: list[Plot] = []
    num = 0
    for row in range(spec.n_rows):
        for col in range(spec.n_cols):
            num += 1
            # Local axis-aligned rectangle (before rotation), east +x / south -y.
            x0 = ox + col * spec.pitch_x
            y0 = oy - row * spec.pitch_y
            x1 = x0 + spec.plot_w
            y1 = y0 - spec.plot_h
            raw = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            corners = [_rotate(x, y, cos_t, sin_t, ox, oy) for x, y in raw]
            plots.append(Plot(num=num, col=col, row=row, corners=corners))
    return plots


# --- Grid position inference (for plots that didn't come from build_plots) ----

def _dominant_angle(plots: list) -> float:
    """Rotation of the plot array, in radians, modulo a quarter turn.

    Every polygon edge votes; because a rectangle's edges are 90° apart, the
    votes are folded into [0, pi/2) and averaged as a circular mean at 4x angle.
    A field digitised at 12° therefore yields ~0.209 rad regardless of which
    corner each ring happens to start at.
    """
    sin_sum = cos_sum = 0.0
    for p in plots:
        c = p.corners
        for a, b in zip(c, c[1:] + c[:1]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            if math.hypot(dx, dy) < 1e-12:
                continue
            ang = math.atan2(dy, dx)
            sin_sum += math.sin(4 * ang)
            cos_sum += math.cos(4 * ang)
    if sin_sum == 0.0 and cos_sum == 0.0:
        return 0.0
    return math.atan2(sin_sum, cos_sum) / 4.0


def _cluster_1d(values: list[float], tol: float) -> list[int]:
    """Index each value into a run of values separated by gaps larger than *tol*."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0] * len(values)
    band = 0
    prev = values[order[0]]
    for pos, i in enumerate(order):
        if pos and values[i] - prev > tol:
            band += 1
        out[i] = band
        prev = values[i]
    return out


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def infer_grid_positions(plots: list) -> list:
    """Fill 0-based ``col``/``row`` from where the plots actually sit. Mutates.

    Used for shapefiles digitised elsewhere (QGIS), which carry geometry but
    often no grid position. Centroids are rotated into the field's own frame
    (see :func:`_dominant_angle`) and then clustered into bands: rows run
    north->south, columns west->east, with a plot's own size setting the gap
    tolerance. A field rotated by more than ~45° may come back with rows and
    columns swapped — harmless, since the rep pattern can key on either axis.
    """
    if not plots:
        return plots
    theta = -_dominant_angle(plots)
    cos_t, sin_t = math.cos(theta), math.sin(theta)

    cx_r, cy_r, widths, heights = [], [], [], []
    for p in plots:
        rot = [(x * cos_t - y * sin_t, x * sin_t + y * cos_t) for x, y in p.corners]
        xs = [a for a, _ in rot]
        ys = [b for _, b in rot]
        cx_r.append(sum(xs) / len(xs))
        cy_r.append(sum(ys) / len(ys))
        widths.append(max(xs) - min(xs))
        heights.append(max(ys) - min(ys))

    rows = _cluster_1d([-y for y in cy_r], max(_median(heights) * 0.5, 1e-9))
    cols = _cluster_1d(cx_r, max(_median(widths) * 0.5, 1e-9))
    for p, r, c in zip(plots, rows, cols):
        p.row, p.col = r, c
    return plots


# --- Trial-design attributes -------------------------------------------------

REP_AXES = ("row", "col")


def assign_pattern(plots: list, rep_axis: str = "row", block_size: int = 1,
                   n_variants: int = 0, variant_start: int = 1,
                   set_rep: bool = True, set_variant: bool = True) -> list:
    """Fill ``rep``/``variant`` for a regular block layout. Mutates *plots*.

    ``rep`` is the 1-based index of the block of *block_size* rows (or columns,
    per *rep_axis*) the plot falls in — the usual RCBD arrangement where each
    replication occupies a contiguous strip of the field.

    ``variant`` counts through the plots of each replication in ``num`` order,
    restarting at *variant_start* for every new rep, and wrapping after
    *n_variants* values. ``n_variants <= 0`` means "don't wrap", i.e. the rep's
    plots are simply numbered through.

    Set *set_rep* / *set_variant* to False to leave that column untouched — e.g.
    to lay down the replication strips and then paint the variants by hand.
    """
    if rep_axis not in REP_AXES:
        raise ValueError(f"rep_axis must be one of {REP_AXES}, got {rep_axis!r}")
    if block_size < 1:
        raise ValueError("Block size must be at least 1.")

    counts: dict[int, int] = {}
    for p in sorted(plots, key=lambda q: q.num):
        axis = p.row if rep_axis == "row" else p.col
        rep = axis // block_size + 1
        i = counts.get(rep, 0)
        counts[rep] = i + 1
        if set_rep:
            p.rep = rep
        if set_variant:
            p.variant = variant_start + (i % n_variants if n_variants > 0 else i)
    return plots


# --- Plot IDs ----------------------------------------------------------------

# Traversal orders offered for automatic numbering. Trials are labelled every
# which way in practice, so the four that actually turn up are all supported:
# straight along rows or columns, and the two snake (boustrophedon) variants
# that follow how a plot harvester or a person walking the field actually moves.
NUMBER_ORDERS = ("rowwise", "serpentine", "colwise", "col_serpentine")


def renumber(plots: list, order: str = "rowwise", start: int = 1,
             step: int = 1) -> list:
    """Assign ``num`` to every plot by walking the grid in *order*. Mutates.

    ``start``/``step`` exist because a plot ID is rarely just 1..N: trials are
    routinely numbered from 101, or in hundreds per block, and re-deriving that
    by hand for a few hundred plots is exactly the tedium this is here to avoid.
    ``col``/``row`` are untouched — they stay the true grid position whatever the
    numbering says.
    """
    if not plots:
        return plots
    if step == 0:
        raise ValueError("Step cannot be zero.")
    if order in ("rowwise", "serpentine"):
        major, minor = (lambda p: p.row), (lambda p: p.col)
    elif order in ("colwise", "col_serpentine"):
        major, minor = (lambda p: p.col), (lambda p: p.row)
    else:
        raise ValueError(f"Unknown numbering order: {order!r}")
    snake = order in ("serpentine", "col_serpentine")

    bands: dict = {}
    for p in plots:
        bands.setdefault(major(p), []).append(p)

    num = start
    for position, key in enumerate(sorted(bands)):
        band = sorted(bands[key], key=minor)
        if snake and position % 2 == 1:
            band.reverse()
        for p in band:
            p.num = num
            num += step
    return plots


def id_report(plots: list) -> tuple[dict, int]:
    """Return ``({duplicated id: how many plots share it}, plots with no id)``.

    A duplicated plot ID silently corrupts the join back to the trial's own
    records — two rows in the results CSV claiming to be the same plot — so the
    designer surfaces this before the shapefile is ever written.
    """
    counts: dict = {}
    missing = 0
    for p in plots:
        num = getattr(p, "num", None)
        if num is None:
            missing += 1
        else:
            counts[num] = counts.get(num, 0) + 1
    return {k: v for k, v in counts.items() if v > 1}, missing


# --- Shapefile output --------------------------------------------------------

# Properties written for every plot. Field names match what the extraction
# engine and README expect (num / col / row), so designed plots feed straight in;
# variant / rep carry the trial design through to the results CSV.
_SCHEMA = {
    "geometry": "Polygon",
    "properties": {"num": "int", "col": "int", "row": "int",
                   "variant": "int", "rep": "int"},
}


def write_shapefile(plots: list, path: str, crs_wkt: str | None) -> str:
    """Write *plots* to a polygon shapefile at *path*. Returns the path.

    ``crs_wkt`` should be the orthomosaic's CRS as WKT so the plots line up with
    the raster in QGIS and during extraction. ``num`` is written 1-based as built;
    ``col``/``row`` are written 1-based to match how trial layouts are labelled.
    Unassigned ``variant``/``rep`` are written as NULL rather than 0, so a missing
    assignment stays visibly missing instead of masquerading as variant 0.
    """
    import fiona
    from fiona.crs import CRS as FionaCRS

    if not plots:
        raise ValueError("No plots to write.")

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    crs = FionaCRS.from_wkt(crs_wkt) if crs_wkt else None

    with fiona.open(path, "w", driver="ESRI Shapefile", schema=_SCHEMA, crs=crs) as dst:
        for p in plots:
            dst.write({
                "geometry": mapping(p.polygon()),
                "properties": {"num": p.num, "col": p.col + 1, "row": p.row + 1,
                               "variant": getattr(p, "variant", None),
                               "rep": getattr(p, "rep", None)},
            })
    return path


# --- Shapefile input ---------------------------------------------------------

def _as_int(value) -> int | None:
    """Attribute value -> int, or None when absent / blank / not a number."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _largest_ring(geom) -> list[tuple[float, float]]:
    """Exterior ring of *geom* (the biggest part of a MultiPolygon), unclosed."""
    if not geom:
        return []
    gtype = geom.get("type")
    if gtype == "Polygon":
        rings = [geom["coordinates"][0]] if geom["coordinates"] else []
    elif gtype == "MultiPolygon":
        rings = [part[0] for part in geom["coordinates"] if part]
    else:
        return []

    best, best_area = [], -1.0
    for ring in rings:
        pts = [(float(c[0]), float(c[1])) for c in ring]
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]                        # drop the closing duplicate
        if len(pts) < 3:
            continue
        area = abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1)
                       in zip(pts, pts[1:] + pts[:1]))) / 2
        if area > best_area:
            best, best_area = pts, area
    return best


def read_shapefile(path: str, target_crs_wkt: str | None = None) -> tuple[list, list[str]]:
    """Load plot polygons from an existing shapefile. Returns (plots, notes).

    Anything with polygons works — including plots digitised in QGIS. Attributes
    are matched case-insensitively: ``num``, ``col``, ``row`` (1-based on disk,
    as :func:`write_shapefile` writes them), ``variant`` and ``rep``. Whatever is
    missing is derived — grid positions from the geometry itself
    (:func:`infer_grid_positions`) and ``num`` in reading order — so the designer
    always has a complete, well-ordered plot list to assign against.

    Geometry is reprojected into *target_crs_wkt* (the orthomosaic's CRS) when
    the two differ, so imported plots land on the imagery. *notes* describes
    anything inferred or skipped, for the caller to surface to the user.
    """
    import fiona
    from rasterio.crs import CRS as RioCRS
    from rasterio.warp import transform_geom

    notes: list[str] = []
    with fiona.open(path, "r") as src:
        src_crs_wkt = src.crs.to_wkt() if src.crs else None
        features = [(dict(f["properties"]), dict(f["geometry"]) if f["geometry"] else None)
                    for f in src]
    if not features:
        raise ValueError("The shapefile contains no features.")

    reproject = False
    if target_crs_wkt:
        if not src_crs_wkt:
            notes.append("the shapefile has no CRS, so it was taken to already "
                         "match the orthomosaic")
        else:
            src_crs = RioCRS.from_wkt(src_crs_wkt)
            dst_crs = RioCRS.from_wkt(target_crs_wkt)
            reproject = src_crs != dst_crs
            if reproject:
                notes.append("geometry was reprojected to the orthomosaic's CRS")

    plots: list[Plot] = []
    skipped = 0
    need_positions = False
    for props, geom in features:
        if reproject:
            geom = transform_geom(src_crs, dst_crs, geom) if geom else None
        corners = _largest_ring(geom)
        if not corners:
            skipped += 1
            continue
        low = {str(k).lower(): v for k, v in props.items()}
        col, row = _as_int(low.get("col")), _as_int(low.get("row"))
        if col is None or row is None:
            need_positions = True
        plots.append(Plot(
            num=_as_int(low.get("num")) or 0,
            col=max(0, (col or 1) - 1),
            row=max(0, (row or 1) - 1),
            corners=corners,
            variant=_as_int(low.get("variant")),
            rep=_as_int(low.get("rep")),
        ))

    if not plots:
        raise ValueError("No polygon features found in the shapefile.")
    if skipped:
        notes.append(f"{skipped} non-polygon feature(s) were skipped")
    if need_positions:
        infer_grid_positions(plots)
        notes.append("col/row were inferred from the plot positions")

    nums = [p.num for p in plots]
    if any(n <= 0 for n in nums) or len(set(nums)) != len(nums):
        for n, p in enumerate(sorted(plots, key=lambda q: (q.row, q.col)), start=1):
            p.num = n
        notes.append("plots were renumbered 1…N in reading order")
    plots.sort(key=lambda p: p.num)
    return plots, notes
