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

## Background music (CC0, public domain)

The tracks in `assets/music` are dedicated to the public domain under
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/): they may be used in videos,
including monetised ones, with no credit required. They were converted to 128 kbps MP3; the
file names start with the mood the app uses to pick them.

| File | Original | Author | Source |
|---|---|---|---|
| `calm - Chill Lofi.mp3` | Chill lofi inspired | omfgdude | https://opengameart.org/content/chill-lofi-inspired |
| `calm - Lofi Loop.mp3` | Lofi Hip Hop Loop | omfgdude | https://opengameart.org/content/lofi-hip-hop-loop |
| `calm - Bossa.mp3` | Bossa Nova (8bit Bossa) | Joth | https://opengameart.org/content/bossa-nova |
| `upbeat - Rhythm Factory.mp3` | Rhythm Factory | Zane Little Music | https://opengameart.org/content/rhythm-factory |
| `upbeat - Summer Sunday.mp3` | Summer Sunday | celestialghost8 | https://opengameart.org/content/summer-sunday |
| `dramatic - The Field of Dreams.mp3` | The Field Of Dreams | pauliuw | https://opengameart.org/content/the-field-of-dreams |
| `dramatic - Next to You.mp3` | Next to You | Joth | https://opengameart.org/content/next-to-you |
| `dramatic - Snowfall.mp3` | Snowfall (looped version) | Kistol | https://opengameart.org/content/snowfall |

## Moving pictures (optional, beta): Wan 2.2 TI2V 5B and umt5-xxl (Apache 2.0)

"Moving pictures" in the AI images tab downloads three files, each pinned to a commit and
checked by SHA-256, and runs them with the same stable-diffusion.cpp program (MIT):

| File | What | Licence | Source |
|---|---|---|---|
| `wan2.2-ti2v-5b-Q4_K_M.gguf` | Wan 2.2 TI2V 5B by Wan-AI (Alibaba), GGUF by QuantStack | Apache 2.0 | https://huggingface.co/QuantStack/Wan2.2-TI2V-5B-GGUF |
| `wan2.2-vae.safetensors` | Wan 2.2 video decoder | Apache 2.0 | same |
| `taew2_2.safetensors` | Wan 2.2 tiny decoder (TAEHV) by Ollin Boer Bohan | MIT | https://github.com/madebyollin/taehv |
| `umt5-xxl-encoder-Q4_K_M.gguf` | umt5-xxl text encoder by Google, GGUF by city96 | Apache 2.0 | https://huggingface.co/city96/umt5-xxl-encoder-gguf |

Nothing of these is bundled in the installer.

## My voice (optional, beta): KittenTTS 2 (Stellon Labs Community License) and PyTorch (BSD-3)

"Make my voice" installs the voice pack on PCs with an NVIDIA graphics card (6 GB or more).
Nothing of it is bundled in the installer; everything is pinned and checked by SHA-256
(`src/vidgen/voicepack.py`, `src/vidgen/voicepack_lock.py`):

| What | Licence | Source |
|---|---|---|
| KittenTTS 2 voice model by KittenML (Stellon Labs), revision `1f00fee5` | Stellon Labs Community License: free for research, non-commercial use, and commercial use under USD $1M yearly revenue or total funding; attribution required | https://huggingface.co/KittenML/kitten-tts-2 |
| Its speaker encoder (`speaker/`) | MIT | same |
| `kittenml` 0.9.6, the program that runs it | Apache 2.0 | https://pypi.org/project/kittenml/ |
| chatterbox-turbo s3gen decoder by Resemble AI, revision `749d1c1a` | MIT | https://huggingface.co/ResembleAI/chatterbox-turbo |
| PyTorch 2.11 (CUDA 12.8) and torchaudio | BSD-3-Clause; the NVIDIA CUDA libraries it ships under NVIDIA's own licence | https://pytorch.org |
| Python 3.12.10 (embeddable) | PSF License | https://www.python.org |
| pip 25.2 and the 80-odd libraries listed in `voicepack_lock.py` (numpy, transformers, diffusers, librosa and others) | each under its own open-source licence (MIT, BSD, Apache 2.0, ISC, PSF) | PyPI |

Voices made with it are made from the user's own recording, on their PC; the recording and the
voice stay there.
