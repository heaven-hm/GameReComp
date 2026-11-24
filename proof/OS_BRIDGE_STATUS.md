# OS bridge status (measured)

| Direction | Status | Proof in this tree | Remaining blocker |
|---|---|---|---|
| Windows PE32 x86 -> macOS arm64 | PARTIAL | `binaries/windows/cube.exe`, `binaries/macos/cube-native`, `screenshots/macos/cube-native.bmp` | No Windows-side controlled scenario: cannot be PASS |
| Windows PE32 x86 -> Linux | NOT TESTED | none in this tree | Cross-target builds refused on this macOS host; no Linux runner here (CI job configured in `docs/ci/github-workflows-ci.yml`, not executed) |
| Windows x64 PE32+ / .NET | DEFERRED | none | CLI refuses with named diagnostic; no x64/CLR lift path |
| macOS Mach-O -> Windows | DEFERRED | none | No PE writer or Win32 runtime; feasibility issue #132/#135 |
| macOS Mach-O -> Linux | DEFERRED | none | `tools/macho/` lifts NeXTSTEP i386 to the host only; no Linux host here |
| Linux ELF -> Windows / macOS | DEFERRED | none | No ELF reader; feasibility issues #137/#138 |

Commands used for the proven row:

```
python3 -m tools.autorecomp <original.exe> --project-dir <tmp> --assets-dir <game-dir> --out-mode full --timeout 12 --no-table
SDL_VIDEODRIVER=cocoa SDL_AUDIODRIVER=dummy RECOMP_SCREENSHOT=<out> RECOMP_SCREENSHOT_MAX=3 RECOMP_SCREENSHOT_EVERY=500 <native-binary>
```

Full 50-game E2E results: `~/Downloads/recomp-apps/END_TO_END_PROOF/AGGREGATE_REPORT.md` (PASS 0, PARTIAL and FAIL rows with hashes and frame colours).
