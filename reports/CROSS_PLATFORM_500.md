# Cross-platform sweep — 500 Windows PE32 binaries

Static Windows analysis for all 500 binaries; macOS labels come only from measured native evidence; Linux is BLOCKED for all rows because no container build completed.

| macOS status | count |
| --- | ---: |
| SUPPORTED_NOT_TESTED | 490 |
| NATIVE_FRAMES_PROOF | 4 |
| NATIVE_PASS_PROCESS | 6 |

Machine-readable result: `reports/cross-platform-500.json`.

**Not claimed**: 500 games do not run on macOS or Linux. 494 rows are "supported by architecture, not tested" — static evidence only. The 6 native PASS rows are process-level Wine-oracle results; the 2 frame rows (Doukutsu, Tetris) are PARTIAL render evidence. Linux has zero completed builds.

