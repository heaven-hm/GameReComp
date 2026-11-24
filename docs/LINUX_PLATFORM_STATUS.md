# Linux platform status (local evidence, fail-closed)

Recorded 2026-11-24, updated after the completed x86 build+run the same day.
**Local Linux runner status: WORKING for PE32 x86 generated projects. Local
Linux game-level status: PARTIAL (process launched, no scenario oracle). No
Linux PASS row is claimed.**

## What actually works now

A prebuilt toolchain image (`gamerecomp/linux-runner:local`, built from
`tools/linux/Dockerfile` on `ubuntu:24.04`) lets `tools/linux_matrix.py` build
a generated project and run the resulting ELF in a Linux container with
`--network none`. The earlier `apk add` attempt stalled in the package
download; `apt-get update` completed (`Fetched 36.3 MB in 38s`), so the
toolchain is installed once into the image instead of on every matrix run.

Measured result for the real `arith-calc.exe` (PE32 i386) generated project:
`build=0`, `run=0`, `elf=OK`, 101.5 s; ELF64/aarch64, SHA-256
`4b27641cbf41f3f74bb6936862c654d6a707c0a7cac969742b0cd0d358b7bd51`. The ELF
printed `arith:start`, deterministic arithmetic results through `arith:done`,
bound 3/3 imports and exited 0 via the guest's `ExitProcess(0)`. Exact
commands, logs and evidence paths: `docs/LINUX_RUNNER_EVIDENCE.md` and
`reports/linux/linux-summary.json`.

The runner found and now guards four real defects exposed by this work:
absolute staging paths passed to `docker -v`; Colima silently mounting an
unshared `/private/var/folders` path as an empty directory; absolute
`.incbin` paths in generated assembly; and host-runtime portability bugs
(x86_64-only `ucontext` register access, BSD-only `sin6_len`, and a 1024-byte
`realpath()` buffer). The last of these aborted the first ELF launch with
glibc `buffer overflow detected`; a container gdb backtrace identified
`__realpath_chk` in `boot()`. All four have regression tests.

## Local runner availability

| Capability checked | Local observation | Consequence |
|---|---|---|
| Docker daemon / Colima | Available (Server 29.5.2, linux/aarch64); VM shares `$HOME`, not `/private/var/folders` | Builds work when `--out`/`--scratch` are under a shared path; runner preflights the mount. |
| Prebuilt Linux toolchain image | Built once from `tools/linux/Dockerfile`; cmake 3.28.3, GCC 13.3.0, SDL2 2.30.0 | Real Linux builds with no per-build package download. |
| `alpine:latest` + `apk add` | Existing image; apk download stalled earlier | No longer used by default; `--provision apk` remains optional and is recorded as network-dependent. |
| Host clang / Zig for Linux target | No Linux sysroot or `ld.lld` on the clang toolchain; host SDL2 is Mach-O | No host cross-target approximation is claimed. |
| Unicorn (tools/lift/difftest.py) | Available; project-scoped run returned `0 agree, 3 not` with `original-faulted`/`nothing-compared` reasons; full 33-address sweep timed out at 900 s | CPU-level verdict BLOCKED/inconclusive, not a match. |
| Frida (tools/frida_probe.py) | Frida installed and Highball Wine binary present, but spawn/attach fails: `unexpected error while probing dyld of target process` | No process call trace; BLOCKED with exact error. |
| Wine original oracle | Not run as a Linux oracle here | No Linux scenario PASS. |

Because the dual-engine signals are BLOCKED/inconclusive, the completed Linux
run is process-level evidence only. It is not a FAIL verdict on a game, and it
is not evidence of gameplay, frame, input/audio or save/load equivalence.

## What GitHub Actions does and does not prove

CI jobs declared on `ubuntu-latest` are configuration evidence only. They are
not local execution evidence on this host. The measured row above comes from a
local Docker/Colima run, not from a workflow definition.

## Coverage scope reminder

Architecture follows PCRecomp: PE32 x86 native is the only structurally
supported class. PE32+ x64, .NET/CLR, Win16/NE, D3D10/11/DXGI, services and
installers are DEFERRED / UNSUPPORTED on Linux as elsewhere. x64-to-Linux was
not demonstrated here; see `docs/LINUX_RUNNER_EVIDENCE.md` for the precise
status of any attempt.

## Status ladder (same vocabulary, kept distinct)

* **PASS** — needs a real Windows oracle plus full evidence; no Linux PASS row.
* **PARTIAL** — process launched and exited 0 with captured output, no scenario
  oracle: this is the local Linux status for arith-calc.
* **BUILD ONLY** — achieved for the real generated project (ELF hashed).
* **FAIL** — no Linux FAIL verdict claimed; earlier build failures were runner
  defects and were fixed, not game failures.
* **BLOCKED** — Unicorn/Frida dual-engine evidence and x64-to-Linux on this host.
* **NOT TESTED** — gameplay, frames, input/audio, save/load and Windows-oracle
  equivalence on Linux.

## Explicit remaining limits

* No Windows original was executed as a Linux oracle, so no 100% compatibility
  or equivalence claim is made.
* Dual-engine evidence is BLOCKED/inconclusive; no fake MATCH or 100% score.
* All 28 tracked issues (#1–#28) remain OPEN; none is reported as closed here.
* A CI configuration or a tool's existence is not evidence of behaviour.

## How to regenerate this evidence

```sh
sh tools/linux/build-image.sh          # once, needs apt network
python3 -m tools.linux_matrix \
  --project <generated-project-dir> \
  --original <original-windows-pe> \
  --out "$HOME/p3-results/linux" \
  --image gamerecomp/linux-runner:local --provision prebuilt \
  --scratch <existing-shared-scratch-dir> --min-free-gb 4
```

`--out`/`--scratch` must be under a Docker-shared host path (this host shares
`$HOME`). The tool fails closed on a missing image, absent toolchain, unshared
mount, disk guard or provisioning problem and records the reason.

## Verification

```sh
python3 -m pytest tests/test_linux_matrix_paths.py tests/test_game_matrix.py \
  tests/test_host_fault_report_linux.py tests/test_hybrid_diff.py -q
```
