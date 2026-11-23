# Automated corpus acceptance

`tools/corpus_acceptance.py` turns CLI-supplied game paths into one
machine-readable acceptance report per corpus run. It records the evidence
that exists and refuses to promote a build, a timeout, a bare exit code or a
single frame into a pass. It owns no game data: originals, native binaries,
pipeline reports, assets, scenario files and output directories are all
arguments.

Most games are `gui` (the default) and are judged by captured frames. A
headless CLI/non-GUI Windows app (7-Zip, PuTTY and friends) never produces a
screenshot, so it could never reach PARTIAL under the frame rules. Such a game
may **explicitly opt in** with `--kind NAME=cli` (or `headless`) **and** a
declared scenario file (`--scenario NAME=PATH`). This unlocks a render-free,
*process-level* evidence path: recorded launch, exact exit code, recomputed
stdout/stderr SHA-256 and the on-disk log. A game with no declared scenario is
never guessed into this path -- it keeps the frame rules and stays
FAIL/PARTIAL there.

## What it reuses (nothing is duplicated)

| Tool | Role in the harness |
|---|---|
| `tools/corpus.inventory` | original SHA-256 + PE metadata (`_entry`), so PE parsing lives in one place |
| `tools.autorecomp` | pipeline result: consumes `reports/report.json` + `manifest.json`, or invokes the pipeline via `python3 -m tools.autorecomp` with `--run-pipeline` |
| `tools.game_runner` | launches one native binary from its game folder; records command, exit, seconds, logs and frame paths |
| `tools.frame_proof` | decodes each BMP frame: exact SHA-256, dimensions, distinct-colour count, blank flag |
| `tools.size_tier_report` | joins the two frame records and supplies the `OK`/`BLANK`/`UNPROVEN` frame marker rules |

The harness only adds: per-game configuration by name, the original/pipeline/
native provenance cross-checks, the five-status classification ladder, and a
local subprocess wrapper for declared headless CLI scenarios (game_runner and
the frame tools are left untouched and are not invoked on that path).

## Run it — declared headless CLI game

A scenario file is a JSON object declaring three fields. The expected hash is
of the **combined stdout+stderr bytes** the tool captures verbatim (it never
trusts a claimed hash without recomputing it):

```json
{
  "args": ["--version"],
  "expected_exit": 0,
  "expected_output_sha256": "<64-hex SHA-256 of combined stdout+stderr>"
}
```

```sh
python3 -m tools.corpus_acceptance \
  --out <evidence-dir> \
  --original 7z=/path/to/7z.exe \
  --native 7z=/path/to/native/7z \
  --kind 7z=cli \
  --scenario 7z=/path/to/7z-version-scenario.json \
  --assets 7z=/path/to/app/folder \
  --timeout 10 \
  --json reports/corpus/acceptance.json \
  --markdown reports/corpus/acceptance.md
```

To produce `expected_output_sha256`, run the native binary once with the same
`args` and hash its combined output (exact bytes, no normalisation), e.g.
`"<binary>" --version > captured.txt 2>&1` then `sha256sum captured.txt` (or
`shasum -a 256 captured.txt` on macOS). The value must be chosen by the caller;
the harness does not invent or bootstrap it. A scenario missing any of the
three fields is recorded `INCOMPLETE` and the game FAILs closed.

`--kind NAME=cli|headless` and `--scenario NAME=PATH` are repeatable per game.
Omitting `--kind` means `gui`; a declared scenario on a GUI game is ignored in
favour of frame evidence.

## Run it — GUI game

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
* `--kind NAME=gui|cli|headless` (repeatable) declares how a game is judged.
  Default `gui`; only `cli`/`headless` opt into render-free process evidence.
* `--scenario NAME=PATH` (repeatable) supplies the declared CLI scenario JSON
  for a headless game (args, expected exit, expected stdout/stderr hash).
* `--no-run` hashes and records binaries but does not execute them.
* `--timeout` is the per-game launch deadline; `--small-max` / `--medium-max`
  are passed through to size tiers.
* `--out`, `--json` and `--markdown` are CLI-supplied output paths. No path in
  this tool points at a particular user, machine, game or download directory.

Game names are matched across `--original`, `--native`, `--assets`,
`--pipeline-report`, `--kind` and `--scenario`; a bare path uses the file stem
as its name.

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
* `kind` — `gui` or `cli`; which evidence ladder was eligible.
* `scenario` — for headless games: `declared`, `complete`, `source` path,
  `args`, `expected_exit`, `expected_output_sha256` and validation `reasons`.
  No expectation is inferred for a game that did not declare one.
* `cli_evidence` — for headless games: `evidence_present`, `exit_matches`,
  `output_matches`, `log_exists` and `reproduced`, all evaluated against the
  recorded launch. Nothing here is invented.
* `launch` — `executed`, exact `exit_code`, seconds, command, log path,
  stdout tail and evidence directory. On the CLI path the log is the raw
  combined stdout/stderr bytes and `output_sha256` is their recomputed hash.
* `frames` — runner frame count, joined/proven/blank/unproven counts, and for
  each frame its path, SHA-256, distinct-colour count and marker, plus one
  `example` proven frame. Empty by design for a headless CLI game. No hash or
  colour count is invented for a frame the proof did not decode.
* `status` and `reasons` — the verdict and every fact used to reach it.

