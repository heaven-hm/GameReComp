# Capture blocker evidence — issues #145 / #146

`tools/capture_blocker_evidence.py` joins **CLI-supplied capture records** into
one deterministic, fail-closed verdict. It does not run a game, a capture
device, Wine or a native binary. It recomputes every digest from bytes actually
supplied, re-decodes frames and WAVs, and never manufactures a PASS.

## Acceptance model

A `PASS` is emitted **only** when all four capabilities pass and share one
non-empty `capture_id` tied to the verified retail-Windows endpoint:

| Capability | Required for `PASS` |
|---|---|
| `windows-endpoint` | verified `retail-windows` host/VM; Wine/Highball/other emulation is rejected |
| `gui-frames` | real frame bytes, SHA-256 recomputed, decodable and non-blank; complete click run; never `console-only` |
| `audio` | real WAV bytes, SHA-256 recomputed, non-silent PCM; silent/all-zero is rejected |
| `native-hash` | native SHA-256 present and equal to the `--native-sha256` expectation |

Anything less is `PARTIAL` (real but incomplete evidence) or `BLOCKED` (no
evidence, or a disqualifying condition). A console-only capture and a silent
capture are **hard blockers**: they can never be promoted, regardless of any
other claim in the same record set. A capability with no record, an unreadable
record or an unknown capability is `BLOCKED`.

`--verify REPORT` replays the join from the paths recorded in the report. If a
record or the report was edited, the recomputed JSON differs and the command
prints `TAMPERED: …` and exits 1. Tests in
`tests/test_capture_blocker_evidence.py` prove that fake promotion claims
without bytes, a silent WAV, a hash mismatch, a session mismatch and tampering
are all rejected, while genuine byte-backed evidence can still reach PASS.

## Measured capture records

The records in `proof/capture-blockers/` are the real measurements supplied to
the CLI. They are claims; the tool verifies none of them by bytes because no
capture artifact is committed with this proof set.

| Record | Measured fact | Consequence |
|---|---|---|
| `windows-console-captured.json` | Highball/Wine `CAPTURED` console run for `arith-calc.exe`; normalized stdout SHA-256 `b6674fd6da06bc9dd36a49a5697efdeb8af7bd354f00b9c6d77359533b572d8f` (the value in `proof/windows-oracle/` and `docs/WINDOWS_ORACLE_ENDPOINT.md`) | `EMULATED_ENDPOINT`, `ENDPOINT_CONSOLE_SCOPE`; `native_sha256: null` |
| `audio-descript-loopback.json` | Descript Loopback Recorder WAV: **254872 frames, all zero** | `AUDIO_SILENT` (plus no hash/bytes here) |
| `audio-screencapturekit.json` | ScreenCaptureKit WAV: **304064 frames, all zero** | `AUDIO_SILENT` (plus no hash/bytes here) |
| `gui-cua-clicks.json` | CUA GUI harness ran **4 of 9** scripted clicks; four screenshots hashed by the harness | `FRAMES_INCOMPLETE`, `FRAME_NO_BYTES`; host GUI, not a Windows guest |
| `native-unavailable.json` | No rebuilt native binary at a stable path | `NATIVE_HASH_NULL` |

## Joined verdict (reproducible)

Command in `proof/capture-blockers/README.md`; result in
`proof/capture-blockers/joined-report.json`:

```
overall: PARTIAL
  windows-endpoint: BLOCKED  [EMULATED_ENDPOINT, ENDPOINT_CONSOLE_SCOPE]
  gui-frames:       PARTIAL  [FRAME_NO_BYTES, FRAMES_INCOMPLETE]
  audio:            BLOCKED  [AUDIO_NO_HASH, AUDIO_NO_BYTES, AUDIO_SILENT]
  native-hash:      BLOCKED  [NATIVE_HASH_NULL]
```

`--verify` reports `OK`. `PARTIAL` means *some real evidence exists but no
capability can pass* — it is explicitly **not** a PASS and closes nothing.

## Exact blockers for #145 — Windows-side scenario capture

1. **No retail-Windows endpoint.** The only real capture is a Highball/Wine
   process-level console run (`highball-wine-console`). Wine emulation is not
   the original Windows host, so `EMULATED_ENDPOINT` applies and the oracle
   label stays outside `REAL_ORACLES`.
2. **No GUI/frame scope.** The console capture sets `gui_frame_evidence: false`;
   it cannot satisfy a GUI scenario (`ENDPOINT_CONSOLE_SCOPE`).
3. **No matching original/native pair.** `native_sha256` is null and no
   `--native-sha256` expectation exists (`NATIVE_HASH_NULL`).
4. **No in-game audio.** Both host captures are all-zero (`AUDIO_SILENT`) and
   are not Windows-guest audio.
5. **Incomplete GUI automation.** Only 4/9 CUA clicks ran, against the host GUI
   (`FRAMES_INCOMPLETE`), with no byte artifacts supplied (`FRAME_NO_BYTES`).

To clear #145: supply a genuine retail-Windows host/VM endpoint, a complete GUI
scenario with real non-blank frame bytes and a non-silent WAV whose hashes are
recomputed here, plus a rebuilt native binary whose SHA-256 matches the
supplied expectation — all under one `capture_id`.

## Exact blockers for #146 — in-game A8 audio acceptance

1. No non-silent PCM capture exists: Descript (254872 frames) and
   ScreenCaptureKit (304064 frames) are entirely zero (`AUDIO_SILENT`).
2. No per-capture WAV bytes/SHA-256 are committed, so
   `tools.audio_capture_adapter` cannot verify a live-device capture here
   (`AUDIO_NO_HASH`, `AUDIO_NO_BYTES`).
3. The two recordings are host loopback/screen captures, not audio from a
   Windows guest running the reconstructed title.

To clear #146: provide a live-device, non-silent WAV with its SHA-256 and
matching bytes, captured from the Windows guest scenario, then let both this
join and `tools.audio_capture_adapter` verify it.

## Status

**Issues #145 and #146 remain OPEN.** This change adds evidence tooling and the
measured blocker records; it does not close, fix or mark either issue done. No
100% compatibility, gameplay equivalence, audible-output correctness or
retail-Windows PASS is claimed.
