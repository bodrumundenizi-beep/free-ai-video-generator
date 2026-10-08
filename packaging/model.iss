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
