# GameReComp 0.4.0

Release date: 2026-11-28

## What's new

- Runtime verification runs the original Windows executable and the rebuilt
  program with identical arguments, then compares exit code, stdout and stderr.
  Lifting is not repeated; source parity is read from the pipeline record.
- Separate GUI percentages for source-code parity and run verification, with
  verbose detail for every blocked or diverged check.
- Any-input picker for Windows PE, macOS `.app`/Mach-O, Linux ELF and folders,
  with fail-closed named backend reasons.
- Batch target flags carried from the GUI into CLI batch runs.
- Rev.ng container source pass with live-socket recovery and real PTML capture.
- Linux portability fixes for generated C (aarch64 fault PC, fortified `realpath`,
  BSD socket field, absolute `.incbin` paths).
- Internal proof/evidence files are no longer shipped in the repository.

## Architecture and OS support

| Target / arch | GUI | CLI | Status |
| --- | --- | --- | --- |
| macOS arm64 | Yes | Yes | Built with cargo; smoke-tested; ad-hoc signed `.app` bundle. |
| macOS x86_64 | No | No | Cross-compilation from this host is not supported. |
| Linux x86_64 | Yes | Yes | Native ELF binaries built with cargo; Debian and Arch packages shipped. |
| Linux arm64 | No | Yes | CLI artifact only; GUI requires a Linux GUI sysroot not available here. |
| Windows x86_64 (GNU) | Yes | Yes | Built with cargo and genuine DXBC shaders. GUI not runtime-verified on Windows: Wine has no usable DXGI adapter. |
| Windows x86 (GNU) | No | No | CLI cross-link failed on missing 32-bit unwind symbols. |
| WASM | No | No | Excluded from this release. |

## Release assets (v0.4.0)

| Asset | Contents |
| --- | --- |
| `GameReComp-cli-v0.4.0-macos-arm64.tar.gz` | macOS arm64 CLI + icons |
| `GameReComp-gui-v0.4.0-macos-arm64.tar.gz` | macOS arm64 GUI + icons |
| `GameReComp-v0.4.0-macos-arm64.zip` | macOS `.app` bundle + icons |
| `GameReComp-cli-v0.4.0-windows-x86_64.zip` | Windows x86_64 GNU CLI + icons |
| `GameReComp-gui-v0.4.0-windows-x86_64.zip` | Windows x86_64 GNU GUI + icons |
| `gamerecomp_0.4.0_amd64.deb` | Debian x86_64 CLI + GUI + desktop/icons |
| `gamerecomp-0.4.0-1-x86_64.pkg.tar.zst` | Arch x86_64 CLI + GUI + desktop/icons |
| `GameReComp-cli-v0.4.0-linux-arm64.tar.gz` | Linux arm64 CLI |
| `GameReComp-v0.4.0-SHA256SUMS.txt` | SHA-256 checksums for all assets |

Every archive includes the app icon set (`.icns`, `.ico` and PNG sizes). No WASM
binary is shipped.

### Input formats

| Input | Accepted by picker | Reconstruction |
| --- | --- | --- |
| Windows PE32 i386 | Yes | Yes — the supported lifting target. |
| Windows PE32+ x86-64 | Yes | No — named refusal; 64-bit guest lifting is not implemented. |
| macOS `.app` / Mach-O | Yes | Source-level port route only; no PE32 lifting. |
| Linux ELF | Yes | Source-level port route only; no PE32 lifting. |

## Verification honesty

A percentage is a count of named checks. `0%` with a named blocker is not a pass,
and a build or process launch is never called gameplay. Runtime parity compares
observable output only; original-versus-rebuilt frame equivalence is not claimed.
Internal proof, logs and screenshots are kept outside the repository.

## Known limitations

- Frida attach may be denied on some macOS hosts; verification no longer depends
  on it.
- The Windows GUI could not be launched under Wine for a real window screenshot:
  Wine's DXGI adapter was unavailable. No fake screenshot was used.
- Gameplay claims still require same-input original-Windows oracle evidence.