## Status ladder (fail-closed)

The first two rungs are shared by every game. The rest depend on `kind` and on
whether a scenario was declared.

Shared:

1. **BLOCKED** — original PE missing/unreadable/invalid, or no usable native
   binary. No launch is attempted and no pass is possible.
2. **BUILD ONLY** — a native binary exists and is hashed, but no launch was
   executed (for example `--no-run`). A build is never a pass.

`gui` (the default) — unchanged frame ladder:

3. **FAIL** — the launch ran but there is no proven non-blank frame (no frames
   captured, or every frame is `BLANK`/`UNPROVEN`).
4. **PARTIAL** — at least one proven, non-blank frame with a hash and colour
   count. This is render evidence only: no Windows oracle, gameplay, input or
   audio equivalence.
5. **PASS** — PARTIAL evidence **plus** all of the PASS gates listed below.

`cli` / `headless` **with a complete declared scenario** — render-free ladder:

3. **FAIL** — the scenario is incomplete, the launch produced no process-level
   evidence (no exit code, no recomputed stdout/stderr hash, no on-disk log), or
   the recorded run **diverges** from the declared exit/output. A missing or
   mismatched expectation is never silently accepted.
4. **PARTIAL** — the declared scenario **reproduced**: the exact declared CLI
   args were launched, the recorded exit code equals `expected_exit`, and the
   recomputed stdout/stderr SHA-256 equals `expected_output_sha256`, with the
   log present on disk. No frame is required or claimed. This is process-level,
   render-free evidence only — explicitly **not** gameplay equivalence.
5. **PASS** — reproduced-scenario PARTIAL **plus all** of the same PASS gates
   below. Because the output hash must match a declared expectation, PASS
   demands stronger evidence than a bare exit code.

PASS gates (both ladders; any missing condition downgrades to PARTIAL and is
named in `reasons` as `PASS withheld: ...`):

* a pipeline verification status of `scenario-pass` or
  `verified-playable-scope` — the only accepted (process-level) oracle;
* a pipeline input SHA-256 matching the original, no failed stage, and a native
  SHA-256 matching the pipeline manifest;
* a non-failing launch exit (`0` or `TIMEOUT`, since a game loop may
  legitimately outlive the deadline);
* GUI only: a proven non-blank frame; CLI only: a reproduced declared scenario.

A headless game with **no declared scenario** never uses the render-free
ladder: it stays under the GUI frame rules and therefore FAILs (or reaches
frame-based PARTIAL) exactly as before. A CLI-supplied native binary with no
pipeline report cannot reach PASS on either ladder, even if it hashes and
renders or reproduces its scenario; its build provenance is unproven. A bare
exit code, a `TIMEOUT`, a build or a hash alone is never a PASS. There is no
code path that writes PASS from any of those.

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

A second, no-launch discovery pass over two optional CLI-supplied directories
(`<downloads-dir>` and `<web-exes-dir>`) found 34 `.exe` files
(22 valid PE, 12 invalid bundled payloads). With no native binaries supplied,
all 34 were **BLOCKED**, including `dhewm3`, `openttd` and `prboom-plus`.
This demonstrates that the supplied web-game corpus is an optional input and
that absence of a native binary is never reported as a pass.

Full machine-readable output: `acceptance2.json`, `acceptance2.md`,
`discovery.json` and the per-game `frame-proof.json` / `run.log` / BMP frames
under the CLI-supplied smoke evidence directory (not committed).

## Limits

* **No original-Windows execution oracle exists here.** PASS is gated on the
  pipeline's process-level oracle at most; it still does not prove gameplay.
  No verdict in this report is a Windows-oracle comparison.
* **No gameplay claim.** A proven frame is render evidence; a reproduced CLI
  scenario is process-level, render-free evidence. Neither establishes input,
  audio, AI, timing or gameplay equivalence with the retail executable.
* **No 100% claim.** This harness never asserts full PE32 coverage or a 100%
  accepted corpus. It records which games have evidence and withholds PASS
  wherever an oracle or provenance is missing. "Maximize coverage" here means
  allowing more *proven* PE32 apps — including headless ones — to reach PARTIAL
  on real evidence, not relaxing the gates.
* A declared CLI scenario must use deterministic output and a caller-chosen
  expected hash. Nondeterministic output (timestamps, absolute paths) or a
  crash before output will not reproduce and correctly FAILs; the harness does
  not guess, bootstrap or invent a hash to make a game pass. A bare exit code,
  with or without an oracle, is never sufficient.
* `frame_proof` decodes uncompressed 24-bpp BMPs. A frame in another format is
  reported as a proof error, never counted as render evidence.
* A timeout means "stayed alive", not "works"; only proven frames lift a
  timed-out GUI run to PARTIAL, and a `TIMEOUT` cannot match an integer
  `expected_exit` on the CLI ladder.
* `--run-pipeline` invokes autorecomp with the full stage plan; its build can
  be slow. The normal acceptance path consumes an existing report instead.
* Live-game success is not claimed from a build or emulator result alone.

## Verification

```sh
python3 -m pytest tests/test_corpus_acceptance.py \
  tests/test_corpus_inventory.py tests/test_corpus_report.py \
  tests/test_game_runner.py tests/test_frame_proof.py \
  tests/test_size_tier_report.py -q
```
