# GameReComp 0.4.0

Release date: 2026-11-28

## What's new

- **Runtime verification, not repeated lifting.** `game-recomp verify` (and the
  GUI **Verify** button) now runs the original Windows executable and the rebuilt
  program with identical arguments, then compares exit code, stdout and stderr.
  Source parity is read from the value recorded by the original pipeline run;
  lifting and recompilation are not repeated.
- **Separate GUI percentages.** Source-code parity and run verification are shown
  as two independent KPIs, with a detail panel containing the exact reason a check
  was blocked or diverged.
- **Any-input picker.** A direct path field accepts Windows PE, macOS `.app` /
  Mach-O, Linux ELF or a folder. Unsupported formats are still accepted by the UI
  and fail closed in the backend with a named reason.
- **Batch targets.** The target selected in the Home configurator is carried into
  batch jobs as `--target-os` / `--target-arch`, so a Linux host can batch to
  Linux while other hosts get the exact cross-target refusal.
- **Rev.ng source pass.** The bundled container wrapper starts a live Colima
  socket when needed and captures real PTML for source-provenance work.
- **Linux portability.** Generated C now builds on Linux/aarch64 with fixes for
  aarch64 fault PCs, fortified `realpath`, BSD-only socket fields and absolute
  `.incbin` paths.
- **Cleaner repository.** Internal proof/evidence files are no longer shipped;
  release notes and user guides remain.

## Architecture and OS support

| Target / arch | GUI | CLI | Status |
| --- | --- | --- | --- |
| macOS arm64 | Yes | Yes | Release artifacts built and smoke-tested on this host. |
| macOS x86_64 | No | No | Cross-compilation from this host is not supported. |
| Linux aarch64 | No (blocked here) | Yes | CLI artifact built; GUI needs a Linux host with GUI sysroot libs. |
| Linux x86_64 | Yes | Yes | Source builds on a Linux/x86_64 host; not produced from macOS host here. |
| Windows x86_64 (GNU) | No | Yes | CLI artifact built via mingw-w64; GUI needs Windows SDK fxc.exe. |
| Windows x86 (GNU) | No | No | CLI cross-link failed: missing 32-bit native unwind symbols. |

## Release assets (v0.4.0)

| Asset | Contents |
| --- | --- |
| `GameReComp-cli-v0.4.0-macos-arm64.tar.gz` | macOS arm64 CLI |
| `GameReComp-gui-v0.4.0-macos-arm64.tar.gz` | macOS arm64 GUI |
| `GameReComp-cli-v0.4.0-linux-arm64.tar.gz` | Linux aarch64 CLI |
| `GameReComp-cli-v0.4.0-windows-x86_64.zip` | Windows x86_64 GNU CLI |
| `GameReComp-v0.4.0-SHA256SUMS.txt` | SHA-256 checksums for all assets |

No Windows GUI, Linux GUI, or WASM binary is shipped in this release.

### Input formats

| Input | Accepted by picker | Reconstruction |
| --- | --- | --- |
| Windows PE32 i386 | Yes | Yes — the supported lifting target. |
| Windows PE32+ x86-64 | Yes | No — named refusal; 64-bit guest lifting is not implemented. |
| macOS `.app` / Mach-O | Yes | Source-level port route only; no PE32 lifting. |
| Linux ELF | Yes | Source-level port route only; no PE32 lifting. |

## Verification honesty

A percentage is a count of named checks. `0%` with a named blocker is not a
pass, and a build or process launch is never called gameplay. Internal proof,
logs and screenshots are kept outside the repository and are not release assets.

## Known limitations

- Original-versus-rebuilt frame equivalence is not claimed; runtime parity
  compares observable output only.
- Frida attach may be denied on some macOS hosts; verification no longer depends
  on it.
- Gameplay claims still require same-input original-Windows oracle evidence.
