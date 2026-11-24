# Corpus acceptance report generator

`tools/corpus_report.py` produces a **machine-readable acceptance report** for
the game corpus from evidence paths supplied on the command line. It is an
*auditor* of the existing corpus tooling: it runs no game, compiles nothing and
reads no hard-coded corpus directory. It records hashes, log paths and frame
proofs, then assigns one of `PASS`, `PARTIAL`, `BUILD ONLY`, `FAIL` or `BLOCKED`
with an explicit reason. **A PASS is never invented from a build, a process
launch, a log line or a non-blank frame.**

This document is paired with `tests/test_corpus_report.py`. Existing tools and
docs are intentionally left unmodified; this agent owns only the three new
files `tools/corpus_report.py`, `tests/test_corpus_report.py` and this document.

## Audit of the existing corpus/acceptance tooling

| Artifact | What it already does | Gap this report addresses |
|---|---|---|
| `tools/game_runner.py` | Runs one already-built binary at a time; records command, exit code, `run.log`, frame paths and the binary SHA-256. A game with no native binary is `BLOCKED`, never `PASS`. | Stops at "ran"; emits no acceptance verdict and does not decode frames. Output is a runner summary, not an acceptance report. |
| `tools/frame_proof.py` | Decodes the runtime's uncompressed 24-bpp BMPs; records per-frame SHA-256, dimensions, distinct-colour count and a fail-closed `blank` flag. No Windows-original column. | Decodes BMP only (a PNG path is reported as an error); says nothing about game identity or verdicts. It is proof of pixels, not of gameplay. |
| `tools/size_tier_report.py` | Joins the runner summary with frame proof, groups games by binary size tier, and marks frames `OK`/`BLANK`/`UNPROVEN`. A game with a non-blank frame is at best `PARTIAL`; no frames is `FAIL`. | Verdict vocabulary is only `PARTIAL`/`FAIL`. It has no `PASS`, `BUILD ONLY` or `BLOCKED` value and does not carry the original input SHA-256. Exit 0 is an "OK run", not an acceptance. |
| `docs/GAME_MATRIX.md` | Defines the fail-closed working status (`PASS` needs a recorded scenario, `PARTIAL` = launched with no independent scenario, `FAIL` = did not get that far, `NOT RUN`/`BLOCKED`); the Agent 3 addendum lists PASS/PARTIAL/FAIL/BLOCKED/NOT TESTED and flags upstream rows as claims with `†`. | Does not use `BUILD ONLY` at all, and a compile-only record is indistinguishable from a launch record in the table vocabulary. The document is prose, not machine-readable, and an IGI `PASS` there rests on a rendered menu, not a full scenario. |

The new generator preserves those fail-closed conventions and adds the missing
`BUILD ONLY` state plus a single, parseable JSON artifact.

## Inputs (all CLI-supplied; no defaults to local paths)

| Flag | File | Used for |
|---|---|---|
| `--summary` | `game_runner` summary JSON | binary path/hash, exit code, evidence dir, log, frame paths |
| `--frame-proof` | `frame_proof` JSON | per-frame SHA-256, dimensions, distinct colours, blank flag |
| `--results` | a results JSON such as `reports/games/results.json` | input SHA-256, build/port details, run status, exit code |
| `--scenarios` | optional scenario assertions JSON | the **only** source of a `PASS` verdict |
| `--out` | output report JSON path | required |

At least one of `--summary` or `--results` is required. A supplied path that is
missing, unreadable or not a JSON object makes the tool exit `2` instead of
silently dropping evidence.

### Scenario JSON format

```json
{
  "schema": 1,
  "records": [
    {
      "game": "tetris-oucc",
      "verdict": "PASS",
      "scenario": "boot-to-menu-and-move-piece",
      "oracle": "original Windows executable",
      "evidence": "recorded menu transition and piece movement",
      "evidence_path": "/absolute/path/to/scenario-evidence.txt",
      "evidence_sha256": "<64-hex sha256 of that file>"
    }
  ]
}
```

A record is accepted only if `verdict` is `PASS`, `scenario`, `oracle` and
`evidence` are non-empty, the oracle is not a dummy/"no oracle" placeholder,
`evidence_path` names an existing file, and — when supplied — `evidence_sha256`
matches the file's actual SHA-256. A rejected claim is kept as a rejection: it
cannot be replaced by a later record for the same game. No evidence file is
read for content or generated on the caller's behalf.

