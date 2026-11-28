#!/usr/bin/env python3
"""Fail-closed Windows-oracle capture harness for A10 evidence (issue #145).

This tool drives a *CLI-supplied* Windows host/VM endpoint, replays a controlled
input script and records what the endpoint returns. It never runs a game itself,
never invents a screenshot, log, exit code or hash, and never claims a build or
an issue closure. Every path and every endpoint comes from the command line;
there are no host-specific defaults baked into this module.

Fail-closed policy
------------------
* No ``--endpoint`` supplied                    -> BLOCKED / NOT TESTED, exit 2.
* Endpoint supplied but unreachable or unhealthy -> BLOCKED / NOT TESTED, exit 2.
* Runner error or incomplete capture            -> FAILED / NOT TESTED, exit 1.
* Complete capture                              -> CAPTURED, exit 0.

A blocked/failed run writes a machine-readable record whose ``records`` list is
empty and whose artifact/hash fields are null: the fail-closed acceptance matrix
cannot promote a row from it. Real digests are computed locally from bytes that
actually arrived from the endpoint (or from the CLI-supplied input files).

Injectable runner interface
---------------------------
A runner is any object with these two methods::

    probe(endpoint: str, timeout: int) -> bool
    capture(request: OracleRequest) -> CaptureResult

``probe`` returns True only when the endpoint answers a health check. ``capture``
returns a mapping (or :class:`CaptureResult`) with keys ``process`` (non-empty
dict), ``exit_code`` (int) and optional ``screenshots`` / ``audio`` / ``logs``
lists of ``{"name": str, "data": bytes}`` entries.

The default runner is ``http`` (:class:`HttpOracleRunner`) which speaks this
small documented HTTP protocol on the supplied base URL:

* ``GET  <endpoint>/health`` -> ``200 {\"status\": \"ok\"}``
* ``POST <endpoint>/capture`` with a JSON body containing ``game_id``,
  ``scenario``, ``timeout``, base64 ``input_script`` and its SHA-256 plus the
  original/native SHA-256 digests. The response is JSON::

      {"process": {...}, "exit_code": 0,
       "screenshots": [{"name": "frame-0000.png", "data_b64": "..."}],
       "audio":       [{"name": "audio.wav",     "data_b64": "..."}],
       "logs":        [{"name": "stdout.log",    "data_b64": "..."}]}

Any other runner can be injected with ``--runner package.module:Attribute``;
the attribute may be a class (instantiated with no arguments) or a ready object.
This lets tests and alternate transports drive the harness without networking.

Output
------
``--out`` receives an oracle-evidence-compatible scenario record::

    {"schema": 1, "generator": ..., "status": "CAPTURED",
     "verification": "CAPTURED", "artifacts_dir": "...",
     "records": [{
        "game": "...", "sha256": "<original>", "native_sha256": "<native>",
        "oracle": "windows-oracle", "oracle_verified": true,
        "scenario": "...", "scenario_evidence": "...",
        "evidence_path": "<capture detail JSON>", "evidence_sha256": "..."
     }]}

The record is accepted by ``tools.acceptance_matrix --oracle-evidence``. Because
the harness cannot itself prove the endpoint is genuinely a retail Windows
host, ``oracle_verified`` means "a capture was actually recorded from the
CLI-supplied endpoint by this harness"; endpoint authenticity remains the
operator's responsibility and is stated in ``scenario_evidence``.

Tamper detection
----------------
``--verify RECORD`` recomputes the evidence-file hash, every artifact hash and
the input/original/native hashes and exits non-zero if any differ from the
recorded values. The same check is available as :func:`verify_record`.

Usage::

    python3 -m tools.windows_oracle \
        --endpoint http://windows-vm:8000 --game-id igi \
        --input-script script.json --original igi.exe --native build/igi \
        --out out/scenario.json --timeout 30
    python3 -m tools.windows_oracle --verify out/scenario.json
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import importlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

SCHEMA_VERSION = 1
GENERATOR = "tools/windows_oracle.py"
ORACLE_NAME = "windows-oracle"  # accepted by tools.acceptance_matrix.REAL_ORACLES

STATUS_CAPTURED = "CAPTURED"
STATUS_BLOCKED = "BLOCKED"
STATUS_FAILED = "FAILED"
VERIFICATION_CAPTURED = "CAPTURED"
VERIFICATION_NOT_TESTED = "NOT TESTED"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BLOCKED = 2

ARTIFACT_KINDS = ("screenshots", "audio", "logs")


class EndpointUnreachable(Exception):
    """The supplied endpoint did not answer a health check."""


class CaptureError(Exception):
    """The runner failed or returned an incomplete/unsafe capture."""


class RunnerError(Exception):
    """Runner configuration or execution failed."""


@dataclass
class OracleRequest:
    """Everything a runner needs to drive one deterministic replay."""

    endpoint: str
    game_id: str
    scenario: str
    timeout: int
    input_script: Path
    input_script_bytes: bytes
    input_script_sha256: str
    original: Path
    original_sha256: str
    native: Path
    native_sha256: str


@dataclass
class CaptureResult:
    """Raw bytes returned by a runner; the harness computes all digests."""

    process: dict
    exit_code: int
    screenshots: list[dict] = field(default_factory=list)
    audio: list[dict] = field(default_factory=list)
    logs: list[dict] = field(default_factory=list)


class OracleRunner(Protocol):
    """Documented, injectable transport contract (see module docstring)."""

    def probe(self, endpoint: str, timeout: int) -> bool:
        """Return True only when the endpoint answers a health check."""
        ...

    def capture(self, request: OracleRequest) -> CaptureResult:
        """Replay the scenario and return the raw captured artifacts."""
        ...


def _url(endpoint: str, path: str) -> str:
    return endpoint.rstrip("/") + "/" + path.lstrip("/")


class HttpOracleRunner:
    """Default runner: HTTP health check + base64 artifact POST."""

    def probe(self, endpoint: str, timeout: int) -> bool:
        parts = urllib.parse.urlsplit(endpoint)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return False
        try:
            with urllib.request.urlopen(_url(endpoint, "health"), timeout=timeout) as resp:
                if resp.status != 200:
                    return False
                body = json.load(resp)
        except (OSError, ValueError, urllib.error.URLError):
            return False
        return isinstance(body, dict) and str(body.get("status", "")).lower() in ("ok", "ready")

    def capture(self, request: OracleRequest) -> CaptureResult:
        payload = {
            "game_id": request.game_id,
            "scenario": request.scenario,
            "timeout": request.timeout,
            "input_script": base64.b64encode(request.input_script_bytes).decode("ascii"),
            "input_script_sha256": request.input_script_sha256,
            "original_sha256": request.original_sha256,
            "native_sha256": request.native_sha256,
        }
        req = urllib.request.Request(
            _url(request.endpoint, "capture"),
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=request.timeout) as resp:
                if resp.status != 200:
                    raise CaptureError(f"capture endpoint returned HTTP {resp.status}")
                data = json.load(resp)
        except urllib.error.HTTPError as exc:
            raise CaptureError(f"capture endpoint returned HTTP {exc.code}") from None
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise CaptureError("capture endpoint error: " + type(exc).__name__) from None
        return parse_capture_response(data)


def parse_capture_response(data: Any) -> CaptureResult:
    """Validate and decode a runner response; never accept fabricated digests."""
    if not isinstance(data, dict):
        raise CaptureError("capture response is not a JSON object")
    process = data.get("process")
    exit_code = data.get("exit_code")
    if not isinstance(process, dict) or not process:
        raise CaptureError("capture response has no process metadata")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise CaptureError("capture response has no integer exit_code")

    def decode(kind: str) -> list[dict]:
        entries = data.get(kind, [])
        if not isinstance(entries, list):
            raise CaptureError(f"capture response {kind} is not a list")
        out: list[dict] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise CaptureError(f"capture response {kind} has a non-object entry")
            name = str(entry.get("name", "")).strip()
            safe = Path(name).name
            if not name or safe in ("", ".", "..") or safe != name:
                raise CaptureError(f"unsafe {kind} artifact name: {name!r}")
            raw = entry.get("data_b64", entry.get("data"))
            if isinstance(raw, str):
                try:
                    blob = base64.b64decode(raw, validate=True)
                except (ValueError, binascii.Error):
                    raise CaptureError(f"invalid base64 for {kind}/{name}") from None
            elif isinstance(raw, (bytes, bytearray)):
                blob = bytes(raw)
            else:
                raise CaptureError(f"no bytes for {kind}/{name}")
            out.append({"name": safe, "data": blob})
        return out

    return CaptureResult(process=process, exit_code=exit_code,
                         screenshots=decode("screenshots"),
                         audio=decode("audio"), logs=decode("logs"))


def load_runner(spec: str) -> OracleRunner:
    """Load a runner from ``module:attribute`` (or the ``http`` built-in)."""
    if spec == "http":
        return HttpOracleRunner()
    if ":" not in spec:
        raise RunnerError("--runner must be 'http' or 'module:attribute'")
    module_name, _, attr_name = spec.partition(":")
    if not module_name or not attr_name:
        raise RunnerError("--runner must be 'module:attribute'")
    try:
        module = importlib.import_module(module_name)
        obj = getattr(module, attr_name)
    except (ImportError, AttributeError) as exc:
        raise RunnerError("cannot load runner: " + type(exc).__name__) from None
    runner = obj() if isinstance(obj, type) else obj
    if not callable(getattr(runner, "probe", None)) or not callable(
            getattr(runner, "capture", None)):
        raise RunnerError("runner must provide probe() and capture()")
    return runner


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Exact SHA-256 of a file; raise OSError when unreadable (never guess)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_required(path: Path, label: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise RunnerError(f"cannot read {label}: {type(exc).__name__}") from None


def _blocked_record(reason: str, *, status: str = STATUS_BLOCKED,
                    verification: str = VERIFICATION_NOT_TESTED) -> dict:
    """A machine-readable record that can never grant PASS: no records, no hashes."""
    return {
        "schema": SCHEMA_VERSION,
        "generator": GENERATOR,
        "status": status,
        "verification": verification,
        "reason": reason,
        "oracle": ORACLE_NAME,
        "artifacts_dir": None,
        "original_sha256": None,
        "native_sha256": None,
        "input_script_sha256": None,
        "screenshots": [],
        "audio": [],
        "logs": [],
        "records": [],
    }


def _write_record(path: Path, record: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _write_artifacts(artifacts_dir: Path, result: CaptureResult) -> dict:
    """Persist returned bytes locally and record locally-computed digests."""
    recorded: dict[str, list[dict]] = {}
    for kind in ARTIFACT_KINDS:
        sub = artifacts_dir / kind
        sub.mkdir(parents=True, exist_ok=True)
        entries: list[dict] = []
        for index, artifact in enumerate(getattr(result, kind)):
            data = artifact["data"]
            name = f"{index:03d}-{artifact['name']}"
            target = sub / name
            target.write_bytes(data)
            actual = sha256_bytes(data)
            if sha256_file(target) != actual:
                raise CaptureError(f"artifact write verification failed: {name}")
            entries.append({"name": artifact["name"], "path": f"{kind}/{name}",
                            "sha256": actual, "bytes": len(data)})
        recorded[kind] = entries
    return recorded


def build_capture_record(*, request: OracleRequest, result: CaptureResult,
                         artifacts: dict, artifacts_dir: Path,
                         detail_path: Path) -> dict:
    """Build the oracle-evidence-compatible scenario record plus its detail file."""
    detail = {
        "schema": SCHEMA_VERSION,
        "generator": GENERATOR,
        "status": STATUS_CAPTURED,
        "game": request.game_id,
        "game_id": request.game_id,
        "scenario": request.scenario,
        "endpoint": request.endpoint,
        "process": result.process,
        "exit_code": result.exit_code,
        "original": {"path": str(request.original), "sha256": request.original_sha256},
        "native": {"path": str(request.native), "sha256": request.native_sha256},
        "input_script": {"path": str(request.input_script),
                         "sha256": request.input_script_sha256},
        "screenshots": artifacts["screenshots"],
        "audio": artifacts["audio"],
        "logs": artifacts["logs"],
    }
    detail_path.parent.mkdir(parents=True, exist_ok=True)
    detail_path.write_text(json.dumps(detail, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    detail_sha = sha256_file(detail_path)

    evidence = (
        f"Windows-oracle capture for scenario {request.scenario!r} on game "
        f"{request.game_id!r}: process recorded, exit={result.exit_code}, "
        f"{len(artifacts['screenshots'])} screenshots, "
        f"{len(artifacts['audio'])} audio files, "
        f"{len(artifacts['logs'])} logs. Captured from the CLI-supplied endpoint; "
        "endpoint authenticity is the operator's responsibility."
    )
    record = {
        "schema": SCHEMA_VERSION,
        "generator": GENERATOR,
        "status": STATUS_CAPTURED,
        "verification": VERIFICATION_CAPTURED,
        "reason": None,
        "oracle": ORACLE_NAME,
        "artifacts_dir": str(artifacts_dir),
        "original_sha256": request.original_sha256,
        "native_sha256": request.native_sha256,
        "input_script_sha256": request.input_script_sha256,
        "screenshots": artifacts["screenshots"],
        "audio": artifacts["audio"],
        "logs": artifacts["logs"],
        "records": [{
            "game": request.game_id,
            "game_id": request.game_id,
            "sha256": request.original_sha256,
            "native_sha256": request.native_sha256,
            "oracle": ORACLE_NAME,
            "oracle_verified": True,
            "scenario": request.scenario,
            "scenario_evidence": evidence,
            "evidence_path": str(detail_path),
            "evidence_sha256": detail_sha,
            "input_script_sha256": request.input_script_sha256,
        }],
    }
    return record


def verify_record(record_path: Path) -> list[str]:
    """Return integrity problems for a captured record (empty list == intact).

    Recomputes the evidence-file hash, every artifact hash and the
    input/original/native digests. Any missing file or mismatch is a problem.
    """
    record_path = Path(record_path)
    problems: list[str] = []
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        return [f"cannot read record: {type(exc).__name__}: {exc}"]
    if not isinstance(record, dict):
        return ["record is not a JSON object"]

    artifacts_dir_raw = record.get("artifacts_dir")
    records = record.get("records")
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        return ["record has no single oracle-evidence entry (nothing was captured)"]
    entry = records[0]
    detail: Any = None

    def check_hash(label: str, path: Path, claimed: Any) -> None:
        try:
            actual = sha256_file(path)
        except OSError:
            problems.append(f"{label} file is missing: {path}")
            return
        if not isinstance(claimed, str) or actual != claimed:
            problems.append(f"{label} hash mismatch: recorded={claimed} actual={actual}")

    evidence_raw = str(entry.get("evidence_path", ""))
    if not evidence_raw:
        problems.append("no evidence_path in record")
    else:
        evidence_path = Path(evidence_raw)
        check_hash("evidence", evidence_path, entry.get("evidence_sha256"))
        try:
            detail = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            detail = None
        if not isinstance(detail, dict):
            problems.append("capture detail file is missing or invalid JSON")
        else:
            if not artifacts_dir_raw:
                problems.append("no artifacts_dir in record")
            else:
                base = Path(artifacts_dir_raw)
                for kind in ARTIFACT_KINDS:
                    listed = detail.get(kind, [])
                    if not isinstance(listed, list):
                        problems.append(f"detail {kind} is not a list")
                        continue
                    for artifact in listed:
                        if not isinstance(artifact, dict):
                            problems.append(f"detail {kind} has a non-object entry")
                            continue
                        rel = str(artifact.get("path", ""))
                        name = artifact.get("name", rel)
                        relative = Path(rel)
                        parts = relative.parts
                        if (relative.is_absolute() or ".." in parts or len(parts) != 2
                                or parts[0] != kind):
                            problems.append(f"unsafe artifact path in detail: {rel!r}")
                            continue
                        check_hash(f"{kind}/{name}", base / relative, artifact.get("sha256"))

    # The top-level input hashes must agree with the detail file (and the files).
    for key, label in (("original", "original"), ("native", "native"),
                       ("input_script", "input_script")):
        claimed = record.get(f"{label}_sha256")
        if isinstance(detail, dict) and isinstance(detail.get(key), dict):
            detail_sha = detail[key].get("sha256")
            if claimed != detail_sha:
                problems.append(f"{label} hash disagrees with capture detail")
            path_raw = detail[key].get("path")
            if path_raw:
                check_hash(label, Path(path_raw), claimed)
        elif isinstance(detail, dict):
            problems.append(f"capture detail has no {key} hash")
    return problems


def _emit_failure(out: Path | None, record: dict, message: str, code: int) -> int:
    print(message, file=sys.stderr)
    if out is not None:
        try:
            _write_record(out, record)
        except OSError as exc:
            print(f"error: cannot write record: {type(exc).__name__}", file=sys.stderr)
    return code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--endpoint", default=None,
                    help="base URL of the Windows host/VM oracle endpoint")
    ap.add_argument("--game-id", help="identifier of the game being captured")
    ap.add_argument("--input-script", type=Path, help="deterministic input script file")
    ap.add_argument("--out", type=Path, help="scenario record JSON to write")
    ap.add_argument("--original", type=Path, help="original Windows executable")
    ap.add_argument("--native", type=Path, help="rebuilt native binary")
    ap.add_argument("--timeout", type=int, default=30,
                    help="endpoint/network timeout in seconds (default: 30)")
    ap.add_argument("--scenario", help="scenario name (default: the game id)")
    ap.add_argument("--runner", default="http",
                    help="'http' (default) or 'module:attribute' injectable runner")
    ap.add_argument("--artifacts-dir", type=Path,
                    help="artifact directory (default: <out stem>-artifacts)")
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

    missing = [name for name, value in (
        ("--game-id", args.game_id), ("--input-script", args.input_script),
        ("--original", args.original), ("--native", args.native), ("--out", args.out),
    ) if value is None]
    if missing:
        ap.error("missing required capture arguments: " + ", ".join(missing))
    if args.timeout < 1:
        ap.error("--timeout must be a positive integer")

    endpoint = (args.endpoint or "").strip()
    if not endpoint:
        return _emit_failure(
            args.out,
            _blocked_record("no --endpoint supplied; Windows oracle NOT TESTED"),
            "BLOCKED: no --endpoint supplied; Windows oracle NOT TESTED",
            EXIT_BLOCKED,
        )

    try:
        runner = load_runner(args.runner)
    except RunnerError as exc:
        return _emit_failure(args.out, _blocked_record(f"runner error: {exc}"),
                             f"BLOCKED: runner error: {exc}", EXIT_BLOCKED)

    try:
        healthy = runner.probe(endpoint, args.timeout)
    except Exception as exc:
        healthy = False
        probe_reason = "endpoint probe error: " + type(exc).__name__
    else:
        probe_reason = "endpoint unreachable or failed health check"
    if not healthy:
        return _emit_failure(
            args.out, _blocked_record(f"{probe_reason}; Windows oracle NOT TESTED"),
            f"BLOCKED: {probe_reason}; Windows oracle NOT TESTED",
            EXIT_BLOCKED,
        )

    input_bytes = _read_required(args.input_script, "input script")
    try:
        original_sha = sha256_file(args.original)
        native_sha = sha256_file(args.native)
    except OSError as exc:
        return _emit_failure(
            args.out, _blocked_record(f"cannot hash input: {type(exc).__name__}"),
            f"BLOCKED: cannot hash input: {type(exc).__name__}", EXIT_BLOCKED,
        )
    request = OracleRequest(
        endpoint=endpoint, game_id=args.game_id,
        scenario=args.scenario or args.game_id, timeout=args.timeout,
        input_script=args.input_script, input_script_bytes=input_bytes,
        input_script_sha256=sha256_bytes(input_bytes),
        original=args.original, original_sha256=original_sha,
        native=args.native, native_sha256=native_sha,
    )

    try:
        result = runner.capture(request)
        if isinstance(result, dict):
            result = parse_capture_response(result)
        if not isinstance(result, CaptureResult):
            raise CaptureError("runner returned an invalid capture result")
        if not result.screenshots:
            raise CaptureError("capture returned no screenshots")
        artifacts_dir = args.artifacts_dir or args.out.parent / f"{args.out.stem}-artifacts"
        artifacts = _write_artifacts(artifacts_dir, result)
        detail_path = args.out.parent / f"{args.out.stem}-capture.json"
        record = build_capture_record(
            request=request, result=result, artifacts=artifacts,
            artifacts_dir=artifacts_dir, detail_path=detail_path,
        )
    except (CaptureError, OSError, RunnerError) as exc:
        message = str(exc) or type(exc).__name__
        return _emit_failure(
            args.out,
            _blocked_record(f"capture failed: {message}", status=STATUS_FAILED),
            f"FAILED: capture failed: {message}; Windows oracle NOT TESTED",
            EXIT_FAILED,
        )

    _write_record(args.out, record)
    problems = verify_record(args.out)
    if problems:
        return _emit_failure(
            args.out,
            _blocked_record("post-write integrity check failed", status=STATUS_FAILED),
            "FAILED: post-write integrity check failed: " + "; ".join(problems),
            EXIT_FAILED,
        )
    print(f"CAPTURED: {args.game_id} scenario {request.scenario} "
          f"exit={result.exit_code} screenshots={len(artifacts['screenshots'])} "
          f"audio={len(artifacts['audio'])} logs={len(artifacts['logs'])}")
    print(f"record: {args.out}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
