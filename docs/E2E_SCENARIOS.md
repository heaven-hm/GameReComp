# E2E scenario matrix (CLI `inspect --json` + GUI selftests)

`tools/e2e_scenarios.py` is a repeatable, headless matrix that runs the real
GameReComp CLI and GUI binaries over every locally supplied PE32 corpus file
and emits **one** deterministic JSON report containing the exact command and
exit code of every step.

Unlike `tools/e2e_suite.py` (which chains CLI + CUA GUI clicks + AI readability
+ game evidence into one workflow), this matrix is deliberately narrow and
deterministic: it exercises only the fail-closed PE header checks that both
binaries expose without a window server, a game, or an AI key.

## Inputs come only from CLI arguments

No PE path, repository path, or binary path is hardcoded in the tool. Every
input is supplied at invocation time:

```sh
python3 -m tools.e2e_scenarios \
    --cli target/release/game-recomp-cli \
    --gui target/release/game-recomp-gui \
    --repo . \
    --exe /path/to/corpus/game1.exe \
    --exe /path/to/corpus/game2.exe \
    [--exe ...] \
    [--project /path/to/project/dir ...] \
    --out test-results/e2e-scenarios/report.json
```

Reuse the corpus roots discovered by `tools/corpus.inventory` if desired, e.g.
`--exe "$SMALL_GAMES_PATH/Game.EXE"`, but the matrix itself never invents a
path. Paths may also come from an inventory JSON by expanding it in the shell;
the tool's contract is simply "one `--exe` per corpus file".

## Scenario matrix

For each `--exe` input the matrix records two steps:

| Step | Command | Meaning |
|---|---|---|
| `cli_inspect_json` | `<cli> inspect <exe> --json --repo <repo>` | Machine-readable PE header check. |
| `gui_selftest_pe` | `<gui> --selftest-pe <exe>` | The GUI's PE-selection path, headless. |

Each optional `--project` input adds one `gui_selftest_project` step:
`<gui> --selftest <project> --repo <repo>` (the GUI's project-model selftest).

Both binaries call the same `inspect_pe` header parser, so the matrix can make
a stronger assertion than "each command exited": it cross-checks that the CLI
and GUI **independently classify each input identically**.

### Stable classification contract

Both binaries use the same exit-code meaning (see `run_inspect_cmd` in
`src/cli.rs` and `run_selftest_pe` in `src/pe.rs`):

| Exit code | Class | Meaning |
|---|---|---|
| 0 | `SUPPORTED` | Valid PE32 x86, a supported target. |
| 1 | `INVALID` | Not a usable PE (fail-closed header check). |
| 2 | `UNSUPPORTED` | Valid PE, but not PE32 x86. |

The CLI class is also derived from the `ok` / `pe32_x86` fields of the
`--json` payload, and the GUI class from its printed `PE: ...` line. A binary
whose exit code disagrees with its own output is flagged `INCONSISTENT`.

### Verdict rules

- **PASS** — both commands ran (no timeout/spawn error), neither was
  internally inconsistent, and CLI and GUI agreed. `SUPPORTED`, `UNSUPPORTED`
  and `INVALID` are all valid agreed outcomes.
- **FAIL** — a runner error, a payload/exit or text/exit inconsistency, a
  CLI/GUI disagreement, or (for `--project`) a nonzero selftest exit code.
- **SKIP** — a supplied input path did not exist; it is recorded but never
  counted as a pass.

An invocation with no inputs, or with only SKIPs, yields an overall `FAIL`
rather than a vacuous pass. The process exit code mirrors the overall verdict
(`0` for PASS, `1` otherwise).

## Report shape

The single `--out` JSON file has this shape (no timestamps, stable ordering,
exact argv lists preserved):

```json
{
  "schema": 1,
  "tool": "tools/e2e_scenarios.py",
  "what_it_proves": "...",
  "inputs": { "cli": "...", "gui": "...", "repo": "...", "timeout_seconds": 30,
              "pe_files": ["..."], "projects": [] },
  "scenarios": [
    { "id": "pe-0001", "kind": "pe", "input": "...", "input_exists": true,
      "sha256": "...",
      "steps": [ { "name": "cli_inspect_json", "command": ["..."],
                   "exit_code": 0, "stdout": "...", "error": null }, ... ],
      "cli_class": "SUPPORTED", "gui_class": "SUPPORTED",
      "agreement": true, "verdict": "PASS" }
  ],
  "matrix": [ { "input": "...", "sha256": "...", "cli": 0, "gui": 0,
                "classification": "SUPPORTED", "verdict": "PASS" } ],
  "summary": { "scenarios": 1, "pass": 1, "fail": 0, "skip": 0,
               "cli_gui_disagreements": 0 },
  "verdict": "PASS"
}
```

`matrix` is a compact per-input view derived from `scenarios`; `command` is the
literal argv list that was executed (not a reconstructed string), and
`exit_code` is the observed return code or the literal `TIMEOUT` /
`SPAWN_ERROR`. Output is capped per step so the report stays bounded.

## Verification and limits

```sh
python3 -m pytest tests/test_e2e_scenarios.py -q
```

The unit tests mock `subprocess.run`; they need no built binaries, no game
assets, and no network. To collect real evidence, first build the CLI and GUI
with the repository's normal Cargo workflow, then invoke the command above
with the binary and corpus paths supplied on the command line.

Honest limits:

- `inspect` / `--selftest-pe` are **header checks**. They do not execute the
  corpus, disassemble code, prove compatibility, or imply any gameplay result.
- Determinism refers to the report content for a fixed set of arguments and
  fixed binary behaviour; it does not pin toolchain or binary versions.
- A `PASS` means the two tools agreed on the header classification, not that
  a game was recompiled or rendered. No issue is auto-closed by this matrix.
