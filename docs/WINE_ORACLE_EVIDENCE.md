# Process-level oracle via Wine — evidence and limits

`tools/wine_oracle.py` runs the **original** Windows PE32 under the locally
installed Highball/CrossOver Wine engine and writes a process-level oracle JSON
(`exit_code`, `stdout_contains`, `stderr_contains`) in the format consumed by
`tools.autorecomp --oracle`. Wine's own diagnostics (FreeType/msync/wineboot)
are filtered so they cannot become fake checks.

## Measured result: base64.exe

```sh
python3 tools/wine_oracle.py \
  --exe ~/Downloads/recomp-apps/windows/x86/apps/base64.exe \
  --out reports/oracle/base64-oracle.json --timeout 60
./GameReComp <same exe> --project-dir /tmp/agent1-base64 --no-table \
  --oracle reports/oracle/base64-oracle.json --timeout 8
python3 -m tools.corpus_acceptance \
  --original base64=<exe> --native base64=/tmp/agent1-base64/build/base64 \
  --pipeline-report base64=/tmp/agent1-base64/reports/report.json \
  --kind base64=cli --scenario base64=reports/oracle/base64-scenario.json \
  --out /tmp/agent1-acceptance-base64 --json ... --markdown ...
```

Result: original SHA-256 `d805661c60b0b99eab94ad0359ece9b91e7a79fecccc630e92ffdf217c48f879`;
native build exit 0, all 6 stdout needles matched; pipeline score **100**,
verification **`scenario-pass`**; corpus acceptance **PASS 1 / FAIL 0**. The
matrix row is `base64-cli-oracle (base64.exe) | PASS - scenario verified`.
Full evidence: `reports/acceptance/base64-oracle-pass.{json,md}`.

## What this proves and what it does not

- It proves the recompiled native binary reproduces the original binary's
  process observables (exit code and deterministic menu output) on macOS ARM64,
  with matching input SHA-256 and native hash.
- It is **not** retail-Windows execution: the oracle runs the original under
  Wine/CrossOver. It is labelled as a process-level oracle, not a Windows
  gameplay oracle.
- It is **not** gameplay equivalence for any GUI game. No frame, input or audio
  comparison was performed for this row.

The same flow was repeated for `arith-calc.exe` (original SHA-256 `9b59ee613acc7b5a1512f8a5fcc5f65317f0fa79452d01728e1715b63f23d7a6`): the native build reproduced deterministic arithmetic results (`fib(20)=6765`, `gcd(1071,462)=21`, `popcount(0xF0F0F0F0)=16`, `rotl(0x12345678,8)=878082066`), pipeline score 100 / `scenario-pass`, corpus acceptance PASS 1 / FAIL 0. Evidence: `reports/acceptance/arith-calc-oracle-pass.{json,md}`. The matrix now has two PASS rows from this method.

Consequently this does not by itself close issue #14 (coverage inventory) or
any game-acceptance issue: those require full corpus coverage and, for games,
render/gameplay evidence. Issues stay open until their own 100% criteria are met.
