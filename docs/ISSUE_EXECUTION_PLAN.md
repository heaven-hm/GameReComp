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
| 11 | Controllers / input | IN_PROGRESS | `feature/upstream-issue-11`, `feature/p10-igi-input`, `feature/dinput8-abi-fix` | `8cbc16e`, `460d631`, `c31d82f`, `8f9ef78` | DInput error codes + DIMOUSESTATE2 fixed; synthetic input fixture; selftests ok | OPEN |
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
| 22 | TrackMania Nations ESWC | FAIL (native execution) | `feature/p10-tmnf-eswc` | `48b85c41…` | native build PASS after disasm guard; strict boot exit 2 (64 unresolved), permissive SIGBUS; no frames; evidence in `docs/GAME_TM_ESWC_EVIDENCE.md` | OPEN |
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

## Integration checkpoint 2 (`feature/integration-checkpoint`)

- Branch pushed and verified at `8f9ef7827ec69a01b99360c47e45ab534e1e2974`.
- Merged: agent2 runtime, matrix tests, provenance/audit docs, mmioOpenW, DInput constants.
- `python3 -m pytest tests/ -q` → 173 passed, 2 skipped, 26 subtests passed.
- `bash runtime/win32hle/build_selftests.sh` → 15 selftests ok.
- GUI repair `feature/gui-repair` at `cfaaf36b…`: cargo PASS, cmake PASS,
  `verify.sh --fast` PASS, `regress --tier fast` 8 checks PASS.
- mmioOpenW `feature/mmio-openw` at `23aad3c…`: real on-disk RIFF fixture.
- No issue was closed: remaining acceptance criteria (live gameplay per game,
  Linux target, GUI agent integration) are not yet verified.

## Integration checkpoint 3 (`feature/integration-checkpoint`)

- Branch pushed and verified at `da1e0e46e5dc1a66b9e37ae2a2f2cc4c74a65553`.
- Engine branch `feature/engine-lifter` at `da1e0e4` implements CPUID on
  non-MSVC hosts with a register/mask selftest (`cpuid_selftest.c`).
- `python3 -m pytest tests/ -q` → 173 passed, 2 skipped, 26 subtests passed.
- `bash runtime/win32hle/build_selftests.sh` → 18 selftests ok.
- Unsupported instruction scan on IGI/Cave Story/Noiz2sa found 16 rare
  mnemonics (das, iretd, into, bound, aad, arpl, aas, int, salc, daa, insb,
  insd, fldenv, aaa, sti, lcall); most are in data regions, and none are
  claimed as supported. They remain on the unsupported list.
- Still no issue closure: game acceptance, Linux target and original-Windows
  oracle evidence remain incomplete.

## Integration checkpoint 4 (`feature/integration-checkpoint`)

- Branch pushed and verified at `a3a054cdf9706611f028065de13eee8e54405800`.
- `feature/api-coverage` (`3885dab`, `a3a054c`): machine-readable PE32 import
  auditor keyed by input SHA-256 (`tools/api_coverage.py`), 4 auditor tests,
  `docs/WIN32_API_COVERAGE.md`, implemented `GetFullPathNameW` and
  `InitializeCriticalSectionEx` with guest ABI selftests.
- `python3 -m pytest tests/ -q` → 177 passed, 2 skipped, 26 subtests passed.
- `bash runtime/win32hle/build_selftests.sh` → 20 selftests ok.
- Auditor on 19 game PE32s found 322 missing distinct imports overall (mostly
  game-owned DLLs/C++ runtime); 22 are Win32 system APIs, listed in the report.
- No issue closed: behavioural correctness and game acceptance remain open.

## Integration checkpoint 5 (`feature/integration-checkpoint`)

- Branch pushed and verified at `51731c28984afb0198c82e0ca75c3327e7f7767d`.
- Threading branch `feature/threading-stress` (`51731c2`) merged agent 27
  (thread/sync semantics) and agent 33 (waitable timers), and adds a 200-thread
  mutex-contention fixture: 200 threads x 40 increments = 8000 exactly, with
  correct exit codes, five consecutive deterministic runs.
- Architecture: guest threads are host pthreads serialised by the win32hle
  machine lock; waits use `hle_block_begin`/`hle_block_end`; handles are typed
  guest-facing integers, never host pthread structures.
- `python3 -m pytest tests/ -q` → 177 passed, 2 skipped, 26 subtests passed.
- `bash runtime/win32hle/build_selftests.sh` → 23 selftests ok.
- No game-level thread-safety claim: no original-game deterministic scenario
  was executed; Linux host testing remains unavailable.

## Integration checkpoint 6 (`feature/integration-checkpoint`)

- Branch pushed and verified at `f73f7b474f16d64c4c81d2ba8bf8fe84836da592`.
- `feature/fsreg-compat` (`f73f7b4`) merged the existing resource-bounds work
  from `feature/agent-32-resources` and adds `RegOpenKeyExW`, `RegEnumValueA/W`
  over the isolated persistent registry store with a real guest ABI selftest.
- The selftest caught and fixed an argument-index bug (type slot vs reserved slot).
- `python3 -m pytest tests/ -q` → 177 passed, 2 skipped, 26 subtests passed.
- `bash runtime/win32hle/build_selftests.sh` → 25 selftests ok.
- Linux host results remain UNTESTABLE here; no game-level registry/resource
  scenario was executed, so no issue was closed.

## Integration checkpoint 7 (`feature/integration-checkpoint`)

- Branch pushed and verified at `ab007b2c41929454b2e90d5e2bce62ec2b82642b`.
- `feature/com-abi` (`c12a339`): real per-thread COM initialisation state
  (S_OK/S_FALSE/RPC_E_CHANGED_MODE, NULL reserved, balanced uninitialise),
  with `com_init_selftest`; merged with the earlier shared IID/interface work.
- `feature/p10-tmnf-eswc` (`a909798`, `48b85c4`): disasm unmappable-extent guard
  and honest FAIL evidence for TrackMania Nations ESWC.
- `python3 -m pytest tests/ -q` → 177 passed, 2 skipped, 26 subtests passed.
- `bash runtime/win32hle/build_selftests.sh` → 27 selftests ok.
- No issue closed; COM functionality is ABI/lifecycle level only, and TM ESWC
  execution genuinely failed.
