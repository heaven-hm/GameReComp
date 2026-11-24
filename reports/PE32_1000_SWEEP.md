# 1,000-binary Windows PE32 static sweep

Analyzed **1000** distinct Windows PE32 binaries (923 `.exe`, 77 `.dll`) found on this Mac in 8.8s.
All **1000** parsed successfully; SHA-256 recorded for each. Combined size 762 MiB, 98,436 imports resolved by the analyzer.

## What this is — and is not

This is a **static PE analysis sweep**: DOS/COFF/optional headers, sections, import tables, entry points and hashes. It proves the binaries are detectable and parseable by the pipeline. It is **not** a native recompilation, launch, render or gameplay result. No row here is classified as working on macOS.

The full machine-readable result is `reports/pe32-1000-static.json`. The complete local PE inventory (9,772 files: 4,926 PE32 and 4,846 PE32+) is `reports/all-windows-binaries.json`.

## Most common imported DLL families

| DLL | binaries |
| --- | ---: |
| kernel32.dll | 954 |
| ucrtbase.dll | 597 |
| ntdll.dll | 501 |
| user32.dll | 372 |
| gdi32.dll | 238 |
| advapi32.dll | 231 |
| api-ms-win-crt-runtime-l1-1-0.dll | 211 |
| api-ms-win-crt-heap-l1-1-0.dll | 207 |
| api-ms-win-crt-stdio-l1-1-0.dll | 203 |
| comctl32.dll | 202 |
| api-ms-win-crt-math-l1-1-0.dll | 201 |
| api-ms-win-crt-locale-l1-1-0.dll | 193 |

## Largest PE32 executables in the sweep

| File | Size | Imports | SHA-256 (12) |
| --- | ---: | ---: | --- |
| `CheatEngine75.exe` | 29223 KiB | 141 | `93bc218fa795` |
| `libstdc++-6.dll` | 26036 KiB | 169 | `2ce0c1d38981` |
| `Silk.NET.Direct3D12.dll` | 14983 KiB | 1 | `daffce41bdae` |
| `igi1.exe` | 14158 KiB | 176 | `4215ffb0e961` |
| `micro.exe` | 11890 KiB | 46 | `b0f3d6ef0f32` |
| `idman643build18.exe` | 11879 KiB | 104 | `7346128fab42` |
| `mastermind_x86.exe` | 11106 KiB | 137 | `dc637c1e1961` |
| `tictactoe_x86.exe` | 11020 KiB | 137 | `c9ef98aefc7a` |

## Scope limits

- The 4,846 PE32+ (x64) binaries are **out of scope**: the PE32 backend fail-closes on them (issue #13). None was recompiled.
- Only a handful of the 1,000 were actually rebuilt and launched: Cave Story (native app + frames), OUCC Tetris (frames), base64/arith-calc/busybox (Wine-oracle process PASS). See `evidence/README.md`.
- Full native rebuild of 1,000 binaries is not feasible here: builds take minutes to hours, and two builds in this session were killed by host OOM (exit 137).
- No issue is closed on static evidence.
