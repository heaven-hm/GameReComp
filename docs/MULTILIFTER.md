# Fail-closed per-function multi-lifter selection

`tools/lift/multilifter.py` is the **selector** half of issue #25. It does not
run a lifter, compile a body or drive a differential harness. It consumes the
JSON reports that `tools/lift/difftest.py --project --json ...` already writes —
one report per candidate lifter, for one function — and decides which body may
be promoted. Every refusal is explicit; there is no silent default.

This document is the exact contract. `tests/test_multilifter.py` pins it.

## Input

`select(lifters)` takes an iterable of `(name, report)` pairs **in descending
priority order**. `report` may be:

| Form | Example |
|---|---|
| a difftest report `dict` | `{"addresses": [{"address": "0x00401000", "verdict": "agree", ...}]}` |
| a list of verdict-row `dict`s | `[{"verdict": "agree", "cases": [...]}]` |
| a JSON string | `'{"addresses": [...]}'` |
| a `pathlib.Path` to a JSON file | `Path("revng_difftest.json")` |

A difftest report is accepted in its native shape (`{"addresses": [...]}`) or
as a single row carrying a top-level `verdict`. Each row may carry `address`,
`verdict`, `detail`, and an optional `cases` list whose entries use difftest's
`{"case": int, "agree": bool, "fields": [...]}` schema.

Malformed input (unreadable file, invalid JSON, a row that is not an object, a
`cases` value that is not a list, a report with no verdict) never raises: it
becomes an `UNVERIFIED` lifter and is therefore blocked.

## Verdict classification

Classification is per row; a lifter's status is the aggregate of its rows.

| Verdict from difftest | Status | May be promoted? |
|---|---|---|
| `agree`, `agree-frame`, `agree-return`, `agree-memory` (any verdict starting with `agree`) with zero diverged cases | `PASS` | **Yes** |
| `diverge` / `diverged` | `DIVERGED` | No |
| `original-faulted`, `harness-faulted`, `faulted` | `FAULTED` | No — a faulted original or harness gives no trustworthy comparison |
| `no-generated-body`, `no-body`, `nothing-generated` | `NO_BODY` | No — there is no body to verify |
| `harness-failed`, `nothing-compared`, empty, unknown, or a malformed report | `UNVERIFIED` | No |

Cross-check on `cases`: an `agree` verdict is authoritative only when no case
contradicts it. A case counts as agreeing **only** if its `agree` field is
exactly `True`; any other value (including a missing flag or a malformed case)
counts as diverged. If an `agree` verdict has `diverged_cases > 0`, the row is
reclassified `DIVERGED` and the decision becomes `FAILED`. If `cases` is present
but empty, the row is `UNVERIFIED` ("nothing was compared"). If `cases` is
absent, the verdict stands on its own — difftest sets `agree` only when its own
diverged counter is zero.

A multi-address report passes only if **every** row passes; its case counts are
summed. When rows disagree, the lifter status is chosen by the deterministic
precedence `FAULTED > NO_BODY > UNVERIFIED > DIVERGED > PASS`: a single blocker
refuses the whole lifter.

## Promotion rule

The selector walks the lifters in the given order. **The first lifter whose
status is `PASS` is the only one promoted.** Evidence that an earlier lifter
diverged, faulted, had no body or was unverified is recorded as a `skipped`
reason but does not prevent falling through to a later passing lifter — that
fallback is the point of the issue. Conversely, a later `PASS` never rewrites an
earlier lifter's status; all lifters are reported.

A `PASS` is always a *scoped* agreement over the cases difftest actually drove.
It is not a claim of universal equivalence, whole-game correctness or
`HIGH_LEVEL_VERIFIED` status.

## Overall decision

