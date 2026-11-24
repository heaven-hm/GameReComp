# Proof tree

Real binaries and captured frames. No derived claims, no filler.

| `binaries/windows/cube.exe` | `7844757c04eea2fc` | 317538 | Original Windows PE32 x86 game, copied unchanged |
| `binaries/macos/cube-native` | `2e73ab3436bf627e` | 4390792 | Native macOS arm64 binary rebuilt from cube.exe by GameReComp |
| `screenshots/macos/cube-native.bmp` | `1bb345a93da8a694` | 189414 | Native macOS frame, 240x263, 4 distinct colours |
| `screenshots/macos/doconfig-frame.bmp` | `21576d0738639c10` | 793494 | Native macOS frame from DoConfig, measured non-blank |

Regenerate hashes with `shasum -a 256 <path>`. Source evidence and full logs live outside this repository in `~/Downloads/recomp-apps/` (`WINDOWS_APPS_TEST_REPORT.md`, `SIDE_BY_SIDE_EVIDENCE/`, `END_TO_END_PROOF/`).

Nothing here is labeled PASS: there is no original-Windows scenario capture on this host. PARTIAL means rendered pixels or process-level evidence only.
