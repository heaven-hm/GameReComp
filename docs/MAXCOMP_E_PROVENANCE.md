# AG-E provenance — x86 lift: `cmpxchg8b`

## Change

GameReComp's 32-bit lifter (`tools/lift/lift32.py`) had no case for
`CMPXCHG8B m64` (`F0 0F C7 /r`). Capstone folds the `LOCK` prefix into the
mnemonic, so a locked 64-bit compare-and-swap fell through to the trailing
`UNIMPLEMENTED` no-op. The compare that usually follows it still passed, so
lock-free code believed every push/pop succeeded: Rockall's heap (behind every
allocation in *Rise of Legends*) drifted until a share-lock claim spun
forever. This adds the missing instruction family and freezes ZF — the only
flag `CMPXCHG8B` changes.

Lifted code runs under one machine lock, so the plain read-modify-write is
already atomic; the `lock` prefix needs no host atomics.

## Upstream source consulted (read-only)

- Project: **PCRecomp** — `https://github.com/sp00nznet/pcrecomp.git`
- Local checkout inspected: `/Users/haseeb-mir/Documents/Code/pcrecomp` at
  `a85ea970f9e0640ab6546d2ca7a01fccb132973d`
  (`changelog: win32hle additions for eJay 2 (#135, #140-#143) (#144)`).
- Remote fix branch: `fix/lift32-cmpxchg8b`, tip
  `b4fb6a23264374dd8ae8d9b423e63085211c1a16` (`CHANGELOG: cite #150`).
- Implementation commit consulted: `c8c189f3463aec74266e79c77d5910eabf9fba58`
  (`lift32: cmpxchg8b, the 64-bit compare-and-swap`, 2026-10-28),
  on that branch. It changed `tools/lift/lift32.py` (+18 lines) and
  `tools/lift/difftest.py` (+7 lines). Upstream issue reference: #150.

No upstream code was copied into this repository by script; the branch was
read with `git show` only. The local checkout and its git metadata were not
modified.

## What was adapted, and why it is compatible

- The upstream `elif m == 'cmpxchg8b' and len(ops) == 1 and
  ops[0].type == X86_OP_MEM:` branch is placed immediately after `cmpxchg`
  and before `sahf`, matching the same anchor in GameReComp's lifter. The
  surrounding `xadd`/`cmpxchg` block is byte-identical between the two trees.
- The emitted C uses only helpers that already exist in GameReComp's runtime
  header `runtime/recomp32/recomp_types.h`: `MEM64` (line 232),
  `recomp_eflags` (line 592), `FK_EFLAGS` (line 411). No runtime change was
  needed.
- The flag freeze mirrors GameReComp's own rotate handling
  (`recomp_eflags_setcf`): build the previous full EFLAGS word, clear bit
  0x40 (ZF), OR in the new ZF, then publish as `FK_EFLAGS` with
  `_flag_state = None` so the lazy flag model reads the frozen word back.
- The two differential cases from upstream were added to `difftest.py`
  unchanged: `lock.cmpxchg8b.equal` and `lock.cmpxchg8b.differ` on
  `f00fc70e`. This repo's `difftest.py` already contained the adjacent
  `lock.cmpxchg.*` cases at the same insertion point.
- A new fast structural regression test, `tools/lift/test_cmpxchg8b.py`, was
  added so the feature is guarded without a C compile. It fails on the base
  commit, where the same bytes lift to
  `; /* UNIMPLEMENTED: lock cmpxchg8b qword ptr [esi] */`.

Only one upstream fix from the `fix/lift32-cmpxchg8b-fucompp-cpuid-mask`
line of work was taken (cmpxchg8b); `fucompp` and the CPUID mask were out of
scope for this task.

## Files owned / changed

- `tools/lift/lift32.py` — added the `cmpxchg8b` lift branch (+20 lines).
- `tools/lift/difftest.py` — added two differential cases (+7 lines).
- `tools/lift/test_cmpxchg8b.py` — new structural regression test.
- `docs/MAXCOMP_E_PROVENANCE.md` — this file (new).

`runtime/recomp32/recomp_types.h` was inspected but needed no change.
`tools/lift/lift32_cpu.py`, `tools/lift/translator.py` and `pipeline.py` were
inspected but not modified.

## Verification (targeted, no native builds)

Commands run from the worktree:

```
python3 tools/lift/test_cmpxchg8b.py          # -> cmpxchg8b: ok
python3 tools/lift/difftest.py -k cmpxchg8b   # -> 2/2 match hardware
python3 tools/lift/difftest.py -k cmpxchg     # -> 4/4 match hardware
python3 tools/lift/difftest.py -k xadd        # -> 2/2 match hardware
python3 tools/lift/difftest.py                # -> 373/374 match hardware
```

Targeted lift tests all passed: `test_bswap`, `test_loopcc`,
`test_sar_narrow`, `test_nontemporal`, `test_computed_ret`,
`test_fpu_operands`, `test_dos_hooks`, `test_addr32_16`, `test_repz_ret`,
`test_cmpxchg8b`.

The single full-suite failure (`sse.sqrtps`, `[00410107] lifted 7F vs cpu
FF`) was reproduced on the unmodified base commit `cb4dc04` and is therefore
pre-existing and unrelated to this change. No C build / game run was
performed here (scope: targeted tests only).
