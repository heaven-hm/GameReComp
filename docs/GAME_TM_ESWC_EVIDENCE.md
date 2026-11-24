# TrackMania Nations ESWC — native macOS arm64 evidence

Scope: build and run the user-supplied Windows PE32 `TmNationsESWC.exe` natively on macOS `arm64`, using this repository's `tools.autorecomp` pipeline and the pinned `win32hle` runtime. All commands below were run from the worktree root on branch `feature/p10-tmn-eswc` on 2026-11-23. No game binary or asset is stored in git; `test-results/` is gitignored.

## 1. Input and identity — PASS

| Item | Value |
| --- | --- |
| Supplied path | `/Users/haseeb-mir/Library/TrackManiaMacOS/games/tmn-eswc/prefix/drive_c/Program Files (x86)/TmNationsESWC/TmNationsESWC.exe` |
| SHA-256 | `822def6627f020035ef0819cf9f85493e952a2efb450d23770b330311ad8109d` |
| Size | 8,989,696 bytes |
| `file` | `PE32 executable (GUI) Intel 80386 (stripped to external PDB), for MS Windows` |
| PE | i386 PE32, image base `0x00400000`, entry `0x00402F4B`, linker 7.00, Windows GUI, timestamp 2019-05-23 23:05:19 UTC |
| Sections | `.text`, `.rdata`, `.data`, `.rsrc`, `.sse` |
| Code range | `0x00401000`–`0x00CE8A00` (9,337,344 bytes) |

```sh
EXE='/Users/haseeb-mir/Library/TrackManiaMacOS/games/tmn-eswc/prefix/drive_c/Program Files (x86)/TmNationsESWC/TmNationsESWC.exe'
EV='test-results/game-tmn-eswc'
PROJ="$TMPDIR/opencode/projects/tmn-eswc"   # /var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/projects/tmn-eswc
shasum -a 256 "$EXE"     # 822def66...10d9d
file "$EXE"
```

Companion-file hashes (original EXE, launcher, installer, `binkw32.dll`, `unicows.dll`) are in `test-results/game-tmn-eswc/companion_hashes.txt`.

## 2. Import inspection — PASS

```sh
python3 tools/pe/extract_imports.py "$EXE" > "$EV/imports.txt"
python3 tools/pe/pe_analyze.py "$EXE" --hash > "$EV/pe_analyze.txt"
python3 tools/pe/pe_analyze.py "$EXE" --json "$EV/pe_analyze.json"
```

Result: **259 imports from 15 DLLs**.

| DLL | Imports | DLL | Imports |
| --- | --- | --- | --- |
| `kernel32.dll` | 126 | `advapi32.dll` | 5 |
| `user32.dll` | 29 | `ole32.dll` | 3 |
| `ws2_32.dll` | 28 | `rpcrt4.dll` | 3 |
| `avifil32.dll` | 22 | `gdi32.dll` | 2 |
| `binkw32.dll` | 16 | `oleaut32.dll` | 2 |
| `wininet.dll` | 10 | `d3d9.dll` | 1 (`Direct3DCreate9`) |
| `winmm.dll` | 10 | `dinput8.dll` | 1 (`DirectInput8Create`) |
| `dsound.dll` | 1 (ordinal 12) | | |

This title uses **Direct3D 9** and **DirectInput 8**; the pinned runtime implements D3D7/DirectDraw and `dinput`, not those interfaces. Movies use `binkw32.dll` (HLE-backed, and its DLL is bundled beside the EXE).

## 3. Native rebuild

Command (exactly as requested):

```sh
python3 -m tools.autorecomp "$EXE" --project-dir "$PROJ" --no-run --no-table > "$EV/autorecomp_build.log" 2>&1
```

### 3.1 Attempt 1 on the stock tree — FAIL

Exit code **2**, 990.4 s spent in `recover`:

```
==> inspect
    ok         0.5s  inspect: i386 PE32, 259 imports, entry 0x00402F4B
==> recover
    failed   990.4s  recover: TypeError: object of type 'NoneType' has no len()
==> report
    ok         0.1s  report: reports in .../reports/report.html
score:        20/100
verification: not-run
failed stages: recover
AUTORECMP_EXIT=2
```

Traceback (copied verbatim to `test-results/game-tmn-eswc/build-failure-traceback.txt`):

```
  File ".../tools/disasm/disasm32.py", line 1143, in find_functions
    close_dispatch_targets(lambda va, n: self.read_bytes(va, n), sizes,
  File ".../tools/disasm/disasm32.py", line 1493, in close_dispatch_targets
    for ins in md.disasm(code, addr):
  File ".../site-packages/capstone/__init__.py", line 1170, in disasm
    size = len(code)
TypeError: object of type 'NoneType' has no len()
```

`read_bytes()` returns `None` for a clamped function extent outside every mapped section; the `try/except Exception` cannot catch a normal `None` return.

