# Desktop GUI

**Status: not implemented, deliberately deferred.** The `autorecomp` CLI is the
only interface today. The product decision (owner, 2026-10-29) is that the GUI
comes after the CLI pipeline is demonstrated end to end — it is not built in
this phase. When it is built it is a native **C++ / Qt 6 Widgets** application,
not a Python or web front end. This page records the stack decision and the
contract the GUI must keep when it is built, so that the GUI cannot drift into
a second, differently-behaving pipeline.

The GUI is a front end over the same backend as the CLI. It does not lift,
compile, run or verify anything itself: every action maps to an `autorecomp`
invocation or to a file those invocations produced.

## Stack decision

**Qt 6 Widgets**, C++17, layered on the `autorecomp` command line.

- Licence: Qt 6 is available under LGPLv3 for dynamic linking — acceptable for
  this MIT project as long as Qt itself is not vendored and remains
  replaceable. This is recorded here so a later change to static linking or
  commercial-only modules is a deliberate licensing decision, not an accident.
- Widgets rather than QML: the application is form-and-table shaped (stage
  graph, function list, log panes, evidence tables). Widgets give native
  controls and accessibility without a separate UI runtime or GPU dependency.
- No web service and no cloud dependency. Tauri was considered and not chosen:
  it adds a webview runtime and a larger packaging surface for no benefit to a
  desktop reconstruction tool.

The GUI process must not require any proprietary network service. AI-assisted
readability is configured through `REVENG_AI_CMD` (see
[SOURCE_PROVENANCE.md](SOURCE_PROVENANCE.md)) and stays optional.

## Contract with the CLI

| GUI action | Backing invocation / file |
|---|---|
| Run the pipeline | `./RevEng <input.exe> --project-dir <dir> [--timeout N] [--oracle JSON]` |
| Resume an existing project | the same command against the existing project directory |
| Readability pass | `./RevEng readability <project>` |
| Stage progress | the `==> <stage>` markers the CLI writes to stdout/stderr |
| Stage results | `<project>/reports/run.json` |
| Function inventory and provenance | `<project>/reports/functions.json` |
| Verification status | `<project>/reports/verification.json` |
| Human-readable report | `<project>/reports/report.html` |
| Readability outcome | `<project>/reports/readability.json`, `readability.html` |
| Rebuilt executable | the path recorded in `reports/run.json` |
| Evidence hashes | `manifest.json` (input SHA-256, source digest, binary SHA-256) |

Exit codes are the CLI's, unchanged: `0` verified scenario pass, `3` ran but
unverified, `2` a stage failed. The GUI shows the code; it does not reinterpret
it.

Progress shown must come from observed stage markers and elapsed time. If the
pipeline has not reported a stage, the stage is not shown as running or
complete. No animated "processing" substitute for real progress.

## Screens

1. **Project wizard** — browse for the original `.exe`, validate that it is a
   PE and which architecture (PE32 accepted, PE32+ rejected with the same
   fail-closed message as the CLI), choose the project directory, optional
   timeout and oracle JSON. Shows the asset-ownership reminder: the tool does
   not download or redistribute game files, and derived output carries the
   original program's rights.
2. **Run dashboard** — the stage graph (inspect → recover → translate →
   project → compile → run → verify → report) drawn from observed markers,
   with counts per stage, elapsed time, exit status and a cancel button that
   terminates the child process. Log panes mirror the CLI's stdout/stderr
   streams separately, as the run report does.
3. **Source explorer** — function tree from `reports/functions.json`,
   searchable by address, name and block; each function shows its provenance
   category (`LOW_LEVEL_FALLBACK`, `UNRESOLVED`, and the reserved
   `HIGH_LEVEL_*`) and its generated C unit. Category totals come from
   `project.json`; the explorer never computes its own.
4. **Build & run** — the compile log, binary path, binary SHA-256, source
   digest and the exact build invocation, all as recorded in the run report.
5. **Verification dashboard** — the recorded status, compared observables
   (exit code, stdout/stderr assertions, fatal markers), oracle gaps and the
   artifact hashes that tie the run to the source tree.
6. **Project management** — list projects under a working directory, show each
   one's latest status from its report files, and open a project in the other
   screens. Import/export is the project directory itself; no database.

## Packaging and tests

- Desktop releases: Windows x64 and macOS ARM64, signed where practical.
- GUI smoke tests run headless with Qt's offscreen platform, driven by a
  synthetic fixture project (no game binary), asserting that the wizard
  rejects a PE32+ input and that the dashboard shows the recorded status of a
  fixture project.
- CLI parity test: for a fixture, the GUI's displayed status, binary hash and
  provenance totals equal those in `reports/`; the GUI may not hold a second
  interpretation of the run.

## Implementation status

| Piece | Status |
|---|---|
| Stack decision recorded (Qt 6 Widgets, LGPLv3 dynamic) | done — this page |
| Scheduling | deferred; CLI pipeline and verification come first |
| Project wizard | not implemented |
| Run dashboard with real stage markers and cancel | not implemented |
| Source explorer with provenance badges | not implemented |
| Build & run screen with hashes | not implemented |
| Verification dashboard | not implemented |
| Project management | not implemented |
| Headless GUI smoke tests and CLI parity tests | not implemented |
