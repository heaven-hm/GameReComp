# GameReComp 0.2.0 — evidence and honest status

Tag: `v0.2.0`. Build from `develop` `5d561bc99bb72de23aad01ccf4b8ce4f799447f9`.

This release ships the PCRecomp-derived PE32 x86 → host pipeline with a large,
fail-closed evidence and tooling layer. It is **not** a claim that every Windows
game or application works.

## Verified on macOS arm64 (actual commands and results)

| Command | Result |
|---|---|
| `python3 -m pytest tests/ -q` | 1105 passed, 14 skipped, 85 subtests passed |
| `python3 tools/lift/difftest.py -k paired` | 16/16 match hardware, 0 divergences |
| `python3 tests/diff/differential_gate.py` | 7/7 PASS |
| `python3 -m tools.proof_checker` | 15/15 artifacts present; hybrid(tetris)=BLOCKED, hybrid(arith)=MATCH |
| `(cd src && cargo test --all-targets)` | 35 passed, 0 failed |

Release gate: `tools/release_gate.py` requires real binaries, non-blank screenshots,
an oracle-backed PASS for every named game, completed roadmap gates and all four
verification booleans before it returns READY. It is not claimed READY here.

## What is proven

- PE32 x86 Windows executables can be inspected, recovered, translated, compiled
  into a native macOS arm64 binary and launched. Captured frames and their
  SHA-256s are in `proof/` and in `~/Downloads/recomp-apps/END_TO_END_PROOF/`.
- 50 games ran end-to-end in 10 parallel batches: every verdict is recorded with
  exact commands, hashes, exit codes, frame counts and distinct-colour counts.
  There are zero fabricated PASS rows.
- Unicorn differential checks compare lifted C against original machine code for
  real functions from those games (flags compared, 8 entry states per address).
- A hybrid Frida/Unicorn/input pipeline with adapters, evidence schema and a
  fail-closed orchestrator is included. Missing Frida/Wine/Linux yields BLOCKED
  or NOT TESTED, never a synthetic PASS.

## What is not proven

- No original-Windows controlled-input scenario was captured here, so no game
  is 100% gameplay-verified. All game rows remain PARTIAL/FAIL/BLOCKED/NOT TESTED.
- Windows → Linux and macOS → Windows/Linux directions are DEFERRED/NOT TESTED.
  `proof/OS_BRIDGE_STATUS.md` lists each direction and its exact blocker.
- PE32+ x64, .NET/CLR and Win16/NE are refused with explicit diagnostics.
- No live Linux runner exists locally; CI configuration is not a Linux result.

## Included evidence

- `proof/README.md`, `proof/INDEX.json` — real Windows original, native macOS
  binary and captured frames with hashes.
- `proof/OS_BRIDGE_STATUS.md` — measured status of every OS direction.
- `docs/PE32_COVERAGE.md`, `docs/ISSUE_STATUS.md`, `docs/HYBRID_TEST_PIPELINE.md`,
  `docs/GAME_MATRIX.md`, `docs/LICENSES.md` — scope and limits.

44 issues remain OPEN; this release does not close them.
