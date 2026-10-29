# Licences and third-party components

## This repository

MIT, copyright 2026 sp00nz — see `LICENSE`.

`NOTICE` is the authoritative inventory of third-party code vendored into this
repository, with the upstream project, version and licence text location for
each entry. At the time of writing it lists the PKWARE DCL explode port in
`tools/assets/is3extract.py` (zlib, `LICENSES/blast-zlib.txt`). New vendored
code must be added there, not only here.

## Runtime dependencies of generated projects

Generated projects link against host libraries; none are vendored:

| Dependency | Licence | Required |
|---|---|---|
| SDL2 | zlib | yes |
| SDL2_ttf | zlib | yes |
| pthreads / host libc | host licence | yes |
| FFmpeg (libavformat, libavcodec, libavutil, libswresample) | LGPL-2.1-or-later for a default build; a GPL-configured FFmpeg makes the linked binary GPL | optional |

If FFmpeg is present, the generated project enables Bink/Miles decoding with
it; the licence of the resulting binary is the user's responsibility and
depends on how their FFmpeg was built.

## Optional analysis dependencies

Some analysis tools import **Capstone** (BSD-3-Clause) when it is installed;
it is not vendored. Tools without it continue where the code path is optional.

The runtime substitutes the host's installed **Liberation** font family for
Arial/MS Sans Serif metrics through SDL2_ttf; no font files are vendored.

## External tools that are invoked, not vendored

- **rev.ng** — the readability harness runs a user-installed rev.ng through
  `REVNG_BIN`/`REVNG_DECOMPILE_CMD`. Its licence and distribution terms are
  those of the user's rev.ng installation.
- **AI readability command** — whatever the user configures in `REVENG_AI_CMD`;
  this repository ships no model or service integration.

## Game inputs

No game executable, asset or derived source of a third-party game is
distributed in this repository. Generated projects contain code derived from
the user's own binary; rights in the original game remain with its owner, and
the generated output must not be redistributed except as that licence allows.
