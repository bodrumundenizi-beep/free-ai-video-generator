"""Fetch LaMa, the model that erases things from a photo, into vendor/lama.

Run:  python packaging/fetch_lama.py            (downloads it, 208 MB)
      python packaging/fetch_lama.py --from F   (uses F, a copy already downloaded)

The unmodified fp32 ONNX export published by Carve (Apache 2.0) is only used if its SHA-256
matches the one pinned below. Its weights are then stored as fp16 and turned back into fp32 when
the model loads: half the size (208 -> 106 MB), the same maths. Computing in fp16 overflowed and
filled holes with white, so only the storage is halved. The result is pinned too.

Needs the `onnx` package (requirements-build.txt). vendor/ is not committed; the release workflow
runs this before PyInstaller, and packaging/ai_video_studio.spec copies vendor/lama into the
bundle. See THIRD-PARTY-LICENSES.md.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import urllib.request

COMMIT = "c3c0c9e468934d62e79c329e35d82dd09ff8c444"
URL = f"https://huggingface.co/Carve/LaMa-ONNX/resolve/{COMMIT}/lama_fp32.onnx"
SHA256 = "1faef5301d78db7dda502fe59966957ec4b79dd64e16f03ed96913c7a4eb68d6"
MODEL = "lama-fp16w.onnx"
MODEL_SHA256 = "cf36c035f814cc3f224e02fcafdacc97672b564594af387e0e53c6dce4edbdda"
LICENCE_URL = "https://www.apache.org/licenses/LICENSE-2.0.txt"
LICENCE_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
LICENCE_NAME = "LICENSE-LaMa-Apache-2.0.txt"
NOTICE = """LaMa: Resolution-robust Large Mask Inpainting with Fourier Convolutions
Copyright (c) 2021 Samsung AI Center (https://github.com/advimman/lama), Apache License 2.0.
ONNX export by Carve (https://huggingface.co/Carve/LaMa-ONNX), Apache License 2.0.
Changed for AI Video Studio: the weights are stored as fp16 and turned back into fp32 when
the model loads. The full licence text follows.

"""

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
DEST = os.path.join(ROOT, "vendor", "lama")


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def half_weights(src: str, dst: str) -> None:
    """``src`` with every large fp32 weight stored as fp16, turned back into fp32 on load."""
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    model = onnx.load(src)
    graph = model.graph
    casts, weights = [], []
    for weight in graph.initializer:
        if weight.data_type == TensorProto.FLOAT and np.prod(weight.dims) >= 1024:
            half = numpy_helper.from_array(numpy_helper.to_array(weight).astype(np.float16),
                                           weight.name + "_fp16")
            weights.append(half)
            casts.append(helper.make_node("Cast", [half.name], [weight.name],
                                          to=TensorProto.FLOAT, name=weight.name + "_to_fp32"))
        else:
            weights.append(weight)
    del graph.initializer[:]
    graph.initializer.extend(weights)
    nodes = list(graph.node)
    del graph.node[:]
    graph.node.extend(casts + nodes)
    onnx.save(model, dst)


def bundle_files() -> list[tuple[str, str]]:
    """(full path, folder inside the bundle) for the model and its licence."""
    model = os.path.join(DEST, MODEL)
    if not os.path.isfile(model):
        raise SystemExit("vendor/lama is missing. Run: python packaging/fetch_lama.py")
    return [(model, "lama"), (os.path.join(DEST, LICENCE_NAME), "lama")]


def _download(url: str, path: str) -> None:
    with urllib.request.urlopen(url, timeout=120) as response, open(path, "wb") as fh:
        shutil.copyfileobj(response, fh, 1 << 20)


def main(argv: list[str]) -> int:
    model = os.path.join(DEST, MODEL)
    os.makedirs(DEST, exist_ok=True)
    licence = os.path.join(DEST, LICENCE_NAME)
    if not os.path.isfile(licence):
        text = licence + ".part"
        _download(LICENCE_URL, text)
        if sha256(text) != LICENCE_SHA256:
            os.remove(text)
            print("SHA-256 mismatch for the Apache 2.0 licence text", file=sys.stderr)
            return 1
        with open(licence, "w", encoding="utf-8") as fh:
            fh.write(NOTICE + open(text, encoding="utf-8").read())
        os.remove(text)
    if os.path.isfile(model) and sha256(model) == MODEL_SHA256:
        print("LaMa is already in vendor/lama")
        return 0
    original = os.path.join(DEST, "lama_fp32.onnx.part")
    if len(argv) == 2 and argv[0] == "--from":
        shutil.copyfile(argv[1], original)
    else:
        print(f"Downloading {URL}")
        _download(URL, original)
    found = sha256(original)
    if found != SHA256:
        os.remove(original)
        print(f"SHA-256 mismatch for lama_fp32.onnx:\n  expected {SHA256}\n  got      {found}",
              file=sys.stderr)
        return 1
    half_weights(original, model)
    os.remove(original)
    found = sha256(model)
    if found != MODEL_SHA256:
        os.remove(model)
        print(f"The halved model came out different:\n  expected {MODEL_SHA256}\n  got      "
              f"{found}\n(another onnx version? requirements-build.txt pins it)", file=sys.stderr)
        return 1
    print(f"LaMa: {os.path.getsize(model) / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
