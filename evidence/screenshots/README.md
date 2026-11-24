# Native frame evidence

Canonical store of captured frames from recompiled macOS ARM64 builds. No
game binaries or proprietary assets are stored here. Each file is a PNG
converted from the runtime's uncompressed BMP capture; `tools/proof_checker.py`
verifies these files exist and are non-empty.

| File | Source | Status |
|---|---|---|
| `cavestory-native-01.png` | Cave Story `.app` launcher, frame 0001 | non-blank, 640x480, 5 colours |
| `cavestory-native-02.png` | Cave Story `.app` launcher, frame 0002 | non-blank, identical scene to 01 |
| `cavestory-binary-01.png` | Cave Story raw native binary, frame 0001 | non-blank, 640x480, 3 colours |
| `tetris-oucc-new-01.png` | OUCC Tetris 1.3 (fresh download), frame 0001 | non-blank, 310x418, 4 colours |
| `tetris-oucc-new-02.png` | OUCC Tetris 1.3, frame 0002 | non-blank, 310x418, 5 colours |

Hashes and exact commands: `docs/NATIVE_APP_EVIDENCE.md`,
`reports/frames/cavestory/frame-proof.json`,
`reports/frames/tetris-new/frame-proof.json`.

These are render-evidence only — not Windows-oracle or gameplay equivalence.
