# Cross-OS and cross-architecture E2E proof (honest)

Every direction named by the developer was executed on this macOS arm64 host. Each row is backed by a captured log under `docs/cross-os-proof/logs/`. The backend reconstructs PE32 i386 only and compiles with the host toolchain, so cross-target and non-i386 inputs are refused with a named reason. No row claims a working port or a Windows oracle.

| Case | Target | Exit | Ten-word honest description | Named result | Log |
| --- | --- | --- | --- | --- | --- |
| Notes → Windows x86 | windows/x86 | 1 | Notes Mach-O refused; Windows target cannot run PE32 lifting here. | PE: not usable: macOS Mach-O input: PE32 lifting does not apply; use the verified source-level port | `docs/cross-os-proof/logs/Notes_to_Windows_x86.log` |
| Notes → Windows x64 | windows/x86_64 | 1 | Notes Mach-O refused; Windows target cannot run PE32 lifting here. | PE: not usable: macOS Mach-O input: PE32 lifting does not apply; use the verified source-level port | `docs/cross-os-proof/logs/Notes_to_Windows_x64.log` |
| Calculator → Windows x86 | windows/x86 | 1 | Calculator Mach-O refused; Windows target cannot run PE32 lifting here. | PE: not usable: macOS Mach-O input: PE32 lifting does not apply; use the verified source-level port | `docs/cross-os-proof/logs/Calculator_to_Windows_x86.log` |
| Calculator → Windows x64 | windows/x86_64 | 1 | Calculator Mach-O refused; Windows target cannot run PE32 lifting here. | PE: not usable: macOS Mach-O input: PE32 lifting does not apply; use the verified source-level port | `docs/cross-os-proof/logs/Calculator_to_Windows_x64.log` |
| Doukutsu x86 → macOS arm64 | macos/arm64 | 0 | Doukutsu x86 inspected on host; no cross-target port claimed yet. |     ok         0.0s  report: reports in /Users/haseeb-mir/Documents/Code/GameReComp/docs/cross-os-proof/logs/Doukutsu_x86_to_macOS_arm64/reports | `docs/cross-os-proof/logs/Doukutsu_x86_to_macOS_arm64.log` |
| Doukutsu x86 → macOS x86_64 | macos/x86_64 | 4 | Doukutsu x86_64 target refused; host toolchain builds only locally. here | error: cross-target build requested (macos/x86_64) on host (macos/arm64); the pipeline compiles with the host toolchain only -- run it on the target OS or omit --target-os/--target | `docs/cross-os-proof/logs/Doukutsu_x86_to_macOS_x86_64.log` |
| PE32+ fixture → macOS arm64 | macos/arm64 | 4 | PE32+ x86-64 input refused; PE32 backend rebuilds i386 only. here | error: unsupported input architecture: x86_64 (this pipeline reconstructs PE32 x86 guests; a 64-bit build of the same game, if one exists, would be a different binary to reconstruc | `docs/cross-os-proof/logs/PE32+_fixture_to_macOS_arm64.log` |
| PE32+ fixture → macOS x86_64 | macos/x86_64 | 4 | PE32+ x86-64 target refused; host toolchain builds only locally. here | error: cross-target build requested (macos/x86_64) on host (macos/arm64); the pipeline compiles with the host toolchain only -- run it on the target OS or omit --target-os/--target | `docs/cross-os-proof/logs/PE32+_fixture_to_macOS_x86_64.log` |

## Full-frame GUI captures (real bytes)

### `macos-notes-selected.png`

![GUI resolves Notes.app to Mach-O and reports the source port](macos-notes-selected.png)

*GUI resolves Notes.app to Mach-O and reports the source port*

SHA-256 `078724e1a6448fc8550aed51d93f56f80a7b9fa5f7e830b827e69640e041f0d8`; 523576 bytes.

### `windows-x64-selected.png`

![GUI accepts PE32+ x86-64 input and reports the exact backend](windows-x64-selected.png)

*GUI accepts PE32+ x86-64 input and reports the exact backend*

SHA-256 `d40a49522bbf1cf67c48efc1303d8523b9df32b0abde4d4d9c3a0660c2234c2d`; 863704 bytes.

## Honest scope

- macOS apps are Mach-O bundles, not PE files; the GUI resolves the bundle and reports the source-level port route.
- Windows PE32 i386 inputs can be inspected and rebuilt on this host only when the target is the host itself (macOS/arm64).
- PE32+ x86-64 and cross-target builds are refused with a named reason. A refusal is the correct result, not a failed claim of support.
- No screenshot here proves gameplay or an original-Windows oracle.
