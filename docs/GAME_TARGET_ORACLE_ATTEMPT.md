# Game-target original-vs-native oracle attempt (issues #15–#24)

## What was built

`tools/game_target_oracle_run.py` is a fail-closed CLI that, from **CLI-supplied
paths only**, drives one original Windows game under Highball/Wine and one
native recompiled binary, captures real screenshot frames (CUA
`get_desktop_state`, macOS `screencapture`, or `RECOMP_SCREENSHOT`), and emits
`PASS` / `PARTIAL` / `BLOCKED` with exact reason codes.

`PASS` is only possible when **all** of these hold from bytes on disk:

1. both sides executed and produced real, non-blank decoded frames;
2. a game window was independently observed in the CUA accessibility tree and
   brought to the foreground for each side (a process or desktop pixels alone
   are explicitly not enough);
3. the same declared deterministic input bytes were delivered to **both**
   sides, and the delivered hashes match the declared `--input-file` hash;
4. coarse 8×8 frame-layout similarity is at or above `--min-similarity`;
5. an independent re-verification (`--verify`) recomputes every SHA-256 and
   frame signature and re-derives the verdict from the bytes with no problems.

Blank/decoded-undecodable frames, missing native input, mismatched input
hashes, low similarity, unconfirmed windows, forged verdict fields and tampered
artifacts can never produce `PASS`. `matrix_promotion` is always `false` and
the oracle label is deliberately not in `acceptance_matrix.REAL_ORACLES`, so
these records cannot promote any game row to a support PASS.

`tests/test_game_target_oracle_run.py`: **29 tests, all passing**. They cover
fake/unconfirmed/blank captures, missing native side, mismatch/low similarity,
forged PASS, and tampering of frames, logs, evidence files and binaries.

## Real attempts actually executed

Both runs used the real CLI with the local originals and the installed
Highball bottle; no game code, assets or binaries were committed. The tool
wrote its records and artifacts under `proof/game-target-oracle/`.

### Issue #15 — Project I.G.I. (2000)

* Command: `highball run -b Games -e x64-crossover26.3-r27 -d <IGI folder> --
  /Users/haseeb-mir/Downloads/IGI/igi.exe` (1,384,448 bytes).
* Original SHA-256: `dea34892d417715873ea61d7b2ba5556655f1ad65385104348146f9679915a01`.
* Record: `proof/game-target-oracle/igi-issue15-attempt.json`
  (evidence `igi-issue15-attempt-evidence.json`, SHA-256
  `5a7205c08992dccb93e6b6b70fa9296e0f287f65c37e6cce9ed6f74a3fbc4547`).
* Two real 3360×2100 PNG frames were captured from CUA and hashed from bytes:
  `c012831b72d1fd51c75bc4e70f8713d82565d5cf10c7cd2a088ebad9c1c8d985`
  (3,918,756 bytes, 1,664 distinct colours) and
  `1dfca65344518b1d89edbfd5df8eb24fb05eec9a5adf734c0fd05563e203b338`
  (3,919,231 bytes, 1,666 distinct colours).
* Wine process was terminated after the declared scenario window
  (`exit_code=-15`, `timed_out=true`). No native side, no input replay.
* **Verdict: PARTIAL** — reasons: `ORIGINAL_WINDOW_UNCONFIRMED`,
  `MISSING_NATIVE_INPUT`, `NO_DECLARED_INPUT`.
* Visual inspection of the captured PNG shows desktop/Finder windows (the
  `recomp-apps` folder), **not** IGI gameplay. These pixels are real capture
  bytes but are **not game evidence**; the record does not count them as a
  confirmed game window.

### Issue #23 — TrackMania Nations Forever (2008)

* Command: `highball run -b Games -e x64-crossover26.3-r27 -d <TMNF folder> --
  .../TmNationsForever/TmForever.exe`.
* Original SHA-256: `5e287e79a07cccc9f58989120fda2668d5da1086321033f162492af7c2671672`.
* Record: `proof/game-target-oracle/tmnf-issue23-attempt.json`
  (evidence `tmnf-issue23-attempt-evidence.json`, SHA-256
  `f52b80cd200fb74db36e4fc8a60c331361a60b8d8daba4b80751fccb05cf5276`).
