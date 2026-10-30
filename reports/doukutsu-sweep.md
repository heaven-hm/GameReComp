# The 36-module sweep, re-run after the oracle's seed fix

Population: Doukutsu's 955 provider functions -> **157 accepted -> 157 generated ->
36 compile**. 35 modules completed (one duplicate address in the list). The first run
was made before the Lead's fix that gave both reference machines the same coherent
seed; these are the regenerated numbers.

| verdict | count | meaning |
|---|---|---|
| `agree` | 7 | eax and the named guest memory both match on 8 entry states |
| `agree-memory` | 8 | the named memory matches; eax is not comparable because the provider recovered the body as returning nothing |
| `nothing-compared` | 15 | no named memory, so the comparison has no subject - never a pass |
| `original-faulted` | 5 | the reference machine could not drive the function from a seeded state (one is a 15-statement body, so this is real state dependence) |
| `diverge` | 0 | **none** - the split converted the eight eax-only differences into the honest partial verdict above |

## The 7 full certifications

| address | named memory | provider statements |
|---|---|---|
| 0x00402020 | 1 | 4 |
| 0x0040B320 | 2 | 3 |
| 0x0040F310 | 1 | 2 |
| 0x0040F320 | 1 | 2 |
| 0x0041C770 | 2 | 5 |
| 0x00472940 | 1 | 2 |
| 0x0047B500 | 5 | 2 |

## The 8 memory-only agreements

Every one is an eax-only difference on a provider-`void` body. The provider's type
inference is an **input, not a verdict**: a function rev.ng recovered as returning
nothing is not evidence that it returns nothing, and the original leaves its last
computed value in eax. The correct response is to report memory agreement separately,
not to make the adapter guess - which is what it did before this split (0x0040DE90
returned 0 where the original returned 1).

## Two lessons recorded with the numbers

**Two implementations of one idea is the defect; the divergence is only the symptom.**
Three instances today: the 256 KB memory window fixed in `oracle_window` but not
`pe_image` (which produced a fault that read as a method limit); the coherent seed
fixed on the Unicorn side but not the lifted side (which produced register
divergences that read as lift bugs); the function extent fixed from the dispatch
table but not from the first `ret` (which produced wrong bodies that read as
transform limits). Each was found by checking a sample by hand.

**Verify a sample of any generated table before it goes in a report.** The Lead
checked one row of the previous table, disassembled it, and found a bug in the
oracle's comparison rather than in the modules. The row was cheap and the table
would otherwise have been wrong in a way that looked like a result.

## `diverge 0` came from a more precise verdict, not a weaker test

The first run of this table reported **8 divergences**. Every one was an eax-only
difference on a provider-`void` body, with the named memory agreeing byte for byte,
and the adapter inventing 0 where the original left its last computed value. The
`agree-memory` verdict records exactly that: memory matches, eax is not comparable
because the provider recovered the function as returning nothing. So the divergence
count went to zero **by making the verdict more precise, not by making the test
weaker** - the eight still carry the fact that eax could not be compared, and a
future reader can see that from the table rather than from this paragraph.

This is the third time in the session that a "failure bucket" was a reporting error
with a real finding underneath: the odd-address "phantoms" were unpacked function
starts, the "0 of 3" certification failure was a clipped memory window, and these
eight "divergences" are a gap in the provider's type inference. In each case the
finding was real and the label was wrong.

## A confident, precise, false negative: how "reached" was nearly reported as "not reached"

Measured before it was claimed, and the first measurement was wrong. A build that
counted entries to the seven dispatched functions through `recomp_trace_enter` -
the lifted-function entry hook - reported **zero for all seven**. The adapters that
the dispatch table points at are not reached through that hook, so the correct
answer was invisible to the instrument. A print inside each of the seven adapters,
in a throwaway build, showed every one firing exactly once.

Had the first number been reported it would have read: "the guest does not reach
the seven" - confident, precise, and false, in the same shape as the phantom
functions, the constant EFLAGS and the 256 KB window. Four instances now, and every
one was caught by looking at the thing being claimed about rather than at a proxy
for it:

> instrument the thing you are claiming about, not the path you expect it to travel

