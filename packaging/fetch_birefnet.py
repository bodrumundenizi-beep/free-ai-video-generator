"""Fetch BiRefNet lite, the model that removes a photo's background, into vendor/birefnet.

Run:  python packaging/fetch_birefnet.py            (downloads it, 224 MB)
      python packaging/fetch_birefnet.py --from F   (uses F, a copy already downloaded)

The fp32 ONNX export published by onnx-community, at a pinned commit, is only used if its
SHA-256 matches the one pinned below. Its weights are then stored as 8-bit numbers (one scale
for each filter) and turned back into fp32 when the model loads: 224 -> 91 MB, about 40 MB once
the installer packs it (the fp16 export packed to 80 MB). The cut-outs are the same: 0.04/255
apart on 17 test photos (2026-11-10). The result is pinned too. Needs the `onnx` package
(requirements-build.txt).

BiRefNet is MIT software; its licence is written beside the model (see
THIRD-PARTY-LICENSES.md). vendor/ is not committed; the release workflow runs this before
PyInstaller, and packaging/ai_video_studio.spec copies vendor/birefnet into the bundle.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import urllib.request

COMMIT = "de15b22ba131738a16dff04aab8bdf8dc32e3ac1"
URL = f"https://huggingface.co/onnx-community/BiRefNet_lite-ONNX/resolve/{COMMIT}/onnx/model.onnx"
SHA256 = "5600024376f572a557870a5eb0afb1e5961636bef4e1e22132025467d0f03333"
MODEL = "birefnet-lite-int8w.onnx"
MODEL_SHA256 = "7c46c4de0ae37256f8b4cf25d708c1f163a5f2551a21e1babba839f2867b48af"
LICENCE_NAME = "LICENSE-BiRefNet-MIT.txt"
LICENCE = """MIT License

Copyright (c) 2024 ZhengPeng

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
DEST = os.path.join(ROOT, "vendor", "birefnet")


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def int8_weights(src: str, dst: str) -> None:
    """``src`` with every large fp32 weight stored as int8 (one scale per output filter),
    turned back into fp32 by DequantizeLinear when the model loads."""
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    model = onnx.load(src)
    graph = model.graph
    steps, weights = [], []
    for weight in graph.initializer:
        a = numpy_helper.to_array(weight)
        if weight.data_type != TensorProto.FLOAT or a.ndim < 2 or a.size < 1024:
            weights.append(weight)
            continue
        top = np.abs(a.reshape(a.shape[0], -1)).max(1)
        scale = np.where(top > 0, top / 127, 1).astype(np.float32)
        q = np.clip(np.round(a / scale.reshape((-1,) + (1,) * (a.ndim - 1))), -127, 127)
        weights += [numpy_helper.from_array(q.astype(np.int8), weight.name + "_q"),
                    numpy_helper.from_array(scale, weight.name + "_s"),
                    numpy_helper.from_array(np.zeros(a.shape[0], np.int8), weight.name + "_z")]
        steps.append(helper.make_node(
            "DequantizeLinear", [weight.name + "_q", weight.name + "_s", weight.name + "_z"],
            [weight.name], axis=0, name=weight.name + "_dq"))
    del graph.initializer[:]
    graph.initializer.extend(weights)
    nodes = list(graph.node)
    del graph.node[:]
    graph.node.extend(steps + nodes)
    onnx.save(model, dst)


def bundle_files() -> list[tuple[str, str]]:
    """(full path, folder inside the bundle) for the model and its licence."""
    model = os.path.join(DEST, MODEL)
    if not os.path.isfile(model):
        raise SystemExit("vendor/birefnet is missing. Run: python packaging/fetch_birefnet.py")
    return [(model, "birefnet"), (os.path.join(DEST, LICENCE_NAME), "birefnet")]


def main(argv: list[str]) -> int:
    model = os.path.join(DEST, MODEL)
    os.makedirs(DEST, exist_ok=True)
    with open(os.path.join(DEST, LICENCE_NAME), "w", encoding="utf-8") as fh:
        fh.write(LICENCE)
    if os.path.isfile(model) and sha256(model) == MODEL_SHA256:
        print("BiRefNet lite is already in vendor/birefnet")
        return 0
    original = os.path.join(DEST, "model_fp32.onnx.part")
    if len(argv) == 2 and argv[0] == "--from":
        shutil.copyfile(argv[1], original)
    else:
        print(f"Downloading {URL}")
        with urllib.request.urlopen(URL, timeout=120) as response, open(original, "wb") as fh:
            shutil.copyfileobj(response, fh, 1 << 20)
    found = sha256(original)
    if found != SHA256:
        os.remove(original)
        print(f"SHA-256 mismatch for model.onnx:\n  expected {SHA256}\n  got      {found}",
              file=sys.stderr)
        return 1
    int8_weights(original, model)
    os.remove(original)
    found = sha256(model)
    if found != MODEL_SHA256:
        os.remove(model)
        print(f"The 8-bit model came out different:\n  expected {MODEL_SHA256}\n  got      "
              f"{found}\n(another onnx or numpy version? requirements pin them)", file=sys.stderr)
        return 1
    print(f"BiRefNet lite: {os.path.getsize(model) / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
