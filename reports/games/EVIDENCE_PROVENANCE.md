# Provenance of the proof files cited by the game matrix

Agent P5's matrix audit (branch `agent-p5/matrix-accuracy`) did **not** run new
binaries and did **not** manufacture evidence. It copied verbatim proof files
that are already committed elsewhere in this repository's own history, so the
matrix on `develop` can cite files that are actually in the tree. `projects/`
is git-ignored, so a `projects/<name>/reports/report.html` path is a pointer to
a local build, **not** a committed proof file.

| Imported path (this branch) | Source commit / branch | What it proves |
| --- | --- | --- |
| `reports/frames/cavestory/frame-proof.json`, `docs/screenshots/native/cavestory-*.png` | `a58681b`, `667e473` on `feature/allissues-8-matrix` | 2/3 non-blank native frames for `Doukutsu.exe`; render evidence only |
| `reports/frames/tetris-new/frame-proof.json`, `docs/screenshots/native/tetris-oucc-new-*.png` | `667e473` on `feature/allissues-8-matrix` | 2/2 non-blank frames for freshly downloaded OUCC Tetris 1.3; render evidence only |
| `reports/frames/chocolate-doom/evidence.json` | `a4f2008` | Chocolate Doom 1.3.0 hash `d1b9dabc…`, 166 EXE + 34 SDL_net imports, 0 unresolved, **no WAD / no frames** |
| `reports/acceptance/{base64,arith-calc}-oracle-pass.{md,json}`, `reports/oracle/{base64,arith-calc}-*.json` | `bbb9dbb`, `3eac3a9` | process-level Wine oracle PASS, score 100, scope is CLI output only; this merge keeps the develop `.md` evidence paths, the `.json` files are also tracked |
| `reports/acceptance/busybox-oracle-pass.json`, `reports/oracle-sweep/busybox-acceptance.json` | `491ba42` | process-level Wine oracle PASS, score 100; the develop row cites `reports/oracle-sweep/busybox-acceptance.json` |
| `reports/oracle-sweep/batch1/{tictactoe,game2048,hangman_x86}-acceptance.json` | `47432da`, `d3f929d` | process-level Wine oracle PASS, score 100 for the three batch-1 games |

Files were copied with `git show <commit>:<path>`; their bytes are unchanged.
The generator's audit in `tools/gamesdb.py` and the regression test in
`tests/test_game_matrix_evidence.py` now reject any PASS/PARTIAL row whose
`evidence` does not resolve to a tracked file in this tree.

On this branch the develop game database is retained as the base. Rows whose
only proof is a git-ignored `projects/**/reports/report.html` build log were
demoted from `partial` to `not-run` with the diagnostic
`measurement not committed (local build log only): ...`; their `port_details`
and hashes are preserved. The IGI splash screenshot is not a frame proof, so
the IGI row stays NOT RUN. The additive committed frame-proof rows for Cave
Story and the downloaded OUCC Tetris, and the Chocolate Doom import audit,
remain PARTIAL with their commit-backed paths. No new game run was performed
for this merge.
