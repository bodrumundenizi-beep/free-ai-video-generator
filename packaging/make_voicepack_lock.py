"""Developer tool: pin the voice pack's packages, with SHA-256s, into src/vidgen/voicepack_lock.py.

Run when changing the pack's versions:    python packaging/make_voicepack_lock.py [freeze.txt]
It sets up the pack's own Python (vidgen.voicepack.bootstrap), lets pip resolve the packages
for it without installing anything, and writes every package as a direct link with its hash,
which the pack then installs with --require-hashes (nothing unpinned or unchecked).

``freeze.txt`` (optional): `pip freeze` of an environment where KittenTTS 2 was heard working;
its versions are kept, so the pack gets the same ones (2026-10-23: Documents\\ktt). Without it,
"only ready-made packages" made pip fall back to years-old omegaconf/hydra-core.
"""

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from vidgen import voicepack  # noqa: E402

WANTED = ["torch==2.11.0+cu128", "kittenml==0.9.6", "soundfile"]   # soundfile: as in freeze.txt
INDEXES = ["--index-url", "https://download.pytorch.org/whl/cu128",
           "--extra-index-url", "https://pypi.org/simple"]
# Pure-Python packages published only as source: built on the user's PC from a checked file,
# with the pinned setuptools (installed first, see voicepack.install).
SOURCE_ONLY = {"antlr4-python3-runtime"}


def main():
    voicepack.bootstrap(report=lambda text, fraction: print(text))
    work = tempfile.mkdtemp()
    constraints = []
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as fh:
            constraints = [line.strip() for line in fh if "==" in line
                           and not line.lower().startswith(("torch==", "torchaudio=="))]
    with open(os.path.join(work, "constraints.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(constraints))
    report = os.path.join(work, "report.json")
    # The source-only package's metadata is read by building it: with the pack's setuptools,
    # as the pack does (the embeddable Python can't make pip's separate build environment).
    subprocess.run([voicepack.python_exe(), "-m", "pip", "install", "--quiet",
                    "--only-binary=:all:", "setuptools==81.0.0", "wheel"], check=True)
    subprocess.run([voicepack.python_exe(), "-m", "pip", "install", "--dry-run",
                    "--ignore-installed", "--only-binary=:all:", "--no-build-isolation",
                    *[f"--no-binary={name}" for name in SOURCE_ONLY], "--quiet",
                    "--report", report, "-c", os.path.join(work, "constraints.txt"),
                    *INDEXES, *WANTED, "setuptools", "wheel"], check=True)
    with open(report, encoding="utf-8") as fh:
        items = json.load(fh)["install"]
    lines = []
    for item in sorted(items, key=lambda i: i["metadata"]["name"].lower()):
        info = item["download_info"]
        sha = info.get("archive_info", {}).get("hashes", {}).get("sha256")
        if not sha:
            raise SystemExit(f"No SHA-256 for {info['url']}")
        lines.append(f"{item['metadata']['name']} @ {info['url']} --hash=sha256:{sha}")
    target = os.path.join(ROOT, "src", "vidgen", "voicepack_lock.py")
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write('"""The voice pack\'s packages, pinned with SHA-256s. Made by '
                 'packaging/make_voicepack_lock.py:\ndon\'t edit by hand."""\n\n'
                 f"WANTED = {WANTED!r}\nSOURCE_ONLY = {sorted(SOURCE_ONLY)!r}\n\n"
                 "LOCK = \"\"\"\\\n" + "\n".join(lines) + '\n"""\n')
    print(f"{len(lines)} packages pinned in {target}")


if __name__ == "__main__":
    main()
