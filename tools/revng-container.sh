#!/bin/sh
# Run upstream rev.ng for one PE in a Linux/amd64 container.
# Usage: revng-container.sh <input.exe> <output-dir>
# The caller (tools.autorecomp.readability) substitutes {pe} and {outdir}
# into REVNG_DECOMPILE_CMD; paths with spaces stay a single argument.
set -eu

if [ "$#" -lt 2 ]; then
    echo "usage: $0 <input.exe> <output-dir>" >&2
    exit 2
fi

PE=$1
OUT=$2
RUNTIME=${REVNG_DOCKER:-}
if [ -z "$RUNTIME" ]; then
    if command -v docker >/dev/null 2>&1; then RUNTIME=docker
    elif command -v podman >/dev/null 2>&1; then RUNTIME=podman
    else
        echo "rev.ng requires docker or podman on macOS/Windows (Linux x86-64 only)" >&2
        exit 3
    fi
fi
IMAGE=${REVNG_IMAGE:-revng/revng:latest}
PE_DIR=$(CDPATH= cd -- "$(dirname -- "$PE")" && pwd)
PE_NAME=$(basename -- "$PE")
mkdir -p -- "$OUT"
OUT_DIR=$(CDPATH= cd -- "$OUT" && pwd)

# Current rev.ng CLI (revng/revng:latest) no longer has `revng decompile`;
# the porcelain command is `quick artifact emit-c-as-single-file BINARY -o FILE`.
exec "$RUNTIME" run --platform linux/amd64 --rm \
    -v "$PE_DIR:/work/in:ro" -v "$OUT_DIR:/work/out" \
    "$IMAGE" revng quick artifact emit-c-as-single-file \
    "/work/in/$PE_NAME" -o /work/out/decompiled.c
