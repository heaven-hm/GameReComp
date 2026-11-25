# Pure-Rust verification backend (`game-recomp-verify`)

`src/verify.rs` + `src/bin/game-recomp-verify.rs` provide a **pure-Rust,
dependency-light** verification engine. No new crates were added: SHA-256,
guest-output normalization, process execution, trace and frame comparison are
implemented with `std` (serde/serde_json only serialize the evidence). The core
comparison functions are wasm-portable; process execution is gated off on wasm.

## Why it exists

The Python engine (`tools/differential.py`, PRs #203/#212) is the research-grade
driver; this Rust backend is the small, fast, embeddable engine for CI/backend
use. Both implement the same fail-closed rules derived from igi-recomp and
IGI-RE: identical input → compare every observable → a missing oracle is
`BLOCKED`, never `PASS`.

## Commands

```sh
cargo build --release --no-default-features --features cli --bin game-recomp-verify

# Windows original under Highball vs any rebuilt target (native, ELF via container):
game-recomp-verify process ORIGINAL.exe REBUILT \
  --original-cmd 'highball run {exe}' \
  --rebuilt-cmd 'docker run --rm --network none -v ... runner /work/app /original' \
  --timeout 60 --out evidence.json

game-recomp-verify trace A.txt B.txt --tolerance 0.001 --out trace.json
game-recomp-verify frames ORIG_DIR REBUILT_DIR --out frames.json
```

`process` replaces `{exe}` in both command templates, runs both with the same
stdin, and compares exit code plus normalized stdout/stderr byte-for-byte.
Normalization strips Highball launcher lines, Wine chatter and GameReComp
runtime diagnostics while the raw text remains in the evidence. `trace` compares
watched values at equal timestamps with an absolute tolerance. `frames` hashes
BMP/PNG sets. Every report is `schema: 1` JSON with input SHA-256 values,
per-check booleans, first differing line, and a `verdict` of `pass`/`fail`/
`blocked`. Exit code is 0 only for `pass`.

## Verification performed

- `cargo test --no-default-features --features cli --lib`: **35 passed**,
  including 5 new tests (SHA-256 known vectors, launcher normalization,
  identical/different runs, missing-oracle BLOCKED, trace tolerance drift).
- Release binary built (`--no-default-features --features cli`, 1.2 MB).
- Real end-to-end case: `arith-calc.exe` under Highball vs the native aarch64
  Linux ELF in `gamerecomp/linux-runner:local` → `verdict: pass`,
  both stdout SHA-256 `b6674fd6da06bc9dd36a49a5697efdeb8af7bd354f00b9c6d77359533b572d8f`,
  exit codes equal — identical to the Python engine's result.

## Limits

Same as the Python engine: process-level parity only. A `pass` for a console
scenario does not prove GUI gameplay, audio, frame timing, or whole-game
equivalence. Game-target closure still requires an original-Windows scenario
oracle (#145–#147).
