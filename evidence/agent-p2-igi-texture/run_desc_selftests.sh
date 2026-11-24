#!/bin/sh
# Build/run the three DirectDraw descriptor selftests with the whole win32hle layer.
set -u
cd "$(dirname "$0")/../../runtime/win32hle"
LIBS=$(pkg-config --cflags --libs sdl2 SDL2_ttf)
OUT=/tmp/p2-p2igi
mkdir -p "$OUT"
for t in ddraw7_igi_texture_desc_selftest.c ddraw7_lock_rect_selftest.c ddraw7_create_selftest.c; do
  echo "=== $t ==="
  set --
  for c in $(ls *.c | grep -v _selftest.c); do set -- "$@" "$c"; done
  if cc -O1 -Wall -Wno-unused-function -Wno-unused-but-set-variable -I../recomp32 -I. \
       "$@" "$t" $LIBS -lpthread -lm -o "$OUT/${t%.c}"; then
    SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy "$OUT/${t%.c}" > "$OUT/${t%.c}.out" 2>&1
    rc=$?
    echo "RC=$rc"
    tail -20 "$OUT/${t%.c}.out"
  else
    echo BUILD_FAIL
  fi
done
