# Linux platform status (local evidence, fail-closed)

Recorded 2026-11-24. **Local Linux status: BLOCKED / NOT TESTED.** No Linux
binary was built, launched or verified on this host, and no Linux PASS row
exists. This document reports the absence of a runner, not a verdict on Linux
compatibility.

## Local runner availability

| Capability checked | Local observation | Consequence |
|---|---|---|
| Docker daemon | Unavailable (no usable socket/daemon) | No container to build or run generated projects. |
| Colima | No usable guest; image download previously stalled | No Linux VM. |
| Lima | No usable Linux guest image | No Linux VM. |
| qemu user/kernel | No usable emulator or guest kernel/image | No ELF execution on this host. |
| `autorecomp --target-os linux` | Refused: foreign OS targets are refused; cross-target builds compile with the host toolchain only | No Linux cross-build from here. |

Because none of these paths works locally, all Linux results are **BLOCKED**
(no runner) and **NOT TESTED** (no run attempted). Neither is a FAIL verdict on
a game, and neither is evidence that a Linux path works.

## What GitHub Actions does and does not prove

CI jobs declared on `ubuntu-latest` are **configuration evidence only**: they
show that a Linux job is described in `.github/workflows/`. They are not local
execution evidence on this host. This document does not claim that those jobs
were run here, that they passed, or that they produced a Linux PASS for any
game. A workflow definition is not an oracle and is not a substitute for a
measured run.

## Coverage scope reminder

Architecture follows PCRecomp: PE32 x86 native is the only structurally
supported class. PE32+ x64, .NET/CLR, Win16/NE, D3D10/11/DXGI, services and
installers are DEFERRED / UNSUPPORTED and are NOT TESTED on Linux as anywhere
else. The supported Linux mechanism, when a runner exists, is
`tools/linux_matrix.py`: it builds and runs the *generated project* in a Linux
container to exercise the Linux host code paths of the native layer. That tool
exists; it was not executed locally. See `docs/PE32_COVERAGE.md` for the full
status ladder and coverage class table.

## Status ladder (same vocabulary, kept distinct)

* **PASS** — needs a real Windows oracle plus full evidence; not available for
  any local Linux row.
* **PARTIAL** — render or recorded process evidence only; no Linux row here.
* **BUILD ONLY** — binary hashed but no launch; no Linux row here.
* **FAIL** — launch ran with no proven frame; no Linux row here.
* **BLOCKED** — no usable runner on this host; this is the local Linux state.
* **NOT TESTED** — no run attempted; the companion label for every Linux class
  here. It is not interchangeable with FAIL or PASS.

## Explicit remaining limits

* No Linux execution, build, frame, import-binding or performance result was
  produced locally. No claim is made that Linux tests were run.
* No original-Windows oracle is available, so this document does not claim 100%
  compatibility, gameplay equivalence, input/audio equivalence or save/load
  fidelity on Linux (or on any other platform).
* All 28 tracked issues (#1–#28) remain OPEN; none is reported as closed here.
* A CI configuration or a tool's existence is not evidence of behaviour.
* Adding a runner later requires new measurements; a documentation edit does
  not change BLOCKED / NOT TESTED into a pass.

## How to regenerate this evidence

When a Docker/Colima-compatible daemon or a Lima/qemu guest is available, supply
all paths on the command line; no user, machine, game or download directory is
hard-coded:

```sh
python3 -m tools.linux_matrix \
  --project <generated-project-dir> \
  --out <results-dir> \
  --image <linux-image-tag> \
  --scratch <existing-scratch-dir> \
  --min-free-gb 4
```

Repeat `--project` for additional generated projects. The tool fails closed
when the daemon is unavailable or the disk guard trips, and records the reason
instead of emitting a Linux PASS. To record game-level status with a real
runner, use `python3 -m tools.corpus_acceptance` as shown in
`docs/PE32_COVERAGE.md`, with every original/native/asset/report path supplied
on the CLI.

## Verification

```sh
python3 -m pytest tests/test_capability_boundaries.py \
  tests/test_corpus_acceptance.py -q
python3 -m pytest tests/test_acceptance_docs.py -q
```
