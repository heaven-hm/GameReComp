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
