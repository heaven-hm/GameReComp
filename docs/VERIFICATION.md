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

## The original-game oracle: decision, not aspiration

**Decision (2026-10-30).** End-to-end comparison against the *original Windows
game* requires a Windows host, and this project does not have one today. That is
a hardware fact, not a design choice, and it is recorded here so that nothing in
this repository implies an oracle that does not exist.

Everything this project claims today is therefore one of two things:

| Claim | What backs it |
| --- | --- |
| "the rebuilt binary runs" | the process's own exit code, its import-call profile, and its frames |
| "the typed C equals its mechanical body" | a differential harness linking both entry points from the shipped source |
| "the game matches the original" | **nothing.** It is not claimed anywhere. |

Three routes to a real oracle, in ascending cost, and what was decided:

1. **A per-function differential against the original bytes.** `difftest.py`
   already runs x86 in Unicorn and compares it against lifted C. Driving it with
   a project's own lifted function, against the original PE's bytes at the same
   address, gives "this function agrees with the original on these inputs" for
   any recoverable function — with no Windows host. **Chosen**, because it needs
   nothing this project lacks and it upgrades every function claim from "the
   lifted C does something plausible" to "it agrees with the original bytes on
   the inputs driven". It is per-function and per-input-set: it does not, and
   must not, be summarised as a whole-game claim.

**Static evidence is not redundant with the bytes, and it is not a substitute.**
Measured on 2026-10-30 over Doukutsu's generated functions, which is the cleanest
statement of the division available:

| method | what it found |
| --- | --- |
| a static cross-check (a provider prototype with fewer parameters than the registers the function reads before writing) | 3 refusals, catching **one** of the three known defects |
| the per-function oracle (drive the function, compare the state) | 3 divergences, **all three** |

The two the static check missed depend on memory read through computed addresses,
which neither the register analysis nor the named-memory extractor can see. So:

> **static evidence can refuse what it can prove wrong; only a run can see what it
> cannot rule out.**

That is why the oracle is a gate and not a decoration, and why the two are both
kept: a static refusal is cheap and catches a whole family at once (an empty body,
an `undef()`-valued body, a lost-input signature), while a divergence can only be
seen by running the thing and watching what comes out.

2. **A Windows host or VM, once.** Record the original's side — exit code,
   stdout/stderr, and, with an IAT-patching DLL or debugger import trace, the
   same call transcript this runtime already emits, plus the same input
   transcript. The comparison tools then work unchanged, because the transcripts
   are the same text format *by construction*. **Deferred, not refused**: this is
   what would let a report say "same calls, same state" about a real game. When a
   Windows host exists this becomes a recording job, not a redesign.

3. **Emulating the original end to end under Unicorn with its DLLs.** That is a
   Windows API emulator inside a CPU emulator — a second project, whose value
   overlaps with 1 and 2 for far more cost. **Refused.**

What would change this decision: a Windows host or VM becoming available (route
2), or the corpus containing a game whose original binary is itself redistributable
and therefore runnable in the same environment (none is). Until then, `scenario-pass`
remains unreachable in practice, and every report says so in its `oracle_gaps`.

## Determinism: what "two runs agree" means here

`divergence.py` compares runs call-by-call. Getting there needed two things that
are not free, and both were measured rather than assumed:

- **A controllable clock** (`RECOMP_CLOCK=virtual`). Two runs of a frame-loop
  game part company on a `timeGetTime` poll within ~118,000 calls otherwise, and
  once they part the clock difference changes which branch the game takes, so the
  traces never come back together. Normalising the clock calls out of a trace does
  not fix it; the clock itself has to be controlled.
- **Per-guest-thread virtual time.** Even with a virtual clock, a single global
  sequence across all threads is not comparable: the audio thread's calls land at
  a different point relative to the main thread's frame loop on every run. With
  per-thread clocks the runs are identical **per thread**; the global interleaving
  still varies, and the tools say so rather than averaging it away.

The stable claim is therefore the per-thread one. "Two runs are identical" is
false as a blanket statement about a threaded game and must not be written as one.

## Known limits

- No original-Windows oracle harness exists in this repository; `scenario-pass`
  can only be claimed against a user-supplied oracle and is scoped to process
  observables. See the decision above.
- Deterministic input replay exists and is exercised on a real game: a recorded
  transcript of what the input shims returned can be fed to another run, and the
  per-thread call sequences match exactly. It covers the shims named in the
  recording's own header, and that header also names what it does **not** cover.
- No frame hashes, audio comparison or save-state checkpoints yet. Frames can be
  captured (`RECOMP_SCREENSHOT`) but are not compared.
- The score is weighted pipeline completion for the recorded hash. It is not
  a measure of game equivalence and must not be read as one.
