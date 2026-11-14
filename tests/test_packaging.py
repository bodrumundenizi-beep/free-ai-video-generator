"""The pins the build relies on: they are written in more than one place, so check they agree."""
import importlib.util
import os
import re

from vidgen import writer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def fetch_llama():
    spec = importlib.util.spec_from_file_location(
        "fetch_llama", os.path.join(ROOT, "packaging", "fetch_llama.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_installer_downloads_exactly_the_model_the_app_accepts():
    from vidgen import cutout, eraser, localvoice

    defines = dict(re.findall(r'#define (\w+) "([^"]*)"', read("packaging", "model.iss")))
    expected = {}
    expected |= {"OldModelFile": writer.MODEL.file, "OldModelSize": str(writer.MODEL.size)}
    for prefix, model in (("Model", writer.NEW), ("Proj", writer.NEW_PROJ),
                          ("Voice", localvoice.MODEL),
                          ("Speakers", localvoice.SPEAKERS), ("Cutout", cutout.WEIGHTS),
                          ("Eraser", eraser.WEIGHTS)):
        expected |= {prefix + "File": model.file, prefix + "Url": model.url,
                     prefix + "Size": str(model.size), prefix + "Sha256": model.sha256}
    assert defines == expected


def test_the_installer_puts_the_model_where_the_app_looks(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\me\AppData\Local")
    assert writer.models_dir() == r"C:\Users\me\AppData\Local\AIVideoStudio\models"
    assert r"{localappdata}\AIVideoStudio\models" in read("packaging", "installer.iss")


def test_the_pinned_llama_build_is_a_full_checksum_of_a_tagged_release():
    pins = fetch_llama()
    assert re.fullmatch(r"[0-9a-f]{64}", pins.SHA256)
    assert pins.TAG in pins.URL and pins.URL.startswith(
        "https://github.com/ggml-org/llama.cpp/releases/download/")


def test_the_bundle_takes_the_server_and_its_libraries_only():
    needed = fetch_llama().needed
    for name in ("llama-server.exe", "llama-server-impl.dll", "llama.dll", "ggml-cpu-haswell.dll",
                 "ggml-vulkan.dll", "libomp.dll", "LICENSE-LLVM-OpenMP"):
        assert needed(name), name
    for name in ("llama-cli.exe", "llama-cli-impl.dll", "llama-bench.exe", ".version"):
        assert not needed(name), name
    assert fetch_llama().SERVER == writer.SERVER_EXE


def test_the_readme_and_the_app_agree_on_the_version():
    version = re.search(r'APP_VERSION = "([^"]+)"', read("src", "gui", "base.py")).group(1)
    assert f"AIVideoStudio-{version}-setup.exe" in read("README.md")
    assert f"'ProductVersion', '{version}'" in read("packaging", "version_info.txt")


def fetch_espeak():
    spec = importlib.util.spec_from_file_location(
        "fetch_espeak", os.path.join(ROOT, "packaging", "fetch_espeak.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_pinned_espeak_build_is_a_full_checksum_of_a_tagged_release():
    pins = fetch_espeak()
    assert re.fullmatch(r"[0-9a-f]{64}", pins.SHA256)
    assert pins.URL.startswith(
        f"https://github.com/espeak-ng/espeak-ng/releases/download/{pins.TAG}/")


def test_only_the_languages_the_app_speaks_go_into_the_app():
    from vidgen import localvoice

    needed = fetch_espeak().needed
    for name in ("espeak-ng.exe", "libespeak-ng.dll", "espeak-ng-data/phondata",
                 "espeak-ng-data/en_dict", "espeak-ng-data\\lang\\gmw\\en-US",
                 "espeak-ng-data/voices/!v/f1", "espeak-ng-data/es_dict",
                 "espeak-ng-data/lang/roa/fr", "espeak-ng-data/lang/roa/pt-BR",
                 "espeak-ng-data/lang/inc/hi"):
        assert needed(name), name
    for name in ("espeak-ng-data/de_dict", "README.md", "espeak-ng-data/lang/gmw/de",
                 "espeak-ng-data/lang/zle/ru"):
        assert not needed(name), name
    assert fetch_espeak().EXE == localvoice.ESPEAK_EXE


def fetch_birefnet():
    spec = importlib.util.spec_from_file_location(
        "fetch_birefnet", os.path.join(ROOT, "packaging", "fetch_birefnet.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_background_remover_is_pinned_bundled_and_licensed():
    from vidgen import cutout

    pins = fetch_birefnet()
    assert re.fullmatch(r"[0-9a-f]{64}", pins.SHA256)
    assert re.fullmatch(r"[0-9a-f]{40}", pins.COMMIT) and pins.COMMIT in pins.URL
    assert pins.MODEL == cutout.MODEL and pins.URL.endswith("/onnx/model.onnx")
    assert re.fullmatch(r"[0-9a-f]{64}", pins.MODEL_SHA256)
    spec = read("packaging", "ai_video_studio.spec")
    assert "+ BIREFNET +" in spec and '"vidgen.cutout"' in spec
    assert "python packaging/fetch_birefnet.py" in read(".github", "workflows", "release.yml")
    licences = read("THIRD-PARTY-LICENSES.md")
    assert "BiRefNet" in licences and pins.LICENCE_NAME in licences
    assert "Copyright (c) 2024 ZhengPeng" in pins.LICENCE


def fetch_lama():
    spec = importlib.util.spec_from_file_location(
        "fetch_lama", os.path.join(ROOT, "packaging", "fetch_lama.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_eraser_is_pinned_bundled_and_licensed():
    from vidgen import eraser

    pins = fetch_lama()
    for pin in (pins.SHA256, pins.MODEL_SHA256, pins.LICENCE_SHA256):
        assert re.fullmatch(r"[0-9a-f]{64}", pin)
    assert re.fullmatch(r"[0-9a-f]{40}", pins.COMMIT) and pins.COMMIT in pins.URL
    assert pins.MODEL == eraser.MODEL
    spec = read("packaging", "ai_video_studio.spec")
    assert "+ LAMA +" in spec and '"vidgen.eraser"' in spec
    assert "python packaging/fetch_lama.py" in read(".github", "workflows", "release.yml")
    assert re.search(r"^onnx==", read("requirements-build.txt"), re.M)
    licences = read("THIRD-PARTY-LICENSES.md")
    assert "LaMa" in licences and pins.LICENCE_NAME in licences


def test_the_editor_models_are_downloaded_from_the_release_the_workflow_fills():
    from vidgen import cutout, eraser

    workflow = read(".github", "workflows", "release.yml")
    for module, pins in ((cutout, fetch_birefnet()), (eraser, fetch_lama())):
        weights = module.WEIGHTS
        assert weights.file == pins.MODEL and weights.sha256 == pins.MODEL_SHA256
        assert weights.url == cutout.RELEASE + weights.file
        assert weights.file in workflow                   # uploaded with the release
    version = re.search(r'APP_VERSION = "([^"]+)"', read("src", "gui", "base.py")).group(1)
    assert cutout.RELEASE.endswith("/v5.2.0/") and version >= "5.2.0"
    spec = read("packaging", "ai_video_studio.spec")
    assert 'not f[0].endswith(".onnx")' in spec           # the models stay out of the installer
