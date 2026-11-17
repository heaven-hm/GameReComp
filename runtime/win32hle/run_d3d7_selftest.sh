#!/bin/sh
# Build and run the Direct3D7 selftest (facade + device half) on a GL driver.
# Usage: sh runtime/win32hle/run_d3d7_selftest.sh
set -u
cd "$(dirname "$0")" || exit 1
CORE=$(ls *.c | grep -v _selftest.c)
OUT=${TMPDIR:-/tmp}/d3d7_selftest_dev

# shellcheck disable=SC2086  # CORE and the pkg-config output are lists
cc -O1 -Wall -Wno-unused-function -Wno-unused-but-set-variable \
   -I../recomp32 -I. $CORE d3d7_selftest.c \
   $(pkg-config --cflags --libs sdl2 SDL2_ttf) -lpthread -lm -o "$OUT" || exit 1

SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-cocoa}" "$OUT"
