#!/usr/bin/env python3
"""Automated game-corpus acceptance harness (fail-closed, evidence only).

This tool *orchestrates* existing tools; it does not reimplement them:

* :mod:`tools.corpus.inventory` -- original SHA-256 and PE metadata.
* :mod:`tools.autorecomp` -- the PE -> source -> native pipeline and its
  ``reports/report.json`` verification outcome (consumed as a supplied
  artifact, or produced by ``--run-pipeline``).
* :mod:`tools.game_runner` -- one native binary at a time, with launch exit,
  logs and captured frame paths.
* :mod:`tools.frame_proof` -- per-frame SHA-256, dimensions and colour counts.
* :mod:`tools.size_tier_report` -- the join/no-invention verdict logic for
  frames (UNPROVEN / BLANK / OK).

Every path is supplied on the command line. No game root, executable, native
binary or oracle path is baked into this file. Missing inputs are reported as
BLOCKED; a build without a launch is BUILD ONLY; render evidence is PARTIAL;
PASS additionally requires an explicit, matching pipeline verification
outcome (a process-level oracle), a non-failing launch and proven frames.
There is no code path that assigns PASS from a build, a frame, an exit code or
a timeout alone -- see ``classify`` and docs/CORPUS_ACCEPTANCE.md.

Headless/CLI apps (7-Zip, PuTTY and friends) never produce a screenshot frame,
so they could never reach PARTIAL under the frame rules. A game may opt in with
``--kind NAME=cli`` (or ``headless``) *and* a declared scenario file
(``--scenario NAME=PATH``) listing the CLI args, expected exit code and the
expected SHA-256 of combined stdout/stderr. Only then is a frame-free,
*process-level* evidence path used: the recorded launch, exact exit code,
recomputed stdout/stderr hash and the on-disk log can lift that game to PARTIAL.
No scenario means the existing frame rules still apply -- nothing is guessed.
This evidence is render-free and explicitly NOT gameplay equivalence.

Usage (GUI game):
    python3 -m tools.corpus_acceptance \
        --out <dir> \
        --original Doukutsu=/path/to/Doukutsu.exe \
        --native Doukutsu=/path/to/native/bin \
        --pipeline-report Doukutsu=/path/to/project/reports/report.json \
        --timeout 12 --json <report.json> --markdown <report.md>

Usage (declared headless CLI game):
    python3 -m tools.corpus_acceptance \
        --out <dir> \
        --original 7z=/path/to/7z.exe --native 7z=/path/to/native/7z \
        --kind 7z=cli --scenario 7z=/path/to/7z-version-scenario.json \
        --assets 7z=/path/to/app/folder --timeout 10 \
        --json <report.json> --markdown <report.md>
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# The corpus inventory tool owns PE parsing/hashing and adds tools/pe to
# sys.path itself; reuse it rather than duplicating its PE metadata logic.
from tools.corpus.inventory import _entry as inventory_entry
from tools import frame_proof, game_runner, size_tier_report

SCHEMA_VERSION = 1

PASS = "PASS"
PARTIAL = "PARTIAL"
BUILD_ONLY = "BUILD ONLY"
FAIL = "FAIL"
BLOCKED = "BLOCKED"
STATUSES = (PASS, PARTIAL, BUILD_ONLY, FAIL, BLOCKED)

# Per-game kind. Anything other than the two headless spellings stays GUI: an
# unknown value is never guessed into a render-free evidence path.
KIND_GUI = "gui"
KIND_CLI = "cli"
CLI_KINDS = ("cli", "headless")

# Only a pipeline outcome that consumed an explicit oracle can support PASS.
# "partial", "not-run", "launch-failed", "trace-diverged" and friends cannot.
PASS_VERIFICATION_STATUSES = ("scenario-pass", "verified-playable-scope")
# A game loop legitimately outlives the runner's deadline; neither this nor a
# clean exit is a *failure*, but neither alone is a pass.
NONFAILING_EXITS = (0, "TIMEOUT")

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def is_sha256(value: Any) -> bool:
    """True only for a 64-character hexadecimal digest string."""
    return (isinstance(value, str) and len(value) == 64
            and all(ch in _HEX_DIGITS for ch in value))


def normalize_kind(value: Any) -> str:
    """Return KIND_CLI only for explicit headless kinds, else KIND_GUI."""
    return (KIND_CLI if str(value or KIND_GUI).strip().lower() in CLI_KINDS
            else KIND_GUI)


def uses_cli_evidence(kind: str, scenario: dict | None) -> bool:
    """True only for an opted-in headless game that *declared* a scenario.

    A headless game without a declared scenario deliberately keeps the existing
    frame rules (and therefore stays FAIL/PARTIAL under those rules).
    """
    return (normalize_kind(kind) == KIND_CLI
            and bool(scenario and scenario.get("declared")))


def sha256_file(path: Path) -> str:
    """Stream a SHA-256; used for the native binary when no pipeline report exists."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_assignment(text: str) -> tuple[str, str]:
    """Parse ``NAME=PATH``; a bare path uses its stem as the name."""
    if "=" in text:
        name, value = text.split("=", 1)
        name, value = name.strip(), value.strip()
        if not name or not value:
            raise ValueError(f"expected NAME=PATH, got {text!r}")
        return name, value
    value = text.strip()
    if not value:
        raise ValueError("empty path")
    return Path(value).stem, value


