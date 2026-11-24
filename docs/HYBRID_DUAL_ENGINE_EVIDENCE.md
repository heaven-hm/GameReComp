# Hybrid dual-engine evidence — arith-calc.exe (process-level MATCH)

This file records one real end-to-end run of `tools/hybrid_diff.py` in which
**both** engines were attempted and their exact outcomes attached. It is a
CPU/process-level differential result only. It is **not** a 100% compatibility
claim: Frida could not attach on this host (exact reason below), and the row is
only a MATCH because `--process-level-match` was passed explicitly with that
limitation disclosed in the report.

Machine-readable evidence: `reports/hybrid-arith-calc.json`.

## 1. Real inputs (not synthetic)

| Artifact | Path | SHA-256 |
|---|---|---|
| Original PE32 (Wine) | `~/Downloads/recomp-apps/windows/x86/apps/arith-calc.exe` | `9b59ee613acc7b5a1512f8a5fcc5f65317f0fa79452d01728e1715b63f23d7a6` |
| Native rebuild (macOS arm64) | pipeline build/arith_calc | `2107338c9df5308fd1d634f41cf222563a55c3226cf4292fa502bf1e7ef35b34` |
| Wine engine | `~/Library/Application Support/Highball/engines/x64-crossover26.3-r27/engine/bin/wine` | — |
| Wine bottle | `~/Library/Application Support/Highball/bottles/Games` | — |

The native binary was produced from the **real** original by the repository's
own pipeline (source/recover/translate/project/compile/run all `ok`, 93/100,
verification `partial`, CLI exit 3 expected for no oracle):

```sh
TMPDIR=/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode \
python3 -m tools.autorecomp \
  ~/Downloads/recomp-apps/windows/x86/apps/arith-calc.exe \
  --project-dir /private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/p6-native/arith-calc \
  --assets-dir ~/Downloads/recomp-apps/windows/x86/apps \
  --out-mode full --timeout 12 --no-table --no-progress
```

No stdin was supplied (the app is deterministic and ignores stdin): `--input ""`,
`stdin_bytes: 0`, `args: []`.

## 2. Exact hybrid command

The run used an **isolated Wine prefix** (`RECOMP_WINE_BOTTLE`), because a
shared wineserver left in a different `WINEMSYNC` mode made Wine exit 1 with no
stdout (a real environment fault observed during this investigation, not a guest
divergence). `wine_oracle.find_bottle()` and the Frida worker both honour it.

```sh
RECOMP_WINE_BOTTLE=/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/p6-wine-bottle \
python3 -m tools.hybrid_diff \
  --exe ~/Downloads/recomp-apps/windows/x86/apps/arith-calc.exe \
  --native /private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/p6-native/arith-calc/build/arith_calc \
  --function 0x4010a0 --seeds 1,2,3,4,5,6,7,8 \
  --hook 'KERNEL32.dll!WriteFile' --hook 'KERNEL32.dll!ExitProcess' \
  --live-frida --timeout 10 --process-level-match \
  --out reports/hybrid-arith-calc.json
```

The command exited 0 with `status: MATCH`,
`matched_engines: ["unicorn","native"]`,
`blocked_engines: ["frida"]`, `diverged_engines: []`.

## 3. Engine-by-engine evidence

### Unicorn — OK (original bytes, non-vacuous)

`tools/lift/difftest.run_unicorn_function` executed the original `.text` bytes at
**0x4010A0**, a real internal Fibonacci function in `arith-calc.exe` (register
and stack operands only; it does not call the unresolved IAT). Eight entry seeds
`1..8` produced seven distinct `eax` results:

```
seed 1 -> eax 0x1, 2 -> 0x1, 3 -> 0x2, 4 -> 0x3,
     5 -> 0x5, 6 -> 0x8, 7 -> 0xd, 8 -> 0x15
```

Report fields: `section: ".text"`, `named: []`, `cases: 8`,
`distinct_eax: 7`, `nonvacuous: true`. The full 8 signatures are in the JSON.

