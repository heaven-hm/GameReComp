# Source provenance

Every function in a generated project carries a provenance category, and every
claim about source quality in reports comes from that category or from a
recorded run — never from the shape of the generated text.

## Categories

| Category | Meaning | Set by |
|---|---|---|
| `HIGH_LEVEL_VERIFIED` | Reconstructed, readable source that compiled and whose behaviour matched an independent reference | Not produced yet; no provider is wired into the build |
| `HIGH_LEVEL_UNVERIFIED` | Reconstructed, readable source that has not passed behavioural comparison | Not produced yet |
| `LOW_LEVEL_FALLBACK` | Compilable register-transfer C at original addresses | `pipeline.stage_project()` default for every recovered function |
| `UNRESOLVED` | The lifter failed; the emitted stub prints and aborts when called | `pipeline.stage_translate()` when `ERROR lifting` appears |

`reports/functions.json` lists every function with its address, size, block and
instruction counts, thunk flag, source unit and category. `project.json`
records the per-category totals. The pipeline currently reports the
high-level counts as zero because no high-level function has entered the
build; renaming variables in low-level C does not change the category.

## What counts as high-level

The register-transfer C from `tools/lift/lift32.py` is **low-level** even
though it is syntactically C: guest registers are locals, memory goes through
`ADDR(va)`, and control flow is a dispatch loop. A function only becomes
high-level when readable reconstructed source for it is compiled into the
delivered binary and that binary passes the verification gate. The readability
harness exists to keep those two steps tied together; it does not award the
category.

## Readability harness

`autorecomp readability <project>` (`tools/autorecomp/readability.py`) is a
post-pipeline pass with three hard rules:

1. source is only touched after it has been built and run,
2. rev.ng is the primary provider,
3. an edit exists only if the rebuilt binary reproduces the baseline process
   evidence.

It does not run as part of the normal pipeline; it is a separate command over
an existing project.

### Gate

Before any provider is invoked the harness checks:

- `manifest.json`, `reports/run.json` and `reports/verification.json` exist,
- the manifest's `source_digest_sha256` equals the digest of the current `src/`
  tree (so the recorded build really came from these files),
- the recorded verification status is one of `partial`, `scenario-pass`,
  `verified-playable-scope` or `trace-diverged` (a launched and observed run),
- the recorded binary still exists.

`not-run` and `launch-failed` baselines are refused with outcome `blocked`.

### rev.ng

rev.ng is discovered through `REVNG_BIN`, then `PATH`, or through
`REVNG_DECOMPILE_CMD` when rev.ng must be wrapped:

```
REVNG_DECOMPILE_CMD='revng decompile {pe} --output-dir {outdir}'
```

The command runs with the output directory as its working directory, is
bounded by the budget, and everything it writes is kept under
`readability/revng/`. Candidate `*.c` files are recorded in
`reports/readability.json`, but they are **not** compiled: there is no ABI
bridge that would let a rev.ng function stand in for a `recomp_func_t` at a
recovered address yet, and the harness does not pretend otherwise. The stage
reports `integration: not-implemented`.

If rev.ng is absent or fails, the pass ends without touching source. AI is not
a fallback for rev.ng.

### Optional AI polish

The AI provider is configured by `REVENG_AI_CMD`; it receives two arguments,
the current source unit and the output path to write:

```
REVENG_AI_CMD='my-tool --rewrite'   # invoked as: my-tool --rewrite <unit> <out>
```

The harness also sets:

- `READABILITY_ATTEMPT` — the current attempt number,
- `READABILITY_FEEDBACK` — a file describing why the previous attempt failed
  (build log tail or changed process evidence), or `none (first attempt)`.

Nothing is sent anywhere unless the user configures such a command. The
provider is told to keep the unit compiling and behaviour-identical.

### Repair loop and acceptance

For each selected source unit (default one):

1. ask the provider for a rewrite,
2. if it changed the file, rebuild with the project's own CMake build,
3. run the rebuilt binary with the recorded command, working directory,
   headless mode and timeout,
4. compare observable evidence with the baseline: exit code, timeout flag, and
   absence of new fatal markers (`Symbol not found`, `Segmentation fault`,
   `unresolved import`, `ERROR lifting`).

A build failure or an evidence change is written to the feedback file and the
attempt is repeated, up to `--max-attempts` (default 3). The file is restored
from backup before every retry, so each attempt starts from the tested source.
Only an attempt that builds **and** reproduces the evidence is kept; its
unified diff is written under `readability/ai/*.patch`. Exhausting the loop
reverts the unit byte-for-byte and records `rejected-after-repair` with the
last failure. Accepted units remain `LOW_LEVEL_FALLBACK`: readability changed,
provenance did not.

Because generated units under `src/recomp/gen/` are rewritten by the next
pipeline run, accepted edits are not permanent. The patch under
`readability/ai/` is the durable record.

### Budget, timeouts and failure handling

- `--max-seconds` (default 900) is a wall-clock budget for the whole pass.
  Every provider, build and run step is clamped to the time remaining.
- The build timeout is derived from the recorded `compile` stage
  (four times its duration plus 60 s, minimum 120 s).
- The run timeout reuses the recorded timeout, extended to three times the
  observed run duration plus 10 s when the baseline exited, capped at 300 s.
  A baseline that timed out keeps its original timeout so the comparison
  remains meaningful.
- Provider invocations are bounded by `--provider-timeout` (default 300 s) or
  the remaining budget, whichever is smaller.
- Exceptions, Ctrl-C and budget exhaustion restore every edited file before
  the report is written. `reports/readability.json` and `readability.html`
  are always produced; the pass logs each step to `logs/readability.log`,
  each provider attempt to `logs/readability-ai-<unit>-<n>.log`, the rev.ng
  run to `logs/readability-revng.log`, the rebuild to
  `logs/readability-build.log`, and the verification run to
  `logs/readability-run.*.log`.

### Outcomes

| Outcome | Meaning |
|---|---|
| `complete` | at least one edit accepted after rebuild and rerun |
| `revng-candidates-recorded` | rev.ng produced candidates; nothing integrated into the build |
| `no-edits` | rev.ng ran; AI produced no changes |
| `blocked` | gate not met, rev.ng unavailable, or AI disabled/without candidates |
| `rejected-after-repair` | every attempt failed; source restored |
| `rejected-compile-failed` / `rejected-evidence-changed` | legacy outcome names kept for reports written by older runs |
| `revng-failed` | rev.ng did not complete; source untouched |
| `budget-exhausted` | wall-clock budget ran out; source restored |
| `error` / `interrupted` | harness exception or Ctrl-C; source restored |

## Manifest and hashes

`manifest.json` records the input SHA-256, a SHA-256 for every file under
`src/`, and the digest over that file list. The rebuilt binary is recorded
with its own SHA-256 and the source digest it was built from. `inspect`
additionally records MD5 and SHA-1 of the input. The readability harness
compares the recorded source digest with the tree before it changes anything,
which is what prevents a stale build from being treated as evidence.
