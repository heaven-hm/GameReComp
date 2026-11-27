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

# A stale DOCKER_HOST (for example a Docker Desktop socket whose daemon is not
# running) used to make the GUI's Rev.ng button fail before the container was
# ever started. Use the configured daemon if it responds; otherwise fall back
# to a live Colima socket, which is the common macOS container runtime.
if ! "$RUNTIME" info >/dev/null 2>&1; then
    for sock in "$HOME/.colima/default/docker.sock" "$HOME/.colima/docker.sock"; do
        if [ -S "$sock" ] && DOCKER_HOST="unix://$sock" "$RUNTIME" info >/dev/null 2>&1; then
            export DOCKER_HOST="unix://$sock"
            break
        fi
    done
fi
if ! "$RUNTIME" info >/dev/null 2>&1 && command -v colima >/dev/null 2>&1; then
    # The VM can be stopped by a host restart; start it so the GUI pass still works.
    colima stop >/dev/null 2>&1 || true
    colima start --runtime docker >/dev/null 2>&1 || true
    for sock in "$HOME/.colima/default/docker.sock" "$HOME/.colima/docker.sock"; do
        if [ -S "$sock" ] && DOCKER_HOST="unix://$sock" "$RUNTIME" info >/dev/null 2>&1; then
            export DOCKER_HOST="unix://$sock"
            break
        fi
    done
fi
if ! "$RUNTIME" info >/dev/null 2>&1; then
    echo "revng-container: no working container daemon (set DOCKER_HOST to a live docker.sock)" >&2
    exit 4
fi

PE_DIR=$(cd -- "$(dirname -- "$PE")" >/dev/null 2>&1 && pwd)
PE_NAME=$(basename -- "$PE")
mkdir -p -- "$OUT"
OUT_DIR=$(cd -- "$OUT" >/dev/null 2>&1 && pwd)

# Current rev.ng CLI (revng/revng:latest) no longer has `revng decompile`;
# the porcelain command is `quick artifact emit-c-as-single-file BINARY -o FILE`.
# The current image accepts -o but silently produces no file with an `.c`
# path; the artifact is written to stdout. Capture that instead, and keep the
# PTML markup (it carries source locations used by provenance checks).
exec "$RUNTIME" run --platform linux/amd64 --rm \
    -v "$PE_DIR:/work/in:ro" \
    "$IMAGE" revng quick artifact emit-c-as-single-file \
    "/work/in/$PE_NAME" > "$OUT_DIR/decompiled.c"
