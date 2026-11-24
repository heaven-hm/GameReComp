# Corpus acceptance report

Generated: 2026-11-24T01:08:02+00:00  
Schema: 1  
Statuses are derived from recorded evidence only; no PASS is assigned from a build, a frame or a bare exit code alone.

## Inputs

- original:base64: /Users/haseeb-mir/Downloads/recomp-apps/windows/x86/apps/base64.exe (ok)
- native:base64: /tmp/agent1-base64/build/base64 (ok)
- scenario:base64: reports/oracle/base64-scenario.json (ok)

## Games

### base64 — **PASS**

- Original: `/Users/haseeb-mir/Downloads/recomp-apps/windows/x86/apps/base64.exe` SHA-256 `d805661c60b0b99eab94ad0359ece9b91e7a79fecccc630e92ffdf217c48f879` PE `PE32` arch `i386`
- Pipeline: ok verification=`scenario-pass` report=`/tmp/agent1-base64/reports/report.json` score=`100`
- Native: `/tmp/agent1-base64/build/base64` SHA-256 `caaa9bd8c53caf77c94cff1f415f92d45f75d32af69607e1234858b7a1d93cd1`
- Launch: executed=True exit=`0` log=`/private/tmp/agent1-acceptance-base64c/base64/run.log`
- Kind: `cli` (declared CLI scenario, complete=True)
- CLI scenario: args=[] expected_exit=0 expected_output_sha256=`6a5de47897f4dd74c3673a4069843b67f9d357561ed1e7f57a36ba532048db03` source=`reports/oracle/base64-scenario.json`
- CLI evidence: output_sha256=`6a5de47897f4dd74c3673a4069843b67f9d357561ed1e7f57a36ba532048db03` log_exists=True exit_matches=True output_matches=True reproduced=True (render-free process-level evidence)
- Frames: runner=None joined=0 proven=0 blank=0 unproven=0
- Reasons:
  - original SHA-256 d805661c60b0b99e... and PE metadata recorded (i386)
  - native SHA-256 caaa9bd8c53caf77... (1869336 bytes, source=cli)
  - native SHA-256 matches pipeline manifest (caaa9bd8c53caf77...)
  - launch exit 0 in 0.3s
  - CLI scenario args=[]: exit 0 vs declared 0, stdout/stderr sha256 6a5de47897f4dd74... vs declared 6a5de47897f4dd74..., log run.log
  - pipeline verification 'scenario-pass' matched
  - scope: declared CLI scenario + process-level oracle, not gameplay equivalence

## Summary

- PASS: 1; PARTIAL: 0; BUILD ONLY: 0; FAIL: 0; BLOCKED: 0

PASS requires a matching pipeline oracle verification, matching input/native hashes and a non-failing launch, plus a proven non-blank frame (GUI) or a reproduced declared CLI scenario with matching stdout/stderr hash (headless). No Windows-oracle comparison is available here: PARTIAL means render or render-free process-level evidence, not gameplay acceptance.
