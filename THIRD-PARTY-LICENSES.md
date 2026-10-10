# Third-party components

AI Video Studio's own source is MIT (see `LICENSE`). The released installer and
portable ZIP redistribute the components below, each under its own licence.

| Component | Licence | Notes |
|---|---|---|
| [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) | MIT | The whole interface |
| [MoviePy](https://github.com/Zulko/moviepy) | MIT | Video assembly |
| [Requests](https://github.com/psf/requests) | Apache-2.0 | Pexels API calls |
| [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) | BSD-2-Clause | Locates and ships the ffmpeg binary |
| [edge-tts](https://github.com/rany2/edge-tts) | **LGPL-3.0** | The optional online voices |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | MIT | Runs the offline voice's model |
| [espeak-ng](https://github.com/espeak-ng/espeak-ng) (bundled program) | **GPL-3.0** | See below |
| [pywinstyles](https://github.com/Akascape/py-win-styles) | MIT | Mica backdrop |
| [FFmpeg](https://ffmpeg.org/) (bundled binary) | **GPL-3.0** | See below |
| [Pillow](https://python-pillow.org/), [NumPy](https://numpy.org/) | MIT-CMU / BSD-3-Clause | Pulled in by MoviePy |

## edge-tts (LGPL-3.0)

edge-tts is imported into this application's process rather than modified.
The LGPL permits that from a differently-licensed program, including an MIT one,
provided the component itself stays LGPL, its licence text ships with the
distribution, and a user can replace it with their own version. The bundled copy
is unmodified upstream, and the source is at the link above.

## FFmpeg (GPL-3.0)

The bundled binary is `ffmpeg-win-x86_64-v7.1.exe`, the "essentials" Windows
build redistributed by imageio-ffmpeg and produced by
[gyan.dev](https://www.gyan.dev/ffmpeg/builds/). It reports
`--enable-gpl --enable-version3`, so **that binary is GPL-3.0**, not LGPL.

This application invokes ffmpeg as a **separate process**, never linking against
it, which is the "mere aggregation" case: shipping a GPL program alongside an
MIT one does not make the MIT code GPL. The obligation that does apply is to the
ffmpeg binary itself — its licence must accompany the distribution and its
source must be available. FFmpeg's source for this version is at
<https://ffmpeg.org/download.html> and the build configuration is published at
the gyan.dev link above.

If you would rather not redistribute a GPL binary at all, the alternatives are
to ship an LGPL ffmpeg build instead, or to not bundle ffmpeg and have the app
download it on first run.

## llama.cpp (MIT) and the Smart writer's model (Apache 2.0)

The Smart writer runs a language model on the user's PC. Two things are involved:

- **[llama.cpp](https://github.com/ggml-org/llama.cpp)** (MIT), the program that
  runs the model. Its unmodified Windows Vulkan build is bundled in the `llama`
  folder and started as a **separate process**. The build includes the LLVM
  OpenMP runtime (`libomp.dll`, Apache 2.0 with LLVM exceptions); its licence
  file ships beside it. The exact release and its checksum are pinned in
  `packaging/fetch_llama.py`.
- **[Qwen3 4B](https://huggingface.co/Qwen/Qwen3-4B-GGUF)** (Apache 2.0, Alibaba
  Cloud), the model. It is **not** part of this download: the installer or the
  app fetches the unmodified file `Qwen3-4B-Q4_K_M.gguf` from the publisher's
  Hugging Face page, and checks its SHA-256, pinned in `src/vidgen/writer.py`.

## The offline voice: Kokoro (Apache 2.0) and espeak-ng (GPL-3.0)

The voiceover is spoken on the user's PC. Three things are involved:

- **[Kokoro 82M](https://huggingface.co/hexgrad/Kokoro-82M)** (Apache 2.0), the
  speech model, and its speaker file. They are **not** part of this download: the
  installer or the app fetches the unmodified ONNX export published by
  [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) (MIT) and checks each
  file's SHA-256, pinned in `src/vidgen/localvoice.py`.
- **[ONNX Runtime](https://github.com/microsoft/onnxruntime)** (MIT) runs the model.
- **[espeak-ng](https://github.com/espeak-ng/espeak-ng)** (GPL-3.0) turns words
  into sounds. Its unmodified Windows build, with the English data only, is
  bundled in the `espeak` folder and run as a **separate program**: the app passes
  it text and reads back its output, and never links to it or loads it. Bundling
  an independent GPL program beside an MIT one is aggregation, which the GPL
  permits; espeak-ng itself stays GPL-3.0. Its licence text ships beside it
  (`LICENSE-espeak-ng-GPL-3.0.txt`), its source is at the link above, and the
  exact release and checksum are pinned in `packaging/fetch_espeak.py`.

## AI images (optional): Z-Image Turbo (Apache 2.0) and stable-diffusion.cpp (MIT)

Nothing for AI images is part of this download. If the user presses Download in the
**AI images** tab, the app fetches three files and checks each one's SHA-256, pinned in
`src/vidgen/imagegen.py`:

- **[Z-Image Turbo](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo)** (Apache 2.0, Alibaba),
  the picture model, as the unmodified GGUF file published by
  [leejet](https://huggingface.co/leejet/Z-Image-Turbo-GGUF).
- Its picture decoder (`ae.safetensors`, Apache 2.0), from
  [Comfy-Org/z_image_turbo](https://huggingface.co/Comfy-Org/z_image_turbo).
- **[stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp)** (MIT), the
  program that runs them: its unmodified Windows Vulkan build from its GitHub releases,
  unpacked into the user's profile and started as a **separate process**.

The model reads the description with the Smart writer's Qwen3 4B file (above).

*This is a description of the licences involved, not legal advice.*
