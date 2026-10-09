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
    defines = dict(re.findall(r'#define (\w+) "([^"]*)"', read("packaging", "model.iss")))
    model = writer.MODEL
    assert defines == {"ModelFile": model.file, "ModelUrl": model.url,
                       "ModelSize": str(model.size), "ModelSha256": model.sha256}


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
    version = re.search(r'APP_VERSION = "([^"]+)"', read("src", "ai_video_studio.py")).group(1)
    assert f"AIVideoStudio-{version}-setup.exe" in read("README.md")
    assert f"'ProductVersion', '{version}'" in read("packaging", "version_info.txt")
