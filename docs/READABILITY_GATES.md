# Readability gates: pre-build exported-symbol guard

`tools/autorecomp/readability.py` is the gated readability harness: rev.ng
first, an optional AI polish pass on generated C, then rebuild, rerun and
evidence comparison, reverting anything that does not reproduce the tested
baseline. This document describes the **pre-build symbol-name guard**, added to
stop a specific wasted attempt.

## The failure it prevents

Generated units in `src/recomp/gen/recomp_*.c` define address-named entry points
such as `sub_00402020`. `src/recomp/gen/recomp_dispatch.c` and
`src/recomp/gen/recomp_funcs.h` refer to those names, and sibling units call
each other through `RECOMP_CALL(...)`. The AI prompt already says not to rename
them, but a provider still sometimes "cleans up" a `sub_XXXXXXXX` name.

Before this guard, that candidate was written to disk and compiled; the failure
only appeared at link (`undefined reference to sub_00402020`), after a full
configure/build cycle. The repair loop then spent an attempt discovering what
was knowable from the candidate text alone.

## Where the guard runs

In `ReadabilityHarness._ai_unit()`, immediately after the provider returns a
candidate and **before** `unit.write_text(...)` and `self._rebuild()`:

```text
provider -> candidate text -> symbol-name guard -> write -> build -> rerun -> compare
                                  |
                                  +-- reject: record step "symbol-guard", continue retry
```

A rejected candidate is never written to the source tree and never built, so
there is nothing to revert and no baseline rebuild to pay for. The rejection is
recorded as a failed attempt with step `symbol-guard`, feeds the next attempt
(both the `READABILITY_FEEDBACK` file and the stdin prompt), and appears in the
final edits record as `last_step: "symbol-guard"`.

## What counts as a protected name

`extract_exported_symbols()` returns a name -> reason map for:

| Source in the C unit | Why it is protected |
| --- | --- |
| `sub_XXXXXXXX` address-named lift functions | The name encodes the guest VA; the dispatch table binds it by name. |
| Top-level, non-static function definitions | Other translation units declare and call them. |
| `{ 0x00402020u, sub_00402020 }` dispatch entries | Direct `recomp_dispatch_table` binding. |
| `RECOMP_CALL(name)` / `RECOMP_DISPATCH(name)` | Sibling-unit call target. |
| `__declspec(dllexport)`, `EXPORT(name)`, `RECOMP_EXPORT` | Explicit export decoration. |

Comments and string literals are blanked before scanning (`_strip_c_noise`), so
adding or removing a note that merely mentions an address does not look like a
symbol change. Local variables and `static` helpers are intentionally *not*
included: renaming those is the point of the readability pass.

`find_symbol_name_changes()` compares original against candidate and reports a
change when a protected name is **lost**. It also compares the set of top-level
definitions separately, because a definition can be renamed while a stale call
reference to the old name remains -- a union-only comparison would miss exactly
the link failure this guard targets. When the candidate introduces exactly one
new protected name, it is attached as `suspected_rename` so the feedback can say
`sub_00402020 -> ReadableEntry`.

## Feedback example

```text
pre-build symbol guard rejected src/recomp/gen/recomp_0007.c: exported/dispatched symbol name(s) changed
src/recomp/gen/recomp_dispatch.c and sibling units bind these names at link time, so renaming them fails the build. Keep every name below byte-for-byte identical:
  - sub_00402020 -> ReadableEntry (address-named dispatched function (sub_XXXXXXXX)) is missing from the candidate
Restore the original names -- including sub_XXXXXXXX address symbols -- and change only local variable names, comments, and control-flow shape.
```

## Tests

`tests/test_readability.py` covers the helper and the repair loop:

- only dispatched/exported names are extracted (not locals, comment mentions or
  static helpers);
- a renamed address symbol is reported with its suspected replacement;
- a definition rename with a stale call reference is still detected;
- local renames and added comments produce no changes;
- the fake-provider repair-loop test proves that a renamed symbol is rejected
  with `step == "symbol-guard"`, the source is left untouched and `_rebuild`
  is never called, and the second attempt's feedback file contains the precise
  reason;
- a safe local rename still reaches the build and is accepted.

Run with:

```sh
python3 -m pytest tests/test_readability.py -q
```

## Limits

- This is a lexical, source-level guard for the C units the AI pass edits. It
  is not a linker, and it does not parse the full C grammar: a function
  signature split across lines or an exotic declaration form may be missed.
  The real build/evidence gates still run for every accepted edit.
- It detects names that are *lost*; adding a new exported name is not a link
  failure and is allowed.
- It does not guard the generated dispatch table itself from edits made by
  other tools -- the table is regenerated by the translator, not by this pass.
