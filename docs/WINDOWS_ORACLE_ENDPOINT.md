# Windows-side scenario capture through a CLI-supplied Highball/Wine endpoint

`tools/highball_oracle_capture.py` is the fail-closed adapter for issue #145. It
turns a **CLI-supplied** Highball/Wine endpoint into a real guest scenario run
and writes a tamper-evident `CAPTURED` record. It does not run a game on its
own, does not invent a screenshot, log, exit code or hash, and does not close
an issue.

Unlike `tools/windows_oracle.py` (which needs an HTTP host/VM endpoint and is
BLOCKED until one is supplied), this adapter uses the Highball CLI itself as the
endpoint. Every host-specific value comes from the command line: `--highball`,
`--bottle`, `--engine`, `--exe`, `--arg`, `--stdin-file`/`--stdin-text`,
`--native` and `--frame`. There are no baked-in user paths.

## Fail-closed contract

| Condition | Status | Exit | `records` | Hashes |
|---|---|---|---|---|
| Endpoint unhealthy / missing CLI path / missing exe or frame / `--scope gui-frame` without real frames | `BLOCKED` | 2 | `[]` | all null |
| Timeout, signal termination, runner error, incomplete capture, failed post-write integrity check | `FAILED` | 1 | `[]` | all null |
| Complete capture from the probed endpoint | `CAPTURED` | 0 | one entry | recomputed locally |

A blocked/failed record writes no artifact directory and no detail file. It can
never be promoted to PASS by `tools/acceptance_matrix.py`.

The tool records the **exact argv** it executed (`command` / `command_display`),
the process exit code, and raw `stdout`/`stderr`/`stdin` bytes. Every SHA-256 is
computed in this adapter from the bytes that actually arrived or from the
CLI-supplied files — never from a value reported by the endpoint. The
normalized `guest_output` strips Highball banners and Wine chatter so the guest
lines are stable; its hash is also recomputed from the persisted text.

## Scope labelling — no GUI/frame over-claiming

`--scope` is mandatory in the data model and defaults to `console-only`:

* `console-only` (default): `scope: "console-only"`, `gui_frame_evidence: false`,
  `frames: []`, `oracle: "highball-wine-console"`, and an explicit `not_claimed`
  string stating that no GUI/frame, screenshot, audio, gameplay, graphics,
  input or retail-Windows oracle evidence is claimed. **A console capture is
  never presented as frame evidence.**
* `gui-frame`: accepted only when at least one real file is passed with
  `--frame`; the bytes are hashed and persisted. Scope becomes `gui-frame`,
  `gui_frame_evidence: true`, `oracle: "highball-wine-frame"`. The record still
  states that this is Wine emulation, not a retail-Windows original.

Both oracle labels are deliberately **not** members of
`tools.acceptance_matrix.REAL_ORACLES`. A Highball/Wine capture — even one with
real frames — therefore cannot satisfy the original-Windows oracle gate, so a
row cannot be promoted to PASS from it. This is intentional: Wine emulation is
not the retail Windows host that issue #145's full acceptance requires.

`--verify RECORD` recomputes the detail-file hash, every raw/frame artifact hash
and byte count, the guest-output hash, the executable/native hashes, and the
scope invariants. Any mismatch exits non-zero and prints `TAMPERED: ...`.
Blocked and failed records are not verifiable as success.

## Injectable endpoint

`--endpoint module:attribute` (or the built-in `highball`) loads an object with:

```python
probe(request: ScenarioRequest) -> (bool, reason)
capture(request: ScenarioRequest) -> RunOutcome
```

The tests inject a fake endpoint through this interface, so no Wine process is
started during unit testing.

## Real capture actually performed

Environment: Highball CLI 0.10.22 at `/Users/haseeb-mir/.local/bin/highball`,
bottle `Games`, engine `x64-crossover26.3-r27`. Real binary:
`/Users/haseeb-mir/Downloads/recomp-apps/platforms/windows/x64/apps/arith-calc.exe`
(SHA-256 `bb67ed0c581a1a00cfce9fd324c4003a970d4869829bb472e5f91b4ee476be3c`).

