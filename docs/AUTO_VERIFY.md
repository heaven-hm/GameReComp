# Automated dual-engine verification

`python3 -m tools.auto_verify` runs the same fail-closed checks over real Windows binaries:

1. **Unicorn** — original x86/x86-64 bytes under controlled entry states.
2. **Frida** — original process under Highball's Wine engine, counting selected calls. Live attach is opt-in (`--live-frida`).
3. **Native replay** — byte-identical stdin to the original under Wine and to the recompiled native binary.
4. **Frame capture** — optional, via `game_runner` + `frame_proof`.

```sh
python3 -m tools.auto_verify \
  --exe ~/Downloads/recomp-apps/windows/x86/apps/arith-calc.exe \
  --native arith-calc=./build/arith_calc \
  --stdin-file tests/fixtures/real-input.bin \
  --hook ExitProcess --hook WriteFile \
  --timeout 10 --out reports/auto-verify
```

Output: one JSON per binary plus `summary.json` and `report.md` in `--out`.

Statuses are **PASS / DIVERGED / BLOCKED**. A missing engine or input is BLOCKED with the exact reason; nothing is promoted to PASS without recorded evidence. This is process/CPU-level evidence — not gameplay equivalence — unless frame evidence is attached and verified.

Run the tests with `python3 -m pytest tests/test_auto_verify.py -q`.
