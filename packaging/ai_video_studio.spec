# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for AI Video Studio.

Build:  pyinstaller --noconfirm --clean packaging/ai_video_studio.spec
Output: dist/AIVideoStudio/AIVideoStudio.exe  (+ _internal/)

onedir, not onefile: the bundle is ~150 MB, most of it ffmpeg. onefile would
re-extract all of it into %TEMP% on every single launch - seconds of delay each
time, a fresh antivirus scan of ffmpeg each time, and a leaked temp folder if
the process is killed. Both delivery channels want a directory anyway: Inno
copies a tree, and the portable ZIP is that tree.
"""

import os

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
ICON = os.path.join(ROOT, "assets", "icon.ico")

a = Analysis(
    [os.path.join(ROOT, "src", "ai_video_studio.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=[],
    # The window icon, looked up at runtime by asset_path().
    datas=[(ICON, "assets")],
    hiddenimports=[
        # Imported inside a try/except in apply_window_effects(); PyInstaller's
        # scan does find it, but being explicit costs nothing.
        "pywinstyles",
        # edge_tts and moviepy are imported lazily inside functions.
        "edge_tts",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # None of these are used; excluding them saves ~40 MB.
        "matplotlib", "scipy", "pandas", "IPython", "jupyter", "notebook",
        "PyQt5", "PyQt6", "PySide2", "PySide6", "wx",
        "pytest", "sphinx",
        # moviepy.config calls find_dotenv() at import time, which walks the
        # filesystem upward for no benefit inside a bundle. It handles the
        # ImportError gracefully.
        "dotenv",
    ],
    noarchive=False,
    optimize=0,
)

# Deliberately NOT collected by hand:
#   - ffmpeg       : pyinstaller-hooks-contrib's hook-imageio_ffmpeg.py already
#                    does collect_data_files('imageio_ffmpeg', subdir="binaries")
#   - ctk themes   : hook-customtkinter.py already does collect_data_files()
# If you ever downgrade pyinstaller-hooks-contrib, re-add them here.

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AIVideoStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX is off on purpose. Packing ffmpeg.exe, python313.dll and the numpy
    # DLLs is the fastest route to a Defender/SmartScreen flag, and it costs
    # startup time on every launch. Not worth trading reputation for ~40 MB.
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=ICON,
    version=os.path.join(ROOT, "packaging", "version_info.txt"),
    contents_directory="_internal",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AIVideoStudio",
)