def discover_roots(roots: list[str]) -> dict[str, str]:
    """Discover original .exe files in CLI-supplied roots; never hard-coded."""
    found: dict[str, str] = {}
    for root_text in roots:
        root = Path(root_text).expanduser()
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() == ".exe":
                name = path.stem
                if name in found:
                    name = f"{path.parent.name}-{name}"
                found[name] = str(path)
    return found


def original_record(path_text: str | None) -> dict:
    """Original SHA-256 + PE metadata, via tools.corpus.inventory."""
    if not path_text:
        return {"status": "MISSING_INPUT", "path": None,
                "reason": "no original PE path supplied for this game"}
    path = Path(path_text).expanduser()
    if not path.is_file():
        return {"status": "MISSING_INPUT", "path": str(path),
                "reason": "original PE path does not exist or is not a file"}
    entry = inventory_entry(path.parent, path, "cli")
    record = {"path": str(path), **entry}
    if entry.get("status") != "ok":
        record["reason"] = entry.get("error", "original PE could not be parsed")
    else:
        record["reason"] = ""
    return record


def pipeline_record(path_text: str | None, original: dict) -> dict:
    """Consume an autorecomp ``reports/report.json`` without trusting its claims."""
    base: dict[str, Any] = {
        "report": None, "status": "NOT_SUPPLIED", "outcome": None,
        "stage_failed": None, "stage_status": {}, "score": None,
        "verification_status": None, "input_sha256": None,
        "native_path": None, "native_sha256": None, "native_size": None,
        "reasons": ["no pipeline report supplied; pipeline result unproven"],
        "blocking_reasons": ["no pipeline report supplied; pipeline result unproven"],
        "claims_verified": False,
    }
    if not path_text:
        return base
    path = Path(path_text).expanduser()
    if path.is_dir():
        path = path / "reports" / "report.json"
    if not path.is_file():
        base["status"] = "MISSING_INPUT"
        base["report"] = str(path)
        base["reasons"] = [f"pipeline report not found at {path}"]
        base["blocking_reasons"] = list(base["reasons"])
        return base
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        base["status"] = "INVALID_REPORT"
        base["report"] = str(path)
        base["reasons"] = [f"cannot read pipeline report {path}: {exc}"]
        base["blocking_reasons"] = list(base["reasons"])
        return base

    outcome = report.get("outcome") or {}
    if not isinstance(outcome, dict):
        outcome = {"status": str(outcome)}
    stages = report.get("stages") or []
    stage_status = {str(s.get("name")): str(s.get("status"))
                    for s in stages if isinstance(s, dict)}
    failed = next((str(s.get("name")) for s in stages
                   if isinstance(s, dict) and s.get("status") == "failed"), None)
    # autorecomp's report.json stores a *relative path* to manifest.json
    # ("manifest.json"); the manifest itself carries input/native hashes.
    manifest = report.get("manifest") or {}
    if isinstance(manifest, str):
        manifest_path = None
        for search_base in (path.parent.parent, path.parent,
                            Path(str(report.get("project") or ""))):
            candidate = search_base / manifest
            if candidate.is_file():
                manifest_path = candidate
                break
        if manifest_path:
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                manifest = {}
        else:
            manifest = {}
    if not isinstance(manifest, dict):
        manifest = {}
    native = manifest.get("binary") or {}
    if not isinstance(native, dict):
        native = {}
    reported_input = report.get("input")
    input_sha = manifest.get("input_sha256") or outcome.get("executable_sha256")
    reasons: list[str] = []
    blocking: list[str] = []

    def note(text: str, *, blocks: bool = False) -> None:
        reasons.append(text)
        if blocks:
            blocking.append(text)

    if failed:
        note(f"pipeline stage {failed!r} failed", blocks=True)
    original_sha = original.get("sha256")
    if original_sha and input_sha and original_sha != input_sha:
        note(f"pipeline input SHA-256 {input_sha[:16]}... does not match original "
             f"{original_sha[:16]}...; its outcome is not credited", blocks=True)
    elif original_sha and input_sha:
        note(f"pipeline input SHA-256 matches original ({input_sha[:16]}...)")
    else:
        note("pipeline report carries no matching input SHA-256", blocks=True)
    if reported_input and original.get("path") and \
            Path(str(reported_input)).name != Path(original["path"]).name:
        note(f"pipeline report names input {reported_input!r}, not this original",
             blocks=True)

    verification = str(outcome.get("status") or "unknown")
    if verification in PASS_VERIFICATION_STATUSES:
        note(f"pipeline verification {verification!r}: "
             f"{outcome.get('reason', '')}")
    else:
        note(f"pipeline verification {verification!r} is not an accepted oracle "
             f"({', '.join(PASS_VERIFICATION_STATUSES)}); PASS is withheld",
             blocks=True)

    base.update({
        "report": str(path),
        "status": "ok" if not failed else "STAGE_FAILED",
        "outcome": outcome,
        "stage_failed": failed,
        "stage_status": stage_status,
        "score": report.get("score"),
        "verification_status": verification,
        "input_sha256": input_sha,
        "native_path": native.get("path"),
        "native_sha256": native.get("sha256"),
        "native_size": native.get("size"),
        "reasons": reasons,
        "blocking_reasons": blocking,
        "claims_verified": not blocking,
    })
    return base