### 3.2 Minimal fix and attempt 2 — PASS

```diff
--- a/tools/disasm/disasm32.py
+++ b/tools/disasm/disasm32.py
@@ -1489,6 +1489,8 @@ def close_dispatch_targets(read_va, functions, code_start, code_end,
                 code = read_va(addr, size)
             except Exception:
                 continue
+            if not code:
+                continue
             end = addr + size
             for ins in md.disasm(code, addr):
```

`python3 tools/disasm/disasm32.py --selftest` → `disasm32.py self-test OK`. The same `autorecomp` command was rerun into `test-results/game-tmn-eswc/autorecomp_build2.log`:

```
==> inspect
    ok         1.5s  inspect: i386 PE32, 259 imports, entry 0x00402F4B
==> recover
    ok      1204.5s  recover: 85216 functions, 10854095 instructions
==> translate
    ok        98.6s  translate: 163 source files, 1128868245 bytes, 0 lift errors
==> project
    ok         0.4s  project: project at .../projects/tmn-eswc
==> compile
    ok      2291.3s  compile: .../build/TmNationsESWC (340894096 bytes, 2289.5s)
==> run
    ok         0.0s  run: run skipped (--no-run)
==> verify
    ok         0.1s  verify: not-run: the rebuilt binary was not run
==> report
    ok         2.3s  report: reports in .../reports/report.html
stages:       inspect(ok), recover(ok), translate(ok), project(ok), compile(ok), run(ok), verify(ok), report(ok)
score:        70/100
verification: not-run
AUTORECMP_EXIT=3
```

Exit 3 is the documented `partial` outcome of `--no-run` (no runtime oracle), not a build failure.

| Rebuilt binary | Value |
| --- | --- |
| Path | `$PROJ/build/TmNationsESWC` |
| Format | `Mach-O 64-bit executable arm64` |
| Size | 340,894,096 bytes |
| SHA-256 | `6419828d89176e1bf4ce3a480669179e733d4d2223ba0236c4e19e1ca9db56a4` |

## 4. Runtime attempts

The rebuilt image embeds the original EXE and the companion DLLs, so the original EXE is passed only to name the assets directory. Screenshot capture is enabled with `RECOMP_SCREENSHOT=<directory>` (the runtime treats the value as a directory name; `RECOMP_SCREENSHOT=1` literally creates a directory named `1`). `RECOMP_SCREENSHOT_MAX=6`, `RECOMP_SCREENSHOT_EVERY=1000 ms`. Each run used `/usr/bin/timeout 30s`.

### 4.1 Strict boot, `SDL_VIDEODRIVER=cocoa` — FAIL

```sh
cd "$EV"
env SDL_VIDEODRIVER=cocoa SDL_AUDIODRIVER=dummy \
    RECOMP_SCREENSHOT="$PWD/frames-cocoa" RECOMP_SCREENSHOT_MAX=6 RECOMP_SCREENSHOT_EVERY=1000 \
    /usr/bin/timeout 30s "$BIN" "$EXE" > run-cocoa-strict.log 2>&1
```

Exit **2**. Tail of `run-cocoa-strict.log`:

```
[pe] unresolved import ws2_32.dll#1
[pe] unresolved import d3d9.dll!Direct3DCreate9
[pe] bound 195 imports, 64 unresolved
[host] C:\TmNationsESWC.exe booted: entry 0x00402F4B, 64 unresolved imports
```

Unresolved import groups (from the same log): `ws2_32.dll` 28, `avifil32.dll` 17, `wininet.dll` 7, `kernel32.dll` 5, `rpcrt4.dll` 3, `winmm.dll` 2, plus `USER32.dll!GetClassLongA`, `dinput8.dll!DirectInput8Create`, `dsound.dll#12`, `d3d9.dll!Direct3DCreate9`. Frames written: **0**.

### 4.2 Strict boot, `SDL_VIDEODRIVER=dummy` — FAIL

```sh
cd "$EV"
env SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \
    RECOMP_SCREENSHOT="$PWD/frames-dummy" RECOMP_SCREENSHOT_MAX=6 RECOMP_SCREENSHOT_EVERY=1000 \
    /usr/bin/timeout 30s "$BIN" "$EXE" > run-dummy-strict.log 2>&1
```

Exit **2**; same `bound 195 imports, 64 unresolved` boot refusal. Frames written: **0**.

### 4.3 Permissive boot, `SDL_VIDEODRIVER=dummy` — FAIL (crash)

```sh
cd "$EV"
env SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy RECOMP_PERMISSIVE=1 \
    RECOMP_SCREENSHOT="$PWD/frames-permissive" RECOMP_SCREENSHOT_MAX=6 RECOMP_SCREENSHOT_EVERY=1000 \
    /usr/bin/timeout 30s "$BIN" "$EXE" > run-dummy-permissive.log 2>&1
```

