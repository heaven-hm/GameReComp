# Getting Started

This guide takes you from a clean checkout to your first inspected, rebuilt and
reported game executable. It describes what the tool does and does not do today,
so you can read your own reports without over-trusting them.

**Use only executables you are legally entitled to run.** RevEng-Studio never
downloads games, never bundles game data, and never copies a game binary into the
repository. Your corpus stays where you put it.

---

## 1. What you are about to run

`autorecomp` is one command with eight stages:

```text
inspect -> recover -> translate -> project -> compile -> run -> verify -> report
```

| Stage | What it produces |
| --- | --- |
| inspect | PE headers, machine type, imports, resources, entry point |
| recover | function boundaries and decoded instructions (Capstone) |
| translate | one C source unit per function, every address tagged `ADDR(0x...)` |
| project | a generated CMake project that compiles exactly those units |
| compile | the rebuilt native executable (one shared adaptive deadline) |
| run | launches the rebuilt binary against your supplied assets |
| verify | a fail-closed status; never infers success it did not observe |
| report | `report.json` and `report.html` with hashes, timings and oracle gaps |

Every generated function is currently labelled `LOW_LEVEL_FALLBACK`: register-
transfer C that compiles and traces back to an address, but is **not** readable
reconstructed source. Do not describe or treat it as human-readable. High-level
reconstruction is the next development phase.

---

## 2. Prerequisites

| Requirement | Why | Check |
| --- | --- | --- |
| Python 3.10+ | the pipeline itself | `python3 --version` |
| Capstone 5 (`pip install capstone`) | x86 decoding | `python3 -c "import capstone; print(capstone.__version__)"` |
| CMake ≥ 3.16 | generated project build | `cmake --version` |
| C compiler (clang or gcc) | generated project build | `cc --version` |
| pkg-config with SDL2 and SDL2_ttf | generated runtime links SDL2 | `pkg-config --exists sdl2 SDL2_ttf && echo ok` |
| pthread and libm | POSIX host runtime | provided by the system |

macOS (Homebrew):

```sh
brew install cmake pkg-config sdl2 sdl2_ttf
python3 -m pip install capstone
```

Debian/Ubuntu:

```sh
sudo apt install build-essential cmake pkg-config libsdl2-dev libsdl2-ttf-dev
python3 -m pip install capstone
```

The generated CMake project also accepts a toolchain file, but the only tested
configuration is a host-native build (macOS arm64 at the time of writing).

---

## 3. Point the tools at your corpus

The corpus tools and the game database read two environment variables. Nothing
is hardcoded; if a variable is unset the corresponding corpus is reported as
`MISSING_INPUT` rather than guessed.

```sh
export SMALL_GAMES_PATH=/path/to/your/games     # the small-games corpus
export LARGE_GAMES_PATH=/path/to/your/large     # optional, larger titles
```

A copy-paste starting point lives in [`config/corpus.env.example`](../config/corpus.env.example).
To refresh the inventory and the game database:

```sh
python3 -m tools.corpus.inventory    # writes reports/corpus/small-games-inventory.*
python3 -m tools.gamesdb             # regenerates table.md from measured results
python3 -m tools.benchmarks          # regenerates docs/BENCHMARKS.md
```

`autorecomp` itself takes the executable path you give it; the environment
variables only tell the database how to record the path relative to your corpus.

---

## 4. First run

```sh
./autorecomp "$SMALL_GAMES_PATH/cavestory/Doukutsu.exe"
```

Useful variants:

```sh
./autorecomp game.exe --assets-dir /path/to/game   # where the game reads its data
./autorecomp game.exe --project-dir projects/mygame
./autorecomp game.exe --timeout 60                 # allow a longer run (default 30 s)
./autorecomp game.exe --json                       # machine-readable final report
./autorecomp game.exe --no-build --no-run          # stop after source generation
./autorecomp game.exe --interactive                # real SDL window/audio
./autorecomp game.exe --split 2000 --split-bytes 16777216   # larger source units
./autorecomp game.exe --jobs 8                     # parallel build
```

Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | `scenario-pass` — every stage passed and the declared scenario was observed |
| 2 | a stage failed (inspect, recover, translate, project, compile or run setup) |
| 3 | the rebuilt binary ran, but verification did not reach `scenario-pass` |

