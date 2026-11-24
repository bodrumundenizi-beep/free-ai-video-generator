"""Fetch TAEF1, the tiny picture decoder, into vendor/taef1.

Run:  python packaging/fetch_taef1.py

The AI pictures program turns each finished drawing into a picture with a decoder. TAEF1 does
that 3 times faster than the full one (1.1 -> 0.4 s on Fast, 2.6 -> 0.8 s on Best, RTX 4060)
and looked the same on all 7 looks (tested 2026-11-22). 9.8 MB, MIT, by Ollin Boer Bohan;
downloaded from a pinned Hugging Face commit and kept only if its SHA-256 matches.

vendor/ is not committed; the release workflow runs this before PyInstaller, and
packaging/ai_video_studio.spec copies vendor/taef1 into the bundle.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import urllib.request

COMMIT = "b1b2d00e9e440cfbf3dedb34266864da86016ceb"
URL = (f"https://huggingface.co/madebyollin/taef1/resolve/{COMMIT}/"
       "diffusion_pytorch_model.safetensors")
SHA256 = "47a6c2bff850da04b267cab70fe3553fef57255eb9a8e76852baa0a87850e54d"
LICENCE = ("LICENSE-TAESD-MIT.txt", "https://raw.githubusercontent.com/madebyollin/taesd/"
           "fdbae2df989db95761f482efb8977e1182fb68d2/LICENSE")

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
DEST = os.path.join(ROOT, "vendor", "taef1")
FILE = "taef1.safetensors"


def bundle_files() -> list[tuple[str, str]]:
    """(full path, folder inside the bundle) for the decoder and its licence."""
    if not os.path.isfile(os.path.join(DEST, FILE)):
        raise SystemExit("vendor/taef1 is missing. Run: python packaging/fetch_taef1.py")
    return [(os.path.join(DEST, name), "taef1") for name in (FILE, LICENCE[0])]


def main() -> int:
    target = os.path.join(DEST, FILE)
    if os.path.isfile(target):
        with open(target, "rb") as fh:
            if hashlib.sha256(fh.read()).hexdigest() == SHA256:
                print("TAEF1 is already in vendor/taef1")
                return 0
    print(f"Downloading {URL}")
    with urllib.request.urlopen(URL, timeout=120) as response:
        data = response.read()
    found = hashlib.sha256(data).hexdigest()
    if found != SHA256:
        print(f"SHA-256 mismatch for TAEF1:\n  expected {SHA256}\n  got      {found}",
              file=sys.stderr)
        return 1
    shutil.rmtree(DEST, ignore_errors=True)
    os.makedirs(DEST)
    with open(target, "wb") as fh:
        fh.write(data)
    with urllib.request.urlopen(LICENCE[1], timeout=60) as response, \
            open(os.path.join(DEST, LICENCE[0]), "wb") as fh:
        fh.write(response.read())
    print(f"TAEF1: {len(data) / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
