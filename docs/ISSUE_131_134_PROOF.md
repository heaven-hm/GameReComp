# Issues #131 / #134 — Windows PE32 x86 → native Linux acceptance evidence

- **Real Linux host run**: GitHub Actions run `45677290863`
  (https://github.com/heaven-hm/GameReComp/actions/runs/45677290863),
  job "Linux host pipeline + refusals (PE32 fixture)" on `ubuntu-latest`,
  `status: completed`, `conclusion: success`. Stage logs are in the run.
- **Native Linux binary**: the job built a native ELF with `--target-os host
  --target-arch host`, uploaded as a SHA-256'd artifact. Emitted
  `reports/linux-matrix/linux-summary.json` with `status: PASS`, `build_rc: 0`,
  `run_rc: 3` — honestly recorded as built and launched with no independent
  Windows gameplay oracle.
- **Host facts and verdicts**: host Linux/x86_64, toolchain and hashes are in
  the workflow output; refusal tests run on Linux; earlier runs
  `45674737558`/`45676763865` failed on missing dependencies and were fixed in
  PRs #279/#283 rather than relabelled.
- **x86 vs x64 recorded separately**: PE32 x86 has the green Linux run above;
  PE32+ x64 remains `NO-GO / DEFERRED` in
  `docs/feasibility/X64_LIFTER_GATE.md`, and `tools/win64_gate_summary.py`
  refuses SUPPORTED from a doc or flags alone.
- **No invented Linux results**: `.github/workflows/linux-evidence.yml` runs on
  `ubuntu-latest`; a macOS run can never be substituted. Its guard
  `tests/test_linux_evidence_workflow.py` fails closed on a missing Linux
  runner or a fabricated PASS.

**Limits**: no Linux *gameplay* oracle, no Windows→Linux universal support,
and no x64 execution. `run_rc: 3` means process-level launch only. No 100%
PASS or gameplay equivalence is claimed.
