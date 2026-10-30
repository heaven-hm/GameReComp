# The per-function oracle: the project's C against the ORIGINAL bytes

`tools/lift/difftest.py --project` compares, for a function at a given address,
the **original machine code** (run under Unicorn) against the **project's own
generated body** (compiled and run natively), from the same entry state, and
diffs the architectural result. It is the first tool in this repository that
makes a claim about the original rather than about us.

It was built against `docs/VERIFICATION.md`'s decision of 2026-10-30 (route 1:
per-function, per-input-set, no Windows host). It reuses difftest's `Case`,
`run_unicorn`'s model of the machine, its harness output format, its `diff()`
comparator and its fail-closed verdict; what is new is a *case source*, not a
second comparator.

## What it proves

For one address and one set of entry states: the original's bytes and the
project's C **agree on the eight general-purpose registers and on every byte of
the guest memory the original's own instructions name**, over the entry states
driven. That is a statement about a function and those inputs, nothing more.

## What it does not prove

- **Not the game.** There is no aggregate verdict anywhere in the tool or its
  report, deliberately: eight entry states agreeing is not a working game.
- **Not other inputs.** The input set is named in the report, per address.
- **Not DF.** Every generated body ends with `RECOMP_FLAGS_OUT()`, which
  publishes its lazy flag state into `g_flag_k/g_flag_a/g_flag_b/g_flag_cf`, and
  the harness derives real EFLAGS from that with `recomp_eflags()` — the same
  function difftest's hand-written cases use. So CF, PF, AF, ZF, SF and OF are
  compared, not assumed. The **direction flag is not among the published words**,
  so DF is the one flag excluded (`UNDEF_FOR_PROJECT` in the tool); a function
  that returned with DF set by a `std` would not be caught, and the report says
  so per address.
- **Not other addresses.** A function the project has no generated body for is
  refused (`no-generated-body`), not skipped silently.

## Method

For each address:

1. **The bytes** are read from the original PE at that VA, inside an executable
   section, or the tool refuses. The report carries the PE's SHA-256, the
   section, and the SHA-256 of the bytes read, so the comparison names exactly
   which binary and which VA it used.
