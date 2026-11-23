# Automated corpus acceptance

`tools/corpus_acceptance.py` turns CLI-supplied game paths into one
machine-readable acceptance report per corpus run. It records the evidence
that exists and refuses to promote a build, a timeout or a single frame into a
pass. It owns no game data: originals, native binaries, pipeline reports,
assets and output directories are all arguments.

## What it reuses (nothing is duplicated)

| Tool | Role in the harness |
|---|---|
| `tools/corpus.inventory` | original SHA-256 + PE metadata (`_entry`), so PE parsing lives in one place |
| `tools.autorecomp` | pipeline result: consumes `reports/report.json` + `manifest.json`, or invokes the pipeline via `python3 -m tools.autorecomp` with `--run-pipeline` |
| `tools.game_runner` | launches one native binary from its game folder; records command, exit, seconds, logs and frame paths |
| `tools.frame_proof` | decodes each BMP frame: exact SHA-256, dimensions, distinct-colour count, blank flag |
| `tools.size_tier_report` | joins the two frame records and supplies the `OK`/`BLANK`/`UNPROVEN` frame marker rules |

The harness only adds: per-game configuration by name, the original/pipeline/
native provenance cross-checks, and the five-status classification ladder.

## Run it

```sh
python3 -m tools.corpus_acceptance \
  --out <evidence-dir> \
  --original Doukutsu=/path/to/Doukutsu.exe \
  --native Doukutsu=/path/to/native/Doukutsu \
  --assets Doukutsu=/path/to/game/folder \
  --pipeline-report Doukutsu=/path/to/project/reports/report.json \
  --timeout 12 \
  --json reports/corpus/acceptance.json \
  --markdown reports/corpus/acceptance.md
```

* `--original NAME=PATH` (repeatable) is the original Windows PE.
* `--games-root DIR` (repeatable) discovers `.exe` files in a supplied tree
  instead of naming them one by one. Missing roots are ignored, never guessed.
* `--native NAME=PATH` (repeatable) is an already-built native binary. If
  omitted, the path in the pipeline manifest is used.
* `--assets NAME=DIR` is the working directory for the launch; it defaults to
  the native binary's parent directory.
* `--pipeline-report NAME=PATH` (repeatable) accepts a `report.json` or its
  project directory. `--run-pipeline` invokes autorecomp for each original
  instead (its own CLI owns the stage plan).
* `--no-run` hashes and records binaries but does not execute them.
* `--timeout` is the per-game launch deadline; `--small-max` / `--medium-max`
  are passed through to size tiers.
* `--out`, `--json` and `--markdown` are CLI-supplied output paths. No path in
  this tool points at a particular user, machine, game or download directory.

Game names are matched across `--original`, `--native`, `--assets` and
`--pipeline-report`; a bare path uses the file stem as its name.

## Per-game record

Each entry in `games[]` contains:

* `original` — `path`, SHA-256, size, `architecture`, `pe_type`, `subsystem`,
  imports, image base and entry point (from the inventory tool), or an
  explicit `MISSING_INPUT` / `invalid-pe` status.
* `pipeline` — report path, stage statuses, first failed stage, score,
  `outcome` (verification status/reason), input SHA-256, manifest native
  path/hash/size, `claims_verified`, and a list of `reasons` plus the subset
  of `blocking_reasons`.
* `native` — path, independently recomputed SHA-256, size and provenance
  (`cli` or `pipeline-manifest`); a manifest hash mismatch is recorded.
* `launch` — `executed`, exact `exit_code`, seconds, command, log path,
  stdout tail and evidence directory from the runner.
* `frames` — runner frame count, joined/proven/blank/unproven counts, and for
  each frame its path, SHA-256, distinct-colour count and marker, plus one
  `example` proven frame. No hash or colour count is invented for a frame
  the proof did not decode.
* `status` and `reasons` — the verdict and every fact used to reach it.

## Status ladder (fail-closed)

1. **BLOCKED** — original PE missing/unreadable/invalid, or no usable native
   binary. No launch is attempted and no pass is possible.