The seven are reached on startup paths, not in the frame loop, so the honest
sentence is: seven generated replacements, certified against the original bytes,
dispatched, and called by a game that runs - each once, on a startup path, with the
frame loop's counts unchanged beside them.

## The `nothing-compared` bucket was 15 certifiable functions in disguise

The guard refused them for having no named guest memory. All 15 write no guest data
and call nothing - they are pure functions, and a pure function's only observable
**is its return value**, so "no named memory" was being read as "no subject". That
is the same category error as the constant EFLAGS, in the opposite direction: the
guard mistaking **narrow** for **vacuous**.

So the verdict is `agree-return`: for a function the original proves pure (no guest
data writes, no calls), the comparison drives eax and says the return value is the
whole contract. The guard still refuses the case it was built for - a function that
writes memory the extractor failed to name - because there the subject really is
missing.

    of the 15:  12 agree-return   3 diverge   0 nothing-compared

**And the three divergences are a new class worth having.** All three are pure
functions whose provider body is a **constant** where the original computes from
its input registers:

    0x004882D9  provider: returns 0x411      original: 0x0      (10 instructions)
    0x00489227  provider: returns 0x300      original: 0x320    (49 instructions)
    0x00489195  provider: returns 0x20000    original: 0x20010  (55 instructions)

The provider's signature for them says `void` (no arguments), so the body could not
use the inputs it actually has - a `__fastcall`/thiscall-style entry recovered as a
no-argument function. This is the same family as the empty bodies: **a provider body
that lost its inputs**. The oracle is the only thing that could have said so, because
the body compiles, looks plausible, and is wrong.

**Certified count, corrected: 7 + 12 = 19** (7 on registers and named memory, 12 on
the return value alone), with 3 real divergences found on the way. The README and
the earlier report understated the pipeline; an understated number is still a wrong
number.

One false positive found fixing this: `has_memory_observable` counted `mov [ebp+8],
al` as a guest-data write, but that is an argument slot in the stack frame. The
stack is not a guest-data observable, and 0x004258C0 was refused because of it.

## The frame loop's core: the x87 helpers make it compile, not correct

`0x00481BB0` is the hottest function in the game — 6.6 million lifted entries in a
300,000-dispatch run — and it is **refused**, because its provider body contains
**180 `undef()` values**. `primitive-types.h` defines `undef(T)` as a read from a
special location: it is rev.ng saying, 180 times, that it could not determine a
value. With `helpers.h` the x87 `helper_f*` wrappers finally have somewhere to go
and the body would **compile**; it would still be built on 180 values the
decompiler does not know.

**This is the architecture working, and it is worth stating plainly.** The provider
failed on the one function that matters most; the low-level lifted body carries it;
the product still runs because the fallback is there. A pipeline that could only
report the provider's successes would have called this progress. This one reports
the provider's failure, refuses the module, and keeps running — and the number that
says so is a refusal message with the count in it, not an absence of warnings.

## Static evidence and the oracle are not redundant

The signature cross-check (a provider prototype with fewer parameters than the
registers the function reads before writing) refuses **3** of the 157 accepted
functions, and catches **one** of the three divergences the oracle found — the one
that reads `ebx`. The other two depend on memory read through computed addresses,
which neither the register analysis nor the named-memory extractor can see.

So: **static evidence can refuse what it can prove wrong; only a run can see what
it cannot rule out.** The static refusals are cheap and catch whole families at
once (empty body, `undef()`-valued body, lost-input signature); the divergences
need the run. `docs/VERIFICATION.md` records the decision and this measurement
beside it.

## The pre-struct-field baseline (measured before the struct-field pass, so its effect can be attributed)

34 modules: 157 parser-accepted -> 154 generated -> 34 compiling (the two new
refusals took two modules out of the population).

| verdict | count |
| --- | --- |
| `agree` | 7 |
| `agree-return` | 12 |
| `agree-memory` | 8 |
| `diverge` | 2 |
| `original-faulted` | 4 |

**19 certified** (7 on registers and named memory, 12 on the return value alone).

**The two divergences are exactly the two the static check cannot see**:
`0x004882D9` and `0x00489195`, both depending on memory read through computed
addresses. `0x00489227` — the one that reads `ebx` — is now a **refusal**, so it
moved from the divergence bucket to the refusal count as predicted. The
`agree-return` twelve are unchanged, and the population dropped by two modules, not
by a handful: the new refusals are acting on the functions they were built for.

