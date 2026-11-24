# hangman_x86

Rebuilt from `hangman_x86.exe` by GameReComp autorecomp.

- Source: `src/recomp/gen/recomp_*.c` (one function per original address,
  `sub_XXXXXXXX`; register-transfer C, provenance `LOW_LEVEL_FALLBACK`)
- High-level source: `src/highlevel/` (typed C plus the generated ABI adapters;
  present only when a module replaced a function, and the mechanical body of a
  replaced function stays compiled as `sub_XXXXXXXX__lowlevel`)
- Runtime: `src/runtime/win32hle/` (pinned copy), `src/runtime/recomp32/recomp_types.h`
- Build: `cmake -S . -B build && cmake --build build -j`
- Run: `./build/hangman_x86` (the program image from `hangman_x86.exe` and any bundled DLLs
  are embedded in the rebuilt binary, so the original executable is not
  needed at run time; pass `[assets-dir]` or set `RECOMP_ASSETS_DIR` if the
  program reads external data files)

Build dependencies: a C compiler, CMake >= 3.16, SDL2 + SDL2_ttf development
packages, pthreads. FFmpeg (libavformat/libavcodec/libavutil/libswresample) is
optional and only enables Bink/MSS movie and audio decoding.

This project does not contain or redistribute the original game's assets.
