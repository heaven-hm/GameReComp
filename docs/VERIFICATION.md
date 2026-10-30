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

## The first certified CALL, and how to reproduce it

A call-bearing module is certified only when its closure is **linked** and its named
memory **unioned** — otherwise the oracle drives the caller and the callee's effect
happens inside the call, invisible. The first module to pass that bar is
`0x0041F9E0`, over the closure `{0x0041F9E0, 0x00419890}`:

    verdict:  agree
    closure:  {functions: 2, named_words: 2, frames_summed: 0}
    entry states: 8, differing: 0

The generated module, the verdict and the exact command are committed together, so
the claim can be re-run rather than believed:

    reports/certified/0x0041F9E0/0x0041F9E0.c     the caller
    reports/certified/0x0041F9E0/0x00419890.c     the callee (its call is real)
    reports/certified/0x0041F9E0/verdict.json     the agree verdict with the closure record

    python3 -m tools.lift.difftest --project projects/cavestory-doukutsu \
        --pe <Doukutsu.exe> --address 0x0041F9E0 \
        --source reports/certified/0x0041F9E0/0x0041F9E0.c,reports/certified/0x0041F9E0/0x00419890.c \
        --symbol sub_0041F9E0 --closure 0x0041F9E0,0x00419890 --cases 8 \
        --json reports/certified/0x0041F9E0/verdict.json

**The standing count, in the form that does not overstate it:** 446 call-bearing
bodies -> 28 multi-function closures generating -> **1 certified**. One, not 28. The
number that moved is the one the closure work existed to move, and it moved once.

## A pass at the oracle is evidence, not proof — with a measured example

The per-function oracle compares **values**: the registers a body leaves and the
bytes of the guest memory it names. It does not compare the **provenance** of those
values — whether a memory operand is a guest address at all.

Two generated modules in this repository demonstrate the gap. `0x004258B0` and
`0x004258C0` were retracted because their bodies emit `MEM8(var_0)` with `var_0` a
host `uint8_t`, reading guest memory at a host local's *value*. **Run the oracle on
either today and it returns `agree-return`**: the values coincide on the inputs
driven, so the verdict passes — and would keep passing.

The modules were removed from the count by a **rule** (no `MEMn` whose operand does
not provably denote a guest VA), not by the oracle. This is the case that separates
the two instruments:

- a **divergence** is the oracle finding something the rules cannot see;
- this is the reverse — a **rule** refusing something the oracle cannot see.

So a certification is necessary and not sufficient. `reports/retracted/` holds both
modules and both `agree-return` verdicts, so the claim can be checked rather than
believed.

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

## What the oracle cannot see yet: the guest stack frame

`named_addrs_from_code` collects the absolute addresses the original's own
instructions name — operands with no base and no index register. That is exactly
right for globals and exactly wrong for the stack: `[ebp-4]` and `[esp+8]` have a
base register, so a function whose only memory traffic is its own frame has **zero**
named addresses.

Measured on two frame-only functions:

    Doukutsu 0x004258B0:  0 absolute addresses named;  2 frame accesses not compared
    Doukutsu 0x004258C0:  0 absolute addresses named;  4 frame accesses not compared

The consequence is specific and dangerous: such a function certifies as `agree`
having compared only its return value, while every frame read and write — the work
it exists to do — goes unchecked. It reports what a fully-checked function reports.
This is the same shape as the constants-EFLAGS comparison, the 256 KB window and
the empty named-memory set: **a comparison that looks stronger than it is.**

Until it is fixed, a `HIGH_LEVEL_VERIFIED` verdict on a function with frame traffic
means "the return value agrees", not "the function agrees". The fix is to compare
the frame region itself — the reference machine already has the stack, and the frame
is that stack — and to report the frame's extent and the bytes compared, so a reader
can tell a `agree` that checked the frame from one that did not.

## A certification can be wrong while the oracle agrees

This is the strongest caution in this document and it has a measured example.

Two modules — `0x004258B0` and `0x004258C0` — emit `MEM8((var_0 & 0xFFFFFFFFULL))`
where `var_0` is a **host byte**. That reads guest memory at a host local's value,
and it is the inversion the pointer model exists to prevent. Both were retracted.

**Run the oracle on them today and it says `agree-return`.** It will keep saying so.
The oracle compares **values**, and the wrong read of the wrong address happens to
produce the number the original produces, so the values agree. What the oracle
cannot see is the **provenance** of the operand — whether the address it read was a
guest VA or a host local — and that is exactly the property that was wrong.

