# Corpus acceptance report

Generated: 2026-11-24T01:10:00+00:00  
Schema: 1  
Statuses are derived from recorded evidence only; no PASS is assigned from a build, a frame or a bare exit code alone.

## Inputs

- original:arith-calc: /Users/haseeb-mir/Downloads/recomp-apps/windows/x86/apps/arith-calc.exe (ok)
- native:arith-calc: /tmp/agent1-arith/build/arith_calc (ok)
- scenario:arith-calc: reports/oracle/arith-calc-scenario.json (ok)

## Games

### arith-calc — **PASS**

- Original: `/Users/haseeb-mir/Downloads/recomp-apps/windows/x86/apps/arith-calc.exe` SHA-256 `9b59ee613acc7b5a1512f8a5fcc5f65317f0fa79452d01728e1715b63f23d7a6` PE `PE32` arch `i386`
- Pipeline: ok verification=`scenario-pass` report=`/tmp/agent1-arith/reports/report.json` score=`100`
- Native: `/tmp/agent1-arith/build/arith_calc` SHA-256 `5a8f405376928e02a4b2ecc6488b3508295618ee0cb047f8d4d17c9852e413ed`
- Launch: executed=True exit=`0` log=`/private/tmp/agent1-acc-arith/arith-calc/run.log`
- Kind: `cli` (declared CLI scenario, complete=True)
- CLI scenario: args=[] expected_exit=0 expected_output_sha256=`0fff5157dbe2309ec769abb9ee4973321930bec4aa68da9b7278a3b9298fbf0c` source=`reports/oracle/arith-calc-scenario.json`
- CLI evidence: output_sha256=`0fff5157dbe2309ec769abb9ee4973321930bec4aa68da9b7278a3b9298fbf0c` log_exists=True exit_matches=True output_matches=True reproduced=True (render-free process-level evidence)
- Frames: runner=None joined=0 proven=0 blank=0 unproven=0
- Reasons:
  - original SHA-256 9b59ee613acc7b5a... and PE metadata recorded (i386)
  - native SHA-256 5a8f405376928e02... (1132840 bytes, source=cli)
  - native SHA-256 matches pipeline manifest (5a8f405376928e02...)
  - launch exit 0
  - CLI scenario args=[]: exit 0 vs declared 0, stdout/stderr sha256 0fff5157dbe2309e... vs declared 0fff5157dbe2309e..., log run.log
  - pipeline verification 'scenario-pass' matched
  - scope: declared CLI scenario + process-level oracle, not gameplay equivalence

## Summary

- PASS: 1; PARTIAL: 0; BUILD ONLY: 0; FAIL: 0; BLOCKED: 0

PASS requires a matching pipeline oracle verification, matching input/native hashes and a non-failing launch, plus a proven non-blank frame (GUI) or a reproduced declared CLI scenario with matching stdout/stderr hash (headless). No Windows-oracle comparison is available here: PARTIAL means render or render-free process-level evidence, not gameplay acceptance.
