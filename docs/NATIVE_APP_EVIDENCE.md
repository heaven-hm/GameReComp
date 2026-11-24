# Native macOS screenshots and app bundle — evidence

These are **native ARM64 frames captured on macOS** from a real PE32 game
recompiled by this repository. They are not Windows-oracle comparisons and do
not prove gameplay equivalence.

## What was built

| Item | Path | Note |
|---|---|---|
| Original PE32 | `~/Downloads/recomp-apps/games/cavestory/Doukutsu.exe` | Cave Story, i386 PE32 |
| Native executable | `dist/macos/CaveStory-Native.app/Contents/MacOS/Doukutsu` | 10,672,536 bytes, built by `./GameReComp ... --project-dir /tmp/agent2-cavestory` |
| macOS app | `dist/macos/CaveStory-Native.app` | self-contained: binary in `Contents/MacOS`, game data in `Contents/Resources`, `Info.plist` linted with `plutil` |
| Launcher | `Contents/MacOS/CaveStoryNative` | `cd` to `Contents/Resources`, then `exec` the native binary |

The embedded PE image means the app does not need the original `.exe` at run
time. The recompiled binary booted with `143 imports bound, 0 unresolved`.

## Exact commands

```sh
# 1. Recompile and build (produced the native binary embedded in the app)
./GameReComp Doukutsu.exe --project-dir /tmp/agent2-cavestory --timeout 30 --no-table

# 2. Run the packaged app's launcher, capture up to 3 frames, 20 s timeout
python3 -m tools.game_runner \
  --game cavestory-app \
  --binary dist/macos/CaveStory-Native.app/Contents/MacOS/CaveStoryNative \
  --folder dist/macos/CaveStory-Native.app/Contents/Resources \
  --out /tmp/agent2-app-frames --timeout 20

# 3. Prove the frames are not blank buffers
python3 -m tools.frame_proof --summary /tmp/agent2-app-frames/summary.json \
  --out /tmp/agent2-app-proof
```

## Recorded results

- App launcher: `exit=TIMEOUT` at 20 s — i.e. it stayed alive and rendering for
  the whole observation window; not a crash.
- Direct raw binary (same game, no app wrapper): also stayed alive 20 s and
  produced 3 frames.
- `frame_proof`: frame 0 blank (1 colour, startup clear); frames 1 and 2
  **non-blank** (5 colours for the app run, 3 for the raw binary), 640x480.
- Runtime log: `[ddraw] SetDisplayMode(640x480x16)`, primary surface created,
  `[screen] cocoa, OpenGL context`, `[dsound] dummy, 44100 Hz`.

## Screenshots added to this repository

| File | Source | SHA-256 |
|---|---|---|
| `../evidence/screenshots/cavestory-native-01.png` | app run, frame 0001 (converted from BMP with `sips`) | `794f41936012a114ac95b9e700ff4345b9ef1e165cdd446dfbc7582d4604345d` |
| `../evidence/screenshots/cavestory-native-02.png` | app run, frame 0002 | `794f41936012a114ac95b9e700ff4345b9ef1e165cdd446dfbc7582d4604345d` |
| `../evidence/screenshots/cavestory-binary-01.png` | raw binary run, frame 0001 | `7cc57071e4e81c783631c5b2f4024dd55bf1319d8f4c11f55263750dbe76a9d5` |

The two app frames are byte-identical because the scene did not change between
captures; that is recorded honestly rather than presented as two different
game states.

## Classification

**PARTIAL**: native executable built, window/rendering path initialised,
non-blank frames proven, app bundle launched and stayed alive. **Not** PASS —
no Windows original oracle, no input/gameplay verification, and no claim of
visual equivalence to the retail game. Single-colour startup frames are never
counted as graphics evidence.