def run_pipeline_for(name: str, original_path: str, work_dir: Path,
                     timeout: float) -> dict:
    """Optionally invoke tools.autorecomp; return the path to its report.

    Deliberately a thin subprocess wrapper: the pipeline's own CLI owns the
    stage plan and exit semantics. Nothing here reimplements a pipeline stage.
    """
    project = work_dir / name
    cmd = [sys.executable, "-m", "tools.autorecomp", original_path,
           "--project-dir", str(project), "--out-mode", "full",
           "--timeout", str(timeout), "--json", "--no-progress", "--no-table"]
    try:
        completed = subprocess.run(cmd, cwd=str(work_dir), stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True,
                                   timeout=timeout + 300, check=False,
                                   encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "ERROR", "report": None,
                "reason": f"autorecomp invocation failed: {exc}"}
    report = project / "reports" / "report.json"
    return {"status": "ok" if report.is_file() else "ERROR",
            "report": str(report) if report.is_file() else None,
            "exit_code": completed.returncode,
            "reason": ("" if report.is_file() else
                       f"autorecomp exit {completed.returncode}; no report; "
                       f"tail: {completed.stdout[-1500:]}")}


def native_record(path_text: str | None, pipeline: dict) -> dict:
    """Native binary path/hash/size from CLI or the pipeline manifest."""
    if path_text:
        path = Path(path_text).expanduser()
    elif pipeline.get("native_path"):
        path = Path(str(pipeline["native_path"])).expanduser()
    else:
        return {"path": None, "sha256": None, "size": None,
                "source": None, "reason": "no native binary supplied or built"}
    if not path.is_file():
        return {"path": str(path), "sha256": None, "size": None,
                "source": "cli" if path_text else "pipeline-manifest",
                "reason": f"native binary missing at {path}"}
    digest = sha256_file(path)
    pipeline_sha = pipeline.get("native_sha256")
    reason = ""
    if pipeline_sha and pipeline_sha != digest:
        reason = (f"native SHA-256 {digest[:16]}... does not match pipeline "
                  f"manifest {str(pipeline_sha)[:16]}...; build provenance unproven")
    elif pipeline_sha:
        reason = f"native SHA-256 matches pipeline manifest ({digest[:16]}...)"
    return {"path": str(path), "sha256": digest, "size": path.stat().st_size,
            "source": "cli" if path_text else "pipeline-manifest",
            "reason": reason}


def frame_summary(analysis: dict | None) -> dict:
    """Condense size_tier_report's joined frame evidence; no new claims."""
    frames = []
    if analysis:
        for row in analysis.get("frames", []):
            marker = str(row.get("marker", ""))
            frames.append({
                "path": row.get("path"),
                "sha256": row.get("sha256"),
                "distinct_colors": row.get("colours"),
                "marker": marker,
                "proven": marker.startswith("OK"),
            })
    proven = [f for f in frames if f["proven"]]
    return {
        "runner_frame_count": analysis.get("frame_count") if analysis else None,
        "joined_frame_count": len(frames),
        "proven_frame_count": len(proven),
        "blank_frame_count": sum(1 for f in frames
                                 if f["marker"].startswith("BLANK")),
        "unproven_frame_count": sum(1 for f in frames
                                    if f["marker"].startswith("UNPROVEN")
                                    or f["marker"].startswith("FAIL")),
        "frames": frames,
        "example": proven[0] if proven else None,
    }


def launch_record(name: str, native: dict, folder: str | None, out_dir: Path,
                  timeout: int, execute: bool, small_max: int,
                  medium_max: int) -> tuple[dict, dict]:
    """Run/reuse game_runner, frame_proof and size_tier_report for one game.

    Returns (launch dict, condensed frame dict). No binary is executed when
    ``execute`` is false; the evidence fields then say so explicitly.
    """
    launch: dict[str, Any] = {
        "executed": False, "exit_code": None, "seconds": None,
        "evidence": None, "log": None, "command": None,
        "reason": "launch not requested (--no-run)",
    }
    if not native.get("path"):
        launch["reason"] = "no native binary to launch"
        return launch, frame_summary(None)
    if not execute:
        return launch, frame_summary(None)

    binary = Path(native["path"])
    if not binary.is_file():
        launch["reason"] = f"native binary missing at {binary}"
        return launch, frame_summary(None)
    assets = Path(folder).expanduser() if folder else binary.parent
    if not assets.is_dir():
        launch["executed"] = True  # attempted, but cannot run from a missing folder
        launch["exit_code"] = "NO_FOLDER"
        launch["reason"] = f"game folder {assets} does not exist"
        return launch, frame_summary(None)

    # game_runner owns the run; its evidence tree is recreated on each run.
    run = game_runner.run_game(name, binary, assets, out_dir, timeout)
    evidence = Path(run["evidence"])
    summary_path = evidence / "runner-summary.json"
    summary = {"schema": 1, "results": [run]}
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    # frame_proof owns frame decoding; call its CLI entry point in-process.
    frame_proof.main(["--summary", str(summary_path), "--out", str(evidence)])
    proof = json.loads((evidence / "frame-proof.json").read_text(encoding="utf-8"))

    # size_tier_report owns the join and the UNPROVEN/BLANK/OK rules.
    analyses = size_tier_report.analyse(
        {"results": [run]}, proof, small_max=small_max, medium_max=medium_max)
    analysis = analyses[0] if analyses else None
    frames = frame_summary(analysis)
    frames["runner_frame_count"] = run.get("frame_count")
    launch.update({
        "executed": True,
        "exit_code": run.get("exit_code"),
        "seconds": run.get("seconds"),
        "evidence": run.get("evidence"),
        "log": str(evidence / "run.log"),
        "command": run.get("command"),
        "stdout_tail": run.get("stdout_tail"),
        "reason": "" if run.get("exit_code") in NONFAILING_EXITS else
                  f"launch exit {run.get('exit_code')!s} is not a clean exit",
    })
    return launch, frames


