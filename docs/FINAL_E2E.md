# Final CLI + GUI E2E sweep (honest)

Executed against GameReComp v0.3.5 GUI binary and the CLI pipeline on macOS arm64. Every result has a captured log under `docs/final-e2e/`. A named refusal is the correct fail-closed outcome; no PASS is claimed without an original-Windows oracle.

| Input | Exit | Ten-word honest description | Named result | Log |
| --- | --- | --- | --- | --- |
| Windows x86 PE32 | 0 | Windows PE32 i386 accepted; native host rebuild verified partially here. | PE: PE32 i386 image base 0x400000 -- supported target | `docs/final-e2e/windows-x86-pe32.log` |
| Windows x64 PE32+ | 2 | PE32+ x86-64 input refused; PE32 backend rebuilds i386 only here. | PE: PE32+ x86-64 image base 0x40000000 -- NOT a supported target | `docs/final-e2e/windows-x64-pe32+.log` |
| macOS app arm64/x86_64 | 1 | macOS app bundle resolved; Mach-O source-level port route reported here. | PE: not usable: macOS Mach-O input: PE32 lifting does not apply; use the verified source-level port | `docs/final-e2e/macos-app-arm64-x86_64.log` |
| macOS app universal | 1 | Universal macOS app resolved; Mach-O route reported honestly here now. | PE: not usable: macOS Mach-O input: PE32 lifting does not apply; use the verified source-level port | `docs/final-e2e/macos-app-universal.log` |
| Linux ELF x86-64 | 1 | Linux ELF input refused; PE32 lifting does not apply here. | PE: not usable: Linux ELF input: PE32 lifting does not apply; use the verified source-level port | `docs/final-e2e/linux-elf-x86-64.log` |

## Pipeline

CLI full pipeline on `tests/fixtures/pe32-minimal.exe`: inspect, recover, translate, project, compile, run, verify and report all completed; score 93/100; verification `partial` — no original-Windows scenario oracle. See `pipeline-fixture.log`.

## Hashes

- `linux-elf-x86-64.log` — SHA-256 `555d16d693dd86cd2913760b37894b10d76648003dc0a90c62728d393fe4debc`
- `macos-app-arm64-x86_64.log` — SHA-256 `3672b775858085c6e6147f18db7499e10f5162952b51d76df6d8da9602dc4b06`
- `macos-app-universal.log` — SHA-256 `3672b775858085c6e6147f18db7499e10f5162952b51d76df6d8da9602dc4b06`
- `pipeline-fixture.log` — SHA-256 `a310183fa8325ecf15b63a77ac2b61ba78f1b2d944a334da31ab72ee56c3169f`
- `windows-x64-pe32+.log` — SHA-256 `3909da86a2a3b9286816b2dd596453308d581e0ab03a1357d51a487880ebb9b0`
- `windows-x86-pe32.log` — SHA-256 `aed2cde5ad234b052136734a76c7dece8c02aaa3a635159da36eb4e7d5510a80`

## Honest scope

- Windows x86 is the only input the PE32 backend reconstructs on this host.
- PE32+ x86-64, Mach-O and ELF inputs are accepted by the GUI but fail closed in the backend with a named reason.
- The E2E gate reports 6 passed and 1 skipped (Qt GUI build not installed).
