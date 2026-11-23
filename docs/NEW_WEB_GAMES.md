# Newly downloaded web PE32 games (B2 / swarm3)

Three **new** legally redistributable Windows PE32 (i386) games, downloaded from
official project sources. **No game binary or asset is committed to this
repository.** All files live in a temporary out-of-repo directory; the paths
below are reproduction evidence only.

- Base: `d19c82a226138f9d36a69346ff5cbb8af5cab6a8`
- Download/extraction root:
  `/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/newgames-web/`
- None of these three games appear anywhere in the base tree (`grep -rilE
  'prboom|dhewm3|openttd'` returned no files). The already-tested OUCC Tetris,
  Chocolate Doom and Noiz2sa were deliberately not reused.

## Games (small / medium / large by game-exe size)

| Tier | Game | Exact download URL | Licence (from official source) | Game exe | Exe bytes | Exe SHA-256 |
|---|---|---|---|---|---|---|
| small | PrBoom+ 2.5.1.7um (`prboom-plus.exe`) | `https://github.com/coelckers/prboom-plus/releases/download/v2.5.1.7um/prboom-plus-2.5.1.7um-win32.zip` | GPL v2 (`prboom2/COPYING` in the upstream repo: "GNU GENERAL PUBLIC LICENSE, Version 2, June 1991") | `exes/prboom-plus.exe` | 2,148,352 | `d2c5e9ca3af1514464f9b93407915176b652d058bf46abf1538962d17bc1a643` |
| medium | dhewm3 1.5.1 (`dhewm3.exe`) | `https://github.com/dhewm/dhewm3/releases/download/1.5.1/dhewm3_1.5.1_win32.zip` | GNU GPL v3 (`dhewm3/COPYING.txt` in archive) plus id Software's Doom 3 GPL *additional terms / excluded code* notice (`dhewm3/README.txt`, "LICENSE" section); bundled-library licences in `LibLicenses.txt` | `exes/dhewm3.exe` | 14,598,144 | `e9eb30c0de3be3f0b08d169351feab335de3347550d43e788a9085534f9fd5d5` |
| large | OpenTTD 15.3 win32 (`openttd.exe`) | `https://cdn.openttd.org/openttd-releases/15.3/openttd-15.3-windows-win32.zip` | GPL v2 (`COPYING.md` in archive: "GNU General Public License, Version 2, June 1991") | `exes/openttd.exe` | 15,055,368 | `36d5a04ae5a34886dd87fff814d9d560ca4f6ac5d18e269a998360436ed8e62e` |

### Downloaded archives (SHA-256, size)

| Archive | Bytes | SHA-256 |
|---|---|---|
| `prboom-plus-2.5.1.7um-win32.zip` | 10,085,877 | `29a3a694c882cdfb7c4566a01db4a0f9b1baf56e701539c9e418866b93d4284f` |
| `dhewm3_1.5.1_win32.zip` | 9,527,763 | `b20cf24ef12d037621f83dfa17e9cd0d46b1d6af4659abdb32137d7802b2c5e2` |
| `openttd-15.3-windows-win32.zip` | 10,370,347 | `3f092edc8f381c3d2d3a59458703899da6f876345b3850a3c76c0dffe68f0e74` |

Provenance cross-check: the OpenTTD archive SHA-256 above matches the value
published on the official OpenTTD 15.3 download page for the 32-bit zip
(`3f092edc8f381c3d2d3a59458703899da6f876345b3850a3c76c0dffe68f0e74`).
The `cdn.openttd.org` host is the project's own binary CDN linked from
`https://www.openttd.org/downloads/openttd-releases/latest`.

## PE header verification (`struct`, no third-party parser needed)

Each game exe was read as bytes and checked with Python `struct`:

- `d[:2] == b'MZ'`
- PE offset `struct.unpack_from('<I', d, 0x3c)[0]`, signature `b'PE\x00\x00'`
- COFF machine `struct.unpack_from('<H', d, pe+4)[0] == 0x014c` (IMAGE_FILE_MACHINE_I386)
- Optional-header magic `struct.unpack_from('<H', d, pe+24)[0] == 0x010b` (PE32, not PE32+)

| Game exe | MZ | PE | machine | opt magic | Verdict |
|---|---|---|---|---|---|
| `prboom-plus.exe` | yes | yes | `0x014c` | `0x010b` | PE32 i386 |
| `dhewm3.exe` | yes | yes | `0x014c` | `0x010b` | PE32 i386 |
| `openttd.exe` | yes | yes | `0x014c` | `0x010b` | PE32 i386 |

## PE import counts (`python3 -m tools.api_coverage`)

Command run from the repository worktree, with the JSON report written
**outside** the repo (so only this doc is added to git):

```sh
python3 -m tools.api_coverage \
  --corpus /private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/newgames-web/exes \
  --out /private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/newgames-web/api-coverage-web.json
```

Auditor summary: `3 inputs, 112 system missing APIs, 210 game-owned missing
APIs, 0 system ordinal imports`.

| Game exe | DLLs referenced | Total PE imports | Names matching a registered shim | Unregistered names |
|---|---|---|---|---|
| `prboom-plus.exe` | 14 | 295 | 193 | 102 |
| `dhewm3.exe` | 30 | 1,214 | 1,086 | 128 |
| `openttd.exe` | 14 | 356 | 247 | 109 |

"Matching a registered shim" is the auditor's *name-registration* rule only —
it is not proof that any function behaves correctly. See
`docs/WIN32_API_COVERAGE.md` for the counting rule and caveats.

## Candidates examined and excluded

- **Simon Tatham's Portable Puzzles** (`cube.exe` etc., MIT, official
  `https://www.chiark.greenend.org.uk/~sgtatham/puzzles/`) — tiny and perfectly
  licensed, but the current official binaries are **PE32+ x86-64**
  (`machine=0x8664`, `opt magic=0x020b`), so they fail the PE32/i386 requirement.
  Downloaded and checked, then excluded.
- **SuperTuxKart 1.3** (GPL, official GitHub release) — the `win.zip` is
  ~748 MB and the 32-bit build is distributed as an installer, not a plain game
  exe; excluded on size/form.
- **SuperTux 0.6.3 / 0.7.0.1 win32** — official builds are MSIs
  (178 MB / 314 MB) that would need extraction; excluded on size.
- **Crispy Doom** — technically eligible (GPL, official win32 zip) but too close
  to the already-tested Chocolate Doom; excluded to keep the set genuinely new.
- Any candidate whose licence could not be read from an official LICENSE/COPYING/
  README file was not downloaded. itch.io/SF mirrors were not used as sources.

## Limitations

- **Verification only.** No rebuild, no lift, no injection and no execution was
  performed for any of these games ("VERIFY light only"). No PASS, gameplay,
  rendering or API-behaviour claim is made.
- Each game needs external data that is not part of the freely redistributable
  binary and was **not** downloaded: a DOOM WAD for PrBoom+, the original Doom 3
  data for dhewm3, and a base graphics set (e.g. freely available OpenGFX) for
  OpenTTD. This is an import/binary audit, not a play test.
- Import numbers are registration-based name counts from `api_coverage.py`; the
  high unregistered counts are expected for large SDL/DirectX-era titles and are
  a coverage finding, not a claim that the games run.
- All paths and hashes are reproduction evidence; the binaries are deliberately
  absent from the repository and from this commit.
