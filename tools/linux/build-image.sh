#!/bin/sh
# Build the network-free Linux toolchain image used by tools/linux_matrix.py.
# Run from anywhere; the build context is this directory.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
docker build -t gamerecomp/linux-runner:local \
    -f "$ROOT/tools/linux/Dockerfile" "$ROOT/tools/linux"