**Why the PE entry faults.** Running from `entry: 0x401B00` instead produces
`OracleFault ... UC_ERR_FETCH_UNMAPPED`: the CRT entry calls `GetStdHandle`,
`WriteFile` and `ExitProcess` through IAT slots whose file values are import
hint RVAs, not resolved addresses, so the reference machine fetches an unmapped
address. The earlier tetris report's `UC_ERR_READ_UNMAPPED` is the same class of
entry-state problem. Targeting a real leaf/internal function removes the need to
guess stack arguments or emulate imports. Regression test
`tests/test_hybrid_real_evidence.py::test_unicorn_real_fib_function_and_entry_root_cause`
asserts both sides (real function OK, entry BLOCKED/fault).

### Frida — BLOCKED (exact reason, bounded)

Live Frida was attempted with `--live-frida`. `tools/frida_probe.bounded_probe`
ran the Frida script in a child process group with a hard wall-clock deadline
(`timeout_s: 5`, observation window clamped to 3 s) and instrumented
`KERNEL32.dll!WriteFile` and `KERNEL32.dll!ExitProcess` on the Wine process.
Result:

```
status: BLOCKED
reason: NotSupportedError: unexpected error while probing dyld of target process
script_sha256: d981d7402c622f3c
killed_on_timeout: false
blocked_class: environment
```

The exact error is carried verbatim; no call counts were invented. A separate
attach attempt on a spawned `/bin/sleep` on this same host gave
`PermissionDeniedError: unable to access process with pid ... from the current
user account` (macOS taskgated, no root/SIP change was made). No Frida call
trace is claimed. The bound is regression-tested by
`test_frida_probe_is_hard_bounded_on_timeout`: on deadline expiry the worker
group is SIGKILLed and the result stays BLOCKED.

### Native replay — MATCH (captured output from both sides)

`tools/input_replay_diff.diff_one` ran the original under Highball Wine and the
pipeline-built native binary with identical (empty) stdin and no arguments:

```
original.exit_code 0, stdout_hash dc10bfee0bdb528994ae67cf9f5a8580f36447daf28b4744556ca178733fb38b
native.exit_code   0, stdout_hash dc10bfee0bdb528994ae67cf9f5a8580f36447daf28b4744556ca178733fb38b
status MATCH
```

Both captured outputs contain the same 11 lines, beginning `arith:start`,
`primes<200=46`, `fib(20)=6765`, `gcd(1071,462)=21`,
`popcount(0xF0F0F0F0)=16`, `rotl(0x12345678,8)=878082066`, ending `arith:done`.
Wine's FreeType warnings are on stderr and are not part of the compared stdout.

## 4. Claim scope

* This is a **process-level MATCH on a deterministic console app**, with a
  separate CPU-level Unicorn signal from real original bytes.
* Frida is BLOCKED for an exact, recorded environment reason; therefore no
  100% compatibility, gameplay, frame or audio claim is made.
* Default `hybrid_one` remains fail-closed: without `--process-level-match` the
  same inputs return BLOCKED. Divergence, Unicorn faults, native timeouts and
  vacuous Unicorn signatures can never be upgraded by that flag.
* `tools/proof_checker.py` reports `hybrid(arith)=MATCH` and
  `hybrid(tetris)=BLOCKED` verbatim; it never upgrades either status.

## 5. Regression tests

```sh
RECOMP_WINE_BOTTLE=<isolated-prefix> RECOMP_P6_NATIVE=<build/arith_calc> \
python3 -m unittest -v \
  tests.test_hybrid_diff tests.test_hybrid_real_evidence \
  tests.test_frida_probe tests.test_proof_checker
```

Added/updated: bounded-Frida reason preservation, opt-in/disclosed
process-level MATCH, fault/divergence/vacuity never allowlisted, committed
evidence integrity, real Unicorn function + entry root cause, real native
replay, and hard Frida timeout. Tests that need the real exe/native binary skip
when those files are absent; the committed-report test does not skip.
