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