def scenario_record(path_text: str | None) -> dict:
    """Load and validate a per-game CLI scenario from a CLI-supplied JSON file.

    The file must declare all three fields; anything missing, malformed or
    unreadable leaves the scenario ``declared`` but ``incomplete`` so the
    classifier can FAIL closed instead of guessing an expectation.
    """
    base: dict[str, Any] = {
        "declared": bool(path_text), "complete": False, "source": path_text,
        "args": None, "expected_exit": None, "expected_output_sha256": None,
        "reasons": [],
    }
    if not path_text:
        return base
    path = Path(path_text).expanduser()
    if not path.is_file():
        base["reasons"].append(f"scenario file not found at {path}")
        return base
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        base["reasons"].append(f"cannot read scenario file {path}: {exc}")
        return base
    if not isinstance(data, dict):
        base["reasons"].append("scenario JSON must be an object")
        return base

    args = data.get("args")
    expected_exit = data.get("expected_exit")
    expected_hash = data.get("expected_output_sha256")
    problems: list[str] = []
    if not isinstance(args, list) or any(not isinstance(a, str) for a in args):
        problems.append("'args' must be a list of strings")
    if isinstance(expected_exit, bool) or not isinstance(expected_exit, int):
        problems.append("'expected_exit' must be an integer")
    if not is_sha256(expected_hash):
        problems.append("'expected_output_sha256' must be a 64-character hex digest")

    base["args"] = args if isinstance(args, list) else None
    base["expected_exit"] = expected_exit if (
        isinstance(expected_exit, int) and not isinstance(expected_exit, bool)) else None
    base["expected_output_sha256"] = expected_hash if is_sha256(expected_hash) else None
    base["reasons"] = problems
    base["complete"] = not problems
    return base


def cli_evidence(scenario: dict, launch: dict) -> dict:
    """Evaluate recorded process-level evidence against a declared scenario.

    Nothing here invents a value: the hash comes from the bytes actually
    captured by :func:`cli_launch_record`, and the log path must exist on disk.
    """
    digest = launch.get("output_sha256")
    log = launch.get("log")
    exit_code = launch.get("exit_code")
    expected_exit = scenario.get("expected_exit")
    expected_hash = scenario.get("expected_output_sha256")
    log_exists = bool(log) and Path(str(log)).is_file()
    evidence_present = bool(
        exit_code is not None and is_sha256(digest) and log_exists)
    exit_matches = bool(scenario.get("complete") and exit_code == expected_exit)
    output_matches = bool(
        scenario.get("complete") and expected_hash and is_sha256(digest)
        and digest == expected_hash)
    return {
        "complete": bool(scenario.get("complete")),
        "evidence_present": evidence_present,
        "exit_code": exit_code,
        "expected_exit": expected_exit,
        "exit_matches": exit_matches,
        "output_sha256": digest,
        "expected_output_sha256": expected_hash,
        "output_matches": output_matches,
        "log": log, "log_exists": log_exists,
        "command": launch.get("command"),
        "reproduced": bool(evidence_present and exit_matches and output_matches),
    }


def cli_launch_record(name: str, native: dict, folder: str | None, out_dir: Path,
                      timeout: int, execute: bool, scenario: dict) -> dict:
    """Launch a headless binary with the declared CLI args and capture evidence.

    Combined stdout/stderr bytes are written verbatim to ``run.log`` and hashed
    from those same bytes; no hash, log or exit code is fabricated. This is a
    thin subprocess wrapper local to the harness -- game_runner is unchanged.
    """
    launch: dict[str, Any] = {
        "executed": False, "exit_code": None, "seconds": None,
        "evidence": None, "log": None, "command": None,
        "output_sha256": None, "stdout_tail": None, "mode": "cli",
        "reason": "launch not requested (--no-run)",
    }
    if not native.get("path"):
        launch["reason"] = "no native binary to launch"
        return launch
    if not execute:
        return launch
    binary = Path(str(native["path"]))
    if not binary.is_file():
        launch["reason"] = f"native binary missing at {binary}"
        return launch
    assets = Path(folder).expanduser() if folder else binary.parent
    if not assets.is_dir():
        launch["executed"] = True  # attempted, but cannot run from a missing folder
        launch["exit_code"] = "NO_FOLDER"
        launch["reason"] = f"game folder {assets} does not exist"
        return launch

    evidence = out_dir / name
    shutil.rmtree(evidence, ignore_errors=True)
    evidence.mkdir(parents=True, exist_ok=True)
    cmd = [str(binary), *(scenario.get("args") or [])]
    started = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=str(assets), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=timeout, check=False)
        output = proc.stdout or b""
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or b""
        if isinstance(output, str):
            output = output.encode("utf-8", "replace")
        exit_code = "TIMEOUT"
    except OSError as exc:
        launch["executed"] = True
        launch["exit_code"] = "LAUNCH_ERROR"
        launch["command"] = cmd
        launch["reason"] = f"CLI launch failed: {exc}"
        return launch

    log_path = evidence / "run.log"
    log_path.write_bytes(output)
    digest = hashlib.sha256(output).hexdigest()
    tail = output.decode("utf-8", "replace")[-3000:]
    launch.update({
        "executed": True,
        "exit_code": exit_code,
        "seconds": round(time.time() - started, 1),
        "evidence": str(evidence),
        "log": str(log_path),
        "command": cmd,
        "output_sha256": digest,
        "stdout_tail": tail,
        "reason": "",
    })
    return launch


