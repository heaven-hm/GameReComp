# Advanced verification engine (`verify-engine/`)

A **self-contained, pure-Rust** differential verification backend for GameReComp.
It lives in its own folder/crate so it can be built, tested, reviewed and reused
independently of the main CLI/GUI crate.

```text
verify-engine/
├── Cargo.toml     # crate only: serde, serde_json, clap
├── README.md      # this file
└── src/
    ├── lib.rs     # engine core: SHA-256, normalization, process/trace/frame, matrix, adapters
    └── main.rs    # CLI: process | matrix | trace | frames
```

## Build and test

```sh
cd verify-engine
cargo test                       # 8 engine unit tests (38 in the main crate suite)
cargo build --release            # standalone binary: target/release/game-recomp-verify
```

The core uses only `std` for hashing and comparison (its own SHA-256, no
external crypto crate). Process execution is gated off on wasm targets, so the
comparison functions remain portable.

## What it verifies

The engine follows the differential methodology of `HenryKun55/igi-recomp`
(per-form instruction sweeps, A/B tick and snapshot comparison) and
`neonixmoddingteam/IGI-RE` (same-state comparison of return values, stack
cleanup, byte-identical memory and call order under multiple x87 control
words), reduced to generic, game-agnostic observables:

- `process` — one scenario: run original and rebuilt with identical args/stdin,
  compare exit code and normalized stdout/stderr byte-for-byte.
- `matrix` — many scenarios: each has its own hex stdin, argv, environment,
  timeout and bounded retries; input/args are SHA-256 hashed into evidence.
  Aggregation is fail-closed: any FAIL wins, otherwise any BLOCKED wins, PASS
  only if every scenario and adapter passed.
- `trace` — A/B watched-value traces, absolute tolerance per numeric column,
  exact match for non-numeric values.
- `frames` — BMP/PNG frame-set SHA-256 comparison (blank frames never count as
  graphics evidence).

### Scenario file

```json
[
  {"name":"empty","stdin_hex":"","args":[],"env":{},"timeout":12,"retries":2},
  {"name":"menu-1","stdin_hex":"310a","args":["--scenario","1"],
   "env":{"KEY":"value"},"timeout":12,"retries":2}
]
```

### Example

```sh
game-recomp-verify matrix ORIGINAL.exe REBUILT scenarios.json \
  --original-cmd 'highball run {exe}' \
  --rebuilt-cmd 'docker run --rm --network none -v ... runner /work/app /original' \
  --adapters unicorn,frida \
  --unicorn-report difftest.json \
  --frida-probe 'python3 tools/frida_probe.py --dry-run' \
  --out evidence.json
```

Highball is the primary original runner (no raw Wine invocation).

## Adapters

- **Unicorn** parses `tools/lift/difftest.py --json`. Function verdicts are
  classified as agree / oracle-faulted / disagree. `original-faulted` is
  **BLOCKED**, never counted as PASS — the original bytes could not be driven.
- **Frida** runs a supplied probe and preserves its exact error text. A
  `--dry-run` probe is explicitly BLOCKED ("no live attach evidence"); no
  synthetic traces are ever created. On this macOS host live Frida is blocked
  by SIP/dyld (`PermissionDeniedError`, `NotSupportedError`) and is recorded
  verbatim.

## Measured evidence

- 8 standalone engine unit tests pass (38 with the main-crate suite): SHA-256 vectors, launcher normalization, identical /
  different runs, missing-oracle BLOCKED, trace drift, fail-closed aggregation,
  scenario decoding, Unicorn classification).
- Real 4-input matrix (Windows `arith-calc.exe` under Highball vs a native
  aarch64 Linux ELF in `gamerecomp/linux-runner:local`): **4/4 PASS**, guest
  stdout SHA-256 `b6674fd6da06bc9dd36a49a5697efdeb8af7bd354f00b9c6d77359533b572d8f`
  on every scenario, exit codes equal.
- The same matrix with Unicorn (1 oracle fault) and Frida (dry-run) adapters:
  **aggregate BLOCKED** — the engine refuses to label a partial oracle a success.
- 100-game identical-binary stress run (engine validation, not game parity):
  see `~/Downloads/recomp-apps/ENGINE100/` for per-game evidence JSON.

## Verdict semantics and limits

`pass` means process-level parity for the supplied inputs only. It is **not**
gameplay, audio, frame-timing, or whole-game equivalence. GUI games require a
deterministic original-Windows input/frame/audio oracle (#145–#147) before any
game-level PASS. `blocked` always means missing evidence, never a soft pass.

The same engine is also compiled into the main crate (`src/verify.rs` +
`src/bin/game-recomp-verify.rs`) for the existing CLI; this folder is the
standalone source of truth for the engine.
