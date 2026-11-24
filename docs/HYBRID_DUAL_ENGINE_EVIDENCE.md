# Hybrid dual-engine evidence — arith-calc.exe (process-level MATCH)

This file records one real end-to-end run of `tools/hybrid_diff.py` in which
**both** engines were attempted and their exact outcomes attached. The ORIGINAL
Windows binary was executed with **`highball run <exe>`**, never a raw `wine`
invocation. It is a CPU/process-level differential result only. It is **not** a
100% compatibility claim: Frida could not attach on this host (exact reason
below), and the row is only a MATCH because `--process-level-match` was passed
explicitly with that limitation disclosed in the report.

Machine-readable evidence: `reports/hybrid-arith-calc.json`.

## 1. Real inputs (not synthetic)

| Artifact | Path | SHA-256 |
|---|---|---|
| Original PE32 | `~/Downloads/recomp-apps/windows/x86/apps/arith-calc.exe` | `9b59ee613acc7b5a1512f8a5fcc5f65317f0fa79452d01728e1715b63f23d7a6` |
| Native rebuild (macOS arm64) | pipeline `build/arith_calc` | `2107338c9df5308fd1d634f41cf222563a55c3226cf4292fa502bf1e7ef35b34` |
| Highball CLI | `/Users/haseeb-mir/.local/bin/highball` (0.10.22) | — |
| Highball bottle / engine / renderer | `Games` / `x64-crossover26.3-r27` / `dxvk` | — |

`highball run` owns the Wine engine, bottle, renderer (dxvk/Metal) and sync mode;
`--dry-run` for this exe shows `Command: <engine>/bin/wine <exe>` with
`WINEPREFIX=<bottle>`, `WINEMSYNC=1` and the Highball framework library path.
The user-verified reference for the original side is
`highball run igi.exe window level5` from `~/Downloads/IGI`.

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

```sh
python3 -m tools.hybrid_diff \
  --exe ~/Downloads/recomp-apps/windows/x86/apps/arith-calc.exe \
  --native /private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/p6-native/arith-calc/build/arith_calc \
  --function 0x4010a0 --seeds 1,2,3,4,5,6,7,8 \
  --hook 'KERNEL32.dll!WriteFile' --hook 'KERNEL32.dll!ExitProcess' \
  --live-frida --timeout 20 --process-level-match \
  --out reports/hybrid-arith-calc.json
```

The command exited 0 with `status: MATCH`,
`original_runner: "highball"`,
`matched_engines: ["unicorn","native"]`,
`blocked_engines: ["frida"]`, `diverged_engines: []`.
The recorded original command is exactly:

```
['/Users/haseeb-mir/.local/bin/highball', 'run',
 '/Users/haseeb-mir/Downloads/recomp-apps/windows/x86/apps/arith-calc.exe']
```

### Raw Wine fallback (never used in this run)

`wine_oracle.find_highball()` returns the CLI when installed; only when it is
absent does `run_original` fall back to `wine <C:\\...\\exe>`, and the record
then says `"runner": "wine-fallback"` and carries the raw command. Setting
`RECOMP_FORCE_WINE_FALLBACK=1` exercises that labelled path in tests. Raw Wine
without Highball's `WINEMSYNC=1` can hit a shared wineserver in a mismatched mode
and exit 1 with no stdout — a real environment fault, not a guest divergence.
This run used Highball, so that mode mismatch does not apply.

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
address. Targeting a real leaf/internal function removes the need to guess stack
arguments or emulate imports. Regression test
`test_unicorn_real_fib_function_and_entry_root_cause` asserts both sides.

### Frida — BLOCKED (exact reason, bounded)

Live Frida was attempted with `--live-frida`. `tools/frida_probe.bounded_probe`
spawned the original through `highball run` (`runner: "highball"` in the record)
in a child process group with a hard deadline (`timeout_s: 5`, observation window
clamped to 3 s), instrumenting `KERNEL32.dll!WriteFile` and
`KERNEL32.dll!ExitProcess`. Result:

```
status: BLOCKED
reason: PermissionDeniedError: unable to access process with pid 86596 from the current user account
script_sha256: d981d7402c622f3c
runner: highball
killed_on_timeout: false
blocked_class: environment
```

The exact error is carried verbatim; no call counts were invented. macOS
taskgated prevents attach without root (no SIP/root change was made). An earlier
attempt in the same session, before the Highball switch, reported
`NotSupportedError: unexpected error while probing dyld of target process` —
both are recorded honestly in the history; neither produced a trace. The bound
is regression-tested by `test_frida_probe_is_hard_bounded_on_timeout`: on
deadline expiry the worker group is SIGKILLed and the result stays BLOCKED.

### Native replay — MATCH (captured output from both sides)

`tools/input_replay_diff.diff_one` ran the original via `highball run` and the
pipeline-built native binary with identical (empty) stdin and no arguments:

```
original.runner  highball
original.exit_code 0, stdout_hash dc10bfee0bdb528994ae67cf9f5a8580f36447daf28b4744556ca178733fb38b
native.exit_code   0, stdout_hash dc10bfee0bdb528994ae67cf9f5a8580f36447daf28b4744556ca178733fb38b
status MATCH
```

Both captured outputs contain the same 11 lines, beginning `arith:start`,
`primes<200=46`, `fib(20)=6765`, `gcd(1071,462)=21`,
`popcount(0xF0F0F0F0)=16`, `rotl(0x12345678,8)=878082066`, ending `arith:done`.
Highball's own `Launching … in bottle …` / `exit=… log: …` banner lines and
Wine/FreeType warnings are runner chatter, filtered by the anchored
`RUNNER_NOISE` regex; guest output is untouched.

## 4. Claim scope

* This is a **process-level MATCH on a deterministic console app**, with a
  separate CPU-level Unicorn signal from real original bytes and the original
  side executed by Highball.
* Frida is BLOCKED for an exact, recorded environment reason; therefore no
  100% compatibility, gameplay, frame or audio claim is made.
* Default `hybrid_one` remains fail-closed: without `--process-level-match` the
  same inputs return BLOCKED. Divergence, Unicorn faults, native timeouts and
  vacuous Unicorn signatures can never be upgraded by that flag.
* `tools/proof_checker.py` reports `hybrid(arith)=MATCH` and
  `hybrid(tetris)=BLOCKED` verbatim; it never upgrades either status.

## 5. Regression tests

```sh
RECOMP_P6_NATIVE=<build/arith_calc> python3 -m unittest -v \
  tests.test_hybrid_diff tests.test_hybrid_real_evidence \
  tests.test_frida_probe tests.test_proof_checker
```

Added/updated: Highball is the primary original runner and the record names it;
raw-Wine fallback is explicitly labelled; bounded-Frida reason preservation;
opt-in/disclosed process-level MATCH; fault/divergence/vacuity never allowlisted;
committed evidence integrity; real Unicorn function + entry root cause; real
native replay; hard Frida timeout. Tests needing real artifacts skip when absent;
the committed-report test does not skip.
