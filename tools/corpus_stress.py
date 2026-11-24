#!/usr/bin/env python3
"""Fail-closed batch stress runner for a CLI-supplied corpus of games.

This tool *orchestrates* existing tools; it does not reimplement PE parsing,
the recompile pipeline, the native launcher or frame decoding:

* :mod:`tools.corpus.inventory` -- original SHA-256 and PE facts.
* :mod:`tools.corpus_acceptance` -- pipeline/native/launch evidence records and
  the evidence-only acceptance ladder.
* :mod:`tools.game_runner`, :mod:`tools.frame_proof`,
  :mod:`tools.size_tier_report` -- launch, exact frame hashes/dimensions/colour
  counts and the UNPROVEN/BLANK/OK frame markers.
* :mod:`tools.acceptance_matrix` -- validation of a *supplied* original-Windows
  oracle claim (real oracle flag, matching original/native hashes, named
  scenario with an evidenced file whose hash is recomputed here).

Every path comes from the command line. Roots supplied with ``--games-root``
are read-only; all generated work, project, TMPDIR and evidence trees are
created under ``--out``, one isolated tree per game. A game that cannot be
processed never stops the batch: it is journalled with an explicit reason.

Verdict ladder (fail closed):

* ``NOT TESTED`` -- discovered but excluded by ``--limit``; no evidence.
* ``BLOCKED`` -- original PE unusable, or no native binary to launch.
* ``BUILD ONLY`` -- native binary recorded but no launch was requested.
* ``FAIL`` -- launch attempted but there is no render/process evidence.
* ``PARTIAL`` -- render/process evidence only; no validated Windows oracle.
* ``PASS`` -- PARTIAL evidence PLUS a supplied, validated original-Windows
  oracle whose original/native hashes match the recorded ones, whose scenario
  evidence file hash is recomputed here, with no unresolved system imports.

A fabricated hash, a bare exit code, a non-blank frame, a "verified" string or
a build report claiming PASS can never produce ``PASS`` on its own.

Usage::

    python3 -m tools.corpus_stress \
        --games-root <dir> [--games-root <dir> ...] \
        --out <dir> [--limit 100] [--jobs 8] [--timeout 12] \
        [--run-pipeline] [--no-run] \
        [--oracle-evidence <oracle.json>] [--api-coverage <coverage.json>] \
        --json <batch.json> --markdown <batch.md>

Per-game sidecars discovered next to each ``<name>.exe`` (so a root can be
self-describing without extra flags)::

    <name>.native        native binary to launch
    <name>.report.json   autorecomp reports/report.json
    <name>.scenario.json declared headless CLI scenario
    <name>.coverage.json api_coverage JSON (registration only)
    <name>.oracle.json   one oracle evidence record (same schema as below)

Oracle evidence JSON (repeatable ``--oracle-evidence``)::

    {"records": [{
      "game": "<game name>", "oracle": "original-windows",
      "oracle_verified": true,
      "input_sha256": "<original SHA-256>",
      "native_sha256": "<native SHA-256>",
      "scenario": "boot-to-menu", "scenario_evidence": "menus match at frame 12",
      "evidence_path": "relative-to-json-or-absolute/file.json",
      "evidence_sha256": "<SHA-256 of that file>"
    }]}

Only ``oracle_verified: true`` together with an accepted original-Windows
oracle name, matching hashes and a hash-matched evidence file is credited.
Nothing here claims gameplay equivalence: PARTIAL is render or render-free
process evidence, and PASS additionally requires the supplied Windows oracle.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from tools import acceptance_matrix as am
from tools import corpus_acceptance as ca
from tools import frame_proof, game_runner, size_tier_report

SCHEMA_VERSION = 1
GENERATOR = "tools/corpus_stress.py"

PASS = "PASS"
PARTIAL = "PARTIAL"
BUILD_ONLY = "BUILD ONLY"
FAIL = "FAIL"
BLOCKED = "BLOCKED"
NOT_TESTED = "NOT TESTED"
STATUSES = (PASS, PARTIAL, BUILD_ONLY, FAIL, BLOCKED, NOT_TESTED)

_SIDECARS = {
    "native": (".native", "native"),
    "pipeline_report": (".report.json", "report.json"),
    "scenario": (".scenario.json", "scenario.json"),
    "coverage": (".coverage.json", "coverage.json"),
    "oracle": (".oracle.json", "oracle.json"),
}
_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def safe_slug(text: str, limit: int = 64) -> str:
    """Stable, path-safe directory stem; never a user path or a wildcard."""
    slug = _SLUG_RE.sub("-", str(text or "game").strip().lower()).strip("-._")
    return (slug or "game")[:limit]


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def discover_games(roots: list[str]) -> tuple[list[dict], list[dict]]:
    """Discover original .exe files only in CLI-supplied roots (read-only)."""
    games: list[dict] = []
    inputs: list[dict] = []
    used: set[str] = set()
    for raw in roots:
        root = Path(raw).expanduser()
        record: dict[str, Any] = {
            "kind": "games-root", "path": str(root), "status": "ok",
            "detail": "", "discovered": 0,
        }
        if not root.is_dir():
            record.update(status="MISSING_INPUT",
                          detail="not an existing directory")
            inputs.append(record)
            continue
        root = root.resolve()
        record["path"] = str(root)
        found = 0
        for path in sorted((p for p in root.rglob("*")
                            if p.is_file() and p.suffix.lower() == ".exe"),
                           key=lambda p: p.as_posix()):
            name = path.stem
            if name in used:
                name = f"{path.parent.name}-{name}"
            candidate, index = name, 2
            while candidate in used:
                candidate = f"{name}-{index}"
                index += 1
            used.add(candidate)
            found += 1
            games.append({
                "name": candidate, "path": str(path),
                "dir": str(path.parent),
            })
        record["discovered"] = found
        inputs.append(record)
    return games, inputs


def sidecars(game: dict) -> dict[str, str | None]:
    """Find optional per-game evidence files next to the original PE.

    Both ``<stem>.report.json`` and a plain ``report.json`` are accepted; the
    native binary may be ``<stem>.native`` or simply ``native``.
    """
    folder = Path(game["dir"])
    stem = Path(game["path"]).stem
    out: dict[str, str | None] = {}
    for key, names in _SIDECARS.items():
        found = None
        for name in names:
            filename = f"{stem}{name}" if name.startswith(".") else name
            candidate = folder / filename
            if candidate.is_file():
                found = str(candidate)
                break
        out[key] = found
    return out


def _source(path: Path, kind: str) -> dict:
    return {"kind": kind, "path": str(path), "sha256": am.sha256_file(path),
            "status": "ok"}


def load_evidence(oracle_paths: list[str], coverage_paths: list[str]) -> tuple[
        list[dict], dict[str, dict], list[dict]]:
    """Load CLI-supplied oracle/coverage JSON using the matrix validators."""
    loaded_oracles, problems = am.load_sources(oracle_paths, "oracle-evidence")
    oracles, oracle_failures = am.index_oracles(loaded_oracles)
    loaded_cov, cov_problems = am.load_sources(coverage_paths, "api-coverage")
    coverage = am.index_coverage(loaded_cov)
    inputs = []
    for problem in problems + oracle_failures + cov_problems:
        inputs.append({"kind": problem.get("kind", "evidence"),
                       "path": problem.get("path"), "status": "INVALID",
                       "detail": problem.get("error") or problem.get("detail")
                       or "invalid evidence file"})
    return oracles, coverage, inputs


def load_sidecar_evidence(games: list[dict]) -> tuple[
        list[dict], dict[str, dict], list[dict]]:
    """Load per-game sidecar oracle and coverage files, scoped to their dir."""
    oracle_wrapped: list[dict] = []
    coverage_wrapped: list[dict] = []
    inputs: list[dict] = []
    for game in games:
        side = sidecars(game)
        for key, kind, sink in (("oracle", "oracle-evidence", oracle_wrapped),
                                ("coverage", "api-coverage", coverage_wrapped)):
            raw = side.get(key)
            if not raw:
                continue
            path = Path(raw)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("expected a JSON object")
            except (OSError, ValueError, UnicodeError) as exc:
                inputs.append({"kind": kind, "path": raw, "status": "INVALID",
                               "detail": f"{type(exc).__name__}: {exc}"})
                continue
            sink.append({"data": data, "source": _source(path, kind)})
            inputs.append({"kind": kind, "path": raw, "status": "ok",
                           "detail": "per-game sidecar"})
    oracles, failures = am.index_oracles(oracle_wrapped)
    for failure in failures:
        inputs.append({"kind": failure.get("kind", "oracle-evidence"),
                       "path": failure.get("path"), "status": "INVALID",
                       "detail": failure.get("error") or "invalid oracle record"})
    coverage = am.index_coverage(coverage_wrapped)
    return oracles, coverage, inputs


def oracle_for(oracles: list[dict], name: str) -> dict | None:
    """Match oracle records by game name; ambiguity is never credited."""
    matches = [o for o in oracles if am.key_of(o.get("game")) == am.key_of(name)]
    if not matches:
        return None
    if len(matches) > 1:
        invalid = dict(matches[0])
        invalid["valid"] = False
        invalid["reasons"] = list(invalid.get("reasons") or []) + [
            f"{len(matches)} oracle claims supplied for this game; all withheld"]
        return invalid
    return matches[0]


def pipeline_command(original_path: str, project_dir: Path,
                     timeout: float) -> list[str]:
    return [sys.executable, "-m", "tools.autorecomp", original_path,
            "--project-dir", str(project_dir), "--out-mode", "full",
            "--timeout", str(timeout), "--json", "--no-progress",
            "--no-table"]


def invoke_pipeline(name: str, original_path: str, project_dir: Path,
                    tmp_dir: Path, timeout: float) -> dict:
    """Invoke autorecomp in an isolated TMPDIR; return report path + command."""
    cmd = pipeline_command(original_path, project_dir, timeout)
    env = dict(os.environ)
    env["TMPDIR"] = str(tmp_dir)
    try:
        completed = subprocess.run(
            cmd, cwd=str(tmp_dir), env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, timeout=timeout + 300,
            check=False, encoding="utf-8", errors="replace")
        exit_code: Any = completed.returncode
        tail = completed.stdout[-1500:]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "ERROR", "report": None, "command": cmd,
                "exit_code": "ERROR",
                "reason": f"autorecomp invocation failed: {exc}"}
    report = project_dir / "reports" / "report.json"
    if not report.is_file():
        return {"status": "ERROR", "report": None, "command": cmd,
                "exit_code": exit_code,
                "reason": f"autorecomp exit {exit_code}; no report; tail: {tail}"}
    return {"status": "ok", "report": str(report), "command": cmd,
            "exit_code": exit_code, "reason": ""}


def launch_command(binary: Path) -> list[str]:
    return [str(binary)]


def run_launch(name: str, binary: Path, assets: Path, evidence_dir: Path,
               tmp_dir: Path, timeout: int) -> tuple[dict, dict | None, list[dict]]:
    """Launch one native binary with an isolated TMPDIR and prove its frames.

    Mirrors :func:`tools.game_runner.run_game` but injects the per-game TMPDIR
    explicitly (mutating ``os.environ`` would race with ``--jobs`` threads).
    The runner, frame proof and size-tier join remain the owning tools.
    """
    shots = evidence_dir / "screenshots"
    if evidence_dir.exists():
        shutil.rmtree(evidence_dir)
    shots.mkdir(parents=True)
    env = dict(os.environ)
    env.update({
        "TMPDIR": str(tmp_dir),
        "SDL_VIDEODRIVER": "cocoa" if sys.platform == "darwin" else "x11",
        "SDL_AUDIODRIVER": "dummy",
        "RECOMP_SCREENSHOT": str(shots),
        "RECOMP_SCREENSHOT_MAX": "3",
        "RECOMP_SCREENSHOT_EVERY": "500",
    })
    cmd = launch_command(binary)
    started = time.time()
    run: dict[str, Any] = {
        "game": name, "binary": str(binary), "folder": str(assets),
        "command": cmd, "env": {"TMPDIR": str(tmp_dir),
                                "SDL_VIDEODRIVER": env["SDL_VIDEODRIVER"],
                                "SDL_AUDIODRIVER": env["SDL_AUDIODRIVER"],
                                "RECOMP_SCREENSHOT": str(shots)},
    }
    try:
        proc = subprocess.run(
            cmd, cwd=str(assets), env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, timeout=timeout,
            encoding="utf-8", errors="replace", check=False)
        run["exit_code"] = proc.returncode
        (evidence_dir / "run.log").write_text(proc.stdout, encoding="utf-8")
        run["stdout_tail"] = proc.stdout[-3000:]
    except subprocess.TimeoutExpired as exc:
        text = exc.stdout or ""
        if isinstance(text, bytes):
            text = text.decode("utf-8", "replace")
        run["exit_code"] = "TIMEOUT"
        (evidence_dir / "run.log").write_text(text, encoding="utf-8")
        run["stdout_tail"] = text[-3000:]
    except OSError as exc:
        run["exit_code"] = "LAUNCH_ERROR"
        run["stdout_tail"] = ""
        (evidence_dir / "run.log").write_text(
            f"launch failed: {exc}\n", encoding="utf-8")
    run["seconds"] = round(time.time() - started, 1)
    frames = sorted(list(shots.glob("frame-*.png")) + list(shots.glob("frame-*.bmp")))
    run["frames"] = [{"path": str(f), **game_runner.image_info(f)}
                     for f in frames]
    run["frame_count"] = len(frames)
    run["evidence"] = str(evidence_dir)

    summary_path = evidence_dir / "runner-summary.json"
    summary_path.write_text(json.dumps({"schema": 1, "results": [run]},
                                       indent=2) + "\n", encoding="utf-8")
    frame_proof.main(["--summary", str(summary_path), "--out", str(evidence_dir)])
    proof_path = evidence_dir / "frame-proof.json"
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    analysis_list = size_tier_report.analyse(
        {"results": [run]}, proof,
        small_max=size_tier_report.parse_size(
            size_tier_report.DEFAULT_SMALL_MAX),
        medium_max=size_tier_report.parse_size(
            size_tier_report.DEFAULT_MEDIUM_MAX))
    analysis = analysis_list[0] if analysis_list else None
    return run, analysis, frame_records(run, proof)


def run_cli_launch(name: str, native: dict, assets: Path, evidence_dir: Path,
                   tmp_dir: Path, timeout: int, execute: bool,
                   scenario: dict) -> dict:
    """Headless process-level launch with the per-game TMPDIR injected.

    Mirrors :func:`tools.corpus_acceptance.cli_launch_record`; the combined
    stdout/stderr bytes are written verbatim and hashed from those same bytes.
    """
    launch: dict[str, Any] = {
        "executed": False, "exit_code": None, "seconds": None,
        "evidence": None, "log": None, "command": None,
        "output_sha256": None, "stdout_tail": None, "mode": "cli",
        "reason": "launch not requested (--no-run)",
    }
    if not execute or not native.get("path"):
        if not native.get("path"):
            launch["reason"] = "no native binary to launch"
        return launch
    binary = Path(str(native["path"]))
    if not binary.is_file():
        launch["reason"] = f"native binary missing at {binary}"
        return launch
    if not assets.is_dir():
        launch["executed"] = True
        launch["exit_code"] = "NO_FOLDER"
        launch["reason"] = f"game folder {assets} does not exist"
        return launch

    target = evidence_dir / name
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["TMPDIR"] = str(tmp_dir)
    cmd = [str(binary), *(scenario.get("args") or [])]
    started = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=str(assets), env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=timeout, check=False)
        output = proc.stdout or b""
        exit_code: Any = proc.returncode
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or b""
        if isinstance(output, str):
            output = output.encode("utf-8", "replace")
        exit_code = "TIMEOUT"
    except OSError as exc:
        launch.update({"executed": True, "exit_code": "LAUNCH_ERROR",
                       "command": cmd, "reason": f"CLI launch failed: {exc}"})
        return launch
    log_path = target / "run.log"
    log_path.write_bytes(output)
    launch.update({
        "executed": True, "exit_code": exit_code,
        "seconds": round(time.time() - started, 1),
        "evidence": str(target), "log": str(log_path), "command": cmd,
        "output_sha256": hashlib.sha256(output).hexdigest(),
        "stdout_tail": output.decode("utf-8", "replace")[-3000:],
        "env": {"TMPDIR": str(tmp_dir)}, "reason": "",
    })
    return launch


def frame_records(run: dict, proof: dict) -> list[dict]:
    """Exact per-frame hash, dimensions, colour count and marker (no invention)."""
    rows: list[dict] = []
    by_path = {str(row.get("path")): row for row in proof.get("frames", [])
               if isinstance(row, dict)}
    for frame in run.get("frames", []):
        path = str(frame.get("path") or "")
        row = by_path.get(path, {})
        marker, digest, colours = size_tier_report.frame_evidence(
            {"path": path, "proof": row})
        rows.append({
            "path": path,
            "sha256": digest,
            "width": row.get("width", frame.get("width")),
            "height": row.get("height", frame.get("height")),
            "distinct_colors": colours,
            "blank": bool(row.get("blank")),
            "marker": marker,
            "exists": bool(path) and Path(path).is_file(),
        })
    return rows


def frames_summary(run: dict, frames: list[dict]) -> dict:
    proven = [f for f in frames if f["marker"].startswith("OK")]
    blank = sum(1 for f in frames if f["marker"].startswith("BLANK"))
    unproven = sum(1 for f in frames
                   if f["marker"].startswith(("UNPROVEN", "FAIL")))
    return {
        "runner_frame_count": run.get("frame_count"),
        "count": len(frames), "joined_frame_count": len(frames),
        "proven_count": len(proven), "proven_frame_count": len(proven),
        "blank_count": blank, "blank_frame_count": blank,
        "unproven_count": unproven, "unproven_frame_count": unproven,
        "frames": frames,
        "example": proven[0] if proven else None,
    }


def import_record(original: dict, coverage: dict[str, dict]) -> dict:
    """Declared PE imports plus registration-only unresolved system imports."""
    digest = am.norm_sha(original.get("sha256"))
    cov = coverage.get(digest or "", {}) if digest else {}
    return {
        "declared_dlls": list(original.get("import_dlls") or []),
        "declared_count": original.get("import_count"),
        "unresolved_system_imports": int(cov.get("missing_system_imports") or 0),
        "unresolved_import_names": list(cov.get("missing_import_names") or []),
        "source": cov.get("source", {}).get("path") if cov else None,
        "registration_only": True,
        "status": ("UNPROVEN" if not cov else
                   ("UNRESOLVED" if cov.get("missing_system_imports") else "CLEAR")),
        "reason": ("no API coverage supplied; unresolved imports unproven"
                   if not cov else
                   "registration-only audit; not a link-time proof"),
    }


def classify_stress(record: dict, oracle: dict | None) -> tuple[str, list[str]]:
    """Fail-closed final verdict; only a validated Windows oracle can grant PASS."""
    base = record["acceptance"]["status"]
    reasons = list(record["acceptance"]["reasons"])
    if base in (BLOCKED, FAIL, BUILD_ONLY, NOT_TESTED):
        return base, reasons + [f"no oracle can promote {base} evidence"]

    missing: list[str] = []
    if oracle is None:
        missing.append("no supplied original-Windows oracle evidence for this game")
    else:
        if am.key_of(oracle.get("game")) != am.key_of(record.get("game")):
            missing.append("oracle claim names a different game")
        if not oracle.get("valid"):
            missing.extend(oracle.get("reasons") or
                           ["oracle evidence failed validation"])
        checks = oracle.get("checks") or {}
        if not checks.get("has_real_oracle_flag"):
            missing.append("oracle_verified is not the boolean true")
        if not checks.get("has_real_oracle_name"):
            missing.append("oracle name is not an accepted original-Windows oracle")
        if not checks.get("has_scenario"):
            missing.append("no named scenario in the oracle claim")
        if not checks.get("has_scenario_evidence"):
            missing.append("no hash-matched scenario evidence file")
        if not (checks.get("has_evidence_file")
                and checks.get("evidence_hash_matches")):
            missing.append("scenario evidence SHA-256 was not recomputed and matched")

        input_sha = am.norm_sha(record["original"].get("sha256"))
        native_sha = am.norm_sha(record["native"].get("sha256"))
        if not input_sha or input_sha != oracle.get("input_sha256"):
            missing.append("oracle input SHA-256 does not match the original PE")
        if not native_sha or native_sha != oracle.get("native_sha256"):
            missing.append("oracle native SHA-256 does not match the launched binary")
        pipeline_sha = am.norm_sha(record["pipeline"].get("native_sha256"))
        if pipeline_sha and pipeline_sha != native_sha:
            missing.append("native SHA-256 differs from the pipeline build hash")
        if record["pipeline"].get("stage_failed"):
            missing.append(f"pipeline stage {record['pipeline']['stage_failed']!r} failed")
        if record["imports"].get("unresolved_system_imports"):
            missing.append(f"{record['imports']['unresolved_system_imports']} "
                           "unresolved system imports in API coverage")

    if record.get("launch", {}).get("exit_code") not in ca.NONFAILING_EXITS:
        missing.append(f"launch exit {record.get('launch', {}).get('exit_code')!s} "
                       "is not a non-failing exit")
    proven = (record["frames"].get("proven_count")
              or record["frames"].get("proven_frame_count"))
    if not (proven or record.get("cli_evidence", {}).get("reproduced")):
        missing.append("no proven, non-blank frame or reproduced CLI scenario")

    if missing:
        return PARTIAL, reasons + [f"PASS withheld: {text}" for text in missing] + [
            "no validated original-Windows oracle comparison; PARTIAL is "
            "render/process evidence only, not gameplay acceptance"]
    return PASS, reasons + [
        f"validated original-Windows oracle {oracle['oracle']!r} for scenario "
        f"{oracle['scenario']!r}",
        f"original SHA-256 {oracle['input_sha256']} and native SHA-256 "
        f"{oracle['native_sha256']} match the recorded evidence",
        f"scenario evidence {oracle['evidence_path']} SHA-256 "
        f"{oracle['evidence_sha256']} recomputed and matched",
    ]


def not_tested_record(game: dict, tag: str, out_dir: Path, reason: str,
                      generated_at: str) -> dict:
    """A discovered game excluded by --limit: no evidence, no invented result."""
    original = ca.original_record(game["path"])
    journal = out_dir / "games" / f"{tag}.json"
    record: dict[str, Any] = {
        "game": game["name"], "tag": tag, "verdict": NOT_TESTED,
        "status": NOT_TESTED, "reasons": [reason],
        "original": original, "pipeline": ca.pipeline_record(None, original),
        "imports": import_record(original, {}),
        "native": {"path": None, "sha256": None, "size": None,
                   "source": None, "reason": "not attempted"},
        "launch": {"executed": False, "exit_code": None, "seconds": None,
                   "reason": "not attempted"},
        "frames": frames_summary({}, []), "cli_evidence": {},
        "acceptance": {"status": NOT_TESTED, "reasons": [reason]},
        "oracle": None, "checks": {},
        "commands": {}, "work_dir": None, "tmp_dir": None,
        "project_dir": None, "json": str(journal), "generated_at": generated_at,
        "seconds": 0.0,
    }
    return record


def process_game(game: dict, tag: str, out_dir: Path, *, timeout: int,
                 execute: bool, run_pipeline: bool, pipeline_timeout: float,
                 generated_at: str, native_map: dict[str, str],
                 report_map: dict[str, str], scenario_map: dict[str, str],
                 coverage: dict[str, dict],
                 oracles: list[dict]) -> dict:
    """Process one game into an isolated work tree; never raise to the caller."""
    name = game["name"]
    work = out_dir / "work" / tag
    tmp_dir = work / "tmp"
    project = work / "project"
    evidence = work / "evidence"
    journal = out_dir / "games" / f"{tag}.json"
    for folder in (tmp_dir, project, evidence, journal.parent):
        folder.mkdir(parents=True, exist_ok=True)

    side = sidecars(game)
    original = ca.original_record(game["path"])
    pipeline: dict = ca.pipeline_record(
        report_map.get(name) or side.get("pipeline_report"), original)
    commands: dict[str, Any] = {}
    if run_pipeline and original.get("path"):
        invoked = invoke_pipeline(
            game["name"], game["path"], project, tmp_dir, pipeline_timeout)
        commands["pipeline"] = invoked.get("command")
        if invoked.get("report"):
            pipeline = ca.pipeline_record(invoked["report"], original)
            if invoked.get("reason"):
                pipeline["reasons"].append(invoked["reason"])
        else:
            pipeline["status"] = invoked["status"]
            pipeline["reasons"].append(invoked.get("reason", ""))
            if not pipeline.get("blocking_reasons"):
                pipeline["blocking_reasons"] = [invoked.get("reason", "")]

    native = ca.native_record(
        native_map.get(name) or side.get("native"), pipeline)
    scenario_path = scenario_map.get(name) or side.get("scenario")
    scenario = ca.scenario_record(scenario_path)
    # Declaring a scenario is the explicit opt-in to render-free CLI evidence.
    kind = ca.KIND_CLI if scenario["declared"] else ca.KIND_GUI
    imports = import_record(original, coverage)
    cli_evidence: dict = {}
    analysis: dict | None = None

    if ca.uses_cli_evidence(kind, scenario):
        launch = run_cli_launch(
            name, native, Path(game["dir"]), evidence, tmp_dir, timeout,
            execute, scenario)
        frames = frames_summary(launch, [])
        commands["launch"] = launch.get("command")
        cli_evidence = ca.cli_evidence(scenario, launch)
    else:
        launch = {
            "executed": False, "exit_code": None, "seconds": None,
            "reason": "launch not requested (--no-run)",
        }
        frame_list: list[dict] = []
        if execute and native.get("path") and Path(native["path"]).is_file():
            run, analysis, frame_list = run_launch(
                name, Path(native["path"]), Path(game["dir"]), evidence,
                tmp_dir, timeout)
            commands["launch"] = run.get("command")
            launch = {
                "executed": True, "exit_code": run.get("exit_code"),
                "seconds": run.get("seconds"), "evidence": run.get("evidence"),
                "frame_count": run.get("frame_count"),
                "log": str(evidence / "run.log"), "command": run.get("command"),
                "stdout_tail": run.get("stdout_tail"),
                "reason": ("" if run.get("exit_code") in ca.NONFAILING_EXITS
                           else f"launch exit {run.get('exit_code')!s} is not "
                                "a clean exit"),
            }
        frames = frames_summary(launch, frame_list)

    base_status, base_reasons = ca.classify(
        original, pipeline, native, launch, frames, analysis,
        kind=kind, scenario=scenario)
    record: dict[str, Any] = {
        "game": name, "tag": tag,
        "original": original, "pipeline": pipeline, "imports": imports,
        "native": native, "launch": launch, "frames": frames,
        "kind": kind, "scenario": scenario, "cli_evidence": cli_evidence,
        "acceptance": {"status": base_status, "reasons": base_reasons},
        "commands": commands, "work_dir": str(work), "tmp_dir": str(tmp_dir),
        "project_dir": str(project), "json": str(journal),
        "generated_at": generated_at,
        "seconds": launch.get("seconds") or 0.0,
    }
    oracle = oracle_for(oracles, name)
    verdict, reasons = classify_stress(record, oracle)
    record["verdict"] = verdict
    record["status"] = verdict
    record["reasons"] = reasons
    record["oracle"] = (
        {"game": oracle.get("game"), "oracle": oracle.get("oracle"),
         "verified": bool((oracle.get("checks") or {}).get(
             "has_real_oracle_flag")),
         "scenario": oracle.get("scenario"),
         "evidence_path": oracle.get("evidence_path"),
         "valid": bool(oracle.get("valid")),
         "source": (oracle.get("source") or {}).get("path")}
        if oracle else None)
    record["checks"] = dict(oracle.get("checks") or {}) if oracle else {}
    journal.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def build_stress(games: list[dict], out_dir: Path, *, timeout: int = 12,
                 execute: bool = True, jobs: int = 1, run_pipeline: bool = False,
                 pipeline_timeout: float = 60.0, generated_at: str | None = None,
                 native_map: dict[str, str] | None = None,
                 report_map: dict[str, str] | None = None,
                 scenario_map: dict[str, str] | None = None,
                 oracle_records: list[dict] | None = None,
                 coverage: dict[str, dict] | None = None,
                 extra_inputs: list[dict] | None = None,
                 tool_command: list[str] | None = None,
                 limit: int = 0) -> dict:
    """Run the batch and assemble a deterministic, evidence-only report."""
    out_dir = Path(out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    generated = generated_at or utc_now()
    side_oracles, side_coverage, side_inputs = load_sidecar_evidence(games)
    oracles = list(oracle_records or []) + side_oracles
    cov = dict(coverage or {})
    cov.update(side_coverage)
    inputs = list(extra_inputs or []) + side_inputs

    selected = games[:limit] if limit and limit > 0 else list(games)
    skipped = games[len(selected):]
    tagged = [(f"{index:05d}-{safe_slug(game['name'])}", game)
              for index, game in enumerate(selected, 1)]

    records: dict[str, dict] = {}
    if jobs > 1 and len(tagged) > 1:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {
                pool.submit(process_game, game, tag, out_dir,
                            timeout=timeout, execute=execute,
                            run_pipeline=run_pipeline,
                            pipeline_timeout=pipeline_timeout,
                            generated_at=generated,
                            native_map=native_map or {},
                            report_map=report_map or {},
                            scenario_map=scenario_map or {},
                            coverage=cov, oracles=oracles): tag
                for tag, game in tagged
            }
            for future in as_completed(futures):
                tag = futures[future]
                try:
                    records[tag] = future.result()
                except Exception as exc:  # never drop a game silently
                    game = next(g for t, g in tagged if t == tag)
                    records[tag] = _exception_record(
                        game, tag, out_dir, exc, generated)
    else:
        for tag, game in tagged:
            try:
                records[tag] = process_game(
                    game, tag, out_dir, timeout=timeout, execute=execute,
                    run_pipeline=run_pipeline, pipeline_timeout=pipeline_timeout,
                    generated_at=generated, native_map=native_map or {},
                    report_map=report_map or {}, scenario_map=scenario_map or {},
                    coverage=cov, oracles=oracles)
            except Exception as exc:  # never drop a game silently
                records[tag] = _exception_record(game, tag, out_dir, exc, generated)

    for index, game in enumerate(skipped, len(tagged) + 1):
        tag = f"{index:05d}-{safe_slug(game['name'])}"
        reason = f"excluded by --limit {limit}; no evidence collected"
        records[tag] = not_tested_record(game, tag, out_dir, reason, generated)
        (out_dir / "games" / f"{tag}.json").write_text(
            json.dumps(records[tag], indent=2) + "\n", encoding="utf-8")

    ordered = [records[tag] for tag in sorted(records)]
    counts = {status: sum(1 for r in ordered if r["verdict"] == status)
              for status in STATUSES}
    return {
        "schema": SCHEMA_VERSION,
        "generator": GENERATOR,
        "generated_at": generated,
        "tool_command": list(tool_command or []),
        "parameters": {
            "timeout": timeout, "execute": execute, "jobs": jobs,
            "run_pipeline": run_pipeline, "pipeline_timeout": pipeline_timeout,
            "limit": limit,
        },
        "inputs": sorted(inputs, key=lambda s: (str(s["kind"]), str(s["path"]))),
        "games": ordered,
        "summary": {"total": len(games), "attempted": len(tagged),
                    "by_status": counts},
    }


def _exception_record(game: dict, tag: str, out_dir: Path, exc: Exception,
                      generated_at: str) -> dict:
    """A harness fault is always recorded as BLOCKED with the exception text."""
    original = ca.original_record(game["path"])
    journal = out_dir / "games" / f"{tag}.json"
    reason = (f"harness exception: {type(exc).__name__}: "
              f"{str(exc)[:300]}")
    record = not_tested_record(game, tag, out_dir, reason, generated_at)
    record.update({
        "verdict": BLOCKED, "status": BLOCKED, "original": original,
        "reasons": [reason], "acceptance": {"status": BLOCKED,
                                            "reasons": [reason]},
    })
    journal.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def render_markdown(report: dict) -> str:
    """Aggregate Markdown with the exact commands and per-game evidence."""
    lines = [
        "# Corpus stress batch report", "",
        f"Generated: {report['generated_at']}  ",
        f"Schema: {report['schema']}  ",
        "Every verdict is derived from recorded evidence only. PASS requires "
        "a supplied, validated original-Windows oracle with matching hashes "
        "and hash-matched scenario evidence; no build, frame or bare exit code "
        "can produce PASS on its own.", "",
        "## Exact command", "",
    ]
    command = report.get("tool_command") or []
    lines += ["```sh", " ".join(command) if command else "(library call)", "```", ""]
    lines += ["## Inputs", ""]
    if not report["inputs"]:
        lines.append("_No evidence inputs supplied._")
    for source in report["inputs"]:
        lines.append(f"- {source['kind']}: `{source.get('path')}` — "
                     f"{source['status']}"
                     + (f" ({source['detail']})" if source.get("detail") else ""))
    counts = report["summary"]["by_status"]
    lines += ["", "## Summary", "",
              f"- Games discovered: {report['summary']['total']}; "
              f"attempted: {report['summary']['attempted']}",
              "- " + "; ".join(f"{status}: {counts.get(status, 0)}"
                               for status in STATUSES),
              "", "## Games", ""]
    if not report["games"]:
        lines.append("_No games supplied; pass --games-root._")
    for game in report["games"]:
        original = game["original"]
        native = game["native"]
        launch = game["launch"]
        pipeline = game["pipeline"]
        imports = game["imports"]
        lines += [
            f"### {game['game']} — **{game['verdict']}**", "",
            f"- Original: `{original.get('path') or 'none'}` SHA-256 "
            f"`{original.get('sha256') or 'none'}` PE "
            f"`{original.get('pe_type') or original.get('status')}` arch "
            f"`{original.get('architecture') or '?'}`",
            f"- Pipeline: status={pipeline.get('status')} "
            f"verification=`{pipeline.get('verification_status')}` "
            f"score=`{pipeline.get('score')}` stages="
            f"`{pipeline.get('stage_status')}`",
            f"- Imports: declared={imports.get('declared_count')} "
            f"unresolved={imports.get('unresolved_system_imports')} "
            f"status={imports.get('status')} "
            f"({imports.get('reason')})",
            f"- Native: `{native.get('path') or 'none'}` SHA-256 "
            f"`{native.get('sha256') or 'none'}` size={native.get('size')}",
            f"- Launch: executed={launch.get('executed')} exit="
            f"`{launch.get('exit_code')}` seconds={launch.get('seconds')}",
            f"- Kind: `{game.get('kind', ca.KIND_GUI)}` "
            f"(acceptance status `{game['acceptance']['status']}`)",
            f"- Oracle: {game['oracle'] if game.get('oracle') else 'not supplied'}",
            "- Frames: "
            f"count={game['frames'].get('count')} "
            f"proven={game['frames'].get('proven_count')} "
            f"blank={game['frames'].get('blank_count')} "
            f"unproven={game['frames'].get('unproven_count')}",
        ]
        for frame in game["frames"].get("frames", []):
            lines.append(
                f"  - `{frame['path']}` sha256=`{frame['sha256']}` "
                f"{frame['width']}x{frame['height']} "
                f"colours={frame['distinct_colors']} — {frame['marker']}")
        lines.append("- Verdict reasons:")
        for reason in game["reasons"]:
            lines.append(f"  - {reason}")
        commands = game.get("commands") or {}
        if commands:
            lines.append("- Exact commands:")
            for key in ("pipeline", "launch"):
                if commands.get(key):
                    lines.append(f"  - {key}: "
                                 f"`{' '.join(str(c) for c in commands[key])}`")
            lines.append(f"  - isolated TMPDIR: `{game.get('tmp_dir')}`")
        lines.append("")
    lines += [
        "PASS requires a real Windows oracle, matching original/native hashes "
        "across all sources, a named scenario with an evidenced file whose hash "
        "is recomputed here, an evidenced launch and no unresolved system "
        "imports. PARTIAL means render or render-free process evidence only; "
        "NOT TESTED means no evidence was collected (e.g. excluded by --limit).",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--games-root", action="append", default=[], metavar="DIR",
                    help="directory to scan recursively for original .exe "
                         "(repeatable; read-only)")
    ap.add_argument("--limit", type=int, default=0,
                    help="process at most N games; remainder are NOT TESTED "
                         "(0 = all)")
    ap.add_argument("--jobs", type=int, default=1,
                    help="parallel per-game workers (default 1)")
    ap.add_argument("--out", required=True, type=Path,
                    help="output root for all generated work/evidence (CLI-supplied)")
    ap.add_argument("--timeout", type=int, default=12,
                    help="per-game native launch timeout in seconds (default 12)")
    ap.add_argument("--run-pipeline", action="store_true",
                    help="invoke tools.autorecomp for each original before launch")
    ap.add_argument("--no-run", action="store_true",
                    help="do not launch native binaries (records BUILD ONLY)")
    ap.add_argument("--pipeline-timeout", type=float, default=60.0,
                    help="autorecomp run-stage seconds (default 60)")
    ap.add_argument("--json", type=Path, help="write the aggregate JSON here")
    ap.add_argument("--markdown", type=Path, help="write the aggregate Markdown here")
    ap.add_argument("--oracle-evidence", action="append", default=[], metavar="FILE",
                    help="JSON with oracle records; the only PASS-capable input")
    ap.add_argument("--api-coverage", action="append", default=[], metavar="FILE",
                    help="api_coverage JSON (registration only; may withhold PASS)")
    ap.add_argument("--native", action="append", default=[], metavar="NAME=PATH",
                    help="native binary override for a game (repeatable)")
    ap.add_argument("--pipeline-report", action="append", default=[],
                    metavar="NAME=PATH", help="autorecomp report override (repeatable)")
    ap.add_argument("--scenario", action="append", default=[], metavar="NAME=PATH",
                    help="headless CLI scenario override (repeatable)")
    ap.add_argument("--generated-at", help="fixed UTC timestamp for deterministic runs")
    args = ap.parse_args(argv)

    if not args.games_root:
        print("argument error: supply at least one --games-root", file=sys.stderr)
        return 2
    if args.jobs < 1 or args.limit < 0:
        print("argument error: --jobs must be >= 1 and --limit >= 0",
              file=sys.stderr)
        return 2
    out = args.out.expanduser().resolve()
    for root in args.games_root:
        if _is_within(out, Path(root).expanduser()):
            print("argument error: --out must not be inside a --games-root "
                  f"({root}); user paths are never overwritten", file=sys.stderr)
            return 2
    try:
        natives = dict(ca.parse_assignment(v) for v in args.native)
        reports = dict(ca.parse_assignment(v) for v in args.pipeline_report)
        scenarios = dict(ca.parse_assignment(v) for v in args.scenario)
    except ValueError as exc:
        print(f"argument error: {exc}", file=sys.stderr)
        return 2

    games, root_inputs = discover_games(args.games_root)
    oracles, coverage, evidence_inputs = load_evidence(
        args.oracle_evidence, args.api_coverage)
    command = [sys.executable, "-m", "tools.corpus_stress",
               *(argv if argv is not None else sys.argv[1:])]
    report = build_stress(
        games, out, timeout=args.timeout, execute=not args.no_run,
        jobs=args.jobs, run_pipeline=args.run_pipeline,
        pipeline_timeout=args.pipeline_timeout,
        generated_at=args.generated_at, native_map=natives,
        report_map=reports, scenario_map=scenarios, oracle_records=oracles,
        coverage=coverage, extra_inputs=root_inputs + evidence_inputs,
        tool_command=command, limit=args.limit)
    markdown = render_markdown(report)
    for target, text in ((args.json, json.dumps(report, indent=2) + "\n"),
                         (args.markdown, markdown)):
        if target:
            target = Path(target).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
    print(markdown)
    counts = report["summary"]["by_status"]
    print("summary: " + "; ".join(f"{key}: {value}" for key, value
                                  in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