def _classify_cli(pipeline: dict, native: dict, launch: dict, scenario: dict,
                  reasons: list[str], exit_code: Any) -> tuple[str, list[str]]:
    """Fail-closed verdict for a declared headless CLI scenario (no frames)."""
    if not scenario.get("complete"):
        return FAIL, reasons + [
            "declared CLI scenario is incomplete: "
            + ("; ".join(scenario.get("reasons") or []) or "unknown"),
            "no process-level evidence to report"]
    ev = cli_evidence(scenario, launch)
    if not ev["evidence_present"]:
        return FAIL, reasons + [
            "CLI scenario has no process-level evidence: "
            f"exit={ev['exit_code']!s}, output_sha256={ev['output_sha256']!s}, "
            f"log={ev['log']!s}",
            "no process-level evidence to report"]
    reasons.append(
        f"CLI scenario args={scenario.get('args')!r}: exit {ev['exit_code']!s} "
        f"vs declared {ev['expected_exit']!s}, stdout/stderr sha256 "
        f"{str(ev['output_sha256'])[:16]}... vs declared "
        f"{str(ev['expected_output_sha256'])[:16]}..., log "
        f"{Path(str(ev['log'])).name}")
    if not (ev["exit_matches"] and ev["output_matches"]):
        divergence = []
        if not ev["exit_matches"]:
            divergence.append(
                f"exit {ev['exit_code']!s} != expected {ev['expected_exit']!s}")
        if not ev["output_matches"]:
            divergence.append("stdout/stderr sha256 does not match declared output")
        return FAIL, reasons + [
            "declared CLI scenario did not reproduce: " + "; ".join(divergence),
            "no process-level evidence to report"]

    missing: list[str] = []
    if exit_code not in NONFAILING_EXITS:
        missing.append(f"launch exit {exit_code!s} is not {NONFAILING_EXITS}")
    if pipeline.get("stage_failed"):
        missing.append(f"pipeline stage {pipeline['stage_failed']!r} failed")
    if not pipeline.get("claims_verified"):
        missing.extend(pipeline.get("blocking_reasons")
                       or ["pipeline result unproven"])
    if pipeline.get("verification_status") not in PASS_VERIFICATION_STATUSES:
        missing.append(f"pipeline verification "
                       f"{pipeline.get('verification_status')!r} is not an accepted "
                       "Windows oracle")
    pipeline_sha = pipeline.get("native_sha256")
    if pipeline_sha and pipeline_sha != native.get("sha256"):
        missing.append(native.get("reason") or "native hash differs from pipeline build")
    if not pipeline_sha and native.get("source") == "cli":
        missing.append("native binary was supplied directly, not tied to a "
                       "pipeline build by hash")
    if missing:
        return PARTIAL, reasons + [
            "PASS withheld: " + m for m in missing] + [
            "render-free process-level evidence only; no Windows oracle or "
            "gameplay equivalence"]
    return PASS, reasons + [
        f"pipeline verification {pipeline.get('verification_status')!r} matched",
        "scope: declared CLI scenario + process-level oracle, not gameplay equivalence"]


