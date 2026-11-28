# native32 C++ throw metadata validation (issue #337)

`runtime/native32/eh32.c` matches a guest throw against `ThrowInfo` /
`CatchableType` / `TypeDescriptor` records built by the guest. Those records
are guest-controlled data. Before the fix, `catchable()` dereferenced them
unchecked: a null pointer reached `MEM32(0)`, an unaligned one crossed an x86
dword, and a negative or hostile count drove an unbounded loop over guest
memory.

The matcher now refuses malformed metadata before touching it:

- `eh_guest_dword()` accepts only a nonzero, dword-aligned address whose four
  bytes lie inside a mapped guest image (`native32_in_guest`), then performs
  the single `MEM32()` read.
- `eh_guest_name()` applies the same guest-span check to the inline decorated
  names, which are byte strings and need no dword alignment.
- Every pointer (`info`, `arr`, `ct`, `td`, `type`) is validated before any
  offset of it is read, including the name addresses used by `strcmp`.
- The catchable count is read as `int`; negative counts and counts above
  `EH_MAX_CATCHABLE` (4096) are refused without entering the loop.
- Each refusal sets an explicit reason (`null`, `misaligned`, `outside guest
  image span`, `null TypeDescriptor`, `hostile ... count`, ...). `dispatch()`
  fails closed on a reason instead of treating corrupt metadata as an
  ordinary "no matching catch", and the shim prints
  `[eh32] C++ throw refused: <reason>`.

No guest state is committed on a refusal: the throw falls through to the
real `_CxxThrowException`, so the host's crash report still sees a genuinely
uncaught exception.

## Verification

    python3 -m pytest tests/test_native32_eh32_validation.py -q

The test compiles the actual helpers and `catchable()` out of the C source
into a portable harness with a flat guest-memory model. It covers null,
misaligned and out-of-span pointers at every level, negative/oversized
counts, an inline name crossing the span, and successful identity and
name-based matches. A guest read outside the modelled span aborts the
harness, so an unvalidated dereference cannot pass silently. No game, Wine
prefix or live injection is involved; this is not a live-game claim.
