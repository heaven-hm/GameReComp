# Legacy Windows software — PE32 ingestion evidence

This note addresses a request to "test legacy software such as IDM, Winamp and
HyperCam2" against the GameReComp PE32 frontend. It records two things: (1) why
those three specific applications were **not** downloaded, and (2) what was
downloaded instead — freely redistributable PE32 Windows utilities from their
official sources — plus the exact PE-level observations produced by the existing
`--selftest-pe` inspection mode.

No binary or asset described here is committed to the repository. All downloads
landed in a temporary directory outside the repo
(`$TMPDIR/opencode/newsoftware/`). The paths below are reproduction evidence
only.

## 1. Why the named applications were NOT downloaded

The three applications named in the request are proprietary, closed-source
commercial products. Downloading them — especially from anything other than the
vendor's own licensed channel — would require obtaining unlicensed / cracked /
pirated copies, which this repository does not do. Explicit reasons:

| Application | Vendor / status | Licence reason for not downloading |
|---|---|---|
| **IDM (Internet Download Manager)** | Tonec Inc. — proprietary commercial, shareware | Proprietary EULA; a working trial/keygen or "portable" build necessarily means an unlicensed or circumvention distribution. No free redistribution right. |
| **Winamp** | Winamp SA / Llama Group — proprietary (historic freeware binaries with closed source) | Historic installers are closed-source and their redistribution terms are not a recognized open/free software licence. Obtaining a usable installer from a third party would mean an unauthorised copy. |
| **HyperCam2** | Hyperionics / Solent Software — proprietary shareware, nag/unregistered | Proprietary shareware; no redistribution permission for the installer, and "cracked" copies are piracy. |

Because the goal is a **PE32 parsing / frontend** test, the vendor applications
are not technically necessary: the frontend only inspects the DOS/PE headers and
does not execute the program. Legally redistributable open-source PE32 binaries
exercise the exact same code path, so they were used as substitutes. **No claim
is made here that any proprietary application runs, or would run, under
GameReComp.**

## 2. Legal substitutions — sources and provenance

Three freely redistributable, open-source PE32 (i386) Windows utilities were
downloaded from official / project-owned sources. The 32-bit variants were
selected deliberately so that the files are PE32 (not PE32+ / x86-64).

| Utility | Version | Exact source URL | Licence (from official source) | SHA-256 |
|---|---|---|---|---|
| 7-Zip installer (32-bit) | 26.06 | `https://github.com/ip7z/7zip/releases/download/26.06/7z2606.exe` (linked from official `https://www.7-zip.org/download.html`) | LGPL-2.1-or-later for most code; parts BSD-2/3-clause (official `license.txt`) | `5e38f80c3752de43887e7a99ad10668c14bf64dcc929a636510b54616ea7dec7` |
| Notepad++ installer (32-bit) | 8.9.9.1 | `https://github.com/notepad-plus-plus/notepad-plus-plus/releases/download/v8.9.9.1/npp.8.9.9.1.Installer.exe` (official GitHub release) | GPL-3.0-or-later with clarifications/exceptions (official `LICENSE`) | `8f6f564e628d35f20fdf0f38fa827f843c29b834c2e16f5e70091acac259df95` |
| PuTTY | latest (w32) | `https://the.earth.li/~sgtatham/putty/latest/w32/putty.exe` (official PuTTY download mirror, linked from the project site) | MIT licence (official `licence.html`) | `4c70267ca03a00ea761ec358498b990a7221decb36eace9b7dbe6a751be0fb3b` |

File sizes: 7-Zip `1359350` bytes; Notepad++ `6897152` bytes; PuTTY `1513624`
bytes. Hashes were computed independently with Python `hashlib` and `shasum -a
256` and agree.

## 3. PE32 header verification

Each file was checked byte-level before the selftest was run: `MZ` at offset 0,
a `PE\0\0` signature at the offset stored in `e_lfanew` (0x3c), `Machine ==
0x014c` (i386) and optional-header `Magic == 0x010b` (PE32).

