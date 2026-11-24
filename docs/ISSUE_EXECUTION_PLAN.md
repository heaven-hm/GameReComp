# Issue execution plan (verified session state)

This tracker records only work that has been built, tested, committed and pushed
in this session. No completion percentage is estimated. GitHub issues are not
closed unless every acceptance criterion is met; currently none have been closed
by this session.

Statuses: NOT_STARTED, IN_PROGRESS, IMPLEMENTED, BUILD_VERIFIED, TEST_VERIFIED,
INTEGRATION_VERIFIED, COMPLETED, BLOCKED, DEFERRED.

| # | Title | Status | Owner / branch | Verified commit | Build / test evidence | GitHub issue |
|---|---|---|---|---|---|---|
| 1 | Master recompilation pipeline | IN_PROGRESS | `feature/upstream-issue-1` | `808863ba73161755a188d79325a2643cebf84b28` | high-level pointer-return fix; 172 passed, 2 skipped | OPEN |
| 2 | IGI / D3D7 / DirectInput | IN_PROGRESS | `feature/upstream-issue-2` | `46dc1ce` | D3D7 FVF/PSIZE fix; 14 selftests ok; 168 passed, 2 skipped | OPEN |
| 3 | D3D8 + replay | IN_PROGRESS | `feature/upstream-issue-3` | `5b3523d4d059b0adab2463d167e1ba887cea9211` | D3D8 COM frontend; 2 targeted + 172 full tests passed | OPEN |
| 4 | Provider benchmarking | NOT_STARTED | — | — | no verified work | OPEN |
| 5 | D3D9 / DirectInput8 | IN_PROGRESS (audit only) | `feature/p10-d3d9-audit`, `feature/p10-dinput8-audit` | `3c81be6`, `4df88bc` | upstream audits pushed; no runtime implementation | OPEN |
| 6 | Legacy D3D / DirectDraw / Glide | IN_PROGRESS | `feature/upstream-issue-6` | `6fb3bb44…`, `31dbf96b…` | IDirect3D legacy interface; 174 passed, 2 skipped; 14 selftests ok | OPEN |
| 7 | D3D9Ex / DXGI / D3D10–11 | NOT_STARTED | — | — | no verified work | OPEN |
| 8 | Win16 / NE | DEFERRED | — | — | roadmap requires x86 PE32 macOS+Linux first; parser dispatch fix on `feature/upstream-issue-8` (`c75c7df1…`) is independent groundwork | OPEN |
| 9 | PE32 loader / DLL / TLS / SEH / CRT | IN_PROGRESS | `feature/agent1-pe32-loader`, `feature/upstream-issue-9`, `feature/agent-26-seh` | `ee6e966591956e3bc5d3df4630ce89b911d0d633`, `d223d8de…`, `90db86e1…` | ordinal/forwarded exports, TLS callbacks, relocation bounds, SEH; 15 selftests ok, 168 passed | OPEN |
| 10 | Audio / video | IN_PROGRESS | `feature/upstream-issue-10` | `9c37a9e5cff4231032c5b78d0e59a78a22a6d643` | real waveOut PCM path; 15 selftests ok, 170 passed | OPEN |
| 11 | Controllers / input | IN_PROGRESS | `feature/upstream-issue-11`, `feature/p10-igi-input` | `8cbc16e`, `460d631`, `c31d82f` | wheel/focus fixes + synthetic input fixture; 15 selftests ok, 170 passed | OPEN |
| 12 | Networking | IN_PROGRESS | `feature/agent2-runtime`, `feature/upstream-issue-12` | `52674039798a33aa2aa992956024d99bf111c245`, `4276c1a1…` | blocking recv/accept + loopback fixture; 15 selftests ok, 168 passed, 8 fast-tier checks pass | OPEN |
| 13 | PE32+ / x64 | DEFERRED | — | — | roadmap prerequisite not met; no production x64 work | OPEN |
| 14 | Compatibility matrix / CI | IN_PROGRESS | `feature/ci-release-gate`, `feature/p10-matrix-tests` | `7015f401…`, `8529d6c` | CI matrix + evidence gate; matrix tests 5 passed/26 subtests; 175 full tests passed | OPEN |
| 15 | Project IGI | PARTIALLY VERIFIED | `feature/agent3-game-acceptance` | `0d4fbbd92963d39953d765f0ff0d4e4cc58c05e2` | native build+run, main menu screenshot with 3,713 colours; input/audio/gameplay not fully tested | OPEN |
| 16 | Hitman Codename 47 | BLOCKED | `feature/p10-hitman-research` | `90a80cee…` | upstream audit pushed; no original Windows files locally | OPEN |
| 17 | Max Payne | BLOCKED | `feature/p10-maxpayne-research` | `48d9511…` | upstream audit pushed; no original Windows files locally | OPEN |
| 18 | Max Payne 2 | BLOCKED | same as #17 | pending agent | no original Windows files locally | OPEN |
| 19 | Hitman 2 | BLOCKED | same as #16 | pending agent | no original Windows files locally | OPEN |
| 20 | Hitman Contracts | BLOCKED | same as #16 | pending agent | no original Windows files locally | OPEN |
| 21 | Hitman Blood Money | BLOCKED | same as #16 | pending agent | no original Windows files locally | OPEN |
| 22 | TrackMania Nations ESWC | IN_PROGRESS | `feature/p10-tmnf-eswc` | pending agent | Windows PE32 binary found and hashed; build in progress | OPEN |
| 23 | TrackMania Nations Forever | IN_PROGRESS | `feature/agent3-game-acceptance` | pending build | Windows PE32 binary found, SHA-256 recorded; build running in background | OPEN |
| 24 | TrackMania United Forever | BLOCKED | — | — | no original Windows files locally | OPEN |
| 25 | Multi-lifter fallback | IN_PROGRESS | `feature/upstream-issue-25` | `0ee2f988df5ba9cff5498cc61df7974cbf40db4e` | opt-in selector; 18 targeted + 188 full tests passed | OPEN |

## Verified common gates (latest)

- `bash runtime/win32hle/build_selftests.sh` → 15/15 ok on `feature/agent2-runtime`.
- `python3 -m pytest tests/ -q` → 168 passed, 2 skipped (varies slightly by branch with added tests).
- `verify.sh --fast` on `feature/ci-release-gate` → 186 passed, 2 skipped, 7 checks PASS, 1 SKIP.

## Integration checkpoints

1. All agent branches must be built and tested before merge.
2. Shared files (`host.c`, `kernel32*.c`, `win32hle.h`, `screen.c`, `dinput.c`,
   `dsound.c`) have single owners per agent; conflicts are resolved by the
   coordinator, not by overwriting.
3. Game acceptance evidence is stored in gitignored `test-results/<game>/`;
   only summaries and hashes in docs are committed.
4. #8 and #13 remain DEFERRED until x86 PE32 macOS and Linux acceptance passes.

## Coordinator checkpoint (latest)

- GUI repair branch `feature/gui-repair` at `cfaaf36b2ddadfea1324ccf42c866965c3a99a7b`: `cargo test --release` PASS, `cmake --build gui/build` PASS, `verify.sh --fast` PASS (6 checks, 1 skip), `regress --tier fast` 8 checks PASS. Build artifacts removed.
- Upstream provenance consolidation `feature/p10-provenance-docs` at `768fb9c1883f359bb6ba3d9f9580d1f6301f6a0a`.
- OpenAL audit `feature/p10-openal-audit` at `2d50432…`, Bink audit `feature/p10-bink-audit` at `91a3bc6…`.
- All listed commits were verified against `git ls-remote` by their owners.