Exit code 3 is the normal outcome while no game has a gameplay oracle yet. Do not
read it as success, and do not lower it to 0 by hand.

### What a run looks like

The pipeline prints one line per stage and how long it took. A typical PE32 game
on this host:

```text
==> inspect
==> recover
==> translate
==> project
==> compile
==> run
==> verify
==> report
```

The first run of a game also writes a project directory:

```text
projects/<name>/
  src/            generated C units, headers, recomp_units.json
  build/          CMake build tree and the rebuilt executable
  reports/        report.json, report.html
  logs/           per-stage logs
```

Generated source is deterministic and address-tagged; it is safe to inspect but
should be treated as machine output, not as a maintained codebase.

---

## 5. Reading the report

`reports/report.json` is the source of truth. Key fields:

| Field | Meaning |
| --- | --- |
| `stages[]` | one entry per stage with `ok`, timing, and stage-specific data |
| `score` | weighted stage completion (inspect 10, recover 10, translate 10, project 10, compile 20, run 15, verify 15, report 10) |
| `outcome.status` | `not-run`, `launch-failed`, `trace-diverged`, `partial`, `scenario-pass` |
| `outcome.executable_sha256` | hash of the rebuilt executable, tied to the input hash |
| `outcome.target` | host/target label — never silently generalised |
| `outcome.checks` | the oracle checks that actually ran |
| `outcome.oracle_gaps` | what verification did **not** cover |

Rules the report follows:

- A stage counts toward the score only when it succeeded for the recorded input
  hash. A crashed run does not earn run/verify credit.
- Verification is fail-closed. If no comparison ran, the status is `launch-failed`
  or `partial`, never `scenario-pass`.
- Oracle gaps are always listed. Today they include "no frame, input or audio
  comparison" and "low-level register C is not high-level reconstructed source"
  on every run.

`report.html` is the same evidence rendered for people. The game database row for
each run is in [`table.md`](../table.md).

---

## 6. Interactive runs and assets

Headless runs force `SDL_VIDEODRIVER=dummy` and `SDL_AUDIODRIVER=dummy` so the
rebuilt binary never opens a window. `--interactive` uses the real drivers, which
is how you look at the result for yourself.

The game reads its data from the executable's directory unless you pass
`--assets-dir`. Point it at the directory containing your own legally obtained
game files. Nothing from that directory is copied into the reports.

The run timeout is 30 seconds by default. A game that needs longer, or that runs
an infinite main loop, produces `timed out` in the run evidence — that is a
measurement, not a failure hidden by the harness.

---

## 7. Build deadline behaviour

Configure and build share one deadline:

```text
deadline = min(3600 s, 600 s + 5 s per MiB of generated source)
```

The default is adaptive because generated source scales with game size; a
measured 375 MiB build used about 4.6 s/MiB wall clock with `--jobs 8`, so the
budget is deliberately above that. Override it explicitly if you know better:

```sh
./autorecomp game.exe --build-timeout 900     # seconds
./autorecomp game.exe --build-timeout 0       # disable the deadline
```

On expiry the whole configure/build process group is killed (not just the direct
child) and the stage fails with `timed_out: true`, the budget used, and the
generated-source size. Nothing is silently retried.

---

## 8. Readability pass (optional)

`autorecomp readability` improves the generated source *after* the pipeline has
proved the project, never before. It is a separate, gated step:

```sh
./autorecomp readability projects/noiz2sa --providers revng
./autorecomp readability projects/noiz2sa --providers all --max-units 1 \
    --max-attempts 3 --max-seconds 900 --provider-timeout 300
```

The gates, in order:

1. the project must have a recorded build matching `src/` on disk
   (`manifest.json` digest), and a verification status of `partial`,
   `scenario-pass`, `verified-playable-scope` or `trace-diverged` — a
   `launch-failed` project is refused, because there is no observed run to
   reproduce;
2. **rev.ng** must be available (`REVNG_BIN`, a `REVNG_DECOMPILE_CMD`
   template, or `revng` on `PATH`) and must produce candidates;
3. the optional AI polish only runs after rev.ng succeeded — it is never a
   substitute for the primary provider.

Every edit is compile-checked, the project is rebuilt and re-run, and the
process evidence must match the recorded baseline; a failing edit is given back
to the provider as feedback for up to `--max-attempts` repairs, then reverted.
The whole pass is bounded by `--max-seconds`, and interruption restores every
edited file. Results are written to `projects/<name>/reports/readability.json`
and `.html`, with the log under `projects/<name>/logs/readability.log`.

Exit codes: `0` for `complete`, `no-edits` or `revng-candidates-recorded`;
`2` for `blocked` (gate or provider unavailable) and for rejected edits. This
build of the product ships no high-level provider yet, so a run without rev.ng
installed is expected to end `blocked` with exit code 2.

---

## 9. Troubleshooting

| Symptom | Cause | What to do |
| --- | --- | --- |
| `not a PE32 executable` / PE32+ rejection at inspect | x86-64 images are refused by design | this is recorded as a measured row, score 0; PE32+ is not supported yet |
| `cmake` not found | missing build tool | install CMake ≥ 3.16 and put it on `PATH` |
| `pkg-config` cannot find SDL2 / SDL2_ttf | missing dev packages | install them (section 2) and re-run |
| compile stage fails with `timed_out: true` | build exceeded the adaptive deadline | re-run with `--build-timeout 0` or a larger value; the report records the budget |
| run stage: `launch-failed`, exit `-6`, stderr mentions `dyld` and `libfreetype` | host Homebrew freetype 2.15.0 references `_HVF_clear_part_cache`, which current macOS `libhvf.dylib` does not export | host toolchain fault; rebuild freetype from source (`brew reinstall --build-from-source freetype`) or otherwise fix the host library — the rebuilt executable is not at fault |
| run stage: explicit `timeout` | the game loop did not exit within `--timeout` | increase `--timeout`; long-running games are expected to be killed |
| verify: `trace-diverged` | original and rebuilt observables disagreed | keep the report; that is the harness doing its job |
| `MISSING_INPUT` for a corpus path | `SMALL_GAMES_PATH`/`LARGE_GAMES_PATH` unset or the file is absent | set the variable or pass the executable path explicitly |

When a run fails, the harness keeps the stage log under `projects/<name>/logs/`
and the report at `projects/<name>/reports/report.json`. Fixes are applied to the
pipeline, not to individual game output: rerun the same command after a fix so
the evidence is comparable.

---

## 10. Where to read next

| You want to know | Read |
| --- | --- |
| how the stages fit together | [`ARCHITECTURE.md`](ARCHITECTURE.md), [`PIPELINE.md`](PIPELINE.md) |
| what the source labels mean and how provenance is tracked | [`SOURCE_PROVENANCE.md`](SOURCE_PROVENANCE.md) |
| how the readability pass and its gates work | [`SOURCE_PROVENANCE.md`](SOURCE_PROVENANCE.md), section 8 above |
| how verification decides pass/fail | [`VERIFICATION.md`](VERIFICATION.md) |
| what upstream backend code is used and under which licence | [`BACKENDS.md`](BACKENDS.md), [`LICENSES.md`](LICENSES.md) |
| measured benchmark data and methodology | [`BENCHMARKS.md`](BENCHMARKS.md), [`table.md`](../table.md) |
| the planned desktop application | [`GUI.md`](GUI.md) |
| runtime coverage and known Win32 gaps | [`RUNTIME_COMPAT.md`](RUNTIME_COMPAT.md) |

For the broader toolbox this product is built on, see the "Upstream pcrecomp
toolbox" section of the [README](../README.md).
