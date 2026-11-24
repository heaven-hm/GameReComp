# Hybrid test pipeline

This document defines the hybrid verification pipeline: what it runs, the exact
commands, the evidence record layout, and the rule that turns engine results
into a status. It describes measured behaviour of the checked-in tools under
`tools/`; it is not an acceptance certificate and it does not close any issue.

The companion documents are `docs/HYBRID_DUAL_ENGINE_EVIDENCE.md` (one recorded
run), `docs/HYBRID.md` (the lifted/real boundary in `runtime/hybrid`),
`docs/AUTO_VERIFY.md` (the multi-binary orchestrator) and `docs/PE32_COVERAGE.md`
(the per-class status ladder).

## Purpose

A build, a disassembly match or a single emulator result says nothing about how
a reconstructed program behaves against the original Windows binary. The hybrid
pipeline combines three independent signals for one PE32 x86 original and its
host-native rebuild:

1. **Unicorn** drives the *original* x86 bytes at a chosen function address with
   controlled entry seeds. It is a CPU-level reference that does not depend on
   Wine or on the recompiled C (`tools/lift/difftest.py`).
2. **Frida** observes the original process under the Highball/Wine engine and
   counts selected Win32/HLE calls (`tools/frida_probe.py`). Live attach is
   opt-in and hard-bounded; a failed attach is recorded with its exact reason.
3. **Native replay** sends byte-identical stdin and arguments to the original
   (via `highball run` when available, otherwise a labelled `wine-fallback`) and
   to the host-native binary, then compares exit code and normalised stdout
   (`tools/input_replay_diff.py`).

Optional frame capture (`tools/game_runner.py` + `tools/frame_proof.py`) attaches
decoded, hashed frames to a run. It is evidence about the frames captured, not
about gameplay. Input determinism is a separate, adapter-injected check
(`tools/input_scenario.py`); it never invents a pump when none is supplied.

The pipeline is fail-closed: an engine that could not run is BLOCKED, not
skipped. The strongest statement it can make by default is that the engines
which ran agree on this input. `MATCH` is CPU/process-level differential
evidence; it is not a 100% compatibility claim and not a gameplay, frame or
audio equivalence claim.

## Supported scope

The only structurally supported class is **PE32 x86 original -> host-native binary**.
The measured host is macOS arm64; there is no Linux runner on this
host, so no Linux execution status is implied. `tools/app_compat_probe.py`
labels a PE32 x86 input `SUPPORTED_CANDIDATE`, which means "structurally eligible
for the pipeline" only. Import binding, lift accuracy, graphics, audio and
gameplay still require per-app evidence.

A run needs real inputs supplied by the caller: an original PE32 x86 executable,
a host-native binary built from it, and (where relevant) real stdin bytes or a
real assets folder. No user path, game name, hotkey or Wine prefix is hard-coded
by the pipeline; every command below takes caller-supplied paths.

## Deferred and unsupported classes

