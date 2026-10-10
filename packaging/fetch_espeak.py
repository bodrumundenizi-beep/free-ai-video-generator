"""Fetch espeak-ng, the program that tells the offline voice how words sound, into vendor/espeak.

Run:  python packaging/fetch_espeak.py

espeak-ng's own Windows installer is downloaded from its GitHub releases and
is only unpacked if its SHA-256 matches the one pinned below. It is unpacked
with Windows' msiexec (an "administrative install", which only copies files
and needs no rights), and the English data is kept: the app speaks English.

espeak-ng is GPL-3.0 software. The app runs it as a separate program and ships
its licence beside it; see THIRD-PARTY-LICENSES.md.

vendor/ is not committed; the release workflow runs this before PyInstaller,
and packaging/ai_video_studio.spec copies vendor/espeak into the bundle.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

TAG = "1.52.0"
URL = f"https://github.com/espeak-ng/espeak-ng/releases/download/{TAG}/espeak-ng.msi"
SHA256 = "7f673c709ea5dd579d3b5ebb98688cc575328a6ab7438d2bc405b88cedaeafb9"
LICENCE_URL = f"https://raw.githubusercontent.com/espeak-ng/espeak-ng/{TAG}/COPYING"

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
DEST = os.path.join(ROOT, "vendor", "espeak")
STAMP = os.path.join(DEST, ".version")
EXE = "espeak-ng.exe"
DATA = "espeak-ng-data"


def needed(relative: str) -> bool:
    """Whether the app needs this file of espeak-ng (path relative to its install folder).

    The program, its library, the shared sound tables and the English dictionary
    and voices. The dictionaries of the other hundred languages are left out.
    """
    parts = relative.replace("\\", "/").split("/")
    if len(parts) == 1:
        return parts[0].lower() in (EXE, "libespeak-ng.dll")
    if parts[0] != DATA:
        return False
    if len(parts) == 2:
        return not parts[1].endswith("_dict") or parts[1] == "en_dict"
    if parts[1] == "lang":
        return parts[2:3] == ["gmw"] and parts[-1].startswith("en")
    return parts[1] == "voices"


def bundle_files() -> list[tuple[str, str]]:
    """(full path, folder inside the bundle) for everything in vendor/espeak."""
    if not os.path.isfile(os.path.join(DEST, EXE)):
        raise SystemExit("vendor/espeak is missing. Run: python packaging/fetch_espeak.py")
    out = []
    for folder, _dirs, files in os.walk(DEST):
        inside = os.path.relpath(folder, DEST)
        for name in sorted(files):
            if name != ".version":
                out.append((os.path.join(folder, name),
                            os.path.normpath(os.path.join("espeak", inside))))
    return out


def main() -> int:
    if os.path.isfile(STAMP) and open(STAMP, encoding="utf-8").read().strip() == SHA256 \
            and os.path.isfile(os.path.join(DEST, EXE)):
        print(f"espeak-ng {TAG} is already in vendor/espeak")
        return 0
    print(f"Downloading {URL}")
    with urllib.request.urlopen(URL, timeout=120) as response:
        data = response.read()
    found = hashlib.sha256(data).hexdigest()
    if found != SHA256:
        print(f"SHA-256 mismatch for espeak-ng.msi:\n  expected {SHA256}\n  got      {found}",
              file=sys.stderr)
        return 1
    with urllib.request.urlopen(LICENCE_URL, timeout=60) as response:
        licence = response.read()

    with tempfile.TemporaryDirectory() as work:
        msi, unpacked = os.path.join(work, "espeak-ng.msi"), os.path.join(work, "out")
        with open(msi, "wb") as fh:
            fh.write(data)
        subprocess.run(["msiexec", "/a", msi, "/qn", f"TARGETDIR={unpacked}"], check=True)
        source = next((folder for folder, _dirs, files in os.walk(unpacked) if EXE in files), None)
        if source is None:
            print(f"{EXE} was not in espeak-ng.msi", file=sys.stderr)
            return 1
        shutil.rmtree(DEST, ignore_errors=True)
        count = 0
        for folder, _dirs, files in os.walk(source):
            for name in files:
                relative = os.path.relpath(os.path.join(folder, name), source)
                if needed(relative):
                    target = os.path.join(DEST, relative)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    shutil.copyfile(os.path.join(folder, name), target)
                    count += 1
    with open(os.path.join(DEST, "LICENSE-espeak-ng-GPL-3.0.txt"), "wb") as fh:
        fh.write(licence)
    with open(STAMP, "w", encoding="utf-8") as fh:
        fh.write(SHA256)
    size = sum(os.path.getsize(path) for path, _inside in bundle_files())
    print(f"espeak-ng {TAG}: {count} files kept ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
