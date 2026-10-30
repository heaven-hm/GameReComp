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

## The function inventory is over-approximated by a third, and the odd entries are two different defects

The renaming pass needs to know what a function is before it names one, and the
first Doukutsu-scale run found that the inventory it was handed contains entries
that are not function starts. `tools/autorecomp/inventory.py` classifies every
dispatch-table entry by walking the instruction stream from the entry addresses:

| | entries | even | real start | boundary inside another function | not any boundary |
|---|---|---|---|---|---|
| corpus total | 50,942 | 38,997 | 38,997 | 6,484 | 5,461 |

23.4% of table entries are at odd addresses. **Control: every even entry IS an
instruction boundary (PASS)** — so the disassembly walk is sound and the odd
entries are genuinely not starts. x86 has no alignment requirement, so an odd
entry is not illegal; a table where a quarter are odd is not a table of functions.

The two odd classes are not the same defect, and the Lead's measurement on
Ikachan's lifted project makes the distinction:

- **phantom** (5,461 corpus-wide; 112 of Ikachan's 175 odd): mid-instruction, so
  it can never be a legitimate call target. Dead weight — build time and a
  corrupted ranking, nothing worse.
- **inside another function** (6,484 corpus-wide; the Lead measured 20 of
  Ikachan's 175 odd by walking from real starts, where this tool's even-address
  seeds also produce boundaries that are not real starts, so this count is an
  upper bound): a real instruction boundary, reachable by a `jmp` — a shared block
  or tail-merge target. Lifting it as an independent function assumes an entry
  state (esp, live registers, x87 stack) it does not have, so if the guest jumps
  there the lifted body is wrong in a way no trace localises.

The true function count for Ikachan is about 307, not 482; every count taken from
a discovery pass is inflated the same way. The corpus numbers in
`reports/rename-density.json` already exclude entries this tool refuses, and their
denominator should read "of 12,726 table entries, of which roughly a third are not
function starts".

This is an upstream defect in the discovery pass (`discover_functions`
conflates a function start with "some address worth disassembling"), reported
here rather than fixed here: changing the inventory changes every game's generated
source, and the regression baseline is recorded against the current one.

## The direction nobody was looking at: functions rev.ng has and we do not

Comparing rev.ng's Doukutsu output (955 declared functions) with our discovery
(3,260, of which 2,019 even) gives the two differences:

- **2,397 in ours, not rev.ng's** — the over-approximation already known.
- **92 in rev.ng's, not ours** — the direction that would be serious if real: a
  function the original calls that we never lifted has no body, so the call lands
  in whatever is at that address: a plausible wrong answer, not a crash.

Measured, three ways, before believing it:

1. **Classification of the 92** (the three-way walk): 59 are even and not a
   boundary of any of our walks (`start_not_lifted`, candidates), 2 are
   boundaries inside another function, 31 are not any boundary at all (phantoms
   in rev.ng's output too).
2. **Reachability**: a 15-second Doukutsu run executed 2,939,862 dispatched calls
   with **0** "no lifted function" fatals; statically, **0** of the 92 are call
   targets anywhere in our lifted code, and **1** is a jump target
   (`0x00487F13`, jumped to from `0x00487DD0`). So the risk is latent, not active
   in the frame loop, and it would need a specific code path to bite.
3. **What the provider actually produced for the 59 candidates**: 50 are EMPTY
   bodies (`void f(void) { }`), 6 abort (`revng_abort`) or read through an address
   below `0x1000` (lost pointer), and 3 have plausible C. Of those 3, the bytes
   are `00 00` padding (`add byte ptr [eax], al`), an `ljmp` into nonsense, and
   `daa` — none is a function.

**Conclusion: this is not a lifter gap.** rev.ng's discovery over-approximates in
this direction exactly as it does in the other, and its empty bodies are the
evidence — it named addresses it could not decompile. More importantly, three of
its "plausible" bodies were decompiled garbage: **plausible-looking C from a
provider is not evidence that an address is a function**, which is why the oracle
(the bytes) is the gate and the provider is an input.

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
