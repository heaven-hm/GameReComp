# AI-assisted development tooling (deterministic probe)

`tools/ai_assist_probe.py` is a small **review aid** for the first-party Win32
HLE shims in `runtime/win32hle`. It is *not* an AI model, does not call any
external API, uses no network, and does not execute game, PE, or lifted code.
It reads C source as text and emits JSON leads for a human to inspect.

The name "AI-assisted" describes the workflow it supports: an agent or a human
reviewer gets a short, reproducible list of *places to look*, then applies
judgement. It does not replace that judgement, and it never claims a shim is
correct.

This is separate from the optional AI readability pass
(`tools/autorecomp/readability.py`), which invokes an external provider to
rewrite generated C and is gated on compilation/evidence. The probe here has
no provider, no network, and no write access to source: it only reads and
reports.

## Scope

* Scans `*.c` files directly inside `runtime/win32hle` (the first-party shim
  modules). Files ending in `_selftest.c` are skipped by default because they
  intentionally contain mock tables and partial shims; `--include-selftests`
  opts back in.
* Reports each lead with a `file`, `line`, `rule`, and human-readable `reason`.
* Output is deterministic: findings are sorted, de-duplicated, relative to the
  repo root, and contain no timestamps or host-specific paths. Identical input
  always produces byte-identical JSON.

## Rules

| Rule | What it looks for | Why it matters |
| --- | --- | --- |
| `unchecked-guest-pointer` | A value from `APTR`/`ASTR`/`hle_ptr`/`A32` is dereferenced (via `*`, `->`, `[]`, `MEM*`, or a known string/memory call) with no syntactic NULL/validity check in the same shim. | In the based-memory build guest VA 0 becomes host NULL, so an unguarded dereference can fault the host instead of returning the Win32 error. |
| `ret-arity-too-small` | A stdcall shim reads argument index *n* (e.g. `A32(1)`) but its `RET`/`RETP`/`RETV` purges fewer than *n+1* words. | The guest stack is left misaligned on return; the failure can surface much later, far from the shim. |
| `inconsistent-ret-arity` | Different `RET` sites in one shim pop different argument counts. | A stdcall callee must purge the same number of words on every path. |
| `missing-ret` | A function named in a shim table reads arguments but contains no `RET`. | A half-written new shim will not restore the guest stack. |
| `duplicate-shim-name` | The same literal shim name is registered more than once, including across files. | Registration order decides which implementation answers; a duplicate is rarely intentional and is easy to introduce when copying a table. |

Each finding is a **lead**, not a verdict: a guard the tool did not recognise,
an intentionally aliased name, or a varargs shim are all legitimate reasons to
dismiss one after reading the code.

## Usage

```sh
# Scan the shipped runtime and print JSON to stdout
python3 -m tools.ai_assist_probe

# Save a report for triage / diffing between revisions
python3 -m tools.ai_assist_probe --out reports/ai-assist-probe.json

# Scan a synthetic or alternate directory
python3 -m tools.ai_assist_probe --source /path/to/c/sources --repo-root /path/to/repo

# Include the selftests too
python3 -m tools.ai_assist_probe --include-selftests
```

JSON shape (schema version 1):

```json
{
  "schema": 1,
  "tool": "ai_assist_probe",
  "source": "runtime/win32hle",
  "files_scanned": 40,
  "summary": {"findings": 0, "by_rule": {}},
  "disclaimer": "Heuristic review aid only. ...",
  "findings": [
    {"file": "runtime/win32hle/kernel32.c", "line": 54,
     "rule": "ret-arity-too-small", "reason": "..."}
  ]
}
```

A recommended triage loop:

1. Generate the report and group findings by `rule` using `summary.by_rule`.
2. Open each `file:line` and decide whether the lead is real. Fix the code, or
   note why the pattern is intentional.
3. Re-run and compare reports. A shrinking report is useful signal; a stable
   or empty one is *not* proof that the layer behaves correctly.

## Limitations

The probe is deliberately simple and text-based. In particular:

* **No compilation, type analysis, or control-flow graph.** Guards are found
  by recognising common `if (...)` shapes; a helper function that validates a
  pointer, a guard expressed differently, or data-flow across calls will not
  be understood. Both false positives and false negatives are expected.
* **Rejecting a lead requires reading the code.** A `RETP(ptr, n)` that returns
  a guest pointer is not a dereference; a library call outside the small known
  list is not assumed to dereference its arguments.
* **Literal arguments only.** Argument indices must be numeric literals
  (`A32(2)`), and `RET` arities must be constant. Computed indices/arities are
  skipped rather than guessed.
* **Cdecl/varargs caveat.** Shims using `RETC`/`RETCV` are exempt, but a cdecl
  shim that spells its return as `RET(x, 0)` and reads an argument will be
  flagged. That is a lead to check the return macro, not proof of a bug.
* **Duplicate names may be intentional.** Ordinal aliases and interface
  vtable names can legitimately repeat across modules; the report does not
  decide intent.
* **Macro-generated names are out of scope.** Only *literal* shim-table names
  are extracted, so token-pasted names (X-macro prefixes, ordinal aliases
  built from `#ord`) are not compared for duplicates.
* **No game verification.** This tool never boots a PE, lifts a function,
  injects a DLL, or observes the retail executable. It cannot validate ABI
  fidelity, return values, state transitions, or anything about a real game.
  Use the project's runtime harnesses and the live-game workflow for that, and
  do not claim live-game success from a probe report alone.

## Tests

```sh
python3 -m pytest tests/test_ai_assist_probe.py -q
```

The tests use synthetic C fixtures in temporary directories: a clean guarded
shim, arity mistakes, cdecl exemption, missing `RET`, checked vs unchecked
guest pointers, duplicates across files, selftest exclusion, and deterministic
JSON/CLI behaviour. They validate the *heuristics*, not the shipped shims.
