#!/usr/bin/env python3
"""Fail-closed Windows-side scenario capture via a CLI-supplied Highball/Wine endpoint (issue #145).

This adapter *configures* a Wine endpoint from CLI-supplied paths only
(``--highball``, ``--bottle``, ``--engine``, ``--exe``), probes it, then drives
one deterministic guest scenario.  It records the **exact** argv executed, the
process exit code and raw stdout/stderr bytes whose SHA-256 digests are
recomputed locally from the bytes that actually arrived -- never from a value
reported by the runner.

Fail closed
-----------
* Missing/invalid CLI paths, missing ``--scope gui-frame`` frames, unhealthy
  endpoint, timeout, runner error or incomplete capture -> BLOCKED/FAILED.
* A blocked/failed record has ``records == []`` and null hash fields; it can
  never be promoted to PASS and never fabricates an artifact, hash or exit code.
* ``--scope`` is always labelled.  A console-only capture is labelled
  ``console-only`` with ``gui_frame_evidence: false`` and an explicit
  ``not_claimed`` string.  It is **never** presented as GUI/frame evidence.
* ``--scope gui-frame`` is only accepted when real frame files are supplied via
  ``--frame`` (hashed from their bytes).  Even then the oracle label is a
  Highball/Wine oracle, not a retail-Windows oracle, because Wine emulation is
  not the original Windows host.

The injected endpoint contract (``--endpoint module:attribute``) is::

    probe(request: ScenarioRequest) -> (bool, reason_string)
    capture(request: ScenarioRequest) -> CaptureOutcome

The default endpoint is :class:`HighballEndpoint`.  Tests inject a fake so no
Wine process is started.

Usage::

    python3 -m tools.highball_oracle_capture \
        --exe /path/to/arith-calc.exe --out out/capture.json \
        --highball /path/to/highball --bottle Games \
        --engine x64-crossover26.3-r27 --stdin-text 'hello world\n' \
        --native /path/to/rebuilt-native --timeout 90
    python3 -m tools.highball_oracle_capture --verify out/capture.json
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

SCHEMA = 1
GENERATOR = "tools/highball_oracle_capture.py"

STATUS_CAPTURED = "CAPTURED"
STATUS_BLOCKED = "BLOCKED"
STATUS_FAILED = "FAILED"
VERIFICATION_CAPTURED = "CAPTURED"
VERIFICATION_NOT_TESTED = "NOT TESTED"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BLOCKED = 2

SCOPE_CONSOLE = "console-only"
SCOPE_FRAME = "gui-frame"
SCOPES = (SCOPE_CONSOLE, SCOPE_FRAME)

# Deliberately NOT an accepted retail-Windows oracle name
# (tools.acceptance_matrix.REAL_ORACLES).  A Highball/Wine capture, even with
# frames, must not be able to promote a row to PASS as an original-Windows
# oracle.  This is the fail-closed guard against over-claiming.
ORACLE_CONSOLE = "highball-wine-console"
ORACLE_FRAME = "highball-wine-frame"

LIMITS = (
    "process-level Highball/Wine oracle; not retail Windows; console-only "
    "unless --scope gui-frame with real --frame artifacts; not gameplay, "
    "graphics, audio or input equivalence"
)
NOT_CLAIMED_CONSOLE = (
    "Console-only Highball/Wine capture. No GUI/frame, screenshot, audio, "
    "gameplay, graphics, input or retail-Windows oracle evidence is claimed."
)
NOT_CLAIMED_FRAME = (
    "Highball/Wine GUI-frame capture with caller-supplied real frame bytes. "
    "Still Wine emulation, not a retail-Windows original, and no audio, "
    "gameplay, input equivalence or generalization to other titles is claimed."
)

STDOUT_RAW = "stdout.raw"
STDERR_RAW = "stderr.raw"
STDIN_RAW = "stdin.raw"
GUEST_OUTPUT = "guest_output.txt"
DETAIL_JSON = "capture-detail.json"

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
HIGHBALL_BANNER_RE = re.compile(
    r"^Launching .+ in bottle .+\[engine: .+renderer: .+\]\.\.\.$", re.IGNORECASE)
HIGHBALL_ARGS_RE = re.compile(r"^Arguments: .+$", re.IGNORECASE)
HIGHBALL_EXIT_RE = re.compile(r"^exit=\d+ after \d+s . log: .+$", re.IGNORECASE)
WINE_NOISE_RE = re.compile(
    r"freetype|or equal to 2\.0\.5|msync:|wineboot:|import_dll|ZwLoadDriver|"
    r"failed to open|wine:|err:", re.IGNORECASE)
HIGHBALL_UNAVAILABLE_RE = re.compile(
    r"^error: (?:no bottles found|bottle .+ not found|wine binary not found|"
    r"no program specified|engine .+ not found)", re.IGNORECASE | re.MULTILINE)


class EndpointUnavailable(Exception):
    """The configured Highball endpoint failed its health probe."""


class CaptureError(Exception):
    """The endpoint runner failed or returned an incomplete/unsafe capture."""


class EndpointError(Exception):
    """Endpoint configuration (module:attribute) could not be loaded."""


@dataclass
class ScenarioRequest:
    """Everything the endpoint needs to drive one deterministic replay."""

    highball: str
    bottle: str
    engine: str | None
    exe: Path
    args: list[str] = field(default_factory=list)
    stdin: bytes = b""
    timeout: int = 90
    cwd: str = ""
    scope: str = SCOPE_CONSOLE
    native: Path | None = None
    frames: list[Path] = field(default_factory=list)
    game_id: str = ""
    scenario: str = ""


@dataclass
class RunOutcome:
    """Binary-safe result of one guest process (also what fake runners return)."""

    command: list[str]
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool = False


class Endpoint(Protocol):
    """Documented, injectable endpoint contract (see module docstring)."""

    def probe(self, request: ScenarioRequest) -> tuple[bool, str]:
        """Return ``(healthy, reason)`` for the configured endpoint."""
        ...

    def capture(self, request: ScenarioRequest) -> RunOutcome:
        """Replay the scenario and return the raw process result."""
        ...


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Exact SHA-256 of a file; raise OSError when unreadable (never guess)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_highball() -> str | None:
    """Resolve the Highball CLI. ``RECOMP_HIGHBALL`` overrides PATH lookup."""
    override = os.environ.get("RECOMP_HIGHBALL")
    if override:
        return override
    return shutil.which("highball")


def normalize_guest_output(stdout_text: str, stderr_text: str) -> str:
    """Strip runner chatter, ANSI and Wine noise; keep real guest lines."""
    def clean(text: str) -> list[str]:
        kept: list[str] = []
        for line in text.splitlines():
            if (HIGHBALL_BANNER_RE.match(line) or HIGHBALL_ARGS_RE.match(line)
                    or HIGHBALL_EXIT_RE.match(line)):
                continue
            if WINE_NOISE_RE.search(line):
                continue
            line = ANSI_RE.sub("", line).rstrip()
            if not line.strip():
                continue
            kept.append(line)
        return kept

    lines = clean(stdout_text) + clean(stderr_text)
    return "\n".join(lines) + "\n" if lines else ""


def run_highball(command: list[str], *, stdin: bytes, timeout: int,
                 cwd: str) -> RunOutcome:
    """Real process runner. Kills the whole process group on timeout."""
    try:
        proc = subprocess.Popen(
            command, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True)
    except OSError as exc:
        raise EndpointUnavailable(f"cannot start highball: {exc}") from exc

    try:
        out, err = proc.communicate(input=stdin, timeout=timeout)
        return RunOutcome(command=list(command), exit_code=proc.returncode,
                          stdout=out or b"", stderr=err or b"", timed_out=False)
    except subprocess.TimeoutExpired as exc:
        out, err = exc.output or b"", exc.stderr or b""
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        try:
            out2, err2 = proc.communicate(timeout=10)
            out, err = out or out2, err or err2
        except subprocess.TimeoutExpired:
            pass  # bounded partial output; it is not claimed as a result
        return RunOutcome(command=list(command), exit_code=None, stdout=out,
                          stderr=err, timed_out=True)


class HighballEndpoint:
    """Default endpoint: CLI-supplied Highball binary, bottle and engine."""

    def __init__(self, runner=None):
        self._runner = runner or run_highball

    def _query(self, highball: str, subcommand: list[str], timeout: int) -> str | None:
        try:
            proc = subprocess.run(
                [highball, *subcommand], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=min(timeout, 20), check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout.decode("utf-8", "replace")

    def probe(self, request: ScenarioRequest) -> tuple[bool, str]:
        highball = request.highball
        if not highball or not Path(highball).is_file() or not os.access(highball, os.X_OK):
            return False, "highball CLI not found or not executable"
        bottles = self._query(highball, ["bottle", "list"], request.timeout)
        if bottles is None:
            return False, "cannot query highball bottles"
        if request.bottle not in bottles:
            return False, f"bottle {request.bottle!r} not configured"
        if request.engine:
            engines = self._query(highball, ["engine", "list"], request.timeout)
            if engines is None:
                return False, "cannot query highball engines"
            if request.engine not in engines:
                return False, f"engine {request.engine!r} not installed"
        return True, "highball CLI, bottle and engine present"

    def capture(self, request: ScenarioRequest) -> RunOutcome:
        command = [request.highball, "run", "-b", request.bottle]
        if request.engine:
            command += ["-e", request.engine]
        command += [str(request.exe), *request.args]
        outcome = self._runner(command, stdin=request.stdin,
                               timeout=request.timeout, cwd=request.cwd)
        if not isinstance(outcome, RunOutcome):
            raise CaptureError("runner returned an invalid outcome")
        combined = (outcome.stdout.decode("utf-8", "replace") + "\n"
                    + outcome.stderr.decode("utf-8", "replace"))
        if HIGHBALL_UNAVAILABLE_RE.search(combined):
            raise CaptureError("highball reported it could not run the guest")
        return outcome


def load_endpoint(spec: str) -> Endpoint:
    """Load an endpoint from ``module:attribute`` (or ``highball`` builtin)."""
    if spec == "highball":
        return HighballEndpoint()
    if ":" not in spec:
        raise EndpointError("--endpoint must be 'highball' or 'module:attribute'")
    module_name, _, attr_name = spec.partition(":")
    if not module_name or not attr_name:
        raise EndpointError("--endpoint must be 'module:attribute'")
    try:
        obj = getattr(importlib.import_module(module_name), attr_name)
    except (ImportError, AttributeError) as exc:
        raise EndpointError("cannot load endpoint: " + type(exc).__name__) from None
    endpoint = obj() if isinstance(obj, type) else obj
    if not callable(getattr(endpoint, "probe", None)) or not callable(
            getattr(endpoint, "capture", None)):
        raise EndpointError("endpoint must provide probe() and capture()")
    return endpoint


def scope_for(scope: str) -> tuple[str, bool, str, str]:
    """Return ``(oracle_name, gui_frame_evidence, not_claimed, limits)``."""
    if scope == SCOPE_FRAME:
        return ORACLE_FRAME, True, NOT_CLAIMED_FRAME, LIMITS
    return ORACLE_CONSOLE, False, NOT_CLAIMED_CONSOLE, LIMITS


def check_scope_invariants(record: dict) -> list[str]:
    """Guard against ever presenting a console capture as GUI/frame evidence."""
    problems: list[str] = []
    scope = str(record.get("scope", ""))
    if scope not in SCOPES:
        problems.append(f"unknown or missing scope: {scope!r}")
        return problems
    frames = record.get("frames")
    if not isinstance(frames, list):
        problems.append("frames is not a list")
        frames = []
    flag = record.get("gui_frame_evidence")
    if scope == SCOPE_CONSOLE:
        if flag is not False:
            problems.append("console-only capture must have gui_frame_evidence=false")
        if frames:
            problems.append("console-only capture must not contain frame evidence")
        if record.get("oracle") != ORACLE_CONSOLE:
            problems.append("console-only capture has the wrong oracle label")
    else:
        if flag is not True:
            problems.append("gui-frame capture must have gui_frame_evidence=true")
        if not frames:
            problems.append("gui-frame capture has no real frame artifacts")
        if record.get("oracle") != ORACLE_FRAME:
            problems.append("gui-frame capture has the wrong oracle label")
    return problems


def _write_json(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _blocked_record(reason: str, *, status: str = STATUS_BLOCKED,
                    scope: str = SCOPE_CONSOLE) -> dict:
    """A record that can never grant PASS: no records and no fabricated hashes."""
    oracle, gui, not_claimed, limits = scope_for(scope)
    return {
        "schema": SCHEMA,
        "generator": GENERATOR,
        "status": status,
        "verification": VERIFICATION_NOT_TESTED,
        "reason": reason,
        "scope": scope,
        "oracle": oracle,
        # A blocked/failed attempt never claims frame evidence, even when the
        # caller requested gui-frame scope.
        "gui_frame_evidence": False,
        "oracle_verified": False,
        "command": None,
        "command_display": None,
        "exit_code": None,
        "timed_out": None,
        "highball": None,
        "bottle": None,
        "engine": None,
        "exe": None,
        "native": None,
        "stdin": None,
        "raw": {},
        "guest_output": None,
        "guest_output_sha256": None,
        "frames": [],
        "artifacts_dir": None,
        "evidence_path": None,
        "evidence_sha256": None,
        "not_claimed": not_claimed,
        "limits": limits,
        "records": [],
    }


def build_capture_record(*, request: ScenarioRequest, outcome: RunOutcome,
                         probe_reason: str, artifacts_dir: Path,
                         detail_path: Path) -> dict:
    """Persist real bytes and build a tamper-evident CAPTURED record.

    Every digest is computed here from bytes that were actually captured or
    read from a CLI-supplied file; no runner-supplied digest is trusted.
    """
    artifacts_dir = Path(artifacts_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    exe_sha = sha256_file(request.exe)
    native_sha = sha256_file(request.native) if request.native else None

    raw_refs: dict[str, dict] = {}
    for key, blob, filename in (("stdout", outcome.stdout, STDOUT_RAW),
                                ("stderr", outcome.stderr, STDERR_RAW),
                                ("stdin", request.stdin, STDIN_RAW)):
        target = artifacts_dir / filename
        target.write_bytes(blob)
        actual = _sha(blob)
        if sha256_file(target) != actual:
            raise CaptureError(f"artifact write verification failed: {filename}")
        raw_refs[key] = {"file": filename, "bytes": len(blob), "sha256": actual}

    block = normalize_guest_output(
        outcome.stdout.decode("utf-8", "replace"),
        outcome.stderr.decode("utf-8", "replace"))
    (artifacts_dir / GUEST_OUTPUT).write_text(block, encoding="utf-8")
    guest_sha = _sha(block.encode("utf-8"))

    frame_refs: list[dict] = []
    if request.scope == SCOPE_FRAME:
        for index, frame in enumerate(request.frames):
            data = frame.read_bytes()
            name = f"{index:03d}-{frame.name}"
            target = artifacts_dir / name
            target.write_bytes(data)
            actual = _sha(data)
            if sha256_file(target) != actual:
                raise CaptureError(f"frame write verification failed: {name}")
            frame_refs.append({"name": frame.name, "file": name,
                               "bytes": len(data), "sha256": actual})

    oracle, gui, not_claimed, limits = scope_for(request.scope)
    command = list(outcome.command)
    detail = {
        "schema": SCHEMA,
        "generator": GENERATOR,
        "status": STATUS_CAPTURED,
        "scope": request.scope,
        "oracle": oracle,
        "gui_frame_evidence": gui,
        "probe": {"ok": True, "reason": probe_reason},
        "endpoint": {
            "kind": "highball-wine",
            "highball": request.highball,
            "bottle": request.bottle,
            "engine": request.engine,
        },
        "command": command,
        "command_display": shlex.join(command),
        "exe": {"path": str(request.exe), "sha256": exe_sha},
        "native": ({"path": str(request.native), "sha256": native_sha}
                   if request.native else None),
        "args": list(request.args),
        "stdin": raw_refs["stdin"],
        "exit_code": outcome.exit_code,
        "timed_out": outcome.timed_out,
        "raw": {"stdout": raw_refs["stdout"], "stderr": raw_refs["stderr"]},
        "guest_output": block,
        "guest_output_sha256": guest_sha,
        "frames": frame_refs,
        "not_claimed": not_claimed,
        "limits": limits,
    }
    detail_path = Path(detail_path)
    _write_json(detail_path, detail)
    detail_sha = sha256_file(detail_path)

    scenario_evidence = (
        f"Highball/Wine scenario {request.scenario!r} ({request.scope}) for "
        f"{request.game_id!r}: exact command {shlex.join(command)!r}, exit "
        f"{outcome.exit_code}; stdout SHA-256 {raw_refs['stdout']['sha256']}, "
        f"stderr SHA-256 {raw_refs['stderr']['sha256']}, recomputed locally from "
        f"the captured bytes. {len(frame_refs)} real frame artifacts. "
        "Wine process-level capture; " + not_claimed
    )
    entry = {
        "game": request.game_id,
        "game_id": request.game_id,
        "sha256": exe_sha,
        "input_sha256": raw_refs["stdin"]["sha256"],
        "native_sha256": native_sha,
        "oracle": oracle,
        "oracle_verified": True,
        "scope": request.scope,
        "gui_frame_evidence": gui,
        "scenario": request.scenario,
        "scenario_evidence": scenario_evidence,
        "evidence_path": str(detail_path),
        "evidence_sha256": detail_sha,
        "exit_code": outcome.exit_code,
        "input_script_sha256": raw_refs["stdin"]["sha256"],
        "not_claimed": not_claimed,
    }
    record = dict(detail)
    record.update({
        "highball": request.highball,
        "bottle": request.bottle,
        "engine": request.engine,
        "verification": VERIFICATION_CAPTURED,
        "reason": None,
        "oracle_verified": True,
        "artifacts_dir": str(artifacts_dir),
        "evidence_path": str(detail_path),
        "evidence_sha256": detail_sha,
        "records": [entry],
    })
    return record


def verify_record(record_path: Path) -> list[str]:
    """Return integrity problems for a record (empty list == intact)."""
    record_path = Path(record_path)
    problems: list[str] = []
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        return [f"cannot read record: {type(exc).__name__}: {exc}"]
    if not isinstance(record, dict):
        return ["record is not a JSON object"]

    records = record.get("records")
    if record.get("status") != STATUS_CAPTURED:
        return ["record status is not CAPTURED; nothing was captured"]
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        return ["record has no single oracle-evidence entry (nothing was captured)"]
    entry = records[0]

    def check_file(label: str, path: Path, claimed: Any, size: Any = None) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            problems.append(f"{label} file is missing: {path}")
            return
        actual = _sha(data)
        if not isinstance(claimed, str) or actual != claimed:
            problems.append(f"{label} hash mismatch: recorded={claimed} actual={actual}")
        if size is not None and len(data) != size:
            problems.append(f"{label} byte-count mismatch: recorded={size} actual={len(data)}")

    artifacts_dir_raw = record.get("artifacts_dir")
    if not artifacts_dir_raw:
        problems.append("record has no artifacts_dir")
        artifacts_dir = None
    else:
        artifacts_dir = Path(artifacts_dir_raw)

    # Evidence detail file + its hash (the anchor for all other claims).
    evidence_raw = str(entry.get("evidence_path", ""))
    if not evidence_raw:
        problems.append("no evidence_path in record")
        detail = None
    else:
        evidence_path = Path(evidence_raw)
        check_file("evidence", evidence_path, entry.get("evidence_sha256"))
        try:
            detail = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            problems.append("capture detail file is missing or invalid JSON")
            detail = None
        if not isinstance(detail, dict):
            problems.append("capture detail is not a JSON object")
            detail = None

    if artifacts_dir is not None:
        raw_meta = record.get("raw", {})
        if not isinstance(raw_meta, dict):
            problems.append("record raw metadata is not an object")
            raw_meta = {}
        for key in ("stdout", "stderr", "stdin"):
            if key == "stdin":
                # stdin metadata is stored at top level, not under "raw".
                meta = record.get("stdin") if isinstance(record.get("stdin"), dict) \
                    else (detail.get("stdin", {}) if isinstance(detail, dict) else {})
            else:
                detail_raw = detail.get("raw", {}) if isinstance(detail, dict) else {}
                meta = detail_raw.get(key) or raw_meta.get(key, {})
            if not isinstance(meta, dict):
                problems.append(f"raw {key} metadata is invalid")
                continue
            filename = str(meta.get("file", f"{key}.raw"))
            if Path(filename).name != filename or filename in ("", ".", ".."):
                problems.append(f"unsafe {key} artifact name: {filename!r}")
                continue
            check_file(key, artifacts_dir / filename, meta.get("sha256"), meta.get("bytes"))

        frames = record.get("frames", [])
        if not isinstance(frames, list):
            problems.append("frames is not a list")
            frames = []
        for index, frame in enumerate(frames):
            if not isinstance(frame, dict):
                problems.append(f"frame {index} is not an object")
                continue
            filename = str(frame.get("file", ""))
            if Path(filename).name != filename or filename in ("", ".", ".."):
                problems.append(f"unsafe frame name: {filename!r}")
                continue
            check_file(f"frame {index}", artifacts_dir / filename,
                       frame.get("sha256"), frame.get("bytes"))

    if isinstance(detail, dict):
        block = detail.get("guest_output", "")
        if not isinstance(block, str):
            problems.append("guest_output is not text")
        else:
            claimed = detail.get("guest_output_sha256")
            if not isinstance(claimed, str) or _sha(block.encode("utf-8")) != claimed:
                problems.append("guest_output sha256 mismatch")
        for key in ("exe", "native"):
            meta = detail.get(key)
            if meta is None and key == "native":
                continue
            if not isinstance(meta, dict) or not meta.get("path"):
                problems.append(f"detail has no {key} path")
                continue
            check_file(key, Path(meta["path"]), meta.get("sha256"))
        record_exe = record.get("exe")
        if not isinstance(record_exe, dict) or \
                record_exe.get("sha256") != (detail.get("exe") or {}).get("sha256"):
            problems.append("exe hash disagrees with capture detail")
        if record.get("native") is not None and detail.get("native") is not None:
            if record["native"].get("sha256") != detail["native"].get("sha256"):
                problems.append("native hash disagrees with capture detail")

    # Cross-check the top-level claim against the detail / entry.
    for key in ("scope", "oracle", "gui_frame_evidence", "exit_code",
                "command", "not_claimed"):
        if isinstance(detail, dict) and record.get(key) != detail.get(key):
            problems.append(f"{key} disagrees with capture detail")
    if isinstance(detail, dict) and isinstance(detail.get("stdin"), dict) \
            and detail["stdin"].get("path"):
        check_file("stdin-claim", Path(detail["stdin"]["path"]),
                   entry.get("input_sha256"))
    if entry.get("scope") != record.get("scope"):
        problems.append("oracle entry scope disagrees with the record")
    if entry.get("gui_frame_evidence") != record.get("gui_frame_evidence"):
        problems.append("oracle entry gui_frame_evidence disagrees with the record")
    if entry.get("input_script_sha256") != entry.get("input_sha256"):
        problems.append("input hashes disagree inside the oracle entry")
    if entry.get("sha256") != (detail or {}).get("exe", {}).get("sha256"):
        problems.append("oracle entry exe hash disagrees with capture detail")
    if entry.get("native_sha256") != ((detail or {}).get("native") or {}).get("sha256"):
        problems.append("oracle entry native hash disagrees with capture detail")
    if entry.get("oracle_verified") is not True:
        problems.append("oracle entry is not marked as executed")

    problems.extend(check_scope_invariants(record))
    return problems


def _emit_failure(out: Path | None, record: dict, message: str, code: int) -> int:
    print(message, file=sys.stderr)
    if out is not None:
        try:
            _write_json(out, record)
        except OSError as exc:
            print(f"error: cannot write record: {type(exc).__name__}", file=sys.stderr)
    return code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", type=Path, help="Windows executable to run under Highball")
    ap.add_argument("--arg", action="append", default=[], help="guest argument (repeatable)")
    ap.add_argument("--stdin-file", type=Path, help="file whose bytes feed guest stdin")
    ap.add_argument("--stdin-text", help="inline text fed to guest stdin")
    ap.add_argument("--out", type=Path, help="scenario record JSON to write")
    ap.add_argument("--native", type=Path, help="rebuilt native binary to hash (optional)")
    ap.add_argument("--highball", help="path to the Highball CLI (default: env/PATH)")
    ap.add_argument("--bottle", default="Games", help="Highball bottle (default: Games)")
    ap.add_argument("--engine", default=None, help="Highball engine id (default: bottle's)")
    ap.add_argument("--scope", choices=SCOPES, default=SCOPE_CONSOLE,
                    help="evidence scope label (default: console-only)")
    ap.add_argument("--frame", action="append", default=[], type=Path,
                    help="real GUI frame artifact (repeatable; required for gui-frame)")
    ap.add_argument("--timeout", type=int, default=90, help="run timeout in seconds")
    ap.add_argument("--game-id", default=None, help="game identifier (default: exe stem)")
    ap.add_argument("--scenario", default=None, help="scenario name (default: game id)")
    ap.add_argument("--endpoint", default="highball",
                    help="'highball' (default) or 'module:attribute' injectable endpoint")
    ap.add_argument("--verify", type=Path, metavar="RECORD",
                    help="verify an existing record's integrity and exit")
    args = ap.parse_args(argv)

    if args.verify is not None:
        problems = verify_record(args.verify)
        if problems:
            for problem in problems:
                print(f"TAMPERED: {problem}", file=sys.stderr)
            return EXIT_FAILED
        print(f"OK: {args.verify}")
        return EXIT_OK

    if args.exe is None or args.out is None:
        ap.error("--exe and --out are required unless --verify is given")
    if args.stdin_file is not None and args.stdin_text is not None:
        ap.error("--stdin-file and --stdin-text are mutually exclusive")
    if args.timeout < 1:
        ap.error("--timeout must be a positive integer")

    out = args.out
    scope = args.scope
    game_id = args.game_id or args.exe.stem
    scenario = args.scenario or game_id

    # Fail closed, in priority order, before any process is started.
    if scope == SCOPE_FRAME and not args.frame:
        return _emit_failure(
            out, _blocked_record("gui-frame scope requires at least one real --frame file",
                                 scope=scope),
            "BLOCKED: gui-frame scope requires real --frame artifacts; NOT TESTED",
            EXIT_BLOCKED)
    if not args.exe.is_file():
        return _emit_failure(
            out, _blocked_record(f"executable not found: {args.exe}", scope=scope),
            f"BLOCKED: executable not found: {args.exe}; NOT TESTED", EXIT_BLOCKED)
    for frame in args.frame:
        if not frame.is_file():
            return _emit_failure(
                out, _blocked_record(f"frame artifact not found: {frame}", scope=scope),
                f"BLOCKED: frame artifact not found: {frame}; NOT TESTED", EXIT_BLOCKED)
    if args.native is not None and not args.native.is_file():
        return _emit_failure(
            out, _blocked_record(f"native binary not found: {args.native}", scope=scope),
            f"BLOCKED: native binary not found: {args.native}; NOT TESTED", EXIT_BLOCKED)

    highball = args.highball or find_highball()
    if not highball or not Path(highball).is_file():
        return _emit_failure(
            out, _blocked_record("highball CLI not found (set --highball or RECOMP_HIGHBALL)",
                                 scope=scope),
            "BLOCKED: highball CLI not found; NOT TESTED", EXIT_BLOCKED)

    if args.stdin_file is not None:
        try:
            stdin_b = args.stdin_file.read_bytes()
        except OSError:
            return _emit_failure(
                out, _blocked_record("cannot read --stdin-file", scope=scope),
                "BLOCKED: cannot read stdin file; NOT TESTED", EXIT_BLOCKED)
    elif args.stdin_text is not None:
        stdin_b = args.stdin_text.encode("utf-8")
    else:
        stdin_b = b""

    cwd = str(args.exe.parent) if args.exe.is_absolute() else str(Path.cwd())
    request = ScenarioRequest(
        highball=highball, bottle=args.bottle, engine=args.engine, exe=args.exe,
        args=[str(a) for a in args.arg], stdin=stdin_b, timeout=args.timeout,
        cwd=cwd, scope=scope, native=args.native, frames=list(args.frame),
        game_id=game_id, scenario=scenario,
    )

    try:
        endpoint = load_endpoint(args.endpoint)
    except EndpointError as exc:
        return _emit_failure(out, _blocked_record(f"endpoint error: {exc}", scope=scope),
                             f"BLOCKED: endpoint error: {exc}; NOT TESTED", EXIT_BLOCKED)

    try:
        healthy, probe_reason = endpoint.probe(request)
    except Exception as exc:
        healthy, probe_reason = False, "endpoint probe error: " + type(exc).__name__
    if not healthy:
        reason = f"endpoint unhealthy: {probe_reason}"
        return _emit_failure(out, _blocked_record(reason, scope=scope),
                             f"BLOCKED: {reason}; NOT TESTED", EXIT_BLOCKED)

    try:
        outcome = endpoint.capture(request)
        if not isinstance(outcome, RunOutcome):
            raise CaptureError("endpoint returned an invalid outcome")
        if outcome.timed_out or outcome.exit_code is None:
            raise CaptureError("guest produced no result (timeout/no exit code)")
        if outcome.exit_code < 0:
            raise CaptureError("guest was terminated by a signal")
        artifacts_dir = out.parent / f"{out.stem}-artifacts"
        detail_path = out.parent / f"{out.stem}-capture.json"
        record = build_capture_record(
            request=request, outcome=outcome, probe_reason=probe_reason,
            artifacts_dir=artifacts_dir, detail_path=detail_path)
    except (CaptureError, EndpointUnavailable, OSError) as exc:
        message = str(exc) or type(exc).__name__
        return _emit_failure(
            out, _blocked_record(f"capture failed: {message}", status=STATUS_FAILED,
                                 scope=scope),
            f"FAILED: capture failed: {message}; NOT TESTED", EXIT_FAILED)

    _write_json(out, record)
    problems = verify_record(out)
    if problems:
        return _emit_failure(
            out, _blocked_record("post-write integrity check failed: " + "; ".join(problems),
                                 status=STATUS_FAILED, scope=scope),
            "FAILED: post-write integrity check failed: " + "; ".join(problems),
            EXIT_FAILED)

    print(f"CAPTURED: {game_id} scenario {scenario} scope={scope} "
          f"exit={outcome.exit_code} "
          f"stdout_sha256={record['raw']['stdout']['sha256']} "
          f"stderr_sha256={record['raw']['stderr']['sha256']}")
    print(f"record: {out}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
