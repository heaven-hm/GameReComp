# Capture-blocker records (issues #145 / #146)

These are **measured facts** supplied on the CLI to
`tools/capture_blocker_evidence.py`. They are *claims*, not verifications: the
tool recomputes digests from CLI-supplied byte files and refuses to promote a
console-only or silent capture. No byte artifact is committed here, so the tool
correctly withholds PASS for every byte-backed capability.

| Record | Measured fact | Why it cannot pass |
|---|---|---|
| `windows-console-captured.json` | Real Highball/Wine `CAPTURED` console run for `arith-calc.exe`, normalized stdout SHA-256 `b6674fd6…b572d8f` | Wine emulation, not retail Windows; `console-only`; `native_sha256: null` |
| `audio-descript-loopback.json` | Descript Loopback Recorder WAV, 254872 frames, all zero | Silent host loopback, not in-game Windows audio |
| `audio-screencapturekit.json` | ScreenCaptureKit WAV, 304064 frames, all zero | Silent host capture, not in-game Windows audio |
| `gui-cua-clicks.json` | CUA harness ran 4 of 9 scripted clicks, screenshots hashed by the harness | Incomplete click run; host GUI, not a Windows guest; byte artifacts not supplied |
| `native-unavailable.json` | No rebuilt native hash at a stable path | `native_sha256: null`, no expected native hash supplied |

`joined-report.json` is the deterministic output of:

```sh
python3 -m tools.capture_blocker_evidence \
  --record proof/capture-blockers/windows-console-captured.json \
  --record proof/capture-blockers/audio-descript-loopback.json \
  --record proof/capture-blockers/audio-screencapturekit.json \
  --record proof/capture-blockers/gui-cua-clicks.json \
  --record proof/capture-blockers/native-unavailable.json \
  --out proof/capture-blockers/joined-report.json

python3 -m tools.capture_blocker_evidence \
  --verify proof/capture-blockers/joined-report.json
```

Run both from the repository root: `--verify` replays the join from the paths
recorded in the report. See `docs/CAPTURE_BLOCKER_EVIDENCE.md` for the verdicts,
exact reason codes and the remaining blockers for #145/#146.