One row moved from `original-faulted` to `agree` between runs (`0x0048708A`). That
is a seeded-state difference rather than a fix, and it is the reason the table is
labelled by run rather than treated as stable.

## RETRACTION (2026-10-30, after the hash check): two of the nineteen certifications were coincidental

**The 19 was 17.** A generated module may not contain a `MEMn` whose operand is a
host local. Scanning all 177 generated modules found **one** such module
(`0x004075E0`, not in the swept set), but the rule that came out of it - **no `MEMn`
whose operand does not provably denote a guest VA** - was then applied to the
*accepted* set, and it refused two functions that had **certified**:

    0x004258B0   MEM8((var_0 & 0xFFFFFFFFULL))       var_0 is a host uint8_t
    0x004258C0   MEM8((var_0 & 0xFFFFFFFFULL)) = ... var_0 is a host uint8_t

Both read guest memory at the *host local's value*. Their `agree-return` verdict was
**coincidental**: the harness seeds registers and named memory from one value per
case, so `MEM8(1)` and the original's table read at index 1 happened to agree on the
inputs driven. The verdict was true for those inputs and the *module* is wrong.

**Retracted: the certified count is 17, not 19** (7 `agree` + 10 `agree-return`).
**Neither affected module was dispatched** - the shipped `highlevel/doukutsu-sweep/`
carries only the seven `agree` functions, which the scan found clean - so the game
never ran them. Exposure: zero. The certification table was not.

This is what the measure-before-fixing rule bought: the defect was reachable from a
count in twenty minutes, and the alternative was publishing 19 with two of them
wrong.

## A certification can be wrong while the oracle agrees, and this is the measured example

The two modules retracted above (`0x004258B0`, `0x004258C0`) **still certify**. Run
the oracle on them today and it returns `agree-return`, with the same eight entry
states and the same named-memory behaviour as any clean certification. The saved
verdicts are in `reports/retracted/` beside the reconstructed module sources, so the
claim is checkable rather than asserted.

**The oracle is right about what it measures, and what it measures is not enough.**
It compares values: `MEM8(var_0)` with `var_0` a host byte happens to read the dword
the original's table reads, so the values agree and the verdict passes. It cannot see
that the operand is not a guest VA, because the operand's *provenance* is not part
of the state it compares.

So the two are not in the 18 because **a rule refused them, not because the oracle
caught them** — the oracle would have certified both, permanently. That is the
strongest argument for the rule-based refusals this project has built, and it is a
different failure from the four vacuous comparisons found today: in those, the
oracle was needed and the *harness* was wrong. Here the oracle is right and
insufficient.

> A pass at the oracle is evidence, not proof. It is necessary and not sufficient:
> it compares what came out, and a wrong read of a wrong address produces the right
> number often enough to pass.

The host-VA rule exists because the oracle cannot see the difference, and it is the
only reason those two are not in the count.

## The audit trail: what is preserved and what is not

The retracted pair is preserved (`reports/retracted/`: both reconstructed modules
and both `agree-return` verdicts). The **sweep's per-address verdicts are not** - they
lived in a temp directory and are gone. That is exactly the gap the auditor found:
a number whose per-address instrument output was never committed is a number someone
has to reconstruct rather than check.

The rule for every future sweep, recorded here so it is not rediscovered: **commit
the raw verdict JSON beside the table**, with the instrument's own record of what
each address compared. The table is a summary of the JSON, not a replacement for it.

## Where the frame work stands (state at the end of this session)

- **Done and verified:** the oracle's frame comparison (`--frame-size`, verdict
  `agree-frame`, extent and base in the record, "frame traffic was NOT compared" when
  absent); the guest-stack frame model in the AST (`&stack + off` -> `MEMn(frame +
  off)`, struct `_SIZE(N)` captured for all 1,877 types, AST acceptance 50 -> 176);
  the purity rule demanding proof; the selftest flake fixed and concurrency-safe.
