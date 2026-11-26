# rev.ng source quality — real CI evidence (issue #25)

**Conclusion: rev.ng did NOT produce measurably improved source on this
fixture.** The active job ran for real and its measured conclusion is
`SCOPED_NOT_PROVEN`: the workflow is green because a genuine `NOT PROVEN` is a
recorded result, not an infrastructure error. Nothing here grants a PASS, and
issue #25 stays OPEN.

- Run URL: https://github.com/heaven-hm/GameReComp/actions/runs/45778254804
- Run ID: `45778254804` (`workflow_dispatch`, head `fa6408b4d64550edd6cc6c3cc64627cece077ff1`)
- GitHub conclusion: `success`; job `revng-evidence` exit code 0. The
  source-quality evaluator itself still returned rc=1; the summary recorded
  `status=SCOPED_NOT_PROVEN`, `scoped_verdict=NOT PROVEN` and the exact scoped
  reason, then exited 0. See `reports/revng/revng-ci-summary.json` in the run
  artifact.
- Machine-readable verdict: `NOT PROVEN` in `proof/revng/SOURCE_QUALITY.json`
  (sealed, `sha256` recomputed and verified)
- Earlier real attempts: `45695146599` failed at the adapter/compile stage
  (`recomp_funcs.h` include path, doubled `U` suffix, void-return cast; fixed in
  PR #301); `45764193627`, `45766972844` and `45768357859` failed only at the
  final step because the measured `NOT PROVEN` was treated as an error and the
  evaluator compared `0x401000` literally against difftest's `0x00401000`.
  Issue #339 fixes both; the numbers below are from the corrected rerun.

## Measured pin and host facts

| Fact | Measured value |
|---|---|
| Archive byte count | `564214476` (exact match) |
| Archive SHA-256 | `f19060db47165388df24343c6c7abc824742ac9063beeb614d474f3c6b764238` (`sha256sum -c checksums.sha256: OK`) |
| rev.ng version on runner | `rev.ng version 1af3940` (native Linux x86-64, no container) |
| PE32 fixture | 1,280 bytes, SHA-256 `d7871ee496e48a3bf79c4cb8f6cac71ca8e84d6f02e2b320c94b3b868de6633f` |
| Function | `0x00401000`, 8 entry states, two named guest addresses |

## Side-by-side measurement

| Evidence | PCRecomp baseline | rev.ng candidate |
|---|---|---|
| C bytes | 1086 | 187 |
| C SHA-256 | `61578445e5748fe476d8c8b344d3a9161b10d5cb542b0c5360b1e4ead1f7a684` | `3e8e4cc976be17617c36b4ece6cae9d7e418336f2eaa6c0c8d0acf29df6b0ec7` |
| `cc -fsyntax-only` rc | 0 | 0 |
| difftest report SHA-256 | `075877f49e0b84509fd8ba8435bb51bb45739e5e00a4f56a33fb7845570393b8` | `eee2066731aad2a00a278fabf7d4a0141647d041b3623987fe3774d2375121b7` |
| difftest verdict | `agree` | `agree-memory` |
| Diverged cases | 0/8 | 8/8 |
| Named locals | 5 (`_cf`, `_df`, `_flag_a`, `_flag_k`, `_fpu_cmp`) | 0 |
| Named parameters | 0 | 0 |
| Gotos | 0 | 0 |
| Non-goto control statements | 0 | 0 |
| Recovered types | 0 | 1 (`generic32_t`) |
| Non-mechanical identifier ratio | 0.36 (18/50) | 0.333333 (3/9) |

Multilifter decision: `PROMOTE`, `chosen_lifter=pcrecomp`; revng row
`DIVERGED` (8/8), PCRecomp row `PASS`. Decision SHA-256
`4d3faeb33e5346c55e48e7e0be6e73db670687a9ee028527e31c4bbaeb9721c1`.

## Why the verdict is NOT PROVEN

Exact reason codes recorded in the sealed JSON:

- `REVNG_DIFFTEST_DIVERGED_FIELD` and `REVNG_DIFFTEST_DIVERGED_CASES` — revng's
  `agree-memory` verdict still has 8/8 driven cases with `agree != true` (eax
  diverges; the adapter calls a `void` revng function, which does not model the
  guest return value). The evaluator refuses a scoped memory-only agreement as
  a full comparison.
- `DECISION_NOT_REVNG`, `DECISION_REVNG_NOT_PASS`, `DECISION_REVNG_DIVERGED` —
  multilifter promoted PCRecomp, not revng.
- `READABILITY_NOT_BETTER` — revng had 0 named locals versus 5 in the baseline
  and a lower non-mechanical identifier ratio (0.333333 vs 0.36). It did not
  recover more structured control flow for this tiny function.
- No `DIFFTEST_ADDRESS_MISMATCH` is present in the corrected report. Addresses
  are now compared numerically, so difftest's `0x00401000` and the requested
  `0x401000` are the same function; a genuinely different or malformed address
  still fails closed.

No compile failure, hash mismatch, missing C output or download failure
occurred in this run. The evaluator still refuses a revng PASS; the workflow
records that measured refusal as a scoped verdict instead of a job error.

## Scope

The result is scoped to this exact fixture, function address and the two C
hashes above. It is not `HIGH_LEVEL_VERIFIED`, not gameplay equivalence, not
100%, and not a general claim about rev.ng source quality on other functions.
`SCOPED_NOT_PROVEN` is not a PASS for revng, PCRecomp or the adapter; it only
reports that this measured evidence did not establish improved revng source.
The adapter used here is a one-function test harness, not a new dispatch path;
the default PCRecomp-only build is unchanged. Issue #25 remains OPEN.
