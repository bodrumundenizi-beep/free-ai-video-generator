# Third-party components

AI Video Studio's own source is MIT (see `LICENSE`). The released installer and
portable ZIP redistribute the components below, each under its own licence.

| Component | Licence | Notes |
|---|---|---|
| [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) | MIT | The whole interface |
| [MoviePy](https://github.com/Zulko/moviepy) | MIT | Video assembly |
| [Requests](https://github.com/psf/requests) | Apache-2.0 | Pexels API calls |
| [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) | BSD-2-Clause | Locates and ships the ffmpeg binary |
| [edge-tts](https://github.com/rany2/edge-tts) | **LGPL-3.0** | Voiceover |
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

*This is a description of the licences involved, not legal advice.*