- **Not done:** the frame path does not compile its samples. Three failures were
  diagnosed and one fixed: a leaked `ptr:` descriptor (fixed), `segment_2` leaking
  inside a cast (partially fixed), and a struct-field access surviving when the
  base's struct name cannot be recovered (open) - plus a call with the wrong argument
  count, which is the frame interface: a callee whose signature gained a `frame`
  parameter makes every caller in the module owe it one.
- **The next piece, as one unit:** the local/guest distinction and the frame-interface
  argument are the same problem - a value crossing a boundary written in the source's
  vocabulary instead of the destination's. The rule to build is structural: every
  value crossing the boundary must be a guest VA, a plain scalar, or a refusal.

## The provider's honest failure rate on this binary

Beyond `0x00481BB0`'s 180: **24 of the 955 provider bodies are refused for
`undef()` values, carrying 2,331 of them in total.** Every one is the decompiler
marking a value it could not determine. That is the provider's own measurement of
what it could not recover on this binary, and it is the number to quote rather than
the one function that happened to be the frame loop's core.

## The marshal's territory, measured before it is built

A call is a value crossing in both directions, so the marshal is not "add an
argument" - it is the conversion of a C call at an AST boundary into a guest-ABI
call. The population it has to cover, measured:

| class | bodies |
|---|---|
| calls another lifted function | **497** |
| no calls (leaf) | 101 |
| refused for `undef()` before any of this applies | 24 |
| an indirect call through a pointer (heuristic, see below) | 333 |

**1,524 call sites, of which 796 target a callee that has a stack frame.** So roughly
half the call sites carry a frame argument today, and a marshal written for the
other half would be wrong at 796 places - which is why the caller must be checked
against a signature declared at the AST boundary rather than at link time.

Two honest limits on the numbers above:
- the **333 indirect** figure is a regex over `*(...) (...)` shapes and therefore
  matches casts as well as calls. It is an upper bound, not a measurement, and it is
  recorded as one. The Lead asked for the refusal class to be measured rather than
  left implicit; measuring it properly needs the AST, not a pattern.
- the 497/101 split is over *provider bodies*, not compiled modules, so it is the
  marshal's whole territory and not the part currently reachable.

## The 333 was mostly casts, and the oracle cannot see a call

**The measured call forms** (the real answer to the regex's upper bound): in the 955
provider bodies there are **1,524 `function_0x(...)` lifted calls**, plus provider
intrinsics - 184 `bit_cast`, 59 `bit_parity`, 50 `lshift`, 31 `llvm_umin_i32`,
18 `llvm_fshl_i32` - 165 `revng_abort`/`raise`, and 152 x87 `helper_f*` helpers. The
"indirect through a pointer" figure was almost entirely casts, as suspected: it is
1,524 lifted calls and about 340 intrinsic-helper calls, and the intrinsics are
declared in `runtime-library.h` but their *semantics* (LLVM intrinsics) have no
implementation here - a separate gap from the marshal.

**And the coverage risk the Lead named is real and structural:** a certification of a
**calling** function drives the caller and compares the caller's outputs, its name-set
memory and (with `--frame-size`) its frame. The callee's effect is *inside* the call:
the callee's own named addresses are not in the caller's comparison, and with a
generated module the callee is not even linked. So:

> a marshal-using module cannot certify the call it makes. The verdict would describe
> a check the instrument cannot perform, which is the rule we have applied to every
> verdict today.

The honest consequence for the plan: the marshal can un-refuse 497 bodies and make
them generate and compile, and **their calls remain unverified** until either the
callee's effect is brought into the comparison (e.g. compare the callee's own
named-memory set after driving the caller) or the module embedding the callee is
exercised as a whole. That is the next design question after the marshal, and it
should be answered before any marshal-using module is certified.

## The intrinsics are a provider-dependency refusal class, sized

The provider's LLVM intrinsic helpers are **declared** in `runtime-library.h` and
**not implemented** here, so a body using one would compile and compute something
else. They are now refused with the intrinsic named:

    353 intrinsic calls across 75 bodies (excluding the undef-refused):
      bit_cast 184 | bit_parity 55 | lshift 50 | llvm_umin_i32 31
      llvm_fshl_i32 17 | llvm_smin_i32 8 | llvm_smin_i16 4 | llvm_smax_i32 3
      llvm_umax_i32 1

