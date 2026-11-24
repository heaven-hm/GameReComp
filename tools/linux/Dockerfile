# Reproducible Linux runner image for tools/linux_matrix.py.
#
# Build once on a host with Docker/Colima; the matrix then needs no network:
#   docker build -t gamerecomp/linux-runner:local -f tools/linux/Dockerfile tools/linux
#
# The generated GameReComp CMake projects link SDL2 + SDL2_ttf, so the image
# carries a native compiler and those development headers. No source from the
# host is baked in: projects are bind-mounted at /work by linux_matrix.py.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential cmake pkg-config \
        libsdl2-dev libsdl2-ttf-dev \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work