def classify(original: dict, pipeline: dict, native: dict, launch: dict,
             frames: dict, analysis: dict | None, kind: str = KIND_GUI,
             scenario: dict | None = None) -> tuple[str, list[str]]:
    """Assign one status with explicit reasons. Fail closed; no invented PASS.

    Ladder (highest-priority gate first), GUI (default):

    1. BLOCKED -- original PE missing/invalid, or no native binary.
    2. BUILD ONLY -- native binary exists but no launch was executed.
    3. FAIL -- launch failed or there is no proven, non-blank frame.
    4. PARTIAL -- at least one proven non-blank frame (render evidence only).
    5. PASS -- PARTIAL evidence plus a matching pipeline oracle verification,
       matching input/native hashes, no failed stage and a non-failing launch.

    Opted-in headless CLI games (kind=cli/headless *with* a declared scenario)
    swap the frame requirement for process-level evidence: a recorded launch,
    exact exit code, recomputed stdout/stderr SHA-256 and the on-disk log must
    reproduce the declared expectation. Divergence or missing evidence is FAIL;
    a reproduced scenario is PARTIAL without an oracle. PASS additionally
    demands the same accepted pipeline oracle + provenance gates as GUI, so a
    bare exit code can never pass. This ladder is render-free and is explicitly
    NOT gameplay equivalence. A game with no declared scenario keeps the GUI
    frame rules above -- nothing is guessed.
    """
    reasons: list[str] = []
    if original.get("status") != "ok":
        return BLOCKED, [
            f"original PE {original.get('status')}: "
            f"{original.get('reason') or 'unusable input'}"]
    reasons.append(f"original SHA-256 {original.get('sha256', '')[:16]}... "
                   f"and PE metadata recorded ({original.get('architecture', '?')})")
    if not native.get("sha256"):
        return BLOCKED, reasons + [
            f"no usable native binary: {native.get('reason') or 'missing'}"]
    reasons.append(f"native SHA-256 {native['sha256'][:16]}... "
                   f"({native.get('size')} bytes, source={native.get('source')})")
    if native.get("reason"):
        reasons.append(native["reason"])

    if not launch.get("executed"):
        return BUILD_ONLY, reasons + [
            f"no launch evidence: {launch.get('reason') or 'binary not executed'}"]
    exit_code = launch.get("exit_code")
    reasons.append(f"launch exit {exit_code!s}"
                   + (f" in {launch['seconds']}s" if launch.get("seconds") else ""))
    if pipeline.get("status") in ("STAGE_FAILED", "ERROR", "MISSING_INPUT",
                                   "INVALID_REPORT"):
        reasons.append(f"pipeline result {pipeline['status']}: "
                       + "; ".join(pipeline.get("reasons") or []))

    if uses_cli_evidence(kind, scenario):
        return _classify_cli(pipeline, native, launch, scenario, reasons, exit_code)

    example = frames.get("example")
    if not example:
        detail = ("no frames captured" if not frames.get("joined_frame_count")
                  else "every captured frame is BLANK or UNPROVEN")
        return FAIL, reasons + [detail + "; no render evidence to report"]
    reasons.append(
        f"proven frame {Path(str(example['path'])).name}: "
        f"sha256 {str(example['sha256'])[:16]}..., "
        f"{example['distinct_colors']} distinct colours "
        f"(runner count {frames.get('runner_frame_count')!s}, "
        f"proven {frames.get('proven_frame_count')!s})")

    missing: list[str] = []
    if exit_code not in NONFAILING_EXITS:
        missing.append(f"launch exit {exit_code!s} is not {NONFAILING_EXITS}")
    if pipeline.get("stage_failed"):
        missing.append(f"pipeline stage {pipeline['stage_failed']!r} failed")
    if not pipeline.get("claims_verified"):
        missing.extend(pipeline.get("blocking_reasons")
                       or ["pipeline result unproven"])
    pipeline_sha = pipeline.get("native_sha256")
    if pipeline_sha and pipeline_sha != native.get("sha256"):
        missing.append(native.get("reason") or "native hash differs from pipeline build")
    if not pipeline_sha and native.get("source") == "cli":
        missing.append("native binary was supplied directly, not tied to a "
                       "pipeline build by hash")
    if analysis and not str(analysis.get("run_marker", "")).startswith("OK") \
            and exit_code != "TIMEOUT":
        missing.append(f"runner marker: {analysis['run_marker']}")
    if missing:
        return PARTIAL, reasons + [
            "PASS withheld: " + m for m in missing] + [
            "render evidence only; no Windows oracle or gameplay equivalence"]
    return PASS, reasons + [
        f"pipeline verification {pipeline.get('verification_status')!r} matched",
        "scope: process-level oracle + native frames, not gameplay equivalence"]