2. **BUILD ONLY** — a native binary exists and is hashed, but no launch was
   executed (for example `--no-run`). A build is never a pass.
3. **FAIL** — the launch ran but there is no proven non-blank frame (no frames
   captured, or every frame is `BLANK`/`UNPROVEN`).
4. **PARTIAL** — at least one proven, non-blank frame with a hash and colour
   count. This is render evidence only: no Windows oracle, gameplay, input or
   audio equivalence.
5. **PASS** — PARTIAL evidence **plus** all of: a pipeline verification status
   of `scenario-pass` or `verified-playable-scope`, a pipeline input SHA-256
   matching the original, no failed stage, a native SHA-256 matching the
   pipeline manifest, a non-failing launch exit (`0` or `TIMEOUT`, since a game
   loop may legitimately outlive the deadline), and a non-blank frame. Any
   missing condition downgrades to PARTIAL and is named in `reasons` as
   `PASS withheld: ...`.

A CLI-supplied native binary with no pipeline report cannot reach PASS, even
if it hashes and renders; its build provenance is unproven. `TIMEOUT` alone is
never a pass. The report has no code path that writes PASS from a build, a
frame count or a hash.

## Smoke evidence (this change)

Two native binaries already present on this machine were launched for 6
seconds each from their supplied game folders. Originals and pipeline reports
came from the CLI; no path is compiled into the tool.

| Game | Original SHA-256 | Native SHA-256 | Pipeline verification | Launch | Frames (proven/colours) | Status |
|---|---|---|---|---|---|---|
| noiz2sa | `389af5a46ee800419e1596c0fae8114ecfedc650f3658907eed4371264ccd3a5` | `b1de183e6b76eebbdf72248ab7f68071b4bc27fa0ba98417ebc3516103170355` | `not-run` | `TIMEOUT` | 3/3 — 27, 29, 30 colours | **PARTIAL** |
| p47 | `41b2f77d2d7ac96821a85a38be3ee7f0f95b4e5096588e38f0c9092e224e520b` | `48780267ed0b76fedd9acf0381bdd2aa219925aaeb59e09a5db91ff376d1adca` | `not-run` | `TIMEOUT` | 0/0 | **FAIL** |

Both pipeline reports matched their originals and native hashes, but neither
carried an accepted oracle (`not-run`), so PASS was impossible by construction.
Frame hashes are per-run evidence (a live game renders different pixels each
run), so they are recorded in the JSON/frame-proof files rather than treated as
stable golden values.

A second, no-launch discovery pass over the optional directories
`/Users/haseeb-mir/Downloads/recomp-apps/games` and
`/private/var/folders/.../opencode/newgames-web/exes` found 34 `.exe` files
(22 valid PE, 12 invalid bundled payloads). With no native binaries supplied,
all 34 were **BLOCKED**, including `dhewm3`, `openttd` and `prboom-plus`.
This demonstrates that the supplied web-game corpus is an optional input and
that absence of a native binary is never reported as a pass.

Full machine-readable output: `acceptance2.json`, `acceptance2.md`,
`discovery.json` and the per-game `frame-proof.json` / `run.log` / BMP frames
under the CLI-supplied smoke evidence directory (not committed).

## Limits

* No original-Windows execution oracle exists here. PASS is gated on the
  pipeline's process-level oracle at most; it still does not prove gameplay.
* `frame_proof` decodes uncompressed 24-bpp BMPs. A frame in another format is
  reported as a proof error, never counted as render evidence.
* A timeout means "stayed alive", not "works"; only proven frames lift a
  timed-out run to PARTIAL.
* `--run-pipeline` invokes autorecomp with the full stage plan; its build can
  be slow. The normal acceptance path consumes an existing report instead.
* Live-game success is not claimed from a build or emulator result alone.

## Verification

```sh
python3 -m pytest tests/test_corpus_acceptance.py \
  tests/test_corpus_inventory.py tests/test_game_runner.py \
  tests/test_frame_proof.py tests/test_size_tier_report.py -q
```