* Two real 3360×2100 PNG frames:
  `4d055e336959467e7149f7d7a3355b65c73b5e3db7b3a8a2535b789530abda8e`
  (2,275,275 bytes, 1,639 distinct colours) and
  `c06b59a27c8272d19f1d9bf1b5d2b35048144588271aa7bb2a12657f696dda8c`
  (2,334,825 bytes, 1,634 distinct colours).
* A separate read-only CUA probe did once observe a Wine window titled
  `TrackMania Modded Forever` (pid 68912, window_id 5996). During the actual
  capture no matching window was present in the accessibility tree, so the
  witness reports `focused_window: null`, `window_title_confirmed: false`.
  That observation is **not** scenario evidence and is not used to upgrade the
  verdict.
* **Verdict: PARTIAL** — reasons: `ORIGINAL_WINDOW_UNCONFIRMED`,
  `MISSING_NATIVE_INPUT`, `NO_DECLARED_INPUT`.

`python3 -m tools.game_target_oracle_run --verify <record>` returned `OK` for
both records with verdict `PARTIAL` and exit status 1 (non-PASS). No PASS was
issued, and **no gameplay equivalence is claimed** from a build, a running
process, Wine output or desktop pixels.

## Verification status

* Targeted: `python3 -m pytest tests/test_game_target_oracle_run.py -q` →
  **29 passed**.
* Full suite: **2287 passed, 34 skipped, 130 subtests passed, 1 failed**.
  The single failure is `tools/assets/test_bin2iso.py::test` (node
  `bin2iso.js` exits 1); it also fails on the untouched base develop checkout,
  so it is pre-existing and unrelated to this change.
* No game original, DLL, asset, trace or screenshot was added to `src/`,
  `runtime/` or tracked game-asset paths.

## Exact blockers per game-target issue (all remain OPEN)

None of #15–#24 meets its own acceptance ladder; this attempt closes none of
them.

* **#15 IGI** — no recompiled native Mission 1; shared D3D7 HLE (#2) still has
  unsupported call slots; no original-vs-native mission frames/state/audio; no
  source-edit/rebuild proof. Original window not confirmed at capture time.
* **#16 Hitman: Codename 47** — no entitled original/DLL set supplied; no
  guest DLL code execution (#9); no Glacier renderer path; no training mission
  comparison; no source-mod proof.
* **#17 Max Payne** — no PC EXE/engine DLLs supplied; no D3D8 facade (#3) or
  multi-DLL loader proof; no bullet-time original-vs-native chapter.
* **#18 Max Payne 2** — independent PC binary not supplied; depends on #3/#9;
  no timed physics/bullet-time comparison or source-edit proof.
* **#19 Hitman 2: Silent Assassin** — no exact edition/DLL set; no GL/D3D8
  route proof; no mission scene comparison.
* **#20 Hitman: Contracts** — no entitled edition/DLL set; `DrawDll` renderer
  choice unmeasured; no executable-DLL graph proof; no mission comparison.
* **#21 Hitman: Blood Money** — no exact retail build/shaders; no D3D9 bridge
  (#5) proof; no mission scene comparison.
* **#22 TrackMania Nations ESWC** — retail StarForce 3.07 protection →
  `BLOCKED_BY_INPUT/PROTECTION`; no inspectable authorised build; no offline
  race comparison. Do not attempt DRM circumvention.
* **#23 TrackMania Nations Forever** — `TmForever.map` not validated against
  the installed PE; no generic map-symbol importer proof; no native race build;
  no checkpoint/trajectory/frame comparison; window not confirmed at capture.
* **#24 TrackMania United Forever** — depends on #23 passing first; distinct
  executable/data not inventoried; no two-environment fixtures; no native race
  comparison.

Common blocker for all ten issues: the acceptance ladder requires a
**playable native scenario independently compared against the matching
original edition** (deterministic input/seed, frames, state/checkpoint, audio
where material) and a provenance-backed source edit/rebuild. No recompiled
native binary for any of these titles exists in the repository, so that ladder
cannot be advanced by this tooling alone.
