# Structured, fail-closed refusals

`tools/autorecomp/refusals.py` turns the refusal evidence already present in an
autorecomp project into **one JSON document with named causes and counts**.
It is a reader, not a runner: it never rebuilds, launches the guest, or edits
the project. It answers one question — *may this project be presented as
accepted?* — and it answers conservatively.

## Why this exists

A generated project can look complete while still containing:

- a lift stub emitted as `/* ERROR lifting sub_00401000: ... */` (the lifter
  failed and the C body aborts when called);
- imports the loader could not bind (`[pe] unresolved import mod!fn` in a
  stderr log or `unresolved_imports` in `reports/run.json`);
- no native binary at all (build skipped, failed or cleaned).

Today each of those lives in a different artifact and a consumer has to know
three file formats to notice them. The refusal report gives CI, the GUI and
reviewers a single typed answer, with source context for every sighting.

## Typed causes

The taxonomy is a closed set (`RefusalCause` in the module). A member name is
a stable wire value; an unknown name must never degrade to acceptance.

| Cause | Read from | `count` means |
|---|---|---|
| `LIFT_ERROR` | `src/recomp/gen/recomp_*.c` markers; `UNRESOLVED` rows in `reports/functions.json` | distinct guest addresses (normalised to `0x%08X`), falling back to the emitted symbol |
| `UNRESOLVED_IMPORT` | `logs/**/*.log` loader lines; `unresolved_imports` fields in `reports/**/*.json` | distinct import names; if the loader printed a count but no names, the count it printed |
| `MISSING_NATIVE_BINARY` | `manifest.json` `binary.path`, plus conventional `build/<name>`, `.exe`, `.wasm` paths | `1` when no candidate file exists, otherwise `0` |

Each cause entry carries `count` (distinct items), `occurrences` (raw
sightings), `sources` (project-relative files) and `items`. Each item keeps its
own `key`, evidence fields, `source` and `line` where a line exists. A repeated
sighting is merged into one item with an `occurrences` counter; when a later
sighting has source context and an earlier one does not, the richer item wins.

Loader/runtime aggregate lines (`bound N imports, M unresolved` and
`booted: entry 0x..., M unresolved imports`) are **not** added to the named
count. They are recorded under `reported_unresolved` and in `aggregates`, so a
log that names two imports out of 86 counts as two named causes, not three.

## Fail-closed rule

```
REFUSED  if any cause count > 0, or any discovered source could not be read,
         or the project directory does not exist
ACCEPTED only when the project directory exists, all discovered sources were
         read, and all three counts are zero
```

Sources are optional: a source-only project has no run log, and that is not an
error. What is *not* optional is an artifact we found but could not read — a
permission failure is a gap in the evidence, so it sets `error` and refuses.
A missing project directory also sets `error` and refuses rather than throwing
or returning an empty success. Every report has `fail_closed: true` and an
explicit `refused` boolean; the CLI exits `1` for `REFUSED` and `0` for
`ACCEPTED`.

## Schema

Top level: `schema`, `project`, `verdict`, `fail_closed`, `refused`, `counts`,
`causes`, `total`, `reported_unresolved`, `sources_scanned`, `native_binary`,
`aggregates`, `error`. The schema string is `gamerecomp.refusals.v1`.

Abridged example from a project with two lift failures, two named unresolved
imports and no binary:

```json
{
  "schema": "gamerecomp.refusals.v1",
  "project": "/work/projects/tf",
  "verdict": "REFUSED",
  "fail_closed": true,
  "refused": true,
  "counts": {
    "LIFT_ERROR": 2,
    "UNRESOLVED_IMPORT": 2,
    "MISSING_NATIVE_BINARY": 1
  },
  "total": 5,
  "reported_unresolved": 2,
  "causes": {
    "LIFT_ERROR": {
      "cause": "LIFT_ERROR", "count": 2, "occurrences": 4,
      "sources": ["src/recomp/gen/recomp_0000.c", "reports/functions.json"],
      "items": [
        {"key": "0x00401000", "address": "0x00401000",
         "symbol": "sub_00401000", "message": "unsupported opcode 0x0F",
         "source": "src/recomp/gen/recomp_0000.c", "line": 1,
         "occurrences": 2}
      ]
    },
    "UNRESOLVED_IMPORT": {
      "cause": "UNRESOLVED_IMPORT", "count": 2, "occurrences": 3,
      "sources": ["logs/run.stderr.log", "reports/run.json"],
      "items": [
        {"key": "bulletml.dll!_BulletMLRunner_new_parser",
         "import": "bulletml.dll!_BulletMLRunner_new_parser",
         "source": "logs/run.stderr.log", "line": 2, "occurrences": 2}
      ]
    },
    "MISSING_NATIVE_BINARY": {
      "cause": "MISSING_NATIVE_BINARY", "count": 1, "occurrences": 1,
      "sources": ["manifest.json"],
      "items": [
        {"key": "native-binary",
         "reason": "no native binary exists for this project",
         "checked": ["/work/projects/tf/build/tf", "..."],
         "manifest_recorded": "/work/projects/tf/build/tf",
         "source": "manifest.json"}
      ]
    }
  },
  "error": null
}
```

## The Stela pattern (and what was not copied)

Stela is GPL-2.0-or-later. Only its *design notes* in
`docs/stela-audit/s1-architecture.md` and `docs/stela-audit/s2-ir-lifting.md`
were consulted; no Stela source, identifier payload or code was copied. This
module is original Python.

| Stela idea (audit notes) | Here |
|---|---|
| `UnsupportedReason` is a closed enum of typed reasons | `RefusalCause` is a closed enum of three named causes |
| Unsupported semantics carry guest context (`address`, `encoding`) | `Refusal` carries `key`, `detail`, project-relative `source` and `line` |
| No interpreter fallback: an unsupported operation is a compile error | No "partial accept": any cause or evidence gap yields `REFUSED`, exit 1 |
| Error conversion preserves instruction context | Aggregation preserves source context per item, even across duplicate keys |
| Fail-closed publication: never publish a cache entry for unsupported semantics | No binary or unreadable source refuses; absence of evidence is not success |

## Usage

```sh
# Human-readable JSON on stdout; optional copy on disk; exit 1 on refusal.
python3 tools/autorecomp/refusals.py projects/wf --json reports/refusals.json
echo "refusal gate: $?"
```

```python
from pathlib import Path
from tools.autorecomp.refusals import collect_refusals

report = collect_refusals(Path("projects/wf"))
if report["refused"]:
    raise SystemExit(f"refused: {report['counts']}")
```

There is deliberately no `pipeline.py` integration: this is an independent,
read-only auditor that can run before or after any pipeline version.

## Limits

- Parsers match the strings emitted today. If the translator changes its
  marker shape or the runtime its loader text, the regexes and tests here must
  change with it; a silent format drift would under-report.
- `reports/functions.json` `lift_errors` is an integer summary and is not used
  as a count — addresses from generated C and `UNRESOLVED` rows are
  authoritative. If only the integer survives, lift failures are undercounted.
- Named imports come from strict boot logs and JSON reports. Permissive-mode
  `[win32hle] FATAL: unimplemented import ...` lines are a different signal
  and are not parsed here (see `tools/autorecomp/diagnose.py`).
- Scans are limited to `logs/**/*.log` and `reports/**/*.json` and the standard
  generated-source path; evidence written elsewhere is not seen.
- The binary check is existence-only. It does not verify the manifest source
  digest, architecture, or that the binary still runs.
- `reported_unresolved` is the maximum aggregate any log/report printed. It
  may come from a different launch than the named imports, so it is reported
  separately rather than merged into `count`.
- The report is static evidence, not a behavioural claim: `ACCEPTED` means *no
  refusal cause was observed in the scanned artifacts*, not that the game was
  verified playable.
