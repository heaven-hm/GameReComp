# RevEng-Studio

<p align="center">
  <img src="assets/recomp-studio-logo.png" alt="RevEng-Studio logo" width="220">
</p>

**Turn old Windows PC games into playable, moddable, portable games with genuinely human-readable source code — automatically.**

**RevEng-Studio** is a game-focused preservation, reconstruction, modding,
and native-porting platform. Its long-term mission is **broad compatibility
across legacy Windows PC games**, not one hand-reconstructed game at a time.
Given a game executable and assets you legally own, the intended unattended
workflow reconstructs understandable, editable C/C++ source, builds a native
executable **from that exact source**, checks gameplay against the original,
and packages the result for preservation, modding, and platform ports.

### What the product is designed to deliver

- **Playable and independently verified:** Rebuilt games should pass
  reproducible original-vs-rebuilt gameplay scenarios, not merely compile or
  display a title screen. Verification reports distinguish tested coverage
  from unverified behaviour.
- **Actually human-readable C/C++:** Recover meaningful types, identifiers,
  structs, functions, and natural control flow, not just `eax`/`esp`
  register-transfer C or prettier pseudocode. Unrecovered functions remain
  explicitly labelled low-level fallbacks; original developer naming cannot
  generally be guaranteed from stripped binaries.
- **Moddable at the source level:** Developers should be able to change game
  logic, rebuild the native executable, and demonstrate the modification
  working in-game.
- **Portable and maintainable:** Reuse a common compatibility runtime and
  platform backends so recovered game logic can target modern operating
  systems without a bespoke rewrite for each game.
- **Broad automated compatibility:** Support many older Windows PC games,
  engines, executable formats, and middleware through reusable infrastructure
  and honest compatibility reporting—not a promise that every game works today.
- **Unattended, non-AI-first processing:** PCRecomp supplies recompilation
  and Windows-game runtime foundations; rev.ng is the intended primary
  high-level structural-source provider. Deterministic analysis comes first;
  AI is **optional** for extra semantic readability. No manual per-function
  reconstruction should be required by the intended workflow. The desktop
  GUI is planned over this same backend.

**Differentiator:** Not another decompiler or game-specific recompilation
script. RevEng-Studio aims to make **verified native game ports with genuinely
modifiable, human-readable source** reproducible across a large catalogue of
legacy Windows games.

The current implementation is an early CLI vertical slice; human-readable
source, verified gameplay, broad native ports, and the GUI described above are
**product goals, not completed features**.

> **Status: v0.1.0 — first vertical slice.** The whole pipeline runs end to end and
> is measured: 19 corpus rows, one game (Ikachan) that binds every import and
> launches, fourteen that compile to a native binary and then stop in the PE
> loader on imports the runtime does not yet answer, four PE32+ inputs refused
> honestly. **No game has passed a gameplay scenario yet, and every generated
> function is still `LOW_LEVEL_FALLBACK` register-transfer C rather than
> reconstructed high-level source.** Those are the two gates that matter next,
> and this README does not claim them.

---

## Built on pcrecomp

