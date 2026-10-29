# Migration audit: legacy recomp-studio

The migration rule for this product: audit the legacy repository first, then
import only proven useful work, one piece at a time, with attribution, a build
and a test. This file is the audit record. It states what exists in the legacy
repository, what it measured, and what this repository has decided to take,
adapt, reference or leave alone.

The legacy checkout is never modified and never deleted. It remains at
`~/Documents/Code/recomp-studio` (remote `heaven-hm/decomp-studio`), which is
outside this repository.

## What was audited

| | |
|---|---|
| Repository | `heaven-hm/recomp-studio`, remote `https://github.com/heaven-hm/decomp-studio.git` |
| Branches present | `develop` (checked out), `feature/decomp-agent`, `master` |
| HEAD at audit time | `0e41669`, 2026-10-29 22:41 +0530 |
| Licence | Apache-2.0 (`LICENSE`), with per-component notices in `THIRD_PARTY_NOTICES.md` |
| Baseline report | `reports/real-games-2026-10-29.md` and `reports/real-games-2026-10-29-logs/` |
| Baseline commit | `af81a1a`, `autorecomp --buildrun --timeout 5` |
| Corpus status ledger | `CORPUS.md` |
| Tool pins | `tools.lock.json` (cmake, ninja, capstone, lief, zydis, unicorn, ...) |

The legacy repository is Apache-2.0, not MIT. That matters for every import:
Apache-2.0 code may be reused in this MIT-licensed repository, but only with
its notice preserved and its files listed in `docs/LICENSES.md`. No legacy
source file has been copied so far. The only legacy material imported is the
branding artwork (`assets/recomp-studio-logo.png` and `assets/screenshots/`),
which is presentation asset shipped by the project owner.

## The measured baseline

The legacy 15-game corpus ran at `af81a1a` with a 120-second pipeline budget, a
1.8 GB process-group RSS cap and a 5-second run timeout. **Zero of fifteen
games were verified playable**: thirteen stopped in translation, two built but
failed verification.

| Game (`recomp-apps/games/` relative path) | Arch | Baseline outcome |
|---|---|---|
| `cavestory/Doukutsu.exe` | x86 | translate stopped at `iretd` (0x41000d) |
| `generally/GeneRally.exe` | x86 | translate stopped at `cvtpd2ps` (0x406a89) |
| `warning_forever/wf.exe` | x86 | translate stopped at `cmpltpd` (0x434830) |
| `herocore/herocore gm7.exe` | x86 | translate stopped at `arpl` (0x40113a) |
| `ikachan/dxIka.exe` | x86 | built; native exit 3 at `kernel32.dll!OpenMutexA`; verification failed |
| `noiz2sa/noiz2sa.exe` | x86 | translate stopped at `arpl` (0x401988) |
| `rrootage/rr.exe` | x86 | translate stopped at `arpl` (0x401842) |
| `torus_trooper/tt.exe` | x86 | translate stopped at `jcxz` (0x42938b) |
| `parsec47/p47.exe` | x86 | translate stopped at `jcxz` (0x4246df) |
| `gunroar/gr.exe` | x86 | translate stopped at `jcxz` (0x42fceb) |
| `tumiki_fighters/tf.exe` | x86 | translate stopped at `jcxz` (0x41c69b) |
| `cannonball/cannonball.exe` | x64 | translate stopped at `cvttpd2dq` (0x14002fe9c) |
| `opentyrian/opentyrian.exe` | x64 | translate stopped at `cmpnless` (0x140008e44) |
| `pixeltetris/Pixeltetris.exe` | x64 | translate stopped at `movntps` (0x14005cc00) |
| `snake/snake-windows-x86_64.exe` | x64 | built; native exit 3 at `_get_initial_narrow_environment`; verification failed |

The import counts in the report are the legacy tool's handler census, not a
claim that the APIs work. The instruction addresses were discovery candidates,
not proof that each instruction was reachable code.

A follow-up at a later legacy commit added lifters for `arpl`, `das`, `popaw`,
`aad`, `salc`, `iretd`, `jcxz` and the `fdisi8087_nop` encoding:

| Game | After the fix |
|---|---|
| Cave Story | 27,341/27,341 lifted, 0 unsupported; native build succeeded |
| Noiz2sa | 384,200/384,200 lifted, 0 unsupported; native build succeeded |
| Torus Trooper | 126,497/126,497 lifted, native build succeeded; a 20 s run timed out with no output because 102 SDL/OpenGL/bulletml imports were unimplemented |
| rRootage | 354,584/354,601 lifted; 17 unsupported, all `movd` |
| Hero Core | 1,999,492/2,000,475 lifted; 983 unsupported; the total sits just above the discovery's 2,000,000-instruction ceiling, and the cap produces no diagnostic, so the run cannot be distinguished from a full traversal |

"Build succeeded" there means translate and build finished. It is not
playability and not verification. Library imports were the dominant blocker
once translation succeeded. The `movd` gap was later fixed in this repository's
`lift32` (`pe32_mmx`); those specific operands were never re-measured, so the
legacy count is stale rather than wrong.

### Ikachan

