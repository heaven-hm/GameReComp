# Issue #25 acceptance-evidence audit

- Gate: `ISSUE25_EVIDENCE_ONLY_CANNOT_CLOSE`
- Generator: `tools/issue25_evidence.py` (schema 1)

**Overall: OPEN** -- this tool reports CLOSABLE or OPEN only; it does not change any issue's state.

| Verdict | Count |
|---|---:|
| CLOSABLE | 0 |
| OPEN | 1 |

| Criterion | Verdict | Reason codes | Evidence |
|---|---|---|---|
| revng_selection | FAIL | SELECTION_NOT_CITED | `issue25_blocker_evidence.json[0]` |
| pcrecomp_fallback_after_revng_fail | FAIL | NO_EVIDENCE | — |
| both_fail_unresolved | FAIL | NO_EVIDENCE | — |
| negative_pointer | FAIL | NO_EVIDENCE | — |
| negative_stack | FAIL | NO_EVIDENCE | — |
| negative_callback | FAIL | NO_EVIDENCE | — |
| determinism | FAIL | NO_EVIDENCE | — |
| opt_in_no_default_regression | FAIL | NO_EVIDENCE | — |
| source_behavior_separation | FAIL | NO_EVIDENCE | — |

## Findings

| Criterion | Reason codes | Details |
|---|---|---|
| revng_selection | SELECTION_NOT_CITED | evidence record has no 'selection' name |
| pcrecomp_fallback_after_revng_fail | NO_EVIDENCE | no evidence record for acceptance criterion 'pcrecomp_fallback_after_revng_fail' |
| both_fail_unresolved | NO_EVIDENCE | no evidence record for acceptance criterion 'both_fail_unresolved' |
| negative_pointer | NO_EVIDENCE | no evidence record for acceptance criterion 'negative_pointer' |
| negative_stack | NO_EVIDENCE | no evidence record for acceptance criterion 'negative_stack' |
| negative_callback | NO_EVIDENCE | no evidence record for acceptance criterion 'negative_callback' |
| determinism | NO_EVIDENCE | no evidence record for acceptance criterion 'determinism' |
| opt_in_no_default_regression | NO_EVIDENCE | no evidence record for acceptance criterion 'opt_in_no_default_regression' |
| source_behavior_separation | NO_EVIDENCE | no evidence record for acceptance criterion 'source_behavior_separation' |

## Recomputed input hashes

| Kind | Name | Path | SHA-256 |
|---|---|---|---|
| selection | blocked | `proof/revng/multilifter_blocked.json` | 970c5f99672050deae7d6d42d10af31fdd084cd258b00cffda65b54d117f6dbd |
| diff_gate | gate | `proof/revng/diff_gate.json` | 95b3faec287f51f3ccb9da02fb9911aea5b384bb43bf0f02948760e2a5b71d09 |
| evidence | — | `proof/revng/issue25_blocker_evidence.json` | ed35d3213151b6fcef6af9ea32c52a47fd4c02c7a9ff5fb415a07cf761cdc8e7 |

## Reproduction command

`python3 tools/issue25_evidence.py --selection blocked=proof/revng/multilifter_blocked.json --diff-gate gate=proof/revng/diff_gate.json --evidence proof/revng/issue25_blocker_evidence.json --json proof/revng/issue25_audit.json --out proof/revng/issue25_audit.md`

## Limits

- The tool reports CLOSABLE or OPEN only. It never closes, reopens, comments on or edits any issue, calls gh or uses the network; it reads only CLI-supplied files and writes only the two CLI-supplied report paths. No GitHub auto-closing keyword is emitted.
- CLOSABLE means the supplied evidence satisfies this gate, not that an issue was or will be closed, and not that a game is fully equivalent.
- This is a joiner over existing reports. It does not invoke rev.ng, PCRecomp lift32, difftest, a compiler or a live game. A synthetic report that satisfies the schema can produce CLOSABLE; provenance of the reports themselves is outside this tool's reach.
- Every supplied file's SHA-256 is recomputed from bytes; every embedded sha256 claim is checked against a canonical digest with that claim removed, and cited artifact hashes are recomputed from disk. Optional seals on tool reports are advisory when absent but binding when present; evidence records must be sealed.
- A selection PROMOTE is accepted only when the chosen lifter is PASS with zero diverged cases and at least one compared case; FAILED/BLOCKED require a null chosen lifter. No booleans in an evidence record can override a linked report.
- Passes are scoped agreements on the cases the supplied reports name. The tool does not claim universal semantic equivalence, whole-game correctness, or HIGH_LEVEL_VERIFIED status, and it rejects any evidence record that does.
- Determinism means byte-identical repeated run reports plus identical derived decisions. It says nothing about a machine/setup not supplied.