That is the same shape of number as the `undef()` rate (24 bodies, 2,331 undefined
values): together they describe how much of this binary's decompilation depends on
things this project does not have. An unimplemented intrinsic is worse than an
`undef()` value in one respect - `undef()` is marked as unknown, while an intrinsic
looks computable and is silently something else.

## The closure cap is justified, and the cap is not the constraint

The closure-linking verification needs a size cap so a recursive call graph cannot
blow up the harness or make a divergence unlocalisable. Measured over the first 400
provider bodies: **median closure 1 function, maximum 109** (0x00401D10 reaches 109,
0x00422510 reaches 105). No closure reached a 256 cap. So the cap is a guard rather
than a limit the real call graphs run into, and the report can say a verified caller
typically linked one function and at most 109.

## The ordering measurement: min over the closure, not class size

A closure is only as usable as its **worst member**, so the metric that decides the
next piece is *closures-unblocked-per-fix*, not how many bodies a refusal class
contains. Measured, and re-runnable:

    python3 -m tools.autorecomp.rename_gen --pe <original> --revng <dir> \
        --revng-c highlevel/doukutsu/doukutsu-revng.c \
        --header highlevel/doukutsu/types-and-globals.h \
        --closure-blockers --json reports/closure-blockers.json

Over the 95 call-bearing bodies that generate alone:

| closures blocked | reason |
|---|---|
| 14 | `Invalid expression` |
| 12 | a dereference with no recoverable width |
| 10 | an unimplemented intrinsic |
| 9 | an empty body |
| 9 | an unbounded offset on a VA |

**Sole-blocker members** (fix one function, N closures become multi-function):
`0x00420640` 10 · `0x004874A7` 5 · `0x00480DC1` 4 · `0x004139A0` 4.

**And `0x00480DC1` must not be chased**: its four closures are blocked by an empty
provider body — the security-cookie check rev.ng could not decompile — so they are
blocked by the provider, not by us. That single line is worth more than the ten we
can fix, because it stops someone spending a day on it.

**The negative result this measurement produced, kept as an asset:** the **124
struct-returns were the largest class and fixing them moved multi-function closures
from zero to zero**. Size-based prioritisation was refuted by measurement. It sits
beside the other two negative results in this report — the 92 functions rev.ng
over-approximated, and the 0-of-3 certification failure that was a clipped window —
as facts that stop the next person repeating the work.

## Two semantic results from the same round

- **`va << 2` is a scaled VA; `va >> n` is a number.** An address times a stride is
  still an address; an address shifted right is not. This was the top blocker at 30
  occurrences, and the boundary rule now encodes what a *scaled array access* means
  rather than treating every shift as mixed-kind arithmetic. Oversized or
  non-constant shifts are still refused.
- **All 1,877 provider struct sizes are multiples of four**, so the hidden-pointer
  word copy is sound — and a hypothetical 6-byte struct would now be **refused**
  rather than copied past its end. The alignment precondition is a check in the code
  rather than an assumption inherited from the data.

## The `StructRef` class is blocked by the provider, not by us

After the offset fix, the top refusal class is "a `StructRef` is neither a VA nor a
scalar" (16 closures). It looks like the next rule to refine. It is not.

**The provider did not recover field layouts.** Measured on
`highlevel/doukutsu/types-and-globals.h`:

    structs with a declared _SIZE            2,081
    field declarations that are padding      3,117
    field declarations with a real type          0
    `pointer_or_number32_t` occurrences          1

So every recovered struct is an **opaque blob of known size with padding where the
fields are**. A `->field` access therefore cannot be classified as a VA or a scalar,
because nothing in the provider's output says what is at that offset. `classify()`
has no `StructRef` branch and refuses; **that is correct given what it knows**, and
adding a branch would mean guessing whether an offset holds a pointer.

This is the same shape as `0x00480DC1`'s empty body: a refusal that looks like our
gap and is the provider's. It should be marked *do not chase* in the same way, so the
next person does not spend a day refining a rule that has no information to refine.

What would change it: a provider pass that records field types, or a Ghidra-supplied
structure database. Neither is our transform, and both are larger than anything left
in the current queue.

