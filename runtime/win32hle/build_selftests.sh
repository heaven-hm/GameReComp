#!/bin/sh
# Build and run every win32hle selftest, each linked with the whole layer.
#
# On Linux x86 the default is gcc -m32: guest addresses are host addresses
# (flat). HLE_BASED=1, or any macOS host, builds 64-bit instead, with the
# guest's 4 GB as one block in the host (based; guest_mem.c), and
# HLE_MEM_BASE=0x100000000000ull puts that block at a fixed address (the lift's
# RECOMP_MEM_BASE). Needs SDL2 and
# SDL2_ttf for the target (README; on a Mac, Homebrew's sdl2 and sdl2_ttf).
set -u
cd "$(dirname "$0")"
CORE=$(ls *.c | grep -v _selftest.c)
TMP=${TMPDIR:-/tmp}
if [ "$(uname -s)" = Darwin ] || [ "${HLE_BASED:-0}" = 1 ]; then
  CC=${CC:-cc}
  ARCH=${HLE_MEM_BASE:+-DRECOMP_MEM_BASE=$HLE_MEM_BASE}
  LIBS=$(pkg-config --cflags --libs sdl2 SDL2_ttf)
  TMP=$TMP/hle-based
  mkdir -p "$TMP"
else
  CC=${CC:-gcc}
  ARCH=-m32
  LIBS=$(PKG_CONFIG_PATH=/usr/lib/i386-linux-gnu/pkgconfig pkg-config --cflags --libs sdl2 SDL2_ttf)
fi
fail=0
for t in *_selftest.c; do
  exe=$TMP/${t%.c}
  # The OpenGL selftests need a real context, which the dummy driver has none of.
  # Any SDL video driver the caller named wins; otherwise a GL one is tried
  # without the driver variables a headless environment may carry.
  case $t in
    d3d7_selftest.c)
      # The device half needs a GL context, which the dummy driver cannot give.
      if [ -n "${SDL_VIDEODRIVER:-}" ]; then VDRV=$SDL_VIDEODRIVER
      elif [ "$(uname -s)" = Darwin ]; then VDRV=cocoa
      else VDRV=x11; fi ;;
    *) VDRV=dummy ;;
  esac
  # shellcheck disable=SC2086  # CORE, ARCH and LIBS are lists
  if $CC $ARCH -O1 -Wall -Wno-unused-function -Wno-unused-but-set-variable -I../recomp32 -I. $CORE "$t" $LIBS -lpthread -lm -o "$exe"; then
    SDL_VIDEODRIVER=$VDRV SDL_AUDIODRIVER=dummy "$exe" > "$exe.out" 2>&1
    case $? in
      0)  echo "ok    $t" ;;
      77) echo "skip  $t (no OpenGL context)" ;;
      *)  echo "FAIL  $t"; tail -5 "$exe.out"; fail=1 ;;
    esac
  else
    echo "BUILD FAIL  $t"; fail=1
  fi
done
exit $fail