The one game that reached a window in the legacy work did so outside the
standard `autorecomp` path. A separate PCRecomp experiment at PCRecomp commit
`a85ea970f9e0640ab6546d2ca7a01fccb132973d` generated low-level C that compiled
and bound all 97 imports, including the DirectSound ordinal; the game opened a
native SDL window and reached its startup dialogs, but the window stayed black.
An adapted GDI implementation fixed packed 1-bit and 4-bit DIB reads, and with a
scripted Enter dismissing the initial dialog an experimental SDL host displayed
Ikachan's title screen. Screenshots exist in the legacy repository
(`apps-running-screenshots/ikachan-pcrecomp-black-window.png` and
`ikachan-pcrecomp-title.png`). None of it was produced by the standard
pipeline, none of it is gameplay, and none of it was independently verified.

### Status vocabulary the legacy ledger uses

`CORPUS.md` classifies each binary as **PORTED** (translated, built, run and
differentially verified), **REFUSED** (a correct refusal: packed, managed .NET,
unsupported input format), **BLOCKED** (a real gap with a named cause) or
**UNMEASURED**. This repository keeps that distinction, because the failure
mode it prevents — treating "nobody has run it" as "it fails" — is the one that
produced the 0/15 headline.

Legacy `REFUSED` entries worth carrying forward: `imgview64.exe` is UPX-packed
and must be unpacked before its code exists; `micro.exe` is a Windows ARM64 PE;
`TicTacToe.exe` and `luhn.exe` are managed .NET, so there is no machine code to
lift.

## Decisions

Nothing in the table below is implemented merely by being listed. Each
"import"/"adapt" row becomes its own commit with a test when it is taken.

| Legacy component | Evidence | Decision |
|---|---|---|
| Corpus metadata: relative paths, failure classes, hashes | `reports/real-games-2026-10-29.md`, `CORPUS.md` | **Import as data.** Already represented in this repo's corpus inventory and `reports/games/results.json`; game binaries and assets are never copied. |
| Differential harness (`core/verification/differential_harness.cpp`) | legacy tests | **Reference for verdict rules.** This repo tests lifts with `tools/lift/difftest.py` (Unicorn) and `difftest64.py` (host CPU). Port test vectors and fail-closed verdict logic only where the existing harness lacks them. |
| Independent oracle (`scripts/native_oracle.py`) | legacy script | **Reference for scaffolding.** It compares a separate process's exit code, stdout and stderr. That is the seed of this repo's `verify` stage; gameplay/frame state, deterministic input and runtime isolation are still to be added. |
| PCRecomp CRT/GDI adaptations (`runtime/pcrecomp_stateless_crt.cpp`, `runtime/pcrecomp/gdidc.c`) | adaptations of PCRecomp `a85ea97`, packed-DIB fix | **Compare before cherry-pick.** Both are adaptations of code this repository already contains. Take a fix only if this repo's `runtime/win32hle` does not already have it, and keep the per-file notice. |
| Synthetic PE/ABI fixtures in `tests/` | ABI, stack, callback, struct, guest-memory cases | **Port the redistributable fixtures**, never the IR or lifter they were written for. |
| Source-quality categories and provenance | `docs/SEMANTIC_BRIDGE.md`, coverage behaviour | **Keep the four labels** (`HIGH_LEVEL_VERIFIED`, `HIGH_LEVEL_UNVERIFIED`, `LOW_LEVEL_FALLBACK`, `UNRESOLVED`); `docs/SOURCE_PROVENANCE.md` defines them for this repo. |
| CLI workflow and report UX | `apps/cli/`, `docs/ARCHITECTURE.md`, `docs/VERIFICATION.md` | **Reference only.** This repo's `autorecomp` already implements inspect/recover/translate/project/compile/run/verify/report; see `docs/BACKENDS.md`. |
| Research reports | `reports/` (gap census, honesty audit, shim audits) | **Reference.** Lessons are cited where they change a decision; historical documents are not treated as current functionality. |
| `core/semantics/x86_64_lifter.cpp` | 64-bit semantic lifter | **Do not port.** This repo lifts 32-bit through `lift32`/`lift32_cpu` and 64-bit through `lift64_cpu`/`generate64`. A second general-purpose recompiler is out of scope by decision. |
| `core/ir/`, `core/ir/cpp_backend.cpp` | custom IR and C++ backend | **Do not port.** Same reason; the product must not grow two engines. |
| `core/codegen/project_generator.cpp` | project generator | **Do not port.** `tools/autorecomp/projectgen.py` is this repo's generator and is exercised by every corpus run. |
| `core/pipeline.cpp` | pipeline | **Do not port.** `tools/autorecomp/pipeline.py` stages are the production path. |
| `runtime/recomp_runtime.cpp` | competing Win32 runtime | **Do not port wholesale.** This repo's Win32 semantics live in `runtime/win32hle`; individual shims are taken only when a real program proves a gap and the shim is independently tested. |
| Branding artwork | `assets/` on legacy `develop` | **Imported** at the owner's request: logo and screenshots under this repo's `assets/`. |

## What this repository measures instead

The legacy numbers above are history, not this product's baseline. This
product's re-measurement of the corpus is the live record in `table.md` and
`reports/games/results.json`, written by `python -m tools.gamesdb` from actual
`autorecomp` runs against `SMALL_GAMES_PATH`. Rows are only promoted when a run
produces the evidence the row claims. The same rule the legacy ledger learned
applies here: a rebuilt binary that exits early is `launch-failed`, not a pass,
and a game nobody has run is `UNMEASURED`, not a failure.