Exit **138** (signal 10 / SIGBUS). Complete log:

```
[screenshot] writing up to 6 frame(s) to .../frames-permissive, at most one every 1000 ms
[pe] <embedded image> mapped at 0x00400000, span 0x8E9000
[pe] <embedded image> mapped at 0x18000000, span 0x38000
[pe] unresolved import USER32.dll!GetClassLongA
[pe] unresolved import WINMM.dll!waveOutRestart
[pe] unresolved import WINMM.dll!waveOutPause
[pe] bound 117 imports, 3 unresolved
[pe] companion binkw32.dll: 3 unresolved imports
[pe] companion binkw32.dll mapped at 0x18000000 (66 exports)
[pe] bound 259 imports, 0 unresolved
[host] C:\TmNationsESWC.exe booted: entry 0x00402F4B, 0 unresolved imports

[win32hle] fatal signal 10 at 0x707e00003c (guest VA 0x7E00003C) in lifted function 0x00410338, esp 0x104FFEB4, pc 0x100114390
```

Guest function `sub_00410338` (in `src/recomp/gen/recomp_0000.c`) contains `RECOMP_ICALL(MEM32(0xACA0B0))` — an indirect call through IAT slot `0xACA0B0` (`kernel32!CompareStringA`). The run dies immediately after boot, before any surface is presented. Frames written: **0**.

### 4.4 Permissive boot, `SDL_VIDEODRIVER=cocoa` — FAIL (same crash)

```sh
cd "$EV"
env SDL_VIDEODRIVER=cocoa SDL_AUDIODRIVER=dummy RECOMP_PERMISSIVE=1 \
    RECOMP_SCREENSHOT="$PWD/frames-cocoa-permissive" RECOMP_SCREENSHOT_MAX=6 \
    /usr/bin/timeout 30s "$BIN" "$EXE" > run-cocoa-permissive.log 2>&1
```

Exit **138**, identical `fatal signal 10 … guest VA 0x7E00003C … lifted function 0x00410338`. Frames written: **0**.

### 4.5 Literal `RECOMP_SCREENSHOT=1` — FAIL (no frames)

```sh
mkdir -p "$EV/literal-1"; cd "$EV/literal-1"
env SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy RECOMP_PERMISSIVE=1 \
    RECOMP_SCREENSHOT=1 RECOMP_SCREENSHOT_MAX=6 \
    /usr/bin/timeout 30s "$BIN" "$EXE" > "$EV/run-literal-screenshot.log" 2>&1
```

Exit **138**. The hook did install (`[screenshot] writing up to 6 frame(s) to 1, at most one every 250 ms`) and created `literal-1/1/`, but the guest crashed before presenting a frame; that directory is empty.

**Frame totals:** `frames-cocoa` 0, `frames-dummy` 0, `frames-permissive` 0, `frames-cocoa-permissive` 0, `literal-1/1` 0. All raw logs and the driver/run commands are preserved under `test-results/game-tmn-eswc/`.

## 5. Verdicts

| Stage | Verdict | Basis |
| --- | --- | --- |
| Binary identification and hashing | **PASS** | SHA-256 and PE metadata recorded from the supplied file. |
| Import inspection | **PASS** | 259 imports from 15 DLLs enumerated and saved. |
| Native rebuild on stock tree | **FAIL** | `recover` crashed on `None` passed to Capstone; exit 2. |
| Native rebuild with one-line guard | **PASS** | 85,216 functions, 1.13 GB C, 0 lift errors, arm64 Mach-O binary linked; exit 3 only because `--no-run` was requested. |
| Strict execution (cocoa/dummy) | **FAIL** | Boot refused: 195 imports bound, 64 unresolved, including `Direct3DCreate9` and `DirectInput8Create`; exit 2. |
| Permissive execution (cocoa/dummy) | **FAIL** | Boot reached the guest entry point, then SIGBUS at guest VA `0x7E00003C` in `sub_00410338`; exit 138. |
| Graphics / frame capture | **FAIL** | Capture hook installed in all five runs; zero frames were written because the guest never presented a surface. |
| Gameplay scenario | **NOT TESTED** | Unreachable: no boot and no rendered frame. No oracle was supplied. |

**Overall: FAIL for native execution.** The title builds natively for macOS arm64 after a genuine pipeline bug fix, but it does not boot strictly (missing D3D9/DirectInput8 and many other shims) and crashes before rendering in permissive mode. No gameplay equivalence claim is made.

Follow-up work, if pursued: implement the missing import shims (notably `d3d9!Direct3DCreate9`, `dinput8!DirectInput8Create`, `dsound` ordinal 12, the `avifil32`/`wininet`/`ws2_32` ordinal surface) and diagnose the `0xACA0B0` indirect-call crash, then rerun with a frame oracle.