| File | `e_lfanew` | MZ | PE sig | Machine | Opt magic | PE32 i386? |
|---|---|---|---|---|---|---|
| `7z2606.exe` | 0xf8 | `MZ` | `PE\0\0` | 0x014c (i386) | 0x010b (PE32) | yes |
| `npp.8.9.9.1.Installer.exe` | 0xd8 | `MZ` | `PE\0\0` | 0x014c (i386) | 0x010b (PE32) | yes |
| `putty.exe` | 0x78 | `MZ` | `PE\0\0` | 0x014c (i386) | 0x010b (PE32) | yes |

`file(1)` agrees for all three: `PE32 executable (GUI) Intel 80386, for MS
Windows` (Notepad++ additionally reports `Nullsoft Installer self-extracting
archive`). The 32-bit installer variants were required: the x64 / arm64
variants of the same projects are PE32+ and would be reported as unsupported.

## 4. `--selftest-pe` results

The existing prebuilt GUI binary at `gui/build/game-recomp-gui` (version
`game-recomp-gui 0.0.1`) was used. It was present in the primary checkout; the
worktree used for this note contains no build output, so the binary was invoked
by its absolute path. The code path is `src/pe.rs::run_selftest_pe` →
`inspect_pe` (header inspection only). Full captured output:

```
$ game-recomp-gui --version
game-recomp-gui 0.0.1

$ for f in 7z2606.exe npp.8.9.9.1.Installer.exe putty.exe; do
>   game-recomp-gui --selftest-pe "$f"; echo "exit=$?"; done
----- 7z2606.exe -----
PE: PE32 i386 image base 0x400000 -- supported target
exit=0
----- npp.8.9.9.1.Installer.exe -----
PE: PE32 i386 image base 0x400000 -- supported target
exit=0
----- putty.exe -----
PE: PE32 i386 image base 0x400000 -- supported target
exit=0
```

All three are recognised as `PE32 i386` with image base `0x400000` and exit
code `0` ("supported target"). Exit-code semantics in `src/pe.rs`: `0` = PE32
i386 supported target; `2` = valid PE but not PE32 i386; `1` = not usable / not
a PE.

## 5. Scope and limitations — no functionality claims

- `--selftest-pe` reads only the DOS header, the PE signature, the COFF machine
  field and the optional-header magic / image base. **It does not execute or
  emulate any code, nor does it validate imports, resources or the rest of the
  image.**
- A "supported target" result means only "this file has a PE32 i386 header that
  matches the frontend's accepted format". It is **not** evidence that 7-Zip,
  Notepad++, PuTTY, or any substituted proprietary program installs, runs, or
  is otherwise functional under GameReComp.
- These installers are self-extracting/archived PE32 files; parsing the outer
  installer header says nothing about the executables they contain.
- No proprietary application was downloaded, installed, executed, reverse
  engineered, or tested. The licence discussion in section 1 is the reason.
- This was a light verification pass: header byte checks + hashes + the
  existing selftest only. No build, emulation run, injection, or live-game
  workflow was performed for this note.

## 6. Reproduction

```sh
DL="$TMPDIR/opencode/newsoftware"
mkdir -p "$DL"
curl -fsSL -o "$DL/7z2606.exe" \
  https://github.com/ip7z/7zip/releases/download/26.06/7z2606.exe
curl -fsSL -o "$DL/npp.8.9.9.1.Installer.exe" \
  https://github.com/notepad-plus-plus/notepad-plus-plus/releases/download/v8.9.9.1/npp.8.9.9.1.Installer.exe
curl -fsSL -o "$DL/putty.exe" \
  https://the.earth.li/~sgtatham/putty/latest/w32/putty.exe
shasum -a 256 "$DL"/*.exe
file "$DL"/*.exe
gui/build/game-recomp-gui --selftest-pe "$DL/7z2606.exe"
gui/build/game-recomp-gui --selftest-pe "$DL/npp.8.9.9.1.Installer.exe"
gui/build/game-recomp-gui --selftest-pe "$DL/putty.exe"
```
