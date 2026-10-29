# Verification

The pipeline never infers correctness from the shape of the generated source
or from a successful build. A result is only as strong as the evidence recorded
in `reports/verification.json`, and the weakest link is always named.

## Statuses

`verify` (in `tools/autorecomp/pipeline.py`) classifies each run as one of:

| Status | Assigned when | Score weight |
|---|---|---|
| `not-run` | the rebuilt binary was not executed (`--no-build`, `--no-run`, or an earlier stage failed) | 0 of 15 |
| `launch-failed` | the process crashed, or exited 2 with `unresolved imports` in its output (boot-failed) | 0 of 15 |
| `trace-diverged` | an oracle was supplied and at least one recorded check mismatched | 15 of 15 |
| `scenario-pass` | an oracle was supplied and every recorded check matched | 15 of 15 |
| `partial` | the process launched and was observed, but no oracle was supplied | 8 of 15 |

The master status list also names `compile-failed` and
`verified-playable-scope`. Neither is emitted by the current pipeline:
`compile-failed` stops the pipeline at the compile stage (there is no run to
verify), and `verified-playable-scope` requires gameplay evidence — frame,
input and audio comparison — that no code in this repository produces yet.
Reports do not use status names they cannot back with evidence.

## What is compared

Without an oracle, verification is process-level: the pipeline records the
command line, working directory, headless mode, timeout, exit code, elapsed
time, timeout flag, boot line (`[host] ... booted: entry ...`,
`[pe] bound N imports, M unresolved`), the unresolved import list, and the
stdout/stderr tails. Launching and surviving to timeout, or exiting, is
`partial` — it proves the binary is not a stub, nothing more.

An oracle is a JSON file supplied with `--oracle`:

```json
{
  "exit_code": 0,
  "stdout_contains": ["booted"],
  "stderr_contains": ["bound 42 imports"]
}
```

Each check is recorded individually in `checks` and a single mismatch turns
the outcome into `trace-diverged` with the mismatching checks named in
`reason`. A pass is scoped to process observables and the report says so
(`oracle compares process observables only, not gameplay state`).

Every verification record carries the oracle gaps that remain, regardless of
status: no independent original-Windows reference run, no frame or audio
comparison, and low-level register C rather than reconstructed high-level
source.

## Fail-closed rules

- The rebuilt program itself refuses to pretend: a PE whose imports cannot be
  bound exits 2 with the unresolved imports named, and `verify` records
  `launch-failed`, not a pass.
- A lifter failure is never silent: the function is emitted as an `UNRESOLVED`
  stub that prints and aborts when called, so a call lands in the evidence.
- The readability harness (see `docs/SOURCE_PROVENANCE.md`) only touches a
  project whose recorded build matches the source tree on disk, and only keeps
  an edit whose rebuild reproduces the previously recorded process evidence.
- `report` runs even after a failed stage, so a failure is always inspectable
  (score, failed stages, log paths).

## Evidence tying binary to source

`manifest.json` records a SHA-256 for every file under `src/`, a digest over
that file list, and the rebuilt binary's own SHA-256 recorded by the compile
stage. `reports/inspect.json` records SHA-256, MD5 and SHA-1 of the input PE.
The verification record repeats the input SHA-256 so a result cannot be
detached from the binary it describes.

## Corpus database

`table.md` is the corpus-wide summary rendered by `tools/gamesdb.py` from
`reports/games/results.json`; `autorecomp` updates both after each run unless
`--no-table` is passed. Rows keep the recorded verification status, score and
input hash, including failures and `MISSING_INPUT` entries for corpus files
that are not present on the current host.

## Known limits

- No original-Windows oracle harness exists in this repository; `scenario-pass`
  can only be claimed against a user-supplied oracle and is scoped to process
  observables.
- No deterministic input replay, frame hashes, audio comparison or save-state
  checkpoints.
- The score is weighted pipeline completion for the recorded hash. It is not
  a measure of game equivalence and must not be read as one.
