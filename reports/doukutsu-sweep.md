# The 36-module sweep: what the oracle says about generated Doukutsu modules

The honest population is **157 accepted -> 157 generated -> 36 compile** of Doukutsu's
955 provider functions; 35 modules completed the sweep (one duplicate address in the
list). Each was generated from evidence, compiled, and compared against the original
bytes on 8 entry states. Statement count is the provider body's, so a divergence on a
1-2 statement body is visibly suspicious in the table itself.

| verdict | count | meaning |
|---|---|---|
| `agree` | 7 | the first certifications of non-empty bodies beyond the two Ikachan ones |
| `diverge` | 8 | real differences - all one class, see below |
| `nothing-compared` | 15 | the vacuous-comparison guard: no named memory, so not a pass |
| `original-faulted` | 5 | the reference machine could not drive it from the synthetic state |

## The divergence class: provider `void` bodies lose the return value

**All eight divergences are eax-only, and all eight provider bodies were recovered as
`void`.** The memory effects agree byte for byte; what differs is the value the
original leaves in eax, which our adapter invents as 0:

    0x0040DE90  provider: *(u32*)(&segment_2+60212) = 1U;      original eax = 1
    0x0040F2D0  provider: stores 3 values, last one computed    original eax = the last value

That is a **provider signature gap**, not a transform bug: rev.ng recovered the function
as returning nothing, the original returns the last computed value, and the adapter has
no way to know. The oracle caught it - which is what it is for - and the correct
response is to treat a `void` body as uncertifiable on eax rather than to make the
adapter guess.

## The certifications

| address | named memory | statements |
|---|---|---|
| 0x00402020 | 1 | 4 |
| 0x0040B320 | 2 | 3 |
| 0x0040F310 | 1 | 2 |
| 0x0040F320 | 1 | 2 |
| 0x0041C770 | 2 | 5 |
| 0x00472940 | 1 | 2 |
| 0x0047B500 | 5 | 2 |

These are real bodies (2-5 provider statements) with guest memory named by the
original's own instructions, and they agree on 8 entry states. They are the first
certifications on the frame loop's side of the program, and the naming pass should
run on them rather than on the two Ikachan functions.

## The other buckets

- **nothing-compared (15)**: the guard from the previous commit working as designed. A
  comparison with no named memory compares nothing, and it is not reported as a pass.
- **original-faulted (5)**: the synthetic entry state could not drive the original
  (one is a 15-statement body, so this is a genuine state dependence, not an empty one).
  This is the class entry-state capture would address - and it is now 5 of 35, not the
  whole failing set. The case for capture is measured, not assumed.
