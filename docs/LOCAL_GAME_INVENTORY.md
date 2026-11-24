# Local Windows game/app inventory

Generated from the binaries you listed on this Mac. **262** executable/DLL files found; **165** are i386 PE32 (in scope for this pipeline), **87** are PE32+ x86-64 (out of scope: the PE32 backend fail-closes on them), **10** could not be parsed.

SHA-256 hashes and full paths are in `reports/local-games-inventory.json`. This is a static inventory only — it is **not** evidence that any binary was rebuilt or run.

## Named game targets

| Binary | PE | Imports | Size | SHA-256 (first 12) | Status |
| --- | --- | ---: | ---: | --- | --- |
| Doukutsu.exe | PE32 | 143 | 1444 KiB | fa18379fe942 | in scope (PE32 i386) |
| Tetris.exe | PE32+ | 258 | 2123 KiB | 846b7cf2db37 | out of scope (PE32+) |
| chocolate-doom.exe | PE32+ | 318 | 855 KiB | 15e81c194df2 | out of scope (PE32+) |
| TicTacToe.exe | PE32+ | 77 | 134 KiB | 121f24e412cc | out of scope (PE32+) |
| IGI-Injector.exe | PE32 | 125 | 475 KiB | 81e6789212bf | in scope (PE32 i386) |
| Project-IGI.exe | PE32 | 9 | 172 KiB | 3f460b89495b | in scope (PE32 i386) |
| igi.exe | PE32 | 128 | 1352 KiB | dea34892d417 | in scope (PE32 i386) |
| igi1.exe | PE32 | 176 | 14158 KiB | 4215ffb0e961 | in scope (PE32 i386) |
| igi1ed.exe | PE32 | 610 | 7952 KiB | 37c947cb137e | in scope (PE32 i386) |
| igi2.exe | PE32 | 216 | 3619 KiB | 2ae67e6ac3c3 | in scope (PE32 i386) |
| TmForever.exe | PE32 | 416 | 10434 KiB | 5e287e79a07c | in scope (PE32 i386) |
| TmNationsESWC.exe | PE32 | 259 | 8779 KiB | 822def6627f0 | in scope (PE32 i386) |
| TmNationsESWCLauncher.exe | PE32 | 277 | 2596 KiB | 4f22317a3b7b | in scope (PE32 i386) |

## What was actually built and tested (this session)

- **Cave Story `Doukutsu.exe`** — native ARM64 binary + self-contained macOS app, 143 imports/0 unresolved, 20 s alive, 2/3 non-blank 640x480 frames. See `docs/NATIVE_APP_EVIDENCE.md`.
- **Ikachan `dxIka.exe`** — native binary built, 12 s alive, but all captured frames were single-colour → no graphics proof; classified BUILD-ONLY.
- **noiz2sa / Pixeltetris** — rebuild killed by host OOM (exit 137); not tested.
- **IGI `igi.exe`** — rebuild in progress; no claim until its report and frames exist.
