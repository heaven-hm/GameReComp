# rev.ng source quality — real CI evidence (issue #25)

This file reports the **measured** outcome of the active
`.github/workflows/revng-evidence.yml` job. It does not grant a verdict by
itself: the machine-readable `proof/revng/SOURCE_QUALITY.json` written by
`tools/revng_source_quality.py` is the authority, and its self-seal must match.

Status before the first real GitHub run: **PENDING — no run yet**. No PASS,
no `SOURCE_IMPROVED`, and no issue state change is implied by this document.

## What is compared

| Field | Value |
|---|---|
| Fixture | PE32 built by `tests/diff/differential_gate.py:build_gate_pe()` |
| Function | `0x00401000` (`mov eax,[0x00404100]; add eax,-2; mov [0x00404104],eax; ret`) |
| Baseline | PCRecomp project body generated from that exact PE by `game-recomp-cli` |
| Candidate | C emitted by the pinned rev.ng distributable, adapted to the canonical `MEM32`/`RETC` contract for this one function |
| Original bytes | Unicorn reference inside `tools/lift/difftest.py --project`, 8 entry states |
| Selection | `tools/lift/multilifter.py --lifter revng=<report> --lifter pcrecomp=<report>` |

Measured and recorded for both candidates: exact C SHA-256 and byte count,
`cc -fsyntax-only` rc, difftest report hash/verdict/case count/diverged count,
and structured-C signals from the repo's own readability tooling (named locals
and parameters, non-goto control statements, recovered types/structs,
non-mechanical identifier ratio). Every hash is recomputed from file bytes by
the evaluator; a claimed hash that disagrees is a `*_HASH_MISMATCH`.

## Verdict rule

`SOURCE_IMPROVED` needs all of: measured compile rc 0 for both candidates;
real, agree-with-zero-divergence difftest reports for both; a multilifter
decision of `PROMOTE` with `chosen_lifter=revng` on those exact reports; no hash
mismatch; and readability signals that are not worse and strictly better in
naming and identifier quality. Anything else is `NOT PROVEN` with exact reason
codes, and the job exits non-zero.

The verdict is scoped to the two exact C hashes, that fixture and that one
address. It is **not** `HIGH_LEVEL_VERIFIED`, gameplay equivalence, 100% or a
general source-quality claim.

## Real run

_To be filled from the actual GitHub Actions run after the first dispatch:
run URL, conclusion, measured hashes/counts/signals and the exact verdict._
