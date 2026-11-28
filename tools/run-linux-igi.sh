#!/bin/sh
# Run the rebuilt Linux IGI binary under Xvfb and capture real frames.
# All paths come from the environment; nothing is hard-coded to a user/machine.
set -eu

PROJECT_DIR="${IGI_PROJECT_DIR:-/Users/haseeb-mir/GameReComp/projects/igi}"
DATA_DIR="${IGI_DATA_DIR:-/Users/haseeb-mir/Downloads/IGI}"
SHOT_DIR="${IGI_SHOT_DIR:-$PWD/docs/igi-levels/linux}"
LEVEL_ARGS="${IGI_LEVEL_ARGS:-window level5}"
SOCKET="${DOCKER_HOST:-unix://$HOME/.colima/default/docker.sock}"
IMAGE="${IGI_LINUX_IMAGE:-ubuntu:24.04}"

mkdir -p "$SHOT_DIR"
DOCKER_HOST="$SOCKET" docker run --rm \
  -e LEVEL_ARGS="$LEVEL_ARGS" \
  -e RECOMP_FORCE_D3D_HAL="${RECOMP_FORCE_D3D_HAL:-0}" \
  -v "$PROJECT_DIR:/src" -v "$DATA_DIR:/data" -v "$SHOT_DIR:/shots" \
  "$IMAGE" bash -lc '
    set -e
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq libsdl2-2.0-0 libsdl2-ttf-2.0-0 libavformat60 \
      libavcodec60 libavutil58 libswresample4 xvfb >/tmp/apt.log 2>&1
    # Generated .incbin paths are absolute and were recorded on a
    # case-insensitive macOS filesystem; mirror them inside the Linux container.
    mkdir -p /Users/haseeb-mir/GameReComp/projects
    ln -sfn /src /Users/haseeb-mir/GameReComp/projects/IGI
    cd /data
    # LEVEL_ARGS is intentionally split into separate guest arguments so
    # `window level5` reaches the guest as two argv entries, not one.
    xvfb-run -a env RECOMP_ASSETS_DIR=/data RECOMP_SCREENSHOT=/shots \
      /src/build-linux/IGI $LEVEL_ARGS
  '