| Class | Status | Behaviour |
| --- | --- | --- |
| PE32+ / x64 | DEFERRED (issue #13 gate not open) | Refused fail-closed at inspection; no x64 loader, lifter or host ABI. |
| .NET / CLR assemblies | UNSUPPORTED | No CLR loader or metadata path; no native import table to reconstruct. |
| Win16 / 16-bit NE | DEFERRED (issue #8 gate not open) | NE lifecycle is not wired end-to-end into the pipeline. |
| D3D10 / D3D11 / DXGI | DEFERRED (issue #7) | No run evidence from this pipeline. |
| Joystick, force feedback, XInput | Out of scope for the input adapter | Rejected by scenario validation; no support is implied by silence. |

These classes have no local evidence in this pipeline: their status is NOT TESTED,
never a quiet PASS. No all-games or all-OS claim is made here, and no
claim is made for any binary that was not supplied to a run.

## Commands

Run every command from the repository root with caller-supplied paths. The
placeholders `<original.exe>`, `<native-binary>`, `<project-dir>`,
`<scenario.json>`, `<real-input.bin>` and `<evidence-dir>` are arguments, not
recommendations to install a particular game.

### Unicorn adapter

Direct CPU-level differential run of the generated project against the original
bytes (repeat `--address` for more functions):

```sh
python3 -m tools.lift.difftest \
  --project <project-dir> --pe <original.exe> \
  --address 0x4010A0 --cases 8 \
  --json <evidence-dir>/difftest-per-address.json
```

`--address` must name a real function in a code section. A console PE entry that
calls unresolved IAT imports faults with `UC_ERR_READ_UNMAPPED`; that is a fault
result, not a pass. For `--project` mode, `--closure` and `--frame-size` widen
the comparison to a call closure and the guest stack frame. Select a leaf or
internal function whose output varies with the seed; identical results across
all seeds are vacuous and are refused by the orchestrator.

### Frida adapter

Print the injection script without attaching (safe in CI):

```sh
python3 -m tools.frida_probe --dry-run --hook ExitProcess --hook WriteFile
```

Attach to an existing process to count calls:

```sh
python3 -m tools.frida_probe \
  --spawn /path/to/highball run /path/to/Original.exe \
  --hook KERNEL32.dll!ExitProcess --hook KERNEL32.dll!WriteFile \
  --timeout 5 --out <evidence-dir>/frida-calls.json
```

The CLI's `--pid` / `--spawn` path is an observation helper and is not the
kill-group-bounded path used by the pipeline. Bounded live capture goes through
`tools.frida_probe.bounded_probe`, reached from `tools.hybrid_diff` with
`--live-frida`: the worker runs in its own process group with a wall-clock
deadline, and on expiry the group and spawned Wine PID are SIGKILLed. A missing
result file, permission error or timeout stays BLOCKED with the exact reason;
no call counts are invented. Frida is an optional dependency; without it the
record is a BLOCKED reason, never an empty successful trace.

### Input adapter

A scenario is a JSON object with timestamped keyboard/mouse events. `t` values
order events; they are not a real-time schedule. Example scenario content:

```json
{"schema": 1, "name": "sample", "events": [
  {"t": 0, "type": "key", "action": "down", "key": "A"},
  {"t": 1, "type": "key", "action": "up", "key": "A"},
  {"t": 2, "type": "mouse", "action": "move", "x": 100, "y": 200}
]}
```

Run the scenario with a caller-supplied pump factory. The factory must return a
fresh `InputPump` per replay implementing `inject(event) -> bool`, `state() ->
dict`, `screenshot() -> bytes` and `close()`:

```sh
python3 -m tools.input_scenario \
  --scenario <scenario.json> --out <evidence-dir>/input-replay \
  --pump mypackage.pump:make_pump --replays 2
```

Without `--pump` the tool writes `status: BLOCKED`. An event the pump does not
report consumed, a blank or undecodable frame, or differing replay records is a
FAIL. A build or an emulator result is never substituted for a real pump.

### Orchestrator

Single-target hybrid differential run (default fail-closed):

```sh
python3 -m tools.hybrid_diff \
  --exe <original.exe> --native <native-binary> --input "" \
  --function 0x4010A0 --seeds 1,2,3,4,5,6,7,8 \
  --hook KERNEL32.dll!WriteFile --hook KERNEL32.dll!ExitProcess \
  --live-frida --timeout 20 \
  --out <evidence-dir>/hybrid.json
```

The same command with `--process-level-match` appended before `--out` may yield
a downgraded `MATCH` when *only* Frida is environment-BLOCKED and Unicorn and
native replay both succeed. The exact Frida reason and
`process_level_only: true` are carried in the record; the claim text says it is
not a 100% compatibility claim. Divergence, guest faults, vacuous Unicorn
signatures and a missing native binary are never allowlisted by that flag.

Multi-target orchestrator (one record per `--exe`, plus `summary.json` and
`report.md` in `--out`):

```sh
python3 -m tools.auto_verify \
  --exe <original.exe> --native <name>=<native-binary> \
  --stdin-file <real-input.bin> --hook WriteFile --hook ExitProcess \
  --timeout 10 --out <evidence-dir>/auto-verify \
  --live-frida --capture-frames
```

`--native` uses `NAME=PATH` and is matched to the executable stem. Live Frida is
opt-in here too, and frame capture is recorded with a return code and log tail;
it is not a gameplay verdict. The report states that no issue is closed by it.

## Evidence schema

Fields below are the ones emitted by `tools/hybrid_diff.py` (single-target
record) and `tools/auto_verify.py` (orchestrator record). Values are JSON; a
missing engine is represented by a BLOCKED sub-record, never by an omitted
field.

| Level | Field | Meaning |
| --- | --- | --- |
| top | `exe`, `status`, `reason` | Input path, final status (`MATCH`, `DIVERGED`, `BLOCKED`), human-readable reason. |
| top | `original_runner` | `highball` or labelled `wine-fallback` or `not-run`. |
| top | `matched_engines`, `blocked_engines`, `diverged_engines` | Per-engine outcome lists. |
| top | `claim`, `note`, `process_level_only` | Scope statement; `true` only for a disclosed downgraded match. |
| top | `artifacts` | `original` and `native` entries with `path` and `sha256`. |
| engine | `unicorn.status`, `blocked_class`, `function`, `entry`, `from_entry`, `section` | Which original bytes ran; `blocked_class` is `fault` or `environment`. |
| engine | `unicorn.status`, `cases`, `seeds`, `named`, `distinct_eax`, `nonvacuous`, `signatures` | Entry states and observed results; `nonvacuous` is true when results differ. |
| engine | `frida.status`, `reason`, `calls`, `counts`, `script_sha256`, `runner`, `timeout_s`, `killed_on_timeout`, `blocked_class` | Bounded observation result; exact error text is preserved. |
| engine | `native.status`, `original`, `native`, `stdin_bytes`, `args` | Replay result and hashes; `original` carries `exit_code`, `sha256`, `stdout_hash`, `runner`, `command`; `native` carries `exit_code`, `stdout_hash`. |
| orchestrator | `hybrid_status`, `frame_capture`, `sha256`, `bytes`, `stdin_bytes`, `hooks`, `timestamp`, `status` | Per-binary orchestrator fields; `frame_capture` records a return code or a skip reason. |
| orchestrator summary | `schema`, `count`, `counts`, `live_frida`, `results` | Aggregate of per-binary records; `report.md` is a rendering, not a new verdict. |
| input adapter | `schema`, `status`, `replays`, `scenario_sha256`, `record_sha256s`, `record_paths`, `what_it_proves`, `not_claimed` | Determinism result. Each step holds `index`, `event`, `consumed`, `state` and a `screenshot` with `path`, `format`, `sha256`, dimensions, `distinct_colors` and `blank`. |

Frame evidence is only a path plus a decoded-colour check and a SHA-256 from the
tool that captured it. This document embeds no screenshots; a reader must open
the recorded frame path in the evidence directory to inspect pixels.

## Fail-closed verdict rules

Applied by `hybrid_one()` in `tools/hybrid_diff.py`, in this order:

1. Any engine with status `DIVERGED` -> `DIVERGED`. Divergence is never upgraded,
   including with `--process-level-match`.
2. Any engine BLOCKED with `blocked_class: fault` (for example
   `UC_ERR_READ_UNMAPPED`, or a native timeout) -> `BLOCKED`.
3. Unicorn OK with `--function` but `nonvacuous: false` (all seeds produced the
   same result) -> `BLOCKED`.
4. Any engine BLOCKED with `blocked_class: environment` -> `BLOCKED` by default.
   The only exception is `--process-level-match`, and only when *all* blocked
   engines are `frida`, Unicorn is OK and native replay is `MATCH`. The reason
   then includes every blocked engine's exact text and the scope is downgraded
   to process-level; this is never a 100% compatibility claim.
5. No `--native` (or missing native binary/original) -> `BLOCKED`; the native
   sub-record carries the reason.
6. Otherwise, with no blocked or diverged engine -> `MATCH`, meaning only that
   the engines which ran agree on this input.

`tools/auto_verify.py` applies the same sub-records and maps any BLOCKED engine
to a `BLOCKED` row, any divergence to `DIVERGED`, and otherwise `PASS` with the
note that this is process/CPU-level evidence unless verified frame evidence is
attached. A build or a non-zero-free exit code alone never yields PASS.
`tools/input_scenario.py` is independently fail-closed: no pump -> BLOCKED,
unconsumed event / undecodable or blank frame / non-identical replays -> FAIL.

These rules are enforced by `tests/test_hybrid_diff.py`,
`tests/test_hybrid_real_evidence.py`, `tests/test_frida_probe.py` and
`tests/test_auto_verify.py`.

## Blocked run example

The committed record `reports/hybrid-tetris.json` is a real BLOCKED run. Its
fields, verbatim where quoted, are:

```json
{
  "game": "tetris-win-32-en.exe",
  "status": "BLOCKED",
  "unicorn": {
    "status": "BLOCKED",
    "reason": "OracleFault: original bytes at 0x004013D0 faulted on case 0: Invalid memory read (UC_ERR_READ_UNMAPPED) -- entry needs real stack arguments"
  },
  "frida": {
    "status": "BLOCKED",
    "reason": "live attach disabled in bounded run (--live-frida available)"
  },
  "native": {
    "status": "BLOCKED",
    "reason": "bounded replay timed out"
  },
  "note": "Both engines are attempted by tools/hybrid_diff.py; this run produced no MATCH. No compatibility claim."
}
```

Reading it: the Unicorn address was the PE entry, which calls imports through
unresolved IAT slots, so it faulted (rule 2). Frida was not attempted with a
live attach, so it is a separate environment BLOCKED reason. Native replay
timed out, which is a real failure to run under the bound, not an environment
skip. The row is BLOCKED under rules 2 and 4; no flag would turn it into a
MATCH. A better-targeted Unicorn function and a bounded native run are required
before any agreement can be claimed.

No screenshot is attached to that record, and none is shown here. The only
screenshots that count as evidence are real frame files written by the tools
with a path and SHA-256 in the record.

## Limits

* This pipeline produces CPU/process-level differential evidence for the
  supplied PE32 x86 pair. It does not establish gameplay, input, audio,
  save/load or frame equivalence, and it makes no 100% compatibility claim.
* It makes no all-games or all-OS claim: x64, .NET and Win16 are DEFERRED or
  UNSUPPORTED, and there is no original-Windows oracle here and no Linux runner.
* `MATCH` with `--process-level-match` is explicitly weaker than a full
  three-engine agreement and exists only to disclose an environment limit.
* This document does not close, resolve or fix any issue; the gates for issues
  #7, #8 and #13 remain open.
* A stale native binary produces stale evidence. Rebuild before drawing a
  conclusion about the current tree.

## Verification

The deterministic guardrail for this document is
`tests/test_hybrid_pipeline_docs.py`. The underlying engine behaviour is covered
by the engine tests named above:

```sh
python3 -m pytest tests/test_hybrid_pipeline_docs.py -q
python3 -m pytest tests/test_hybrid_diff.py tests/test_hybrid_real_evidence.py \
  tests/test_frida_probe.py tests/test_auto_verify.py tests/test_input_scenario.py -q
```
