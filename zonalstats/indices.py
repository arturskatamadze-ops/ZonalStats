"""
Vegetation index library.

Each index has a name, a formula (in band-role tokens), a human description and
a category. The built-in library is broad; an index is only *offered* in the GUI
when every band it needs exists in the selected sensor (see ``available_for``).

User-added indices are persisted to ``indices.json`` next to the .exe and merged
with the built-ins on startup, so custom indices survive restarts and ship with
the program.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from .formula import formula_dependencies, validate_formula, FormulaError
from .paths import config_path

INDICES_FILE = "indices.json"


@dataclass
class Index:
    name: str
    formula: str
    description: str = ""
    category: str = "Custom"

    def dependencies(self) -> set[str]:
        try:
            return formula_dependencies(self.formula)
        except FormulaError:
            return set()

    def available_for(self, roles: set[str]) -> bool:
        """True if every band this index needs is present in *roles*."""
        deps = self.dependencies()
        return bool(deps) and deps.issubset(roles)


# --- Built-in library --------------------------------------------------------
# Formulas reference roles: Blue, Green, Red, RedEdge, NIR, LWIR.
_BUILTIN: list[Index] = [
    # ---- Red / NIR broadband ----
    Index("NDVI", "(NIR - Red) / (NIR + Red)",
          "Normalized Difference Vegetation Index. General greenness/biomass.",
          "Red/NIR"),
    Index("SR", "NIR / Red",
          "Simple Ratio (RVI). NIR-to-Red ratio; sensitive at high biomass.",
          "Red/NIR"),
    Index("DVI", "NIR - Red",
          "Difference Vegetation Index.", "Red/NIR"),
    Index("SAVI", "1.5 * (NIR - Red) / (NIR + Red + 0.5)",
          "Soil Adjusted Vegetation Index (L=0.5). Reduces soil background.",
          "Red/NIR"),
    Index("OSAVI", "(NIR - Red) / (NIR + Red + 0.16)",
          "Optimized SAVI (L=0.16).", "Red/NIR"),
    Index("MSAVI", "(2*NIR + 1 - sqrt((2*NIR + 1)**2 - 8*(NIR - Red))) / 2",
          "Modified SAVI; self-adjusting soil factor.", "Red/NIR"),
    Index("EVI2", "2.5 * (NIR - Red) / (NIR + 2.4*Red + 1)",
          "Two-band Enhanced Vegetation Index (no blue band needed).",
          "Red/NIR"),

    # ---- Green / NIR ----
    Index("GNDVI", "(NIR - Green) / (NIR + Green)",
          "Green NDVI. More sensitive to chlorophyll than NDVI.", "Green"),
    Index("GCI", "(NIR / Green) - 1",
          "Green Chlorophyll Index (CIgreen).", "Green"),
    Index("GRVI", "(Green - Red) / (Green + Red)",
          "Green-Red Vegetation Index.", "Green"),
    Index("NDWI", "(Green - NIR) / (Green + NIR)",
          "Normalized Difference Water Index (McFeeters); water/moisture.",
          "Green"),

    # ---- Red Edge ----
    Index("NDRE", "(NIR - RedEdge) / (NIR + RedEdge)",
          "Normalized Difference Red Edge. Canopy N / chlorophyll, less saturating.",
          "RedEdge"),
    Index("CIrededge", "(NIR / RedEdge) - 1",
          "Red Edge Chlorophyll Index.", "RedEdge"),
    Index("LCI", "(NIR - RedEdge) / (NIR + Red)",
          "Leaf Chlorophyll Index.", "RedEdge"),
    Index("PSRI", "(Red - Green) / RedEdge",
          "Plant Senescence Reflectance Index.", "RedEdge"),
    Index("MCARI",
          "((RedEdge - Red) - 0.2*(RedEdge - Green)) * (RedEdge / Red)",
          "Modified Chlorophyll Absorption in Reflectance Index.", "RedEdge"),
    Index("TCARI",
          "3 * ((RedEdge - Red) - 0.2*(RedEdge - Green) * (RedEdge / Red))",
          "Transformed Chlorophyll Absorption in Reflectance Index.", "RedEdge"),
    Index("NDREI", "(RedEdge - Green) / (RedEdge + Green)",
          "Normalized Difference Red Edge Index (green-based).", "RedEdge"),

    # ---- Blue-band (needs Blue: Altum etc.) ----
    Index("EVI", "2.5 * (NIR - Red) / (NIR + 6*Red - 7.5*Blue + 1)",
          "Enhanced Vegetation Index; atmosphere/soil resistant.", "Blue/NIR"),
    Index("ARVI", "(NIR - (2*Red - Blue)) / (NIR + (2*Red - Blue))",
          "Atmospherically Resistant Vegetation Index.", "Blue/NIR"),

    # ---- RGB / visible only ----
    Index("GLI", "(2*Green - Red - Blue) / (2*Green + Red + Blue)",
          "Green Leaf Index (RGB).", "Visible"),
    Index("VARI", "(Green - Red) / (Green + Red - Blue)",
          "Visible Atmospherically Resistant Index (RGB).", "Visible"),
    Index("ExG", "2*Green - Red - Blue",
          "Excess Green index (RGB).", "Visible"),
    Index("TGI", "Green - 0.39*Red - 0.61*Blue",
          "Triangular Greenness Index (RGB).", "Visible"),
    Index("RGBVI", "(Green**2 - Red*Blue) / (Green**2 + Red*Blue)",
          "RGB Vegetation Index.", "Visible"),
    Index("NGRDI", "(Green - Red) / (Green + Red)",
          "Normalized Green-Red Difference Index (RGB).", "Visible"),
]


def builtin_indices() -> list[Index]:
    return [Index(i.name, i.formula, i.description, i.category) for i in _BUILTIN]


def load_indices() -> list[Index]:
    """Built-ins plus user indices from indices.json (user entries win by name)."""
    by_name: dict[str, Index] = {i.name: i for i in builtin_indices()}
    order = [i.name for i in builtin_indices()]
    path = config_path(INDICES_FILE)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            for raw in data:
                idx = Index(
                    name=str(raw["name"]).strip(),
                    formula=str(raw["formula"]).strip(),
                    description=str(raw.get("description", "")),
                    category=str(raw.get("category", "Custom")),
                )
                if idx.name and idx.formula:
                    if idx.name not in by_name:
                        order.append(idx.name)
                    by_name[idx.name] = idx
        except (json.JSONDecodeError, ValueError, KeyError, TypeError):
            pass
    return [by_name[name] for name in order]


def save_indices(indices: list[Index], path: str | None = None) -> str:
    """Persist the full index library to indices.json. Returns the path."""
    path = path or config_path(INDICES_FILE)
    data = [
        {"name": i.name, "formula": i.formula,
         "description": i.description, "category": i.category}
        for i in indices
    ]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return path


def add_or_update_index(indices: list[Index], idx: Index,
                        allowed_roles: set[str] | None = None) -> list[Index]:
    """Validate *idx* and insert/replace it (by name) into *indices*.

    Raises FormulaError if the formula is invalid. ``allowed_roles=None`` means
    validate against all known roles (so an index can be defined even if the
    current sensor lacks the band).
    """
    validate_formula(idx.formula, allowed_roles)
    out = [i for i in indices if i.name != idx.name]
    out.append(idx)
    return out
