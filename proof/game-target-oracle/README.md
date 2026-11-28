# Game-target oracle attempt artifacts

These are honest **PARTIAL** attempt records, not passes and not gameplay
equivalence evidence. See `docs/GAME_TARGET_ORACLE_ATTEMPT.md`.

| File | Meaning |
|---|---|
| `igi-issue15-attempt.json` | Verdict record for the issue #15 IGI original run under Highball/Wine. `PARTIAL`. |
| `igi-issue15-attempt-evidence.json` | Detail claims (command, hashes, frame analyses, witness). |
| `igi-issue15-attempt-artifacts/original/stdout.raw`, `stderr.raw` | Exact process output bytes from the run. |
| `igi-issue15-attempt-artifacts/original/original-frame-000.png`, `-001.png` | Real CUA desktop captures; visually Finder/desktop windows, **not** IGI gameplay. |
| `tmnf-issue23-attempt.json` | Verdict record for the issue #23 TrackMania Nations Forever original run. `PARTIAL`. |
| `tmnf-issue23-attempt-evidence.json` | Detail claims for the TMNF run. |
| `tmnf-issue23-attempt-artifacts/original/*` | Exact output bytes and real CUA desktop captures. |

Every hash in the records is SHA-256 recomputed from the bytes on disk by
`tools.game_target_oracle_run.verify_record`; `--verify` reports `OK` for both
records (exit status 1 because both are non-PASS).

No original game executable, DLL, asset, trace or game screenshot is committed
here. The local originals were supplied by CLI path and only their SHA-256 was
recorded. No native recompiled binary, deterministic replay, frame similarity
or original-vs-native comparison exists in these artifacts, so none of issues
#15–#24 is closed by them.
