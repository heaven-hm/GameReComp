# Single-command end-to-end suite (CLI + GUI + AI + evidence)

`tools/e2e_suite.py` chains the existing tools into one repeatable run and
writes `test-results/e2e/e2e-report.json` and `e2e-report.md`. It inherits the
AI key from the caller's environment; no credential value is written to any
report.

## Stages actually executed

1. **CLI smoke** — `--version`, `about`, `inspect --json <real PE32>` (all rc 0).
2. **CLI AI readability** — `readability --providers ai` on a tiny tested CMake
   fixture; the harness compiles, reruns and compares baseline evidence.
3. **GUI via CUA** — launches the rebuilt GUI binary, discovers its window with
   CuaDriver's accessibility tree, clicks through to the Agents view, presses
   the labelled `Improve C Readability (AI)` AX button, captures a screenshot
   and reads the resulting `readability.json`.
4. **Game evidence** — aggregates `game_runner` + `frame_proof` counts (frames,
   blank/non-blank).
5. **Recompile ledger** — counts native binaries only when `compile: ok` exists.

## Final measured run

```
cli_version          rc=0
cli_about            rc=0
cli_inspect          rc=0
cli_ai_readability rc=0 outcome=complete
gui_ai_readability button=True outcome=complete
verdict PASS
```

Aggregate evidence: 11 games executed, 4 with frames, 12 frames total
(8 non-blank, 4 blank); 8 native binaries with a successful compile, 2
header-only/partial builds.

## Defects found and fixed during this E2E run

- AI renamed a dispatched symbol (`sub_00402020`) on its first attempt; the
  harness correctly rejected it, but the failed build removed the baseline
  binary and a later run was falsely blocked. `_revert_and_rebuild()` now
  restores the binary after every rejected edit.
- The suite's first version inspected the CMake fixture (not a PE) and stopped;
  it now accepts `--inspect-exe` with a real PE32 file.
- A single AI attempt was too brittle; the suite now allows up to 3 repair
  attempts with the harness's failure feedback.

## Honest limits

- "complete" for AI means one tiny fixture function was rebuilt and reproduced
  baseline evidence — not that game C was improved.
- GUI clicking is verified by AX token presence and screenshot; CUA reports
  generic clicks as `effect: unverifiable`, so the harness checks state via the
  resulting report, not by trusting the click call.
- No issue was closed and no 100% claim is made.
