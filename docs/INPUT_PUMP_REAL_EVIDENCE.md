# Real input-pump evidence (issue #147)

`tools/real_input_pump_proof.py` is a fail-closed CLI for input-pump replay
evidence. It is deliberately not another pumped-in fake: no injected pump,
runner object or in-memory adapter can produce a `PASS` from it.

## Verdict contract

| verdict | exit | meaning |
| --- | --- | --- |
| `PASS` | 0 | CLI-supplied records still match the CLI-supplied native executable; two independent native runs produced byte-identical records; every event was consumed; every frame still hashes to a non-blank frame. |
| `FAIL` | 1 | Tampered hash, non-canonical record, divergent replays, unconsumed events, blank frame, path traversal or a broken protocol field. |
| `NOT_TESTED` | 2 | Synthetic/fake adapter, missing provenance, a script posing as a binary, or no real binary supplied. Never `PASS`. |

The tool **recomputes** the binary hash/size, scenario hash, each record hash,
and each frame hash and non-blank check. It never runs an injected adapter in
`verify` mode. `capture` mode runs a real native process directly; it writes
records and then calls the same verifier before claiming anything.

Evidence layout (all paths are CLI arguments; none are hard-coded):

```
out/manifest.json          provenance, scenario hash, replay hashes/paths
out/scenario.json          canonical event stream
out/replay-N/record.json   canonical per-run record
out/replay-N/frames/*.txt  captured console frames (one per consumed event)
```

Provenance must declare `adapter_kind: real_native_process`,
`capture_method: stdin_script`, the native binary's basename/SHA-256/size and
the host platform/architecture. Anything else is `NOT_TESTED`.

## Genuine capture performed for this change

A real, already-built native game binary from this host was used, not a fake
pump:

* binary: `platforms/macos/arm64/games/tetris` under
  `~/Downloads/recomp-apps/` (native Mach-O arm64 rebuild of a PE32 Tetris);
  SHA-256 `14712f460e5dc5e483493b05b7b884f6fcf9d1c96d2acbb415a045843abda32d`
* input pump: the game's real stdin read loop. The prompt
  `Controls: Choose column (0-9) to drop block, Q to quit:` is printed once
  per consumed input by the native process itself, so the consumed-event
  count is an observation of the real process, not an assertion by an adapter.
* scenario (`scenario.json`): key events `0`, `9`, `4`, `Q` at `t = 0,1,2,3`;
  canonical SHA-256 `b64182b2ca90290317e18ae2d433b285770bad5bc68a89683f42a716bd3b4002`.

Commands actually run (round 1 creates the evidence and verifies it; round 2
is an independent re-verification):

```sh
python3 -m tools.real_input_pump_proof capture \
  --binary ~/Downloads/recomp-apps/platforms/macos/arm64/games/tetris \
  --scenario <temp>/real-input-pump-evidence/scenario.json \
  --out <temp>/real-input-pump-evidence/out --prompt 'Controls:'

python3 -m tools.real_input_pump_proof verify \
  --manifest <temp>/real-input-pump-evidence/out/manifest.json \
  --binary ~/Downloads/recomp-apps/platforms/macos/arm64/games/tetris \
  --out <temp>/real-input-pump-evidence/verify
```

Both commands returned exit 0 with:

```json
{
  "binary_sha256": "14712f460e5dc5e483493b05b7b884f6fcf9d1c96d2acbb415a045843abda32d",
  "record_sha256s": [
    "4f79514d8dc444cbf0a14f73b5f591305c9e37d1e535c0d59c14d50173e03451",
    "4f79514d8dc444cbf0a14f73b5f591305c9e37d1e535c0d59c14d50173e03451"
  ],
  "replays": 2,
  "scenario_sha256": "b64182b2ca90290317e18ae2d433b285770bad5bc68a89683f42a716bd3b4002",
  "status": "PASS"
}
```

(`what_it_proves` and `not_claimed` are also emitted; the exact full JSON is
the CLI output reproduced above.) The four captured frames in `replay-0` have
SHA-256 values
`f0c69b9be2983484322207590a9a52619d6db9c2b8697a1b2b46dd33c2fbc5c4`,
`5e4ebb5bb173caecba8c27e66d8bccf305ea762a7f5b8cd71d46d61edfbf688d`,
`061dd80a6b3ad29fc373d276e5d884ba350c7e80eefaf451b8799bb01c01ad22`,
`a182ef86d239d138264dfb960e13b7df346bdb9999ba40b7266138b82f667c9e`;
`replay-1` produced the same four hashes. Each frame contains the board and
prompt, with multiple distinct non-blank symbols.

Changing only `adapter_kind` in that manifest to `fake` and re-running
`verify` returned exit 2:

```json
{
  "reason": "NotTested: adapter_kind 'fake' is not a real native process; synthetic/fake adapters are NOT TESTED",
  "status": "NOT_TESTED"
}
```

Tests in `tests/test_real_input_pump_proof.py` cover this refusal for every
fake/synthetic adapter name, tampered frame/record/scenario hashes, divergent
replays, unconsumed events, blank frames, path traversal and the absence of a
fake `PASS` claim. Those tests use hand-written records as *logic fixtures*
only; they are not evidence that a game ran.

## Scope and limits

* This is a **console** input pump (stdin key events and board frames) of a
  native rebuilt Tetris binary. It is not a Windows-original run, not Project
  I.G.I., and not A7 in-game evidence. No gameplay, graphics-parity or audio
  equivalence is claimed.
* The tool verifies records and hashes; it cannot cryptographically prove
  that a provenance header was not hand-authored. The genuine capture above
  was produced by the CLI running the real process, and the declared binary
  hash is re-checked against a CLI-supplied file.
* The evidence directory lives outside the repository (the exclusive file set
  for this task does not include evidence assets). Re-running the two commands
  above with the same binary/scenario reproduces the hashes exactly.
* Issue #147 now has a fail-closed harness plus real native-process evidence
  for a console game. If the acceptance bar requires Windows-original or
  I.G.I. A7 in-game evidence, that remains a blocker and #147 must **not** be
  closed on this evidence alone.
