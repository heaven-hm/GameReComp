# GR6 — x86 instruction semantics: provenance and evidence

## Scope

Audit of `tools/lift/lift32*.py` and `runtime/recomp32/recomp_types.h` for a real
x86 correctness gap affecting games. This change fixes **one** gap: the
overflow flag (OF) read by `jo` / `jno` / `seto` / `setno` / `cmovo` / `cmovno`
when the statically-known flag setter is `cmp`, `sub` or `dec`.

Files owned by this task and touched here:

- `tools/lift/lift32.py` — the static flag-consumer pairing path
- `tools/lift/difftest.py` — differential regression cases
- `runtime/recomp32/recomp_types.h` — the `CMP_O` / `CMP_NO` condition macros
- `docs/GRPROD_6_PROVENANCE.md` — this file (new)

No pipeline code was modified.

## The gap

`recomp_types.h` defined:

```c
#define CMP_O(a, b)    0  /* TODO: overflow detection */
#define CMP_NO(a, b)   1
```

`lift32.py`'s `_make_condition()` emits those macros whenever a condition is
paired with a statically known `cmp`, `sub` or `dec` setter. Concretely,
`cmp eax, ecx; seto bl` lifted to `SET_LO8(ebx, (CMP_O(_flag_a, _flag_b)) ? 1 : 0)`,
which is the constant `0`. `sub; setno` was the constant `1`. The other flag
paths were correct: `recomp_eflags()` (used by PUSHFD and by the differential
harness's flag oracle) derives OF from the tuple, and `recomp_cond()` derives it
for join points and for `add`/`inc`. Only the static subtraction pairing was
broken, so a flag-only differential test could not expose it.

This matters in games: checked allocation, saturating clamps and overflow
guards are exactly the code that reads OF, and the failure is a plausible
length or coordinate rather than a crash.

## Upstream inspection (read-only)

Upstream PCRecomp is `https://github.com/sp00nznet/pcrecomp` (MIT). Two local
read-only checkouts were inspected:

| Checkout | HEAD at inspection | What it has |
|---|---|---|
| `/Users/haseeb-mir/Documents/Code/pcrecomp` | `a85ea970f9e0640ab6546d2ca7a01fccb132973d` | `#define CMP_O(a, b) 0 /* TODO: overflow detection */`, same `_make_condition()` fall-through |
| `…/maxcomp/upstream-pcrecomp` | `7c07a0b42059b6dc4313d9b4ebded6f7a72db8f3` | same placeholder at `runtime/recomp32/recomp_types.h:440` |

Both snapshots predate this fix and carry the **same** gap, so there was no
compatible upstream implementation to copy. No upstream source was copied or
modified by this task; the checkouts were read only.

## Derivation of the fix (no copied code)

The replacement expression is derived from the x86 definition of OF for a
subtraction and from this repository's existing derivations:

- `recomp_cond()` in `recomp_types.h`, `default:` branch (`FK_CMP`/`FK_DEC`):
  `of = ((a ^ b) & (a ^ r)) >> 31`, where `r = a - b`.
- `recomp_eflags()` in the same file, `default:` branch: the identical formula.
- `sbb.idiom`'s neighbouring derivation `recomp_flags_sbb()`.

`CMP_O` now uses that formula; `CMP_NO` is its negation. `lift32.py`'s
`_flag_capture()` left-aligns narrow operands (8-bit `<< 24`, 16-bit `<< 16`),
so the sign bit of a narrow operand is bit 31 and the same expression is exact
at 8, 16 and 32 bits — the same reason the existing `CMP_S` / `CMP_L` / `CMP_G`
macros work on narrow operands.

Two callers must not use a subtraction overflow:

- logic instructions (`test`/`and`/`or`/`xor`) clear OF. `jo`/`jno` were added
  to `test_only` in `_make_condition()` as constants `0`/`1`; without this,
  `test eax, 0x80000000` would read `CMP_O(eax, 0x80000000)` and could report
  an overflow no logic instruction ever set.
- `bt` leaves OF undefined; it keeps the deterministic `0`/`1` the old constant
  macro gave rather than deriving a subtraction it never performed.

## Evidence

Targeted difftest (`python3 tools/lift/difftest.py -k paired`):

- with the fix: **16/16 match hardware, 0 known divergences, 0 failures**
- negative control (both `recomp_types.h` macros temporarily restored to the
  constants and the two `lift32.py` additions removed): **10/16 match,
  6 failures** — `cmp.paired.seto-overflow`, `cmp.paired.setno-overflow`,
  `sub.paired.seto`, `dec.paired.seto`, `cmp8.paired.seto`,
  `cmp16.paired.seto`. Files were restored immediately afterwards.

New cases cover overflow and no-overflow for `cmp`, `setno`'s inverse answer,
`sub`, `dec`, 8-bit and 16-bit left-aligned operands, and a `test` case
guarding the logic-clears-OF path.

Full difftest (`python3 tools/lift/difftest.py`): **380/381 match hardware**,
including all new cases. The single failure, `sse.sqrtps`, is a pre-existing
NaN-payload divergence (lifted `0x7F...` vs CPU `0xFF...`); it reproduces
unchanged at the base commit `cb4dc04` with this change reverted, so it is not
attributable to this work.

## Limitations

- No live-game measurement. The evidence is differential (Unicorn x86 reference
  vs lifted C compiled and executed) plus the pre-existing harness; no claim of
  a verified in-game fix is made.
- `CMP_P` / `CMP_NP` remain placeholders. Static `jp`/`jnp` do not use them —
  `_make_condition()` computes parity via `RECOMP_PF` — so this task left them
  untouched rather than widening the change.
- `jo`/`jno` as conditional *branches* were not added as difftest cases: the
  harness lifts a branch to an out-of-function tail dispatch, which is not a
  clean register-value comparison. The condition is shared with `setcc`, so the
  same emitted macro is covered by the `seto`/`setno` cases.
- `sse.sqrtps` NaN-payload divergence and any other baseline difftest failures
  remain open; they are outside this task's scope.