So the retraction was made by a **rule**, not by the oracle. The rule — *no `MEMn`
may be emitted whose operand does not provably denote a guest VA* — is the only
reason those two are not in the certified count, and no amount of oracle running
would ever have removed them.

The general statement, which is why this section exists:

> **A pass at the oracle is evidence, not proof.** It attests that the value which
> came out matched, on the inputs driven, for an instrument that can see outputs and
> cannot see how they were produced. A transform that produces the right answer for
> the wrong reason passes it forever.

This is a different failure from the four vacuous comparisons this project has
already corrected (a constant EFLAGS, a clipped 256 KB window, an empty
named-memory set, an uncompared stack frame). In those the harness was wrong. Here
the harness is right about what it measures and **the thing it measures is not
enough** — which is why rules that demand proof of an operand are not redundant
with running the function.

## What a call hides from a certification

A certification drives one function and compares what that function's own bytes
name: its return value, its absolute-memory operands, and — with `--frame-size` —
its frame. **A call's effect is inside the call.** The callee's named addresses are
not in the caller's comparison, and when the module under test is generated the
callee is not even linked into the harness. So:

> **A module that calls another function cannot certify the call it makes.** The
> verdict would be `agree` about a function whose most consequential behaviour was
> never executed.

This is the same shape as the four vacuous comparisons and the retracted pair — a
verdict implying a wider check than the instrument performed — and it is the reason
marshal-using modules are `HIGH_LEVEL_UNVERIFIED` rather than certified, however
many of them generate and compile.

The fix is to widen the instrument, not to narrow the claim:

1. **link the transitive callee closure** into the harness, so the call executes;
2. **union the named-memory set** over that closure, so the callee's writes are
   compared;
3. **seed every frame in the closure**, not just the caller's;
4. **state the closure size in the verdict** — "compared 3 functions, 14 named
   words, 2 frames" is a claim a reader can weigh, where a bare `agree` is not.

Cap the closure and refuse beyond the cap with the size in the reason: an
unbounded closure would make a divergence hard to localise as well as slow to run.

## Provider dependencies we do not have

Separate from the marshal, and measured rather than assumed: some provider bodies
call LLVM intrinsics whose semantics are declared in `runtime-library.h` but not
implemented here — 184 `bit_cast`, 59 `bit_parity`, 50 `lshift`, 31
`llvm_umin_i32`, 18 `llvm_fshl_i32`. A body depending on one of those cannot be
correct until the semantics exist; unlike the marshal there is no workaround,
because an unimplemented intrinsic is a function that compiles and computes
something else.

Together with the 24 bodies carrying 2,331 `undef()` values, this describes how much
of this binary's decompilation rests on things the provider could not supply or we
have not implemented. Those counts belong beside the certified count, not behind it.

## The one rule that produced every good decision here

When a verdict and an instrument disagree about how much was checked, there are two
repairs: **widen what the instrument can see, or weaken what the verdict claims.**
Every time this project chose the first, the number went down and became trustworthy.
Every time it chose the second, the number went up and was wrong.

| chose to widen the instrument | chose to weaken the claim |
| --- | --- |
| compare the frame (extent, base, bytes) | a 256 KB window that clipped every real address |
| prove purity by classifying every operand | per-register seeds that made a coherence test impossible |
| link the transitive callee closure | a fixed seed pattern per register, so two machines entered in different states |
| report the closure size in the verdict | a `compares` field claiming eight registers when one was compared |

The right-hand column is not laziness — each was a reasonable local decision that
made a result appear. That is exactly what makes it dangerous: a verdict that is
narrower than it claims looks identical to one that is honest, and only the count
reveals it, later, when it is retracted.

So the question to ask of any verification change is not "does this pass" but **"what
can this instrument see, and does the verdict say exactly that"**. If a claim is
wider than the instrument, the instrument has to grow. If the instrument cannot grow,
the claim shrinks and says why in the record.

Two corollaries learned the hard way here:

- **A comparison whose subject count is zero is a failure, not a pass.** The
  `nothing-compared` verdict exists because a check that compared nothing once
  reported `agree`.
- **An unimplemented intrinsic is worse than an `undef()` value.** `undef()` is
  marked as unknown and gets refused; an intrinsic looks computable and is silently
  something else. 75 bodies here depend on 353 such calls.

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
