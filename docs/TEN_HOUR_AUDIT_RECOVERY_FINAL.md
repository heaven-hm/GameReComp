# 10-hour audit — final recovery state

## Window and measured activity

- Window: **2026-11-23T11:09:26+0530 → 2026-11-23T21:09:26+0530**.
- 101 commits, 40 branches, 161 file-changes, 19,818 insertions, 257 deletions.
- 0 unpushed GameReComp commits in the window; 9 uncommitted files in the main checkout were pre-existing user work and were not touched.
- 20 worktrees at audit start; 19 after removing finished clean trees. 28 GitHub issues remain OPEN. 75 open PRs to `develop`.

## Auditor findings and recovery actions

| Auditor finding | Recovery action | Evidence |
|---|---|---|
| AUD2/AUD4: D3D8 and D3D9 COM frontends correct but never registered | Added both `win32hle_register(...)` calls in `runtime/win32hle/host.c`; extended `tests/test_runtime_registration.py` | 2 registration tests pass; full suite 450 passed, 11 skipped, 26 subtests |
| AUD10: SDK CI red on all `main` runs | Branch `feature/sdk-ci-fix` on `heaven-hm/GameRecomp-Recompiler`: install pytest in CI, portable C11 `dup_string` instead of POSIX `strdup`, remove 270 tracked build artifacts, avoid Rust ETXTBSY flake, define POSIX feature macros before headers in the C EINTR test | PR [#1](https://github.com/heaven-hm/GameRecomp-Recompiler/pull/1); local C/C++/Python tests pass; final CI re-run pending at report time |
| AUD5: A24 frame sink had no production caller | Re-checked merged tree: `sdl12.c:242` calls `hle_present_surface`; stale finding does not apply to merged code, but no live game frame was proven | source inspection |
| AUD3: a10 commit-title overstatement | Ledger records two Rtl shims as honest `ERROR_CALL_NOT_IMPLEMENTED`, not full unwinds | `docs/TEN_HOUR_AUDIT_RECOVERY.md` |
| AUD7/AUD8: standalone tools not wired into CI | Documented as next tasks; not silently claimed complete | next-task list |
| AUD8: raw Stela benchmark data absent from repo | Documented UNVERIFIED; no fabricated numbers | audit report |

## Git state after recovery

- GameReComp `feature/stela-port` at **`2fc64b020d0e0cc9c72123385f25b0195b6c3c6f`** (pushed, remote-verified).
- SDK `feature/sdk-ci-fix` at **`98dc945aa9509d946c718d454d6c9875a2c056b2`** (pushed with `heaven-hm`, PR #1 open).
- No force push; `develop` and SDK `main` were not modified directly.

## Honest completion status

All 28 issues remain OPEN. No 100% claim, no gameplay acceptance, no Linux runner, no original-Windows oracle. Subsystem percentages are withheld because no complete feature inventory exists; inventing denominators would violate the audit rules.

## Remaining highest-priority tasks

1. Finish SDK PR #1 CI to green (verify the final Ubuntu C job after the POSIX macro fix).
2. Merge SDK PR #1 into `main` only once all eight CI jobs pass.
3. Re-run GameReComp full combined test suite after the D3D8/D3D9 registration merge on a clean integration worktree.
4. Finish R10 benchmark branch.
5. Fix a14 harness skip (`d3d7_extra_selftest.c` cocoa-driver case).
6. Fix `dinput.c` placeholder and register `dinput8.dll` pseudo-module.
7. Wire standalone tools into `verify.sh`.
8. Commit raw benchmark JSON.
9. Collect remaining auditor reports (AUD1/AUD6/AUD9).
10. Integration/tracker worktree recreation only if needed — branches and PRs are intact.