2. **The memory compared** is derived from the original's own instructions
   (absolute displacement operands inside the image window, stopping at the
   function's first `ret`), not from the generated source, so it is the memory
   the function actually touches whatever the generator emitted or wherever the
   adapter lives.
3. **The entry state** is one defined state driven across cases: the PE's
   sections mapped as the loader lays them out, the registers set from a
   per-case seed, `esp` pointing at a stack page whose top word is a return
   sentinel, and each named address holding `0x9E3779B9 * (case + 1)` on **both**
   sides.
4. **The project's side** is `sub_XXXXXXXX` — the symbol the shipped dispatch
   table calls, which is the adapter into the typed function where a high-level
   replacement exists, and the mechanical body where none does. The report names
   which symbol and which source unit it compared.
5. The two results are compared with difftest's existing `diff()`; any differing
   register, named flag or byte is a divergence, and a divergence is reported per
   case.

## Measured results (2026-10-30)

Command:

```
python3 -m tools.lift.difftest --project projects/ikachan \
  --pe /Users/haseeb-mir/Downloads/recomp-apps/games/ikachan/dxIka.exe \
  --address 0x0040BA58 --address 0x00401000 --cases 8 \
  --json reports/per-function-oracle.json
```

Exit code 0. `2 address(es): 2 agree, 0 not`.

| address | function | symbol compared | named memory | cases | verdict |
|---|---|---|---|---|---|
| `0x0040BA58` | msvc_rand_next | `sub_0040BA58` (high-level adapter) | `0x00413350` | 8 | **agree** |
| `0x00401000` | reset_global_configuration | `sub_00401000` (high-level adapter) | `0x00413BE0`..`0x00413C1C` (15 words) | 8 | **agree** |

The bytes at `0x0040BA58` are `a1 50 33 41 00 69 c0 fd 43 03 00 05 c3 9e 26 00
a3 50 33 41 00 c1 f8 10 25 ff 7f 00 00 c3` — `mov eax,[0x413350]; imul eax,
0x343FD; add eax,0x269EC3; mov [0x413350],eax; sar eax,0x10; and eax,0x7FFF;
ret` — which is the LCG the hand-written module at `highlevel/ikachan/` and
rev.ng's decompilation both describe. That agreement is the point of starting
here: the answer was known in advance, and the tool reproduced it.

### Negative controls

| control | verdict |
|---|---|
| the same address but the **wrong PE** (`gunroar/gr.exe` at `0x0040BA58`) | `original-faulted` — different code, unmapped write; no agreement fabricated |
| a VA **inside** a function (`0x0040BA5C`, no generated body) | `no-generated-body` — refused, not silently skipped |

## Certifying a GENERATED module

`--source FILE --symbol NAME` compares a generated module's adapter (not the
project's own body) against the original bytes. The adapter matters: the typed
function is plain C, its result is a host value, and the guest reads `eax` — the
adapter is what marshals one into the other, and it is the symbol a dispatch
table would call, so it is the right thing to certify.

| generated module | address | symbol | cases | verdict |
|---|---|---|---|---|
| msvc_rand_step | `0x0040BA58` | `sub_0040BA58` | 8 | **agree** |
| store_block_00413be0 | `0x00401000` | `sub_00401000` | 8 | **agree** |

The exact claim each supports: *this generated function, compiled and run from
these entry states, agrees with the original bytes on the eight general-purpose
registers and on every byte of the guest memory the original names.* Nothing
about other inputs, and nothing about the game: the module is generated beside
the project and is **not part of the shipped game**. The two facts go together —
"verified against the original bytes; not part of the shipped game" — because
either alone reads as something neither is.

## The flags are NOT comparable for a plain-C replacement, and that is a finding

The flag comparison reads what the body publishes (`g_flag_k/a/b/cf` via
`RECOMP_FLAGS_OUT`) and derives EFLAGS with `recomp_eflags()`. A **lifted** body
publishes that state at every `ret`; a **plain-C** body does not, because the
original's arithmetic flags are a side effect of the instruction stream that typed
C does not reproduce. Measured: with `--flags`, the shipped high-level module and
the generated module both diverge on PF (and ZF for `0x00401000`), and they are
right to — the flags are not modelled, so anything the harness reads there is
stale. The oracle compares registers and memory by default and says why; `--flags`
exists for lifted bodies, which are the only ones that can honour it.

This also corrects an earlier claim of mine: the first version of the oracle
hard-coded the real side's EFLAGS to 0, so the flag comparison was vacuous — it
only ever passed because the named bits happened to be clear on both sides. With
the real `EFLAGS` register read, the difference is visible and the honest scope is
"registers and memory", not "flags included".

## The function inventory: a retracted claim, and what parity actually means

**Retracted (2026-10-30, by the Lead): "the inventory is over-approximated by a
third" and every "phantom" count derived from it were wrong.** The classifier
seeded its instruction walk from **even addresses** and stopped each walk at the
first `ret`, so a real function beginning at an odd offset immediately after
another function's terminating `ret` was structurally invisible to it - and was
then labelled "mid-instruction phantom" for the sole reason that the classifier
could not see it. Disassembling the bytes settled it: Doukutsu's five called
odd-address functions all begin with coherent code (`mov eax,[edx+4] / and ... /
ret`, `cmp ecx,[0x498b20] / jne / ret`, `push ebx / push esi / call ...`), and
**Doukutsu's own PE entry point is at an odd address (0x00481D27)**.

So the odd-address percentage is a **packing fact, not a phantom count**: this
linker does not pad function starts, so address parity carries no information
about whether an address is a function. Corrected classification, seeded from the
PE entry point and real call targets (`tools/autorecomp/inventory.py`):

| | entries | odd | entered from the entry point | not entered |
|---|---|---|---|---|
| corpus total | 50,942 | 11,945 (23.4%) | 3,407 (920 odd, 2,487 even) | 47,535 |

"Not entered" is not "phantom": a function reached only through an indirect call
is invisible to a direct-call walk. The number is reported as what it is.

The general lesson is worth keeping: **a classifier that quietly excludes its
subject produces a confident, precise, wrong answer.** This one did it three
times today - once in the Lead's tool, once in the named-memory window, once in
the register scope - and each time the only thing that caught it was a control or
a cross-check, never reading the code.

## The oracle's scope limit: a synthetic entry state cannot certify arguments

The oracle on a called odd-address function returns `original-faulted`, not
`agree`: `0x00485FF5` opens with `mov eax,[edx+4]`, so a synthetic entry state
with a garbage `edx` makes the **original** fault before our body is reached. The
verdict says "I could not drive this function from a guessed state", which is not
"our body is wrong". **Functions that dereference their arguments cannot be
certified by synthetic entry states at all** - the fix, when it is needed, is to
capture the real entry state from a live run rather than invent one. Recorded
beside the register-scope limitation, with the same status: a known limit of the
method, not a finding about the code.

## The honest chain: 228 accepted, 228 generated, 106 compile, and almost none certifiable

Every stage is a strict subset of the one before it, and collapsing them is how a
pipeline gets credit it has not earned:

| stage | count | what it means |
|---|---|---|
| parser accepts | 228 of 955 | the AST model can read the provider's body |
| generates C | 228 | the module source is produced |
| compiles | 106 | the C builds against the runtime headers |
| certifies | 0 of 3 sampled | the oracle does not relate the body to the original bytes |

Two scope refusals moved the first number down from 524, correctly: a function
returning a provider struct **by value** (184) uses a hidden-pointer return the
module model cannot express, and a function **calling another lifted function**
(56+) needs that function's guest-ABI entry, which is not marshalled. Both are
honest refusals that would otherwise have become compile errors.

The compile failures that remain are provider helpers (`helper_f*_wrapper`, 101)
and provider types in local declarations - the unsaved headers again.

**And the fourth number is the important one: of three hot, compiling, called
functions sampled (0x00480DC1 at 7,180 calls, 0x00483734 at 3,785, 0x00472FF0 at
372), none certified.** Two returned `original-faulted` because the oracle's
synthetic entry state cannot drive a function that dereferences its arguments
(the scope limit recorded above), and one hit a harness gap (an import call not
resolved to the runtime's shim, now fixed). The effective set is therefore *much
smaller* than 228, and no plan should be made on the chain's first number.

## The generator's next gate, measured: pycparser's C coverage

Of Doukutsu's 955 provider functions the AST model accepts **120**. The 835
refusals cluster into a handful of C forms, not a fundamental gap:

| refusals | parse error |
|---|---|
| 390 | `before: *` |
| 244 | `Invalid function definition` |
| 109 | `before: stack` |
| 55 | `Invalid expression` |
| 10 | `before: argument_0` |
| 7 | `Invalid declaration` |
| ~20 | recovered-pointer / width refusals |

Four or five C forms account for ~815 of them. That is the gate between the
transform and the hot functions: the census shows the frame loop's core
(`0x00481BB0`, 6.6 million entries in a 300,000-dispatch run) is refused by the
*parser*, not by the pointer model.

## The ruling: what a plain-C replacement can be verified for

**`HIGH_LEVEL_VERIFIED` means registers and memory for a plain-C replacement.**
Flags are out of scope, with the reason recorded rather than assumed: typed C has
no flag model at all — `state = state * 0x343FD + 0x269EC3` sets no CF, and nothing
in a generated module could, short of hand-writing the flag effects of every
operation, which is exactly the machine-level detail typed C exists to remove. A
gate that required flags would forbid every typed replacement, so the scope is
registers and memory, and `--flags` exists for lifted bodies, the only ones that
publish a flag state (`RECOMP_FLAGS_OUT`).

**A known behavioural limitation, recorded beside the ruling rather than left
implicit:** because typed C does not model guest flags, a caller that read flags
immediately after a call would see stale values where the original saw the
callee's. Compiled code does not normally rely on this — flags are call-clobbered
and compilers do not preserve them across calls — but that has not been measured
here, and "probably never happens" is not "cannot happen". The scope above is a
verification scope, not a claim that the behaviour is identical.

**And the fact that produced all of this, which is a result in its own right:** the
first version of this oracle hard-coded the real machine's EFLAGS to 0, so the
"flags included" comparison passed only because the named flag bits happened to be
clear on both sides. Reading the real `EFLAGS` register made the difference
visible immediately — every plain-C replacement diverged. **A comparison whose one
side is a constant is not a comparison**, and it produced a confident pass. It was
found by making the tool stricter, not by trusting it.

## Two bugs found in the tool before it agreed

The first runs disagreed, and per the task the tool was the suspect — correctly:

1. The harness seeded the named addresses with a value computed in **Python**
   from the case *count* rather than from the case index, so every case was the
   same case and every comparison was a divergence. The seed is now emitted as C
   over the loop counter.
2. The named-memory disassembly ran past the function's `ret` into the next
   function, collecting 22 globals for a function that touches one. It stops at
   the first `ret` now.

Both would have produced confident, wrong divergence reports. Neither was
"fixed" by adjusting the expectation: the expectation was the hand-written
module, and it was the tool that was wrong.