Command run (exact argv from the record):

```
/Users/haseeb-mir/.local/bin/highball run -b Games -e x64-crossover26.3-r27 /Users/haseeb-mir/Downloads/recomp-apps/platforms/windows/x64/apps/arith-calc.exe
```

Result:

| Field | Value |
|---|---|
| Status / verification | `CAPTURED` / `CAPTURED` |
| Exit code | `0` (not timed out) |
| Scope | `console-only`; `gui_frame_evidence: false` |
| Guest stdout SHA-256 (raw bytes) | `f735ee773d08facf0bff3de3b172bf1c03de8483ded56a883dcc3850dbeaa341` |
| Guest stderr SHA-256 (raw bytes) | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (empty) |
| stdin SHA-256 (empty) | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| Original exe SHA-256 | `bb67ed0c581a1a00cfce9fd324c4003a970d4869829bb472e5f91b4ee476be3c` |
| Normalized guest-output SHA-256 | `b6674fd6da06bc9dd36a49a5697efdeb8af7bd354f00b9c6d77359533b572d8f` |
| Rebuilt native SHA-256 | `null` (no rebuilt native binary available at a stable path) |
| Detail evidence SHA-256 | `147cda32e840d232c8ad507790f6f8e0b582c9a6e0d381a58ea49357a831e907` |
| `--verify` | `OK` (exit 0) |

The normalized guest-output digest
`b6674fd6da06bc9dd36a49a5697efdeb8af7bd354f00b9c6d77359533b572d8f` is exactly
the value recorded in `proof/windows-oracle/README.md` and
`proof/windows-oracle/windows-vs-macos-console.json` — a real Highball guest run
reproduces the documented console output byte-for-byte. The raw stdout digest
varies between runs because Highball appends its own log-path banner; only the
normalized guest block is deterministic, and both the raw bytes and their hash
are recorded honestly.

The record, detail file and raw artifacts from this run live outside the
repository at
`/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/waveG/real-run/`
(`capture.json`, `capture-capture.json`, `capture-artifacts/`). They are not
committed: issue #145's full acceptance requires a genuine original-Windows
GUI/frame scenario with screenshots, audio hashes and a matching rebuilt native
binary, none of which this console-only Wine run supplies.

## Exact blocker for closing #145

This adapter delivers real Windows-side (Highball/Wine) scenario capture with a
labelled console-only `CAPTURED` record, but it does **not** satisfy #145's full
acceptance criterion:

1. No independent **retail-Windows** host/VM GUI endpoint is configured; Wine
   emulation is not the original Windows host.
2. No GUI/frame scenario with real screenshots and audio hashes was captured —
   the only runnable existing binary here is a console app (`arith-calc.exe`).
3. No rebuilt native binary for that PE is available at a stable path, so
   `native_sha256` is null and there is no matching-original/native pair.

Consequently the oracle labels are intentionally outside `REAL_ORACLES`, and no
row is promoted to PASS. Issue #145 should remain **OPEN** until a genuine
original-Windows endpoint capture with screenshots, audio hashes, matching
original/native hashes and deterministic input exists.

## Usage

```sh
python3 -m tools.highball_oracle_capture \
  --exe /path/to/arith-calc.exe --out out/capture.json \
  --highball /path/to/highball --bottle Games \
  --engine x64-crossover26.3-r27 \
  --stdin-text 'hello world\n' --native /path/to/rebuilt-native \
  --game-id arith-calc --scenario console-run --timeout 90

python3 -m tools.highball_oracle_capture --verify out/capture.json
```

For GUI/frame evidence, add `--scope gui-frame --frame /path/to/frame.png`
(repeatable). Requesting `gui-frame` without real `--frame` files is BLOCKED.
