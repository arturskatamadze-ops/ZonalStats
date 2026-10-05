"""
Sensor registry: maps each camera/sensor to the band order of its orthomosaic.

A sensor maps 1-based raster band numbers to *band role* tokens used by index
formulas. Built-in defaults cover the cameras the user works with; an optional
``sensors.json`` next to the .exe can override or add sensors (e.g. to add the
Agrocam NDVI camera later) without rebuilding.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .paths import config_path

SENSORS_FILE = "sensors.json"


@dataclass
class Sensor:
    name: str
    # role -> 1-based band index, e.g. {"Blue": 1, "Green": 2, ...}
    bands: dict[str, int] = field(default_factory=dict)
    default_nodata: float = -10000.0

    @property
    def roles(self) -> list[str]:
        """Roles ordered by band number."""
        return [r for r, _ in sorted(self.bands.items(), key=lambda kv: kv[1])]

    @property
    def band_count(self) -> int:
        return len(self.bands)

    def role_set(self) -> set[str]:
        return set(self.bands.keys())


# --- Built-in defaults -------------------------------------------------------
# Altum band order (confirmed from the user's existing Altum script):
#   1 Blue, 2 Green, 3 Red, 4 Red Edge, 5 NIR, 6 LWIR (thermal)
# DJI M3M band order (confirmed by the user):
#   1 Green, 2 Red, 3 Red Edge, 4 NIR
_BUILTIN: dict[str, Sensor] = {
    "MicaSense Altum": Sensor(
        name="MicaSense Altum",
        bands={"Blue": 1, "Green": 2, "Red": 3, "RedEdge": 4, "NIR": 5, "LWIR": 6},
        default_nodata=-10000.0,
    ),
    "DJI M3M": Sensor(
        name="DJI M3M",
        bands={"Green": 1, "Red": 2, "RedEdge": 3, "NIR": 4},
        default_nodata=-10000.0,
    ),
}


def _serialise(sensor: Sensor) -> dict:
    return {
        "bands": {str(v): k for k, v in sensor.bands.items()},
        "default_nodata": sensor.default_nodata,
    }


def write_default_sensors_file(path: str | None = None) -> str:
    """Write the built-in sensors to a JSON file the user can edit. Returns path."""
    path = path or config_path(SENSORS_FILE)
    data = {name: _serialise(s) for name, s in _BUILTIN.items()}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return path


def save_sensors(sensors: dict[str, Sensor], path: str | None = None) -> str:
    """Persist custom / modified sensors to sensors.json. Returns the path.

    Built-in sensors that are unchanged are omitted, so the file stays small and
    only records what the user actually added or overrode. ``load_sensors`` then
    merges these back over the built-in defaults on next launch.
    """
    path = path or config_path(SENSORS_FILE)
    data: dict[str, dict] = {}
    for name, s in sensors.items():
        builtin = _BUILTIN.get(name)
        if builtin and builtin.bands == s.bands and builtin.default_nodata == s.default_nodata:
            continue  # unchanged built-in — no need to write it out
        data[name] = _serialise(s)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return path


def is_builtin(name: str) -> bool:
    return name in _BUILTIN


def _parse_sensor(name: str, raw: dict) -> Sensor:
    # JSON stores band-number -> role; invert to role -> band-number.
    bands_in = raw.get("bands", {})
    bands = {role: int(num) for num, role in bands_in.items()}
    return Sensor(name=name, bands=bands,
                  default_nodata=float(raw.get("default_nodata", -10000.0)))


def load_sensors() -> dict[str, Sensor]:
    """Built-in sensors merged with (overridden by) sensors.json if present."""
    sensors = {name: Sensor(name, dict(s.bands), s.default_nodata)
               for name, s in _BUILTIN.items()}
    path = config_path(SENSORS_FILE)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            for name, raw in data.items():
                sensors[name] = _parse_sensor(name, raw)
        except (json.JSONDecodeError, ValueError, KeyError):
            # A broken user file should not crash the app; fall back to builtins.
            pass
    return sensors
