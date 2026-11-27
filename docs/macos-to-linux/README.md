# macOS apps → Linux, and Windows games → Linux

## macOS apps (Ghostty, Calculator, Chess)

The GUI accepts all three `.app` bundles (`/Applications/Ghostty.app`,
`/System/Applications/Calculator.app`, `/System/Applications/Chess.app`) — the
Calculator capture is in `docs/cua-e2e/anypath-macos-app-result2.png`. The CLI now
resolves `.app` bundles the same way (`tools.autorecomp.cli.resolve_app_bundle_path`)
and reports a **named** reason instead of a generic rejection:

```
error: Mach-O detected — accepted as a source-level port input; PE32 lifting does not apply to macOS binaries
```

Raw logs: `Ghostty.app-host.txt`, `Calculator.app-host.txt`, `Chess.app-host.txt`.
Asking the CLI for a `linux/x86_64` target from this macOS host is refused with a
second named reason: cross-target builds need a Linux host; this pipeline compiles
with the host toolchain. Logs: `*.app.txt`.

**No claim is made that Ghostty/Calculator/Chess run on Linux.** They are Mach-O
binaries; running them on Linux requires a source port, not PE32 i386 lifting. The
GUI/CLI accepts them and states that exact limitation.

## Windows games → Linux

The Linux build of the same generated IGI C is in progress. Earlier attempts found
and fixed two portability bugs:

1. generated `.incbin` paths are absolute and case-sensitive (`projects/IGI` vs
   `projects/igi`), mirrored with a symlink in `tools/run-linux-igi.sh`;
2. `host.c` used x86-64 glibc `REG_RIP`/`gregs[]` unconditionally; on aarch64 Linux
   the fault PC comes from `uc_mcontext.pc`.

Once the build completes, `tools/run-linux-igi.sh` captures real Linux frames under
Xvfb into `docs/igi-levels/linux/`.
