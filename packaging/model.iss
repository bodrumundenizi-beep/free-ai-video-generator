; The Smart writer's model, for the installer's download page.
;
; These four values must be the same as NEW in src/vidgen/writer.py - the app
; only accepts a model file with exactly that size and SHA-256.
; tests/test_packaging.py fails if the two ever differ.
; (#ifndef so a test build can point the installer at a small local file.)

#ifndef ModelFile
  #define ModelFile "Qwen3VL-4B-Instruct-Q4_K_M.gguf"
#endif
#ifndef ModelUrl
  #define ModelUrl "https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF/resolve/main/Qwen3VL-4B-Instruct-Q4_K_M.gguf"
#endif
#ifndef ModelSize
  #define ModelSize "2497281664"
#endif
#ifndef ModelSha256
  #define ModelSha256 "66358cb18bb6b3b1b6675aa412c7a88ef01d228f481184d13668e5201c730a0a"
#endif

; Its image reader (5.3: the Smart writer also looks at pictures). NEW_PROJ in writer.py.
#ifndef ProjFile
  #define ProjFile "mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf"
#endif
#ifndef ProjUrl
  #define ProjUrl "https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF/resolve/main/mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf"
#endif
#ifndef ProjSize
  #define ProjSize "453974304"
#endif
#ifndef ProjSha256
  #define ProjSha256 "30ba2c7dd3127a4561b6cba9d13d0f711c91bdb38742e2f56d73c8cb596bd06d"
#endif

; 5.2's Smart writer (MODEL in writer.py). Only looked for: when it is there, this is an
; update, and the new one is a ticked box instead of a given.
#ifndef OldModelFile
  #define OldModelFile "Qwen3-4B-Q4_K_M.gguf"
#endif
#ifndef OldModelSize
  #define OldModelSize "2497280256"
#endif

; The offline voice: its model and its speakers. The same rule: these must be
; MODEL and SPEAKERS in src/vidgen/localvoice.py.

#ifndef VoiceFile
  #define VoiceFile "kokoro-v1.0.onnx"
#endif
#ifndef VoiceUrl
  #define VoiceUrl "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.onnx"
#endif
#ifndef VoiceSize
  #define VoiceSize "325505369"
#endif
#ifndef VoiceSha256
  #define VoiceSha256 "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a"
#endif

#ifndef SpeakersFile
  #define SpeakersFile "kokoro-voices-v1.0.bin"
#endif
#ifndef SpeakersUrl
  #define SpeakersUrl "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin"
#endif
#ifndef SpeakersSize
  #define SpeakersSize "28214398"
#endif
#ifndef SpeakersSha256
  #define SpeakersSha256 "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"
#endif

; The Image editor's models (Remove background and Erase), only fetched when the
; "AI photo tools" box is ticked. The same rule: these must be WEIGHTS in
; src/vidgen/cutout.py and src/vidgen/eraser.py.

#ifndef CutoutFile
  #define CutoutFile "birefnet-lite-int8w.onnx"
#endif
#ifndef CutoutUrl
  #define CutoutUrl "https://github.com/bodrumundenizi-beep/free-ai-video-generator/releases/download/v5.2.0/birefnet-lite-int8w.onnx"
#endif
#ifndef CutoutSize
  #define CutoutSize "90708050"
#endif
#ifndef CutoutSha256
  #define CutoutSha256 "7c46c4de0ae37256f8b4cf25d708c1f163a5f2551a21e1babba839f2867b48af"
#endif

#ifndef EraserFile
  #define EraserFile "lama-fp16w.onnx"
#endif
#ifndef EraserUrl
  #define EraserUrl "https://github.com/bodrumundenizi-beep/free-ai-video-generator/releases/download/v5.2.0/lama-fp16w.onnx"
#endif
#ifndef EraserSize
  #define EraserSize "106291857"
#endif
#ifndef EraserSha256
  #define EraserSha256 "cf36c035f814cc3f224e02fcafdacc97672b564594af387e0e53c6dce4edbdda"
#endif
