# Stela benchmark method

How to measure the GameReComp recompilation pipeline for the Stela port work,
and how to turn two measurements into a comparison. This page documents the
tools `tools/stela_bench.py` and `tools/stela_compare.py`.

## Rule zero: a measurement is not a speed claim

`stela_bench` records what happened for one input under one set of settings. It
does **not** prove that anything got faster. **No speed claim is valid without a
measured before/after pair**: two results produced by this tool, for the *same*
input hash, under the *same* mode, jobs, timeout, and compiler settings, with the
change under test as the only difference. One number is a datum, not a speedup.
If you did not run both sides yourself and keep both JSON files, you have an
opinion, not a measurement.

## Tools and exact commands

Run both from the repository root. Inputs and outputs are CLI-supplied; no game
path is hard-coded.

### Measure one side

```
python3 -m tools.stela_bench --pe <exe> --project <dir> --out <json> \
    [--mode source|binary] [--jobs 1] [--timeout 1800]
```

- `--pe` — the PE32 input to measure (its SHA-256 is recorded).
- `--project` — an output project directory. Use a *different* directory for the
  baseline and the optimized run so each keeps its own `reports/report.json`
  and `build/` binary; the tool reads metrics from `<project>/reports/report.json`
  and the binary size from `<project>/build/<project.name>`.
- `--out` — the JSON result file to write.
- `--mode source` (default) maps to `--out-mode source-only`: no compiler run,
  so `binary_bytes` is 0 and `compile_seconds` is `null`.
- `--mode binary` maps to `--out-mode binary-only`: use this when you intend to
  compare compile time or binary size.
- `--jobs` and `--timeout` are forwarded to the pipeline; keep them identical
  across sides.

### Compare two sides

```
python3 -m tools.stela_compare --before <before.json> --after <after.json> \
    [--out <compare.json>]
```

The tool prints the comparison JSON to stdout and optionally writes it to
`--out`. Each row has `metric`, `before`, `after`, and `delta_percent`, where
`delta_percent = round((after - before) * 100 / before, 2)` (a zero baseline
yields `0.0`, never a division error). Metrics are `wall_seconds`,
`generated_source_bytes`, `binary_bytes`, `compile_seconds`, plus one
`stage:<name>` row per stage in `stages_seconds`. For seconds and bytes a
negative `delta_percent` is an improvement.

## Baseline / optimized workflow

1. **Baseline.** With the settings you want to compare *against*, run
   `stela_bench` on your input and save the result as `before.json`.
2. **Change one thing.** Apply the pipeline/compiler change under test (see
   provenance below). Keep the input, mode, jobs, timeout, and host identical.
3. **Optimized.** Run `stela_bench` again on the *same* `--pe`, into a *new*
   `--project` directory, saving `after.json`.
4. **Compare.** Run `stela_compare` with both JSON files and keep its output.

```
# 1. baseline
python3 -m tools.stela_bench --pe /path/to/game.exe \
  --project /path/to/baseline-proj --out /path/to/before.json \
  --mode binary --jobs 1

# 3. optimized (same --pe, same --mode/--jobs/--timeout; new --project)
python3 -m tools.stela_bench --pe /path/to/game.exe \
  --project /path/to/optimized-proj --out /path/to/after.json \
  --mode binary --jobs 1

# 4. compare
python3 -m tools.stela_compare \
  --before /path/to/before.json --after /path/to/after.json \
  --out /path/to/compare.json
```

Before quoting any number, check all of the following:

- `same_input` is `true` in the comparison. It compares `input_sha256` from the
  two results; `false` means you measured different executables and the rows are
  not comparable. This is the same-input guard — never report a speedup from a
  pair that fails it.
- Both `rc` values are `0`. A failed run is not a fast run. On timeout the
  benchmark sets `rc` to the string `"TIMEOUT"` (with the captured output tail),
  not a numeric exit code; `stela_compare` skips such non-numeric values rather
  than treating a timeout as a measurement.
- Use stage rows (`stage:<name>`) for stage-specific claims and `wall_seconds`
  only for end-to-end wall time; wall time includes process startup and the
  report/binary stat work, so it is not a pure stage timer.

## Compiler settings provenance

`stela_bench` does not define compiler flags. It invokes the real pipeline with:

```
python3 -m tools.autorecomp <pe> \
  --project-dir <dir> --assets-dir <pe-parent> \
  --no-table --no-progress --input-os windows --input-arch x86 \
  --target-os host --target-arch host \
  --jobs <jobs> --out-mode source-only|binary-only --no-run
```

so the compile step comes from the pipeline, not from the benchmark:

- `tools/autorecomp/pipeline.py` configures the build with CMake
  `-DCMAKE_BUILD_TYPE=Release` and builds with `cmake --build --parallel <jobs>`.
  Its compile deadline is adaptive: `min(3600, 600 + 5.0 * source MiB)` seconds
  (see `default_build_timeout` in that file).
- `tools/autorecomp/projectgen.py` generates the CMake project, which sets
  `CMAKE_C_STANDARD 11` and defaults `CMAKE_BUILD_TYPE` to `Release`.

An "optimized" run therefore means changing those shared pipeline/template
settings (or the host compiler on `PATH`) and recording exactly what changed.
The benchmark itself only selects `--out-mode` (`source-only` vs `binary-only`),
`--jobs`, and the input/target OS/arch flags. Because the percentages are only
meaningful relative to the settings that produced them, write down the compiler
version, flags, and pipeline revision for each side alongside the JSON files.

## Testing the tooling

The tests use mocked `subprocess` and synthetic report JSON, so they need no
game binary, compiler, or network:

```
python3 -m pytest tests/test_stela_bench.py -q
```

They cover stage and byte extraction, TIMEOUT handling, percentage calculation
(including the zero-baseline guard), and the same-input guard.
