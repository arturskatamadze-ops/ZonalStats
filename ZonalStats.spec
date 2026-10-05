# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for ZonalStats (one-file Windows GUI .exe).

rasterio, fiona and pyproj ship their own GDAL/PROJ DLLs and data directories;
collect_all() pulls those in so the bundled exe is self-contained and needs no
QGIS/OSGeo install on the target machine.

Build:  .venv\\Scripts\\pyinstaller ZonalStats.spec --noconfirm --clean
"""

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
# PIL is needed for the Plot Designer preview; collect_all pulls in the
# _imagingtk binary so PIL.ImageTk works inside the frozen Tk app.
for pkg in ("rasterio", "fiona", "pyproj", "shapely", "PIL"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Submodules PyInstaller's static analysis can miss (imported lazily by rasterio).
hiddenimports += [
    "rasterio.sample", "rasterio.vrt", "rasterio.control",
    "rasterio._features", "rasterio.features", "rasterio.warp", "rasterio.crs",
    "rasterio.windows", "fiona.schema",
    "affine", "PIL.ImageTk", "PIL._tkinter_finder",
]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "tkinter.test", "PyQt5", "PySide2"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ZonalStats",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,          # set True temporarily if you need to see tracebacks
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
