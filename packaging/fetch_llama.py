"""Fetch llama.cpp, the program that runs the Smart writer's model, into vendor/llama.

Run:  python packaging/fetch_llama.py

The Windows build is downloaded from llama.cpp's own GitHub releases and is
only unpacked if its SHA-256 matches the one pinned below, so a release build
can never pick up a different binary than the one that was tested. vendor/ is
not committed; the release workflow runs this before PyInstaller, and
packaging/ai_video_studio.spec copies what the app needs into the bundle.

To move to a newer llama.cpp: change TAG and SHA256 together, run this, and run
the Smart writer once from source before releasing.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import sys
import urllib.request
import zipfile

TAG = "b11503"
# The Vulkan build: runs on the processor like the plain one, and can also use an
# NVIDIA, AMD or Intel graphics card when the user turns that on.
ASSET = f"llama-{TAG}-bin-win-vulkan-x64.zip"
URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{TAG}/{ASSET}"
SHA256 = "aacab515e72c4c5bbfd8d2b54de5c08ff23848245727f5667aa2a854bc1617ab"

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
DEST = os.path.join(ROOT, "vendor", "llama")
STAMP = os.path.join(DEST, ".version")
SERVER = "llama-server.exe"


def needed(name: str) -> bool:
    """Whether the app's bundle needs this file of the llama.cpp build.

    The server, the libraries it loads (one ggml-cpu-*.dll per processor
    family; llama.cpp picks at start), and the licence files. The other
    command-line tools and their *-impl.dll halves are left out.
    """
    lower = name.lower()
    if lower == SERVER or lower.startswith("license"):
        return True
    if not lower.endswith(".dll"):
        return False
    return not lower.endswith("-impl.dll") or lower == "llama-server-impl.dll"


def bundle_files() -> list[str]:
    """Full paths of the vendor/llama files that go into the app."""
    if not os.path.isfile(os.path.join(DEST, SERVER)):
        raise SystemExit("vendor/llama is missing. Run: python packaging/fetch_llama.py")
    return [os.path.join(DEST, name) for name in sorted(os.listdir(DEST)) if needed(name)]


def main() -> int:
    if os.path.isfile(STAMP) and open(STAMP, encoding="utf-8").read().strip() == SHA256 \
            and os.path.isfile(os.path.join(DEST, SERVER)):
        print(f"llama.cpp {TAG} is already in vendor/llama")
        return 0
    print(f"Downloading {URL}")
    with urllib.request.urlopen(URL, timeout=120) as response:
        data = response.read()
    found = hashlib.sha256(data).hexdigest()
    if found != SHA256:
        print(f"SHA-256 mismatch for {ASSET}:\n  expected {SHA256}\n  got      {found}",
              file=sys.stderr)
        return 1
    shutil.rmtree(DEST, ignore_errors=True)
    os.makedirs(DEST)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.infolist():
            name = os.path.basename(member.filename)
            if member.is_dir() or not name:
                continue
            with archive.open(member) as source, open(os.path.join(DEST, name), "wb") as target:
                shutil.copyfileobj(source, target)
    if not os.path.isfile(os.path.join(DEST, SERVER)):
        print(f"{SERVER} was not in {ASSET}", file=sys.stderr)
        return 1
    with open(STAMP, "w", encoding="utf-8") as fh:
        fh.write(SHA256)
    kept = bundle_files()
    print(f"llama.cpp {TAG}: {len(os.listdir(DEST)) - 1} files unpacked, {len(kept)} go into "
          f"the app ({sum(os.path.getsize(p) for p in kept) / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