def render_markdown(report: dict) -> str:
    lines = [
        "# Corpus acceptance report",
        "",
        f"Generated: {report['generated_at']}  ",
        f"Schema: {report['schema']}  ",
        "Statuses are derived from recorded evidence only; no PASS is "
        "assigned from a build, a frame or a bare exit code alone.",
        "",
        "## Inputs",
        "",
    ]
    for source in report["inputs"]:
        lines.append(f"- {source['kind']}: {source.get('path') or 'not supplied'} "
                     f"({source['status']})")
    lines += ["", "## Games", ""]
    if not report["games"]:
        lines.append("_No games were configured; pass --original/--games-root._")
    for game in report["games"]:
        original, native, launch = game["original"], game["native"], game["launch"]
        frames, pipeline = game["frames"], game["pipeline"]
        lines += [
            f"### {game['game']} — **{game['status']}**",
            "",
            f"- Original: `{original.get('path') or 'none'}` "
            f"SHA-256 `{original.get('sha256') or 'none'}` "
            f"PE `{original.get('pe_type') or original.get('status')}` "
            f"arch `{original.get('architecture') or '?'}`",
            f"- Pipeline: {pipeline['status']} verification="
            f"`{pipeline.get('verification_status')}` report="
            f"`{pipeline.get('report') or 'none'}` score=`{pipeline.get('score')}`",
            f"- Native: `{native.get('path') or 'none'}` "
            f"SHA-256 `{native.get('sha256') or 'none'}`",
            f"- Launch: executed={launch['executed']} exit="
            f"`{launch.get('exit_code')}` log=`{launch.get('log') or 'none'}`",
            f"- Kind: `{game.get('kind', KIND_GUI)}`"
            + (f" (declared CLI scenario, complete="
               f"{game.get('scenario', {}).get('complete')})"
               if game.get("scenario", {}).get("declared") else ""),
        ]
        if game.get("scenario", {}).get("declared"):
            scenario = game["scenario"]
            evidence = game.get("cli_evidence") or {}
            lines += [
                f"- CLI scenario: args={scenario.get('args')} "
                f"expected_exit={scenario.get('expected_exit')} "
                f"expected_output_sha256=`{scenario.get('expected_output_sha256')}` "
                f"source=`{scenario.get('source')}`",
                f"- CLI evidence: output_sha256="
                f"`{launch.get('output_sha256')}` "
                f"log_exists={evidence.get('log_exists')} "
                f"exit_matches={evidence.get('exit_matches')} "
                f"output_matches={evidence.get('output_matches')} "
                f"reproduced={evidence.get('reproduced')} "
                f"(render-free process-level evidence)",
            ]
        lines += [
            f"- Frames: runner={frames.get('runner_frame_count')} "
            f"joined={frames.get('joined_frame_count')} "
            f"proven={frames.get('proven_frame_count')} "
            f"blank={frames.get('blank_frame_count')} "
            f"unproven={frames.get('unproven_frame_count')}",
        ]
        for frame in frames["frames"]:
            lines.append(f"  - `{frame['path']}` sha256=`{frame['sha256']}` "
                         f"colours={frame['distinct_colors']} — {frame['marker']}")
        lines.append("- Reasons:")
        for reason in game["reasons"]:
            lines.append(f"  - {reason}")
        lines.append("")
    counts = report["summary"]["by_status"]
    lines += ["## Summary", "",
              "- " + "; ".join(f"{status}: {counts.get(status, 0)}"
                               for status in STATUSES),
              "",
              "PASS requires a matching pipeline oracle verification, matching "
              "input/native hashes and a non-failing launch, plus a proven "
              "non-blank frame (GUI) or a reproduced declared CLI scenario with "
              "matching stdout/stderr hash (headless). No Windows-oracle "
              "comparison is available here: PARTIAL means render or "
              "render-free process-level evidence, not gameplay acceptance.",
              ""]
    return "\n".join(lines)


