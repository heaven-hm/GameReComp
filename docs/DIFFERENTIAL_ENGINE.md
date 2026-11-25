# Generic differential engine (for all games and apps)

`tools/differential.py` turns the differential-testing ideas used by the IGI
recompilation projects into one fail-closed, game-agnostic driver. It is not a
new recompiler; it reuses Unicorn per-function checks (`tools/lift/difftest.py`),
frame proofs (`tools/frame_proof.py`) and the project's primary Highball
original runner.

## Where the method comes from

- **igi-recomp** (`HenryKun55/igi-recomp`, `tools/recomp/difftest.py`,
  `tools/recomp/x87check.py`, `tools/tick_compare.py`): collects every distinct
  non-control-flow instruction form in the original binary, runs each form from
  the *same random state* in Unicorn and in generated C with all flags live,
  several x87 control words, and compares registers, flags, FPU stack and
  touched memory. Its A/B tick and snapshot comparators watch values at equal
  game time and report the largest per-field difference.
- **IGI-RE** (`neonixmoddingteam/IGI-RE`, `docs/VERIFY.md`, `tests/emu.py`): a
  rewrite passes only when, from identical state, it has the same return value
  (including `st(0)`), the same stack cleanup, byte-identical memory changes,
  and the same callee calls in order; fake callees and system stubs make the
  comparison local. Multiple x87 control words catch float rewrites that only
  agree under round-to-nearest.

GameReComp's engine keeps the **principles** — same input, compare everything
observable, never turn a missing oracle into success — while dropping the
IGI-specific addresses, object types and clock constants (those belong in
game-target evidence, not in a shared runtime).

## What it compares

```sh
# Windows original under Highball vs rebuilt native / Linux ELF:
python3 -m tools.differential process \
  --original arith-calc.exe --rebuilt rebuilt-elf \
  --rebuilt-cmd 'docker run --rm --network none -v ... runner /work/app /original' \
  --frames ORIG_DIR REBUILT_DIR \
  --trace ORIG_TRACE REBUILT_TRACE --tolerance 0.001 \
  --functions difftest-report.json \
  --out evidence.json
```

| Check | Comparison | Fail closed when |
|---|---|---|
| `exit_code` | exact | original/rebuilt could not be started |
| `stdout_exact` | byte-for-byte after stripping launcher and GameReComp diagnostics (raw logs kept in evidence) | launcher error (e.g. Highball exit 53) |
| `frames_exact` | SHA-256 of every frame file in both directories | either side has no frames |
| `trace` | per-column max absolute delta against tolerance; non-numeric values exact | no overlapping timestamps |
| `functions:*` | every case in a `difftest.py --json` report is `agree-return`/`PASS` | any case disagrees |

`--original-cmd` defaults to `highball run {exe}` (primary original runner) and
is overridable for tests or alternate runners. `--rebuilt-cmd` supports native
executables and containerised ELF runners.

## Verdicts

- **PASS** — every requested check matched; process-level scope only.
- **FAIL** — at least one requested check mismatched, with the first differing
  line / frame hashes / field deltas recorded.
- **BLOCKED** — the oracle or runner was unavailable (missing binary, Wine
  startup failure, Docker daemon down, timeout). Never PASS, never FAIL.

The evidence JSON records both raw logs, both command lines, input hashes,
per-check booleans, first differences, exact stdout SHA-256 values, and the
`limits` string. It is machine-readable (`schema: 1`) so #14's evidence registry
can consume it.

## Worked example (measured 2026-11-25)

`arith-calc.exe` (PE32 x86) original under Highball vs the native aarch64 ELF
built in `gamerecomp/linux-runner:local`:
`status: PASS`, `exit_code` equal, guest stdout SHA-256
`b6674fd6da06bc9dd36a49a5697efdeb8af7bd354f00b9c6d77359533b572d8f` on both
sides. Evidence: `~/Downloads/recomp-apps/LINUX_PROOF/` and the engine's
`--out` report.

## Limits

A process PASS is a **100% process-level** statement for the supplied args and
stdin. It is not gameplay parity, frame timing, audio, or a whole-game oracle.
GUI games need a deterministic input/frame/audio harness (#145–#147) before any
game-level PASS; until then their honest status is PARTIAL or BLOCKED.
