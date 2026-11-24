"""Fetch Real-ESRGAN, which sharpens every AI picture after it is drawn, into vendor/realesrgan.

Run:  python packaging/fetch_esrgan.py

Its Vulkan build (one program for NVIDIA, AMD and Intel cards) is downloaded from
Real-ESRGAN's own GitHub release and only unpacked if its SHA-256 matches the one pinned
below. Only the program, its OpenMP library and the 1.2 MB realesr-animevideov3 x4 model
are kept (about 7.5 MB): it drew cleaner lines on the app's drawn looks than the 33 MB
x4plus model, which smeared faces (tested 2026-11-21). Real-ESRGAN is BSD-3-Clause, its
ncnn program MIT; both licences ship beside it.

vendor/ is not committed; the release workflow runs this before PyInstaller, and
packaging/ai_video_studio.spec copies vendor/realesrgan into the bundle.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import sys
import urllib.request
import zipfile

TAG = "v0.2.5.0"
URL = (f"https://github.com/xinntao/Real-ESRGAN/releases/download/{TAG}/"
       "realesrgan-ncnn-vulkan-20220424-windows.zip")
SHA256 = "abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d"
LICENCES = {
    "LICENSE-Real-ESRGAN-BSD-3-Clause.txt":
        f"https://raw.githubusercontent.com/xinntao/Real-ESRGAN/{TAG}/LICENSE",
    "LICENSE-Real-ESRGAN-ncnn-vulkan-MIT.txt":
        "https://raw.githubusercontent.com/xinntao/Real-ESRGAN-ncnn-vulkan/v0.2.0/LICENSE",
}

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
DEST = os.path.join(ROOT, "vendor", "realesrgan")
STAMP = os.path.join(DEST, ".version")
EXE = "realesrgan-ncnn-vulkan.exe"
KEEP = (EXE, "vcomp140.dll", "models/realesr-animevideov3-x4.bin",
        "models/realesr-animevideov3-x4.param")


def bundle_files() -> list[tuple[str, str]]:
    """(full path, folder inside the bundle) for everything in vendor/realesrgan."""
    if not os.path.isfile(os.path.join(DEST, EXE)):
        raise SystemExit("vendor/realesrgan is missing. Run: python packaging/fetch_esrgan.py")
    out = []
    for folder, _dirs, files in os.walk(DEST):
        inside = os.path.normpath(os.path.join("realesrgan", os.path.relpath(folder, DEST)))
        out += [(os.path.join(folder, name), inside) for name in sorted(files)
                if name != ".version"]
    return out


def main() -> int:
    if os.path.isfile(STAMP) and open(STAMP, encoding="utf-8").read().strip() == SHA256 \
            and os.path.isfile(os.path.join(DEST, EXE)):
        print(f"Real-ESRGAN {TAG} is already in vendor/realesrgan")
        return 0
    print(f"Downloading {URL}")
    with urllib.request.urlopen(URL, timeout=120) as response:
        data = response.read()
    found = hashlib.sha256(data).hexdigest()
    if found != SHA256:
        print(f"SHA-256 mismatch for Real-ESRGAN:\n  expected {SHA256}\n  got      {found}",
              file=sys.stderr)
        return 1
    shutil.rmtree(DEST, ignore_errors=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in KEEP:
            target = os.path.join(DEST, name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(archive.read(name))
    for name, url in LICENCES.items():
        with urllib.request.urlopen(url, timeout=60) as response, \
                open(os.path.join(DEST, name), "wb") as fh:
            fh.write(response.read())
    with open(STAMP, "w", encoding="utf-8") as fh:
        fh.write(SHA256)
    size = sum(os.path.getsize(path) for path, _inside in bundle_files())
    print(f"Real-ESRGAN {TAG}: {len(bundle_files())} files ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