def build_acceptance(configs: list[dict], out_dir: Path, *, timeout: int = 12,
                     execute: bool = True, small_max: int | None = None,
                     medium_max: int | None = None,
                     run_pipeline: bool = False,
                     pipeline_timeout: float = 60.0) -> dict:
    """Build one acceptance report from CLI-supplied per-game configuration."""
    out_dir = Path(out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    small_max = small_max if small_max is not None else \
        size_tier_report.parse_size(size_tier_report.DEFAULT_SMALL_MAX)
    medium_max = medium_max if medium_max is not None else \
        size_tier_report.parse_size(size_tier_report.DEFAULT_MEDIUM_MAX)
    generated = datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")
    inputs: list[dict] = []
    games: list[dict] = []

    for cfg in configs:
        name = str(cfg.get("name") or "unnamed")
        original = original_record(cfg.get("original"))
        inputs.append({"kind": f"original:{name}",
                       "path": cfg.get("original"), "status": original["status"]})

        pipeline = pipeline_record(cfg.get("pipeline_report"), original)
        if run_pipeline and original.get("path"):
            invoked = run_pipeline_for(name, original["path"], out_dir,
                                       pipeline_timeout)
            inputs.append({"kind": f"pipeline-run:{name}",
                           "path": invoked.get("report"),
                           "status": invoked["status"]})
            if invoked.get("report"):
                pipeline = pipeline_record(invoked["report"], original)
                if invoked.get("reason"):
                    pipeline["reasons"].append(invoked["reason"])
            else:
                pipeline["status"] = invoked["status"]
                pipeline["reasons"].append(invoked.get("reason", ""))

        native = native_record(cfg.get("native"), pipeline)
        inputs.append({"kind": f"native:{name}", "path": cfg.get("native"),
                       "status": "ok" if native.get("sha256") else "MISSING_INPUT"})

        kind = normalize_kind(cfg.get("kind"))
        scenario = scenario_record(cfg.get("scenario"))
        if kind == KIND_CLI or scenario["declared"]:
            inputs.append({"kind": f"scenario:{name}",
                           "path": scenario["source"],
                           "status": ("ok" if scenario["complete"]
                                      else ("NOT_DECLARED" if not scenario["declared"]
                                            else "INCOMPLETE"))})
        analysis = None
        if uses_cli_evidence(kind, scenario):
            # Headless + declared scenario: frame-free process-level evidence.
            launch = cli_launch_record(
                name, native, cfg.get("assets"), out_dir, timeout, execute, scenario)
            frames = frame_summary(None)
        else:
            launch, frames = launch_record(
                name, native, cfg.get("assets"), out_dir, timeout, execute,
                small_max, medium_max)
            runner_summary = Path(str(launch.get("evidence") or "")) / "runner-summary.json"
            proof_path = Path(str(launch.get("evidence") or "")) / "frame-proof.json"
            if launch.get("executed") and runner_summary.is_file() and proof_path.is_file():
                summary = json.loads(runner_summary.read_text(encoding="utf-8"))
                proof = json.loads(proof_path.read_text(encoding="utf-8"))
                analyses = size_tier_report.analyse(
                    {"results": [summary]}, proof,
                    small_max=small_max, medium_max=medium_max)
                analysis = analyses[0] if analyses else None
        evidence = cli_evidence(scenario, launch)
        status, reasons = classify(original, pipeline, native, launch, frames,
                                   analysis, kind=kind, scenario=scenario)
        games.append({
            "game": name, "status": status, "reasons": reasons,
            "kind": kind, "scenario": scenario, "cli_evidence": evidence,
            "original": original, "pipeline": pipeline, "native": native,
            "launch": launch, "frames": frames,
        })

    counts = {status: sum(1 for g in games if g["status"] == status)
              for status in STATUSES}
    report = {
        "schema": SCHEMA_VERSION,
        "generated_at": generated,
        "tool": "tools.corpus_acceptance",
        "reused_tools": ["tools.corpus.inventory", "tools.autorecomp",
                         "tools.game_runner", "tools.frame_proof",
                         "tools.size_tier_report"],
        "parameters": {"timeout": timeout, "execute": execute,
                       "small_max": small_max, "medium_max": medium_max,
                       "run_pipeline": run_pipeline,
                       "pipeline_timeout": pipeline_timeout},
        "inputs": inputs,
        "games": games,
        "summary": {"total": len(games), "by_status": counts},
    }
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--original", action="append", default=[], metavar="NAME=PATH",
                    help="original Windows PE for a game (repeatable)")
    ap.add_argument("--games-root", action="append", default=[], metavar="DIR",
                    help="directory to scan for original .exe files (repeatable)")
    ap.add_argument("--native", action="append", default=[], metavar="NAME=PATH",
                    help="native binary for a game (repeatable)")
    ap.add_argument("--assets", action="append", default=[], metavar="NAME=DIR",
                    help="working folder to launch a native binary from (repeatable)")
    ap.add_argument("--pipeline-report", action="append", default=[],
                    metavar="NAME=PATH",
                    help="autorecomp report.json or project dir (repeatable)")
    ap.add_argument("--kind", action="append", default=[], metavar="NAME=KIND",
                    help="per-game kind: gui (default), cli or headless (repeatable)")
    ap.add_argument("--scenario", action="append", default=[], metavar="NAME=PATH",
                    help="path to a CLI scenario JSON for a headless game "
                         "(repeatable; args, expected_exit, expected_output_sha256)")
    ap.add_argument("--run-pipeline", action="store_true",
                    help="invoke tools.autorecomp per original before classifying")
    ap.add_argument("--pipeline-timeout", type=float, default=60.0,
                    help="run-stage seconds for --run-pipeline (default 60)")
    ap.add_argument("--no-run", action="store_true",
                    help="do not execute native binaries (record BUILD ONLY instead)")
    ap.add_argument("--out", required=True, type=Path,
                    help="evidence/output directory (CLI-supplied)")
    ap.add_argument("--timeout", type=int, default=12,
                    help="per-game launch timeout in seconds (default 12)")
    ap.add_argument("--small-max", default=size_tier_report.DEFAULT_SMALL_MAX)
    ap.add_argument("--medium-max", default=size_tier_report.DEFAULT_MEDIUM_MAX)
    ap.add_argument("--json", type=Path, help="write the JSON report here")
    ap.add_argument("--markdown", type=Path, help="write the Markdown report here")
    args = ap.parse_args(argv)

    try:
        originals = discover_roots(args.games_root)
        originals.update(dict(parse_assignment(v) for v in args.original))
        natives = dict(parse_assignment(v) for v in args.native)
        assets = dict(parse_assignment(v) for v in args.assets)
        pipelines = dict(parse_assignment(v) for v in args.pipeline_report)
        kinds = dict(parse_assignment(v) for v in args.kind)
        scenarios = dict(parse_assignment(v) for v in args.scenario)
        small_max = size_tier_report.parse_size(args.small_max)
        medium_max = size_tier_report.parse_size(args.medium_max)
    except ValueError as exc:
        print(f"argument error: {exc}", file=sys.stderr)
        return 2

    names = sorted(set(originals) | set(natives) | set(pipelines)
                   | set(kinds) | set(scenarios))
    configs = [{"name": name, "original": originals.get(name),
                "native": natives.get(name), "assets": assets.get(name),
                "pipeline_report": pipelines.get(name),
                "kind": kinds.get(name), "scenario": scenarios.get(name)}
               for name in names]
    report = build_acceptance(
        configs, args.out, timeout=args.timeout, execute=not args.no_run,
        small_max=small_max, medium_max=medium_max,
        run_pipeline=args.run_pipeline, pipeline_timeout=args.pipeline_timeout)
    markdown = render_markdown(report)
    for target, text in ((args.json, json.dumps(report, indent=2) + "\n"),
                         (args.markdown, markdown)):
        if target:
            target = Path(target).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
    print(markdown)
    counts = report["summary"]["by_status"]
    print("summary: " + "; ".join(f"{k}: {v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
