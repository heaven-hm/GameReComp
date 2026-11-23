# Differential integration gate (`tests/diff/`)

An `agree` verdict from the per-function oracle is only evidence if the thing
being compared can actually differ. This gate proves that, for two regression
classes that have already bitten this project, and refuses a body that breaks
either one.

## The regression class

`tools/lift/difftest.py` compares a candidate body against real x86 bytes run on
Unicorn. Two ways to get a *false* pass are documented in that file's own
comments:

* **Flags.** The reference machine once published a hard-coded zero EFLAGS. All
  flag comparisons then passed because the named bits were clear on both sides.
  Only a *lifted* body publishes flags (`RECOMP_FLAGS_OUT` → `recomp_eflags()`),
  so the default excludes them; a defect in either the reference capture or the
  comparator still looks like a clean run.
* **Named memory.** The compared address set is derived from the original bytes
  (`named_addrs_from_code`). If that extraction stops early or misses an
  operand, the comparison silently compares nothing at that address and reports
  agreement.

The existing unit tests check `diff()` with hand-built dictionaries
(`tests/test_per_function_oracle.py`). They cannot catch a defect in the
*reference machine* or in the address extraction, because they never run either.
This gate drives real bytes through the real reference machine and then mutates
one observable at a time.

## The fixture

`tests/diff/differential_gate.py` derives a synthetic PE32 from
`tests/fixtures/make_pe32_fixture.py` (no game corpus, no provider, no SDL2) and
replaces `.text` with a single function at `0x00401000`:

```asm
mov eax, [0x00404100]     ; named global, read
add eax, -2               ; flag subject: CF/PF/AF/ZF/SF all vary across cases
mov [0x00404104], eax     ; named global, written -> memory subject
ret                       ; named_addrs_from_code stops here
```

Eight entry states use difftest's own seed (`case + 1`) on both sides. The gate
then asserts:

| Check | What a failure means |
|---|---|
| `named-memory-from-original-bytes` | both the read and the write global are missing from the comparison set |
| `reference-flags-nonvacuous` | the reference's EFLAGS is constant (the hard-coded-zero regression) |
| `flag-break-hidden-when-flags-excluded` | the plain-C default is no longer a scoped claim |
| `flag-break-detected-when-flags-compared` | a ZF-only bug escapes the lifted-body comparison |
| `memory-break-detected-at-named-address` | a wrong byte at the written global escapes the comparison |
| `clean-body-agrees` | the comparator is trigger-happy rather than correct |
| `multilifter-promotes-only-clean-body` | a broken verdict would be promoted by the selector |

The last check emits verdicts in difftest's `--project` JSON row shape and
passes them to `tools/lift/multilifter.select()`. The clean body must `PROMOTE`;
the flag-broken and memory-broken bodies must `FAILED`. This is the integration
point: `multilifter` is a pure decision function and will happily promote a
vacuous `agree`, so the non-vacuity evidence has to come from the comparator
side.

`tests/diff/test_differential_gate.py` also injects both defects (zeroed EFLAGS
and a memory-blind comparator) and asserts the gate fails, so the gate itself
cannot rot into an always-pass.

## Running it

```sh
sh verify.sh --differential          # opt-in gate inside the shared verify script
python3 tests/diff/differential_gate.py --json /tmp/diff-gate.json
python3 -m pytest tests/diff/ -q      # the same assertions, unattended
```

`verify.sh` accepts `--fast` and `--differential` together in either order.
The gate is opt-in for the script (it is not part of the default or `--fast`
run), but its assertions are ordinary pytest tests and therefore already run in
the default `pytest tests/` pass. A run writes `/tmp/verify-diff-gate.json` with
the full report when invoked through `verify.sh`.

Gate exit codes: **0** every check passed, **1** a check failed (a real
regression), **2** the gate could not run (missing dependency, changed fixture
layout, malformed PE). Code 2 is deliberately distinct from a comparison
failure: an unavailable harness is not evidence of a broken body.

## Report

The JSON report carries the fixture's code bytes, named addresses and PE SHA-256
so a result can be tied to the exact subject, one `name`/`ok`/`detail` record per
check, and the three selector decisions. `what_it_does_not_prove` states the
scope in the report itself.

## Audit notes

These are the findings from auditing the four named areas; only the gap below
was changed, to keep the gate's blast radius small.

* **`tools/lift/difftest.py`** — the reference side already reads real EFLAGS
  (`run_unicorn_function` → `UC_X86_REG_EFLAGS`), flag comparison is scoped by
  `Case.undef` and `--flags`, and named memory comes from the original bytes and
  stops at `ret`. What it lacks is a guard that the captured state actually
  varies: a future "simplification" back to a constant EFLAGS is invisible to
  its own tests. That guard now lives in the gate, which exercises these exact
  functions rather than duplicating them.
* **`tools/lift/multilifter.py`** — already fail-closed: `agree*` with zero
  diverged cases is the only promotion path, case flags are cross-checked, and
  faults/no-body/unverified reports fall through or block. It is intentionally
  a pure decision function over existing verdicts; it cannot know whether a
  verdict was vacuous, so the gate supplies that evidence before selection.
* **`verify.sh`** — the single shared gate for CI and developers. The new
  `--differential` flag keeps the new Unicorn-driven check opt-in while the
  pytest path keeps it covered by default.
* **CI layout** — `.github/workflows/release.yml` only builds and publishes
  release artifacts. The canonical CI definition is
  `docs/ci/github-workflows-ci.yml`, whose `python` job calls `sh verify.sh
  --fast`. That file is outside this change's ownership; enabling
  `--differential` there is a one-line follow-up for whoever owns CI configuration.

## Limits

* One synthetic function, eight seeded entry states. This proves the
  *comparator* is non-vacuous for flags and named memory; it is not a claim
  about any other function, entry state or the game.
* The candidate side is a copied-and-mutated reference result, not C compiled
  from a provider. The C compile/link path (`build_project_cases`) and the
  hand-written `Lifter` cases are covered elsewhere and are unchanged here.
* `difftest.oracle()` is a documented sequential instrument; this gate runs one
  function sequentially and does not change that.
