# PE32 coverage (evidence-backed, fail-closed)

Recorded 2026-11-24 from a fresh, fail-closed corpus run. This document reports
what was actually measured for this project's PE32 x86 native path. It is a
status readout, not an acceptance certificate: no entry below is a gameplay or
compatibility claim.

Architecture follows PCRecomp: **PE32 x86 native is the only structurally
supported class**. Every other binary class in the table is explicitly
DEFERRED / UNSUPPORTED and is never advertised as working.

## Status ladder (keep these distinct)

| Status | Meaning | What it does **not** mean |
|---|---|---|
| **PASS** | PARTIAL evidence **plus** a real Windows oracle: matching pipeline verification (`scenario-pass` / `verified-playable-scope`), matching original and native hashes, a non-failing launch and a proven non-blank frame. | Not reachable in this environment; no original-Windows oracle exists here. |
| **PARTIAL** | Render evidence (a proven, non-blank decoded frame with hash and colour count) or recorded process evidence only. | No Windows-oracle comparison, no gameplay/input/audio equivalence. Never a PASS. |
| **BUILD ONLY** | A native binary exists and is hashed, but no launch was executed (for example `--no-run`). | A build is never a pass. |
| **FAIL** | The launch ran but produced no proven, non-blank frame (0 frames, blank/unproven frames, crash, unresolved imports, fatal exit). | A zero exit code alone (e.g. `ExitProcess(0)`) is not a pass. |
| **BLOCKED** | Original PE missing/unreadable/invalid or no usable native binary; no launch attempted. | Not a verdict on the game. |
| **NOT TESTED** | Documentation-level label for a class or case no run was ever attempted on. It is deliberately separate from FAIL and BLOCKED. | Not a PASS, not a FAIL; no evidence was produced. |

The harness `tools/corpus_acceptance` emits the first five statuses; **NOT
TESTED** is the reporting label used for classes outside any run. There is no
code path that assigns PASS from a build, a frame count, a hash or a timeout.

## Coverage class table

| Binary / feature class | Architecture status | Evidence status here | Notes |
|---|---|---|---|
| PE32 x86 native (original + rebuilt native pair) | Structurally supported | PARTIAL at best locally | The only class the pipeline reconstructs and the harness can launch. |
| PE32+ / x64 | DEFERRED / UNSUPPORTED | NOT TESTED | Refused fail-closed at `inspect` (`unsupported input architecture: x86_64`); Issue #13 gate not open. |
| .NET / CLR assemblies | UNSUPPORTED | NOT TESTED | No CLR loader or metadata path; not reconstructed. |
| Win16 / 16-bit NE | DEFERRED / UNSUPPORTED | NOT TESTED | NE lifecycle not wired end-to-end; Issue #8 gate not open. |
| D3D10 / D3D11 / DXGI | DEFERRED / UNSUPPORTED | NOT TESTED | No implementation or run evidence; Issue #7. |
| Windows services | UNSUPPORTED | NOT TESTED | No service host model in the native layer. |
| Installers / setup packages | UNSUPPORTED | NOT TESTED | Not a PE game input; never counted as a game result. |

## Fresh corpus run — 2026-11-24

Tool: `python3 -m tools.corpus_acceptance`. Eight CLI-supplied original/native
pairs were run fail-closed; the machine-readable report was written to a path
outside the repository (not committed). Result summary: **PASS 0, PARTIAL 2,
BUILD ONLY 0, FAIL 6**.

| Game pair | Status | Evidence recorded |
|---|---|---|
| `noiz2sa` | **PARTIAL** | 3 proven frames |
| `tetris` | **PARTIAL** | 3 proven frames |
| `gm-doom` | **FAIL** | exit 255 |
| `gm-p47` | **FAIL** | timeout, 0 frames |
| `gm-rr` | **FAIL** | timeout, 0 frames |
| `gm-wf` | **FAIL** | DirectDraw7 init MessageBox, then `ExitProcess(0)` |
| `rc-7zip` | **FAIL** | 20 unresolved imports |
| `rc-prboom` | **FAIL** | 102 unresolved imports |

A timeout means "stayed alive", not "works"; the two PARTIAL rows come only
from proven frames. `gm-wf` exited 0 but is still FAIL because no proven,
non-blank frame was captured. No run reached PASS, because PASS requires an
oracle that is not available here.

## Stale-evidence caveat (important)

The native binaries in that run were built **before today's merged runtime
changes**. Their unresolved-import counts are therefore **stale evidence**:
they show what those older binaries lacked, not what the current source still
lacks. They must not be read as proof that the current tree is missing those
APIs.

The current source genuinely still lacks: kernel32 wide-string APIs, winmm
MIDI, SDL2 2.x window/renderer APIs, some user32 / advapi32 / shell32 APIs,
and several MSVCRT data symbols. Other workstreams are addressing these gaps in
parallel; this document does not record any of that work as complete. New
coverage needs a fresh build and a fresh run to become evidence.

## Explicit remaining limits

* No original-Windows oracle is available. This document does not claim 100%
  compatibility, gameplay equivalence, input/audio/save-load equivalence or
  any verdict for an unsupplied binary.
* All 28 tracked issues (#1–#28) remain OPEN; none is reported as closed here.
* API-name registration is not behavioural verification; a name being present
  does not prove it matches the original DLL under every call pattern.
* DEFERRED / UNSUPPORTED classes have no local evidence at all; their status is
  NOT TESTED, never a quiet PASS.
* Live-game success is not claimed from a build, a unit test or an emulator
  result alone.

## How to regenerate this evidence

Supply every path on the command line; no user, machine, game or download
directory is hard-coded:

```sh
python3 -m tools.corpus_acceptance \
  --out <evidence-dir> \
  --original NAME=/path/to/Original.exe \
  --native NAME=/path/to/native/binary \
  --assets NAME=/path/to/game/folder \
  --pipeline-report NAME=/path/to/reports/report.json \
  --timeout 12 \
  --json <report-dir>/acceptance.json \
  --markdown <report-dir>/acceptance.md
```

Repeat `--original`, `--native`, `--assets` and `--pipeline-report` once per
pair, matching names across flags. Use freshly built natives so import evidence
is not stale. `--no-run` records BUILD ONLY instead of launching.

## Verification

```sh
python3 -m pytest tests/test_corpus_acceptance.py \
  tests/test_frame_proof.py tests/test_capability_boundaries.py -q
python3 -m pytest tests/test_acceptance_docs.py -q
```