This project stands on **[pcrecomp](https://github.com/sp00nznet/pcrecomp)** by
[Ned Heller (@sp00nznet)](https://github.com/sp00nznet) and its contributors —
the x86-16/32/64 disassemblers and lifters, the PE/NE/LE/Mach-O tooling, and the
`recomp32`/`win32hle` runtime model this product's generated code links against.
Thank you; the hard, unglamorous reverse-engineering groundwork is theirs.

Everything in [`docs/UPSTREAM_TOOLBOX.md`](docs/UPSTREAM_TOOLBOX.md) documents
that toolbox as it exists in this tree. Pinned versions, per-file obligations and
licence texts are in [`docs/LICENSES.md`](docs/LICENSES.md) and [`NOTICE`](NOTICE).
Where this repository extends upstream code, the file keeps its upstream notice
and the change is recorded in [`CHANGELOG.md`](CHANGELOG.md).

## What RevEng-Studio adds

pcrecomp is a toolbox: many tools, each doing one job well, driven per project by
that project's own `run_lift.py`. RevEng-Studio is the **automated,
game-focused product built around that toolbox**: one reproducible workflow
for recovering readable source, compiling it into a native game, verifying
actual gameplay, and enabling source-level modifications and ports across
many legacy Windows titles. Source readability and verified moddability—not
merely producing a binary—are the intended distinguishing deliverables.

| | pcrecomp (toolbox) | RevEng-Studio (product) |
| --- | --- | --- |
| Entry point | per-project `run_lift.py` drivers, tools called one at a time | `./RevEng game.exe` — inspect → recover → translate → project → compile → run → verify → report |
| Output bias | lifted C that reproduces CPU state | the same, **plus** a labelled source-quality split (`HIGH_LEVEL_VERIFIED` / `HIGH_LEVEL_UNVERIFIED` / `LOW_LEVEL_FALLBACK` / `UNRESOLVED`) and a gate that refuses to relabel anything without evidence |
| Verification | `difftest` against Unicorn, per lifter | a fail-closed per-run verdict — `not-run` / `compile-failed` / `launch-failed` / `trace-diverged` / `partial` / `scenario-pass` / `verified-playable-scope` — in a JSON+HTML evidence bundle with hashes and oracle gaps |
| Scope of a run | one target, one driver | a project directory that is self-contained and rebuildable: generated source, the pinned runtime copy, CMake, logs, reports |
| Games | per-project repositories | a measured corpus with a published matrix, per-input SHA-256, and a score that cannot rise unless the stage actually succeeded |
| Failure | whatever the driver does | a classified, recorded outcome — including `MISSING_INPUT` for a game that is not present, never an invented path |

### Where it is deliberately different

- **Source quality is a first-class output, not a by-product.** Every function
  carries one of four provenance categories and every run reports the split. A
  function is only called readable when a real provider produced it and a gate
  accepted it; register-transfer C is never described as "human-readable C"
  merely because it is syntactically valid C.
- **Fail-closed everywhere.** PE32+ is refused at inspect rather than attempted
  and reported vaguely. An unresolved function aborts instead of silently doing
  nothing. A stage that did not run cannot score. A binary that never boots
  cannot earn the run stage's points. A shim that cannot do the work returns
  failure rather than success — no dummy-success Win32 stubs on the critical
  path.
- **No hidden delegation.** The rebuilt executable is built from the generated
  project's source and nothing else. The original PE is mapped read-only as the
  guest's data image; every guest instruction executed is generated C dispatched
  through `recomp_lookup`. No emulator, no Wine path. Audited, not asserted —
  see [`reports/audit-2026-10-30.md`](reports/audit-2026-10-30.md).
- **A measured corpus, published honestly.** [`docs/GAME_MATRIX.md`](docs/GAME_MATRIX.md)
  carries every game with what the port produced and its working status;
  failures and missing inputs are rows, not footnotes.

## What works today

```text
Windows PE32 game + user-supplied assets
  inspect    parse headers, imports, resources, entry point; refuse PE32+ loudly
  recover    discover functions and instructions (Capstone)
  translate  each function -> C register-transfer unit, address-tagged
  project    generate a self-contained CMake project that vendors the runtime
  compile    cmake + native C compiler, one shared adaptive deadline
  run        launch the rebuilt binary headless (SDL dummy drivers) or --interactive
  verify     fail-closed status, naming the exact unresolved imports when it stops
  report     report.json + report.html: hashes, timings, provenance, oracle gaps
```

- One command: `./RevEng path/to/game.exe`
- Byte-capped source units (1000 functions or 8 MiB per unit, whichever closes
  first) with `recomp_units.json` mapping original address → function → unit
- Adaptive build deadline: `600 s + 5 s/MiB` of generated source, capped at 3600 s
- `--fresh-build` for when a moved system dependency (a Homebrew keg) leaves a
  stale `CMakeCache.txt` behind; `--reuse-build` to adopt a build whose recorded
  source digest still matches
- Exit codes: `0` scenario-pass, `2` a stage failed, `3` built/ran but unverified
- Game database and matrix generated from measured runs:
  [`table.md`](table.md), [`docs/GAME_MATRIX.md`](docs/GAME_MATRIX.md)

## Measured so far

19 measured executable rows, each tied to the input's SHA-256.

| Result | Games |
| --- | --- |
| 93/100 — boots, binds every import, launches and exits on its own; no oracle, so `partial` | Ikachan (dxIka.exe) |
| 85/100 — compiled to a native binary, then stopped in the PE loader on unresolved imports (`launch-failed`) | 14 PE32 games: Cave Story (Doukutsu, OrgView, DoConfig), GeneRally (GeneRally.exe, TrackEditor.exe), Gunroar, Hero Core (gm5, gm7), Noiz2sa, Parsec47, rRootage, Torus Trooper, Tumiki Fighters, Warning Forever |
| 0/100 — PE32+ refused at inspect, recorded honestly as a measured row | Cannonball, OpenTyrian, Pixeltetris, Snake |

Runtime coverage is the measured ceiling, not translation: the `launch-failed`
rows record exit 2 and the exact import names their runtime could not bind.
Closing those gaps is measurable work, not a hope — registering the MSVC CRT
bridge alone took Noiz2sa from 121 unresolved imports to 28.

## Honest limits

- **All generated functions are `LOW_LEVEL_FALLBACK`.** Register-transfer C:
  compilable, address-traceable, not yet human-readable. High-level
  reconstruction is Phase 2 and is not wired into the build.
- **PE32 (i386) only.** PE32+ x86-64 is detected and refused at inspect.
- **No gameplay verification yet.** Oracle checks are process-level (exit code,
  stdout/stderr, launch); frame, input and audio comparison is a recorded oracle
  gap in every report.
- **No independent original-game oracle process yet.** `--oracle` compares the
  rebuilt process against a user-supplied JSON of expected observables; it does
  not run the original Windows binary.
- **The three extra corpus titles** (`igi.exe`, `ac.exe`, `halo.exe`) are not
  present locally and are reported as `MISSING_INPUT`, never invented.
- **GUI deferred.** No desktop application ships in v0.1; the design and its
  contract with the CLI are in [`docs/GUI.md`](docs/GUI.md).
- **Generated projects are build output and are not committed.** `projects/` is
  gitignored; the tracked artifacts are the pipeline, plus the matrix and table
  that record the input hash each row was measured against.

## The games

Every game this product knows about, with what the port produced and its working
status. Generated by `python -m tools.gamesdb --matrix`; the full table (upstream
projects, unmeasured corpus entries and the upstream claims) is
[`docs/GAME_MATRIX.md`](docs/GAME_MATRIX.md).

<!-- GAME-MATRIX:BEGIN -->
| Name | OS | Arch | Port details | Working status |
| --- | --- | --- | --- | --- |
| Cannonball | Windows | x86-64 (PE32+) | inspect failed: PE32+ is not supported by the PE32 backend yet (machine x86-64); fail-closed instead of guessing | NOT RUN |
| Cave Story | Windows | i386 (PE32) | 406 fns recovered; 2 units / 4.1 MiB C; binary DoConfig 1301 KiB; 88 imports bound, 2 unresolved; low-level 406/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| Cave Story | Windows | i386 (PE32) | 3260 fns recovered; 8 units / 50.7 MiB C; binary Doukutsu 8368 KiB; 137 imports bound, 6 unresolved; low-level 3260/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| Cave Story | Windows | i386 (PE32) | 551 fns recovered; 2 units / 5.4 MiB C; binary OrgView 1517 KiB; 120 imports bound, 2 unresolved; low-level 551/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| GeneRally | Windows | i386 (PE32) | 2577 fns recovered; 46 units / 373.9 MiB C; low-level 2577/ high-level verified 0; compile failed: build failed (see /Users/haseeb-mir/Documents/Code/RevEng-Studio/projects/GeneRally/logs/compile.log | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| GeneRally | Windows | i386 (PE32) | 311 fns recovered; 2 units / 7.0 MiB C; binary TrackEditor 2152 KiB; 88 imports bound, 59 unresolved; low-level 311/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| Gunroar | Windows | i386 (PE32) | 3288 fns recovered; 5 units / 19.6 MiB C; binary gr 7174 KiB; 63 imports bound, 73 unresolved; low-level 3288/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| Hero Core | Windows | i386 (PE32) | 12424 fns recovered; 14 units / 59.4 MiB C; binary herocore_gm5 17276 KiB; 353 imports bound, 160 unresolved; low-level 12424/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| Hero Core | Windows | i386 (PE32) | 15788 fns recovered; 18 units / 79.6 MiB C; binary herocore_gm7 22159 KiB; 356 imports bound, 176 unresolved; low-level 15788/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| Ikachan | Windows | i386 (PE32) | 482 fns recovered; 3 units / 9.6 MiB C; binary dxIka 2175 KiB; 97 imports bound; low-level 482/ high-level verified 0 | PARTIAL - launches, no scenario oracle - process launched and exited 1; no oracle supplied |
| Noiz2sa | Windows | i386 (PE32) | 3024 fns recovered; 6 units / 36.3 MiB C; low-level 3024/ high-level verified 0; compile failed: build failed (see /Users/haseeb-mir/Documents/Code/RevEng-Studio/projects/noiz2sa/logs/compile.log) | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| OpenTyrian | Windows | x86-64 (PE32+) | inspect failed: PE32+ is not supported by the PE32 backend yet (machine x86-64); fail-closed instead of guessing | NOT RUN |
| Parsec47 | Windows | i386 (PE32) | 1930 fns recovered; 3 units / 13.2 MiB C; binary p47 4355 KiB; 61 imports bound, 90 unresolved; low-level 1930/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| Pixeltetris | Windows | x86-64 (PE32+) | inspect failed: PE32+ is not supported by the PE32 backend yet (machine x86-64); fail-closed instead of guessing | NOT RUN |
| Snake | Windows | x86-64 (PE32+) | inspect failed: PE32+ is not supported by the PE32 backend yet (machine x86-64); fail-closed instead of guessing | NOT RUN |
| Torus Trooper | Windows | i386 (PE32) | 2342 fns recovered; 4 units / 15.1 MiB C; binary tt 5035 KiB; 61 imports bound, 94 unresolved; low-level 2342/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| Tumiki Fighters | Windows | i386 (PE32) | 1760 fns recovered; 3 units / 12.0 MiB C; binary tf 3830 KiB; 59 imports bound, 86 unresolved; low-level 1760/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| Warning Forever | Windows | i386 (PE32) | 2624 fns recovered; 6 units / 33.6 MiB C; binary wf 7193 KiB; 141 imports bound, 6 unresolved; low-level 2624/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| rRootage | Windows | i386 (PE32) | 3199 fns recovered; 7 units / 41.3 MiB C; binary rr 9895 KiB; 18 imports bound, 146 unresolved; low-level 3199/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
<!-- GAME-MATRIX:END -->
**Working status** is the pipeline's fail-closed verdict, not an impression:
`PASS` requires a recorded scenario, `PARTIAL` means the rebuilt process launched
but no independent scenario was run, `FAIL` means it did not get that far.
**Port details** are read back from each project's own `reports/report.json`;
`LOW_LEVEL_FALLBACK` functions are register-transfer C, not reconstructed
high-level source.

## Quick start (CLI)

Requirements: Python 3, CMake ≥ 3.16, a C compiler, `pkg-config` with SDL2 and
SDL2_ttf. See [`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md) for the
step-by-step newcomer guide.

```sh
# point the tools at your own legally obtained corpus (never committed)
export SMALL_GAMES_PATH=/path/to/your/games

./RevEng "$SMALL_GAMES_PATH/cavestory/Doukutsu.exe"
./RevEng game.exe --assets-dir /path/to/game/files   # where the game reads data
./RevEng game.exe --timeout 60 --json                # longer run, machine output
./RevEng game.exe --no-build                         # stop after source generation
./RevEng game.exe --interactive                      # real window/audio, not dummy SDL
./RevEng game.exe --fresh-build                      # discard a stale build cache
```

Each run writes a self-contained project:

```text
projects/<name>/
  src/            generated C units + headers + recomp_units.json
  build/          CMake build tree and the rebuilt executable
  reports/        report.json and report.html (hashes, stages, oracle gaps)
  logs/           per-stage logs
```

## Documentation

| Document | Contents |
| --- | --- |
| [`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md) | Install, first run, reading the report, troubleshooting |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Component map and data flow |
| [`docs/PIPELINE.md`](docs/PIPELINE.md) | Stages, weights, timeouts, recovery loop |
| [`docs/BACKENDS.md`](docs/BACKENDS.md) | Translation backends and their status |
| [`docs/VERIFICATION.md`](docs/VERIFICATION.md) | Verification statuses, oracle gaps, fail-closed rules |
| [`docs/SOURCE_PROVENANCE.md`](docs/SOURCE_PROVENANCE.md) | How every delivered line maps to the input binary |
| [`docs/RUNTIME_COMPAT.md`](docs/RUNTIME_COMPAT.md) | Win32/runtime coverage and known gaps |
| [`docs/GAME_MATRIX.md`](docs/GAME_MATRIX.md) | Every game: OS, arch, port details, working status |
| [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) | Generated benchmark tables and methodology |
| [`docs/GUI.md`](docs/GUI.md) | The planned desktop application and its CLI contract |
| [`docs/LICENSES.md`](docs/LICENSES.md) | Pinned upstream versions and licences |
| [`docs/UPSTREAM_TOOLBOX.md`](docs/UPSTREAM_TOOLBOX.md) | The pcrecomp toolbox this product is built on |
| [`reports/audit-2026-10-30.md`](reports/audit-2026-10-30.md) | Adversarial audit: delegation, shim honesty, claims vs evidence |
| [`table.md`](table.md) | Generated game database for the measured corpus |
| [`CHANGELOG.md`](CHANGELOG.md) | Release history |

## Roadmap, in the order the evidence supports it

1. **Runtime coverage** — close the measured import gaps so the fourteen built
   binaries boot. In progress: SDL 1.2, SDL_mixer, OpenGL/GLU, the MSVC CRT and
   the Win32 atom table have landed.
2. **High-level source** — a real decompiler provider behind the readability
   gate, so functions can leave `LOW_LEVEL_FALLBACK` with evidence rather than
   by assertion.
3. **Independent verification** — an original-process oracle, deterministic
   scenario replay and frame/input evidence, so a status above `partial` can be
   earned.
4. **Targets** — Windows x86 → native Windows x64, then the same game on macOS
   ARM64, each as its own verified gate.
5. **Desktop GUI** — project wizard, run dashboard, source explorer and
   verification dashboard over the same backend the CLI drives.

## Contributing and licence

MIT — see [`LICENSE`](LICENSE). Third-party notices are in [`NOTICE`](NOTICE) and
[`docs/LICENSES.md`](docs/LICENSES.md); contributors are credited in
[`CONTRIBUTORS.md`](CONTRIBUTORS.md). No game binary or asset is redistributed
here, and none should be committed.
