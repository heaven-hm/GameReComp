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
There is no code path that assigns PASS from a build, a frame or a timeout
alone -- see ``classify`` and docs/CORPUS_ACCEPTANCE.md.

Usage:
    python3 -m tools.corpus_acceptance \
        --out <dir> \
        --original Doukutsu=/path/to/Doukutsu.exe \
        --native Doukutsu=/path/to/native/bin \
        --pipeline-report Doukutsu=/path/to/project/reports/report.json \
        --timeout 12 --json <report.json> --markdown <report.md>
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import subprocess
import sys
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

# Only a pipeline outcome that consumed an explicit oracle can support PASS.
# "partial", "not-run", "launch-failed", "trace-diverged" and friends cannot.
PASS_VERIFICATION_STATUSES = ("scenario-pass", "verified-playable-scope")
# A game loop legitimately outlives the runner's deadline; neither this nor a
# clean exit is a *failure*, but neither alone is a pass.
NONFAILING_EXITS = (0, "TIMEOUT")


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


def classify(original: dict, pipeline: dict, native: dict, launch: dict,
             frames: dict, analysis: dict | None) -> tuple[str, list[str]]:
    """Assign one status with explicit reasons. Fail closed; no invented PASS.

    Ladder (highest-priority gate first):

    1. BLOCKED -- original PE missing/invalid, or no native binary.
    2. BUILD ONLY -- native binary exists but no launch was executed.
    3. FAIL -- launch failed or there is no proven, non-blank frame.
    4. PARTIAL -- at least one proven non-blank frame (render evidence only).
    5. PASS -- PARTIAL evidence plus a matching pipeline oracle verification,
       matching input/native hashes, no failed stage and a non-failing launch.
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
        "assigned from a build or a frame alone.",
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
              "input/native hashes, a non-failing launch and a proven non-blank "
              "frame. No Windows-oracle comparison is available here: PARTIAL "
              "means render evidence, not gameplay acceptance.",
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
        launch, frames = launch_record(
            name, native, cfg.get("assets"), out_dir, timeout, execute,
            small_max, medium_max)
        analysis = None
        runner_summary = Path(str(launch.get("evidence") or "")) / "runner-summary.json"
        proof_path = Path(str(launch.get("evidence") or "")) / "frame-proof.json"
        if launch.get("executed") and runner_summary.is_file() and proof_path.is_file():
            summary = json.loads(runner_summary.read_text(encoding="utf-8"))
            proof = json.loads(proof_path.read_text(encoding="utf-8"))
            analyses = size_tier_report.analyse(
                {"results": [summary]}, proof,
                small_max=small_max, medium_max=medium_max)
            analysis = analyses[0] if analyses else None
        status, reasons = classify(original, pipeline, native, launch, frames,
                                   analysis)
        games.append({
            "game": name, "status": status, "reasons": reasons,
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
        small_max = size_tier_report.parse_size(args.small_max)
        medium_max = size_tier_report.parse_size(args.medium_max)
    except ValueError as exc:
        print(f"argument error: {exc}", file=sys.stderr)
        return 2

    names = sorted(set(originals) | set(natives) | set(pipelines))
    configs = [{"name": name, "original": originals.get(name),
                "native": natives.get(name), "assets": assets.get(name),
                "pipeline_report": pipelines.get(name)} for name in names]
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
