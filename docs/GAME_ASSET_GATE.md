# Game asset gate (issues #15-#19)

`tools/game_asset_gate.py` is a fail-closed CLI gate for the five game-target
issues that remain blocked because owned original Windows binaries and data
files are unavailable:

| Issue | Target | Ledger status |
|---|---|---|
| #15 | Project I.G.I. — independently verified native Mission 1 | OPEN (A6 menu only; input/audio/gameplay not verified) |
| #16 | Hitman: Codename 47 — PE32 multi-DLL Glacier + first mission | OPEN (A0: original binary absent) |
| #17 | Max Payne — MAX-FX D3D8, DLL lifecycle, bullet-time oracle | OPEN (A0: original binary absent) |
| #18 | Max Payne 2 — MAX-FX/D3D8 physics and mission verification | OPEN (A0: original binary absent) |
| #19 | Hitman 2: Silent Assassin — Glacier multi-DLL + D3D8/OpenGL | OPEN (A0: original binary absent) |

The gate makes the A0 blocker **measurable**: it says exactly which declared
original files are absent, unreadable or unverifiable. It is a readout tool,
not a closure mechanism. No issue is acted on by it.

## What it does and does not prove

The tool only reads a CLI-supplied JSON manifest, hashes bytes on disk and
prints a verdict. It never downloads a game, builds or launches a binary,
writes a game asset, contacts a network endpoint, edits a document or changes
an issue's state. Every path is supplied through CLI arguments; nothing about
a user, host, Wine prefix, game install or key binding is baked in.

A **PASS** means only:

1. the manifest names one of issues #15-#19, a platform, an executable and at
   least one required data file;
2. the executable and every required data file exist as regular files;
3. every declared SHA-256 is a real 64-hex digest and equals the digest
   recomputed from that exact file;
4. a scenario evidence file was supplied, exists, is non-empty and is a JSON
   object, and any declared evidence digest also matches.

A PASS is **not** a claim that a game is playable, that gameplay, frames, audio,
input, save/load or mission parity was verified, or that any issue reached a
final status. Assets can be byte-perfect while the recompiled binary still
fails to boot. The scope string in every report states this limit explicitly.

## Verdicts and exit codes

| Verdict | Exit | Meaning |
|---|---|---|
| `PASS` | 0 | All declared checks above succeeded. |
| `NOT TESTED` | 1 | A file was present but its declared hash was malformed or disagreed with the recomputed hash, a declared path was duplicated, or the evidence was not a JSON object. Integrity was not established. |
| `BLOCKED` | 2 | A required file, hash declaration, platform, issue, required-file list or scenario evidence file was missing/unreadable. |

BLOCKED outranks NOT TESTED so a partial check can never look better than a
complete one. The report always has `issue_closed: false`; verdict vocabulary
is limited to PASS / BLOCKED / NOT TESTED and no closure claim is emitted.

Each report carries `missing[]` (exact paths and reasons), `mismatched[]`
(declared vs recomputed digests), per-asset statuses, `scenario_evidence`, and
`extra_files`. Extra files are informational: they are never fused into the
declared set, so a matching hash in an undeclared file cannot satisfy a
declared path that is absent.

## Manifest

```json
{
  "schema": 1,
  "issue": 15,
  "game": "Project I.G.I.",
  "platform": "windows",
  "executable": {"path": "IGI.EXE", "sha256": "<real 64-hex SHA-256>"},
  "required_files": [
    {"path": "DATA/LEVEL1.WAD", "sha256": "<real 64-hex SHA-256>"}
  ],
  "scenario_evidence": "/path/to/scenario-evidence.json",
  "scenario_evidence_sha256": "<real 64-hex SHA-256>"
}
```

* `issue` / `issues` must resolve to one or more of 15-19; other numbers are
  out of scope and block the gate.
* `scenario_evidence` may also be `{"path": ..., "sha256": ...}`. The optional
  `scenario_evidence_sha256` is verified when present; absence of the digest is
  not a block, but absence of the **file** is.
* Relative paths resolve against `--assets-root` when supplied, otherwise
  against the manifest's own directory. Absolute paths are used as given.
* Digests are never invented here. A declared digest is only ever compared to
  a digest recomputed from the bytes the caller supplies.

## Usage

```sh
# Complete bundle: assets live beside the manifest.
python3 -m tools.game_asset_gate --manifest /path/to/manifest.json \
  --out /path/to/verdict.json

# Assets in a separate directory; evidence supplied independently of the manifest.
python3 -m tools.game_asset_gate --manifest /path/to/manifest.json \
  --assets-root /path/to/original-game-files \
  --scenario-evidence /path/to/scenario-evidence.json \
  --out /path/to/verdict.json
```

Reasons print to stderr; the one-line verdict prints to stdout. The process
exit code is the verdict code, so a written `PASS` in a manifest can never turn
a rejected bundle into a success.

## Tests

`tests/test_game_asset_gate.py` builds synthetic manifests and tiny fake asset
files under `tmp_path`. It covers every in-scope issue, missing/malformed
manifests, missing and unreadable files, missing/malformed/mismatched hashes,
missing/empty/non-JSON evidence, the CLI evidence override, extra files that
must not rescue a missing required file, duplicate declarations, deterministic
reports, CLI exit codes, absence of hard-coded paths and absence of closure
keywords. No real game asset is required.

```sh
python3 -m pytest tests/test_game_asset_gate.py -q
```
