; The Smart writer's model, for the installer's download page.
;
; These four values must be the same as MODEL in src/vidgen/writer.py - the app
; only accepts a model file with exactly that size and SHA-256.
; tests/test_packaging.py fails if the two ever differ.
; (#ifndef so a test build can point the installer at a small local file.)

#ifndef ModelFile
  #define ModelFile "Qwen3-4B-Q4_K_M.gguf"
#endif
#ifndef ModelUrl
  #define ModelUrl "https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf"
#endif
#ifndef ModelSize
  #define ModelSize "2497280256"
#endif
#ifndef ModelSha256
  #define ModelSha256 "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5"
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