## Verdict rules (fail-closed)

| Verdict | Assigned when | Reason recorded |
|---|---|---|
| `PASS` | A **validated** scenario record exists **and** the game has a run record plus a log file or a non-blank proven frame. | Names the scenario, the oracle, the evidence path and the quoted evidence. |
| `PARTIAL` | A scenario PASS claim was rejected or lacks run evidence (downgraded); or the process launched/stayed alive until timeout with no scenario or oracle; or only a non-blank frame is proven. | "no independent scenario or oracle", "non-blank frame proof only", or "scenario PASS claim present but run/log/frame evidence is missing; downgraded". |
| `BUILD ONLY` | Build/input evidence is present (binary, binary hash or input hash/port details) but there is no run record. | "build/input evidence recorded but no run record; no process launch claimed". |
| `FAIL` | Explicit abort/crash/boot-failure status or failure text, or every captured frame is single-colour `BLANK`. | The recorded status text or the blank-frame finding. |
| `BLOCKED` | Runner reported `BLOCKED` (no native binary), or there is no build, input or run evidence at all. | The runner's reason, or "no build, input or run evidence supplied". |

Notes on the rules:

* A non-zero exit code alone is **not** a failure: several native GUI games in
  this corpus exit `1` after a normal launch. Failure requires an explicit
  abort/crash/boot-failed marker or all-blank frames.
* A frame with no matching proof entry stays `UNPROVEN` with null hash and null
  colour count. A proof row without a hash also stays `UNPROVEN`; it is never
  assumed blank. A single-colour frame is `BLANK` and is not graphics evidence.
* A frame path match by path alone is used only when exactly one proof row has
  that path; ambiguous matches stay unproven.
* `PASS` is deliberately hard to reach. With no `--scenarios` file the count of
  `PASS` verdicts is always zero.

## Output shape

The report is a JSON object with `schema_version`, `generator`,
`generated_at_utc`, `label`, `note`, `sources` (the CLI paths actually read),
`verdict_vocabulary`, `limits`, `counts` (one integer per verdict) and `games`.
Each entry in `games` has:

* `game`, `verdict`, `reason`;
* `checks`: booleans for binary identity, log file, run record, frame proof,
  non-blank frame and valid-PASS-scenario;
* `evidence`: binary path and SHA-256, input SHA-256, log path/existence, exit
  code, run status, per-frame records, source names and any validated scenario.

No image bytes, log contents or guessed values are embedded.

## Usage

```sh
python3 -m tools.corpus_report \
  --summary test-results/game-matrix/execution/summary.json \
  --frame-proof test-results/game-matrix/proof/frame-proof.json \
  --results reports/games/results.json \
  --scenarios /absolute/path/to/scenarios.json \
  --out test-results/corpus-report.json \
  --label "nightly corpus acceptance" --note "source of truth: CLI paths"
```

Without scenarios, omit `--scenarios`; the report then records facts and
caps every game at `PARTIAL`.

Tests:

```sh
python3 -m pytest tests/test_corpus_report.py -q
```

## Smoke run over existing evidence

A smoke run was executed against the repository's existing
`reports/games/results.json` (no scenarios supplied):

* 102 games — `PASS=0`, `PARTIAL=95`, `BUILD ONLY=0`, `FAIL=7`, `BLOCKED=0`.
* The smoke report was written outside the repository; it is not a committed
  artifact and is not evidence of any game's acceptance.
* Targeted tests: `15 passed`.

## What this report does not claim

* No original-Windows execution oracle exists in this environment. There is no
  Windows frame or differential gameplay comparison here, and Wine is not used
  as the final runtime.
* A `PARTIAL` verdict proves only that a recorded process launched or a
  non-blank frame was decoded; it does not establish correct gameplay, input,
  audio, save/load or menu behaviour.
* Hashes, colour counts, log and frame paths are reported only from the files
  supplied on the command line. A missing file, absent proof or unverifiable
  scenario is reported as such — never filled in as a pass.
* The smoke numbers above describe one local run over one available results
  file. They are not a claim about issues being closed or about completion of
  the wider corpus; the acceptance ledger remains fail-closed.