| Decision | When | `chosen_lifter` |
|---|---|---|
| `PROMOTE` | At least one lifter is `PASS` | name of the first `PASS` lifter |
| `FAILED` | No `PASS`, and at least one lifter is `DIVERGED` **or** has `diverged_cases > 0` | `null` |
| `BLOCKED` | No `PASS` and no divergence: every lifter is `FAULTED`, `NO_BODY`, `UNVERIFIED`, or the list is empty | `null` |

`FAILED` means a real comparison found a difference. `BLOCKED` means the
selector could not obtain a trustworthy comparison at all. Both carry one
reason string per lifter, in priority order, so a caller can see exactly why
each candidate was rejected. There is no fourth outcome and no partial
promotion.

### Never promote

A lifter is promoted **only** by a clean `agree*` verdict with zero diverged
cases. In particular the selector never promotes:

- an `original-faulted` or `harness-faulted` body;
- a `no-generated-body` report;
- an unverified, `harness-failed`, `nothing-compared`, malformed or empty
  report;
- an `agree` verdict contradicted by its own case list.

## Python API

```python
from tools.lift import multilifter

decision = multilifter.select([
    ("revng", "reports/revng_difftest.json"),
    ("pcrecomp", "reports/pcrecomp_difftest.json"),
])
if decision.promoted:
    use(decision.chosen_lifter)
else:
    fail(decision.decision, decision.reasons)   # "FAILED" or "BLOCKED"
```

- `select(lifters) -> Decision` — the entry point.
- `classify_row(row) -> _RowResult` — classify a single raw verdict row
  (internal helper, exposed for introspection/tests).
- `load_report(report) -> list` — normalise any accepted input form to raw rows.
- `LifterResult` fields: `name`, `status`, `verdict`, `address`,
  `diverged_cases`, `total_cases`, `reasons`, `.passed`.
- `Decision` fields: `decision`, `chosen_lifter`, `lifters`, `reasons`,
  `.promoted`, `.to_dict()`, `.to_json()`.

Output is a JSON-serialisable object:

```json
{
  "decision": "PROMOTE",
  "chosen_lifter": "pcrecomp",
  "reasons": [
    "skipped 'revng' (DIVERGED): verdict 'diverge': diverged; 1/3 case(s) diverged",
    "promoted 'pcrecomp' (PASS): verdict 'agree': agrees with zero diverged cases over 3 case(s)"
  ],
  "lifters": [
    {"name": "revng", "status": "DIVERGED", "verdict": "diverge",
     "address": "0x00401000", "diverged_cases": 1, "total_cases": 3,
     "reasons": ["verdict 'diverge': diverged; 1/3 case(s) diverged"]}
  ]
}
```

## CLI

```sh
python3 tools/lift/multilifter.py \
  --lifter revng=reports/revng_difftest.json \
  --lifter pcrecomp=reports/pcrecomp_difftest.json \
  --json reports/selection.json
```

`--lifter NAME=REPORT` is repeatable and order defines priority; `REPORT` is a
file path or inline JSON. The decision JSON is printed to stdout unless
`--quiet`, and optionally written to `--json PATH`.

Exit codes: **0 = `PROMOTE`**, **1 = `FAILED`**, **2 = `BLOCKED`**. A missing
`--lifter`, an invalid `NAME=REPORT` spec, or an unreadable report never
produces exit code 0: the first two are argument errors, the last is a blocked
decision.

## Scope and limitations

- One decision per function. Reports for several addresses are supported only
  so a lifter cannot pass while any of its addresses diverges; this selector
  does not map results back into a build or rewrite source.
- It is a pure decision function over **existing** verdicts. It neither invokes
  providers (rev.ng / PCRecomp `lift32`) nor runs `difftest.oracle`; producing
  the per-lifter reports is the caller's responsibility.
- `agree` means scoped agreement on the driven cases and documented
  observables. It is not evidence about other entry states, other functions or
  the game as a whole. Live-game differential evidence remains a separate gate.
- This module does not change the default pipeline. It is opt-in for callers
  that choose to consume the decision.
