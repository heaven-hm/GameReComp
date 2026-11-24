#!/usr/bin/env python3
"""Fail-closed Frida capture adapter for the hybrid orchestrator.

Orchestrator interface
----------------------
The only entry point an orchestrator needs is::

    from tools.frida_capture_adapter import capture

    record = capture(exe, scenario, timeout, out,
                     hooks=["kernel32!ExitProcess"],
                     frida="frida", highball="/cli/supplied/highball",
                     wine=None, endpoint=None)

``capture(exe, scenario, timeout, out) -> record`` returns a JSON-serialisable
dict. The caller must branch on ``record["status"]`` and
``record["verification"]``:

``CAPTURED`` / ``CAPTURED``
    A real Frida session produced hook hits and the artifacts were written to
    disk and hashed from their actual bytes.
``BLOCKED`` / ``NOT TESTED``
    Frida or the Windows process host was absent/unreachable, no hooks were
    configured, or the bounded run hit its deadline. ``records``, ``hook_hits``,
    ``call_counts``, ``screenshots`` and all hash fields are empty/null: there
    is no evidence and the orchestrator must not claim a result.
``FAILED`` / ``NOT TESTED``
    A Frida session existed but returned an invalid/incomplete capture or the
    post-write integrity check found tampering. Also carries no evidence.

No hard-coded machine paths are used: Frida, the highball CLI/Wine executable,
an optional host health endpoint, the scenario and the output directory are all
supplied by the caller (CLI flags or keyword arguments). Defaults are absent on
purpose. A build, an emulator result or a fake Frida module can never be
reported as a live capture by this module; a fake module supplied on the CLI is
an injectable test transport, and the record always names the spec used.

Frida module contract (injectable, see ``--frida module[:attribute]``)
----------------------------------------------------------------------
The module (or the named attribute of it) must provide::

    spawn(command: list[str], env: dict) -> int
    attach(pid: int) -> session
    resume(pid: int) -> None

``session.create_script(source)`` returns a script with ``load()`` and
``exports_sync`` exposing ``calls()`` (or ``hits()``) and, optionally,
``screenshots()``. Hits are API names (``module!function``); they are counted
only from what Frida actually returned. A screenshot entry is
``{"name": "frame.png", "data_b64": "..."}``; only those bytes are written.

Process host
------------
``--highball`` (preferred) is an executable supplied on the command line and
is launched as ``highball run <exe>``. ``--wine`` is the labelled fallback. An
optional ``--endpoint`` is a health URL for that host: ``GET <endpoint>/health``
must answer ``200 {"status": "ok"}`` before any capture is attempted. The
health probe has the caller's timeout, so an unreachable host cannot hang.

Bounded execution
-----------------
The attach/spawn runs in a child process group with a wall-clock deadline. On
expiry the whole group and the recorded Frida PID are SIGKILLed and the result
stays BLOCKED with an explicit reason. A missing result file is itself a
BLOCKED reason; status is never inferred from a silent process.

Usage::

    python3 -m tools.frida_capture_adapter --exe <pe32> --scenario boot-to-menu \
        --hook 'KERNEL32.dll!ExitProcess' --frida frida \
        --highball /cli/supplied/highball --out evidence/frida.json \
        --timeout 10 --endpoint http://host/health
    python3 -m tools.frida_capture_adapter --verify evidence/frida.json
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from tools.frida_probe import script_source

SCHEMA_VERSION = 1
GENERATOR = "tools/frida_capture_adapter.py"

STATUS_CAPTURED = "CAPTURED"
STATUS_BLOCKED = "BLOCKED"
STATUS_FAILED = "FAILED"
VERIFICATION_CAPTURED = "CAPTURED"
VERIFICATION_NOT_TESTED = "NOT TESTED"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BLOCKED = 2

ROOT = Path(__file__).resolve().parents[1]

# Minimal child body: it only reads a config file and writes one result file.
_WORKER = """
import sys
from tools import frida_capture_adapter as adapter
adapter._worker(sys.argv[1], sys.argv[2])
"""


class CaptureBlocked(Exception):
    """The capture cannot be attempted; the reason is recorded verbatim."""


class CaptureError(Exception):
    """The capture was attempted but returned incomplete/unsafe data."""


def canonical_bytes(value: Any) -> bytes:
    """Stable JSON encoding used for records, hashes and artifact writes."""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CaptureError(f"value is not deterministic JSON: {exc}") from exc


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Exact SHA-256 of a file; raise OSError when unreadable (never guess)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_value(value: Any) -> str:
    return sha256_bytes(canonical_bytes(value))


def load_scenario(scenario: Any) -> Any:
    """Accept a scenario name, a dict, or a path to a JSON scenario file.

    Nothing is synthesised: a JSON file is read as-is; a plain string stays a
    plain string and is hashed from its canonical JSON representation.
    """
    if isinstance(scenario, dict):
        return scenario
    if isinstance(scenario, Path) or isinstance(scenario, str):
        path = Path(scenario)
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeError) as exc:
                raise CaptureBlocked(f"cannot read scenario {path}: {exc}") from exc
            if not isinstance(data, dict):
                raise CaptureBlocked("scenario file root must be a JSON object")
            return data
        if isinstance(scenario, str):
            return scenario
    raise CaptureBlocked("scenario must be a name, a dict or a JSON file path")


def _normalise_hooks(hooks: Any, scenario: Any) -> list[str]:
    raw = hooks
    if raw is None and isinstance(scenario, dict):
        raw = scenario.get("hooks")
    if not isinstance(raw, (list, tuple)) or not raw:
        raise CaptureBlocked(
            "no hooks configured; refusing a vacuous capture (pass --hook or "
            'a "hooks" list in the scenario)')
    result: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise CaptureBlocked("each hook must be a non-empty string")
        if item not in result:
            result.append(item)
    return result


def _scenario_args(scenario: Any) -> list[str]:
    if not isinstance(scenario, dict):
        return []
    args = scenario.get("args", [])
    if not isinstance(args, (list, tuple)) or any(
            not isinstance(a, str) for a in args):
        raise CaptureBlocked('scenario "args" must be a list of strings')
    return list(args)


def load_frida(spec: str | None) -> Any:
    """Resolve the Frida transport supplied by the caller; fail closed."""
    if not isinstance(spec, str) or not spec.strip():
        raise CaptureBlocked("no Frida module supplied (--frida); NOT TESTED")
    name = spec.strip()
    if ":" in name:
        module_name, _, attribute = name.partition(":")
        if not module_name or not attribute:
            raise CaptureBlocked(f"bad --frida spec {name!r}; expected module:attribute")
        try:
            module = getattr(importlib.import_module(module_name), attribute)
        except (ImportError, AttributeError) as exc:
            raise CaptureBlocked(
                f"cannot load frida from {name!r}: {type(exc).__name__}") from exc
    else:
        try:
            module = importlib.import_module(name)
        except ImportError as exc:
            raise CaptureBlocked(
                f"frida module {name!r} is not importable: {exc}") from exc
    if isinstance(module, type):
        try:
            module = module()
        except Exception as exc:
            raise CaptureBlocked(
                f"cannot instantiate frida {name!r}: {type(exc).__name__}") from exc
    missing = [name for name in ("spawn", "attach", "resume")
               if not callable(getattr(module, name, None))]
    if missing:
        raise CaptureBlocked(
            "frida transport is missing required method(s): " + ", ".join(missing))
    return module


def _resolve_executable(value: str | None) -> str | None:
    """Resolve a CLI-supplied executable only; no host-specific defaults."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    candidate = Path(text)
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    found = shutil.which(text)
    return found


def health_check(endpoint: str, timeout: int) -> None:
    """Prove a supplied Windows host endpoint is reachable before capture."""
    parts = urllib.parse.urlsplit(endpoint)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise CaptureBlocked(f"invalid host endpoint {endpoint!r}; NOT TESTED")
    url = endpoint.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=max(1, min(timeout, 5))) as resp:
            if resp.status != 200:
                raise CaptureBlocked(f"host endpoint returned HTTP {resp.status}; NOT TESTED")
            body = json.load(resp)
    except CaptureBlocked:
        raise
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise CaptureBlocked(
            f"host endpoint unreachable ({type(exc).__name__}); NOT TESTED") from exc
    if not isinstance(body, dict) or str(body.get("status", "")).lower() not in (
            "ok", "ready"):
        raise CaptureBlocked("host endpoint failed its health check; NOT TESTED")


def resolve_host(*, highball: str | None, wine: str | None,
                 endpoint: str | None, timeout: int) -> dict:
    """Choose the Windows process host from caller-supplied configuration."""
    if endpoint:
        health_check(endpoint, timeout)
    hb = _resolve_executable(highball)
    wine_exe = _resolve_executable(wine)
    if hb:
        runner, command = "highball", [hb, "run"]
    elif wine_exe:
        runner, command = "wine-fallback", [wine_exe]
    else:
        raise CaptureBlocked(
            "no Windows process host supplied (--highball or --wine); NOT TESTED")
    return {"runner": runner, "command": command, "highball": hb,
            "wine": wine_exe, "endpoint": endpoint}


def _spawn_command(host: dict, exe: Path, scenario: Any) -> list[str]:
    return list(host["command"]) + [str(exe)] + _scenario_args(scenario)


def _safe_name(raw: Any, label: str) -> str:
    name = str(raw or "").strip()
    safe = Path(name).name
    if not name or safe in ("", ".", "..") or safe != name:
        raise CaptureError(f"unsafe {label} name: {name!r}")
    return safe


def _screenshot_entry(item: Any) -> dict:
    """Decode a screenshot reported by Frida; no bytes, no artifact."""
    if not isinstance(item, dict):
        raise CaptureError("screenshot entry is not an object")
    name = _safe_name(item.get("name"), "screenshot")
    raw = item.get("data_b64", item.get("data"))
    if isinstance(raw, str):
        try:
            blob = base64.b64decode(raw, validate=True)
        except (ValueError, binascii.Error):
            raise CaptureError(f"invalid base64 screenshot: {name}") from None
    elif isinstance(raw, (bytes, bytearray)):
        blob = bytes(raw)
    else:
        raise CaptureError(f"no bytes for screenshot {name}")
    return {"name": name, "data_b64": base64.b64encode(blob).decode("ascii")}


def _worker(config_path: str, result_path: str) -> None:
    """Child body: run one bounded Frida session and write a raw result."""
    cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
    result: dict = {"status": "BLOCKED", "reason": "worker produced no result"}
    try:
        frida = load_frida(cfg.get("frida_spec"))
        command = list(cfg["command"])
        env = dict(os.environ)
        if cfg.get("wine") and cfg.get("bottle"):
            env["WINEPREFIX"] = str(cfg["bottle"])
        pid = cfg.get("pid")
        if pid is None:
            pid = frida.spawn(command, env=env)
            if cfg.get("pid_path"):
                Path(cfg["pid_path"]).write_text(str(pid))
        session = frida.attach(pid)
        try:
            script = session.create_script(cfg["source"])
            script.load()
            if pid is not None and cfg.get("resume", True):
                frida.resume(pid)
            window = max(0.05, min(1.0, float(cfg["timeout"]) / 4.0))
            time.sleep(window)
            exports = script.exports_sync
            hits = exports.hits() if callable(getattr(exports, "hits", None)) \
                else exports.calls()
            if not isinstance(hits, (list, tuple)):
                raise CaptureError("frida hits are not a list")
            clean_hits = []
            for hit in hits:
                if not isinstance(hit, str) or not hit.strip():
                    raise CaptureError("frida returned a non-string hook hit")
                clean_hits.append(hit)
            shots = []
            if callable(getattr(exports, "screenshots", None)):
                for item in exports.screenshots():
                    shots.append(_screenshot_entry(item))
        finally:
            session.detach()
        result = {"status": "OK", "pid": pid, "command": command,
                  "runner": cfg["runner"], "hits": clean_hits,
                  "screenshots": shots}
    except BaseException as exc:
        result["reason"] = f"{type(exc).__name__}: {str(exc)[:400]}"
    Path(result_path).write_text(json.dumps(result), encoding="utf-8")


def _base_record(*, exe: Any, scenario: Any, timeout: int) -> dict:
    return {
        "schema": SCHEMA_VERSION, "generator": GENERATOR,
        "status": STATUS_BLOCKED, "verification": VERIFICATION_NOT_TESTED,
        "reason": None, "exe": str(exe), "exe_sha256": None,
        "scenario": scenario, "scenario_sha256": None, "timeout_s": timeout,
        "hooks": [], "frida": None, "host": None, "artifacts_dir": None,
        "evidence_path": None, "evidence_sha256": None, "hook_hits": [],
        "call_counts": {}, "screenshots": [], "artifacts": [], "records": [],
    }


def _write_record(out: Path, record: dict) -> None:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(canonical_bytes(record))


def _blocked(out: Path, base: dict, reason: str, **extra: Any) -> dict:
    base["status"] = STATUS_BLOCKED
    base["verification"] = VERIFICATION_NOT_TESTED
    base["reason"] = reason
    base.update(extra)
    _write_record(out, base)
    return base


def _count_hits(hits: list[str], hooks: list[str]) -> dict[str, int]:
    """Counts are derived from real Frida hits, never from configuration."""
    return {hook: sum(1 for hit in hits if hit == hook) for hook in hooks}


def _persist_artifacts(artifacts_dir: Path, hits: list[str],
                       screenshots: list[dict]) -> list[dict]:
    """Write real bytes to disk and hash the bytes that were actually written."""
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []

    def save(relative: Path, data: bytes, name: str) -> None:
        target = artifacts_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        actual = sha256_file(target)
        if actual != sha256_bytes(data):
            raise CaptureError(f"artifact write verification failed: {relative}")
        entries.append({"name": name, "path": str(relative),
                        "sha256": actual, "bytes": len(data)})

    save(Path("hook-hits.json"), canonical_bytes(hits), "hook-hits.json")
    shots_dir = Path("screenshots")
    for index, shot in enumerate(screenshots):
        blob = base64.b64decode(shot["data_b64"], validate=True)
        save(shots_dir / f"{index:03d}-{shot['name']}", blob, shot["name"])
    return entries


def verify_record(record_path: Path) -> list[str]:
    """Recompute every hash in a captured record; empty result == intact."""
    record_path = Path(record_path)
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        return [f"cannot read record: {type(exc).__name__}: {exc}"]
    if not isinstance(record, dict):
        return ["record is not a JSON object"]
    entries = record.get("records")
    if record.get("status") != STATUS_CAPTURED or not isinstance(entries, list) \
            or len(entries) != 1 or not isinstance(entries[0], dict):
        return ["record has no single CAPTURED entry (nothing was captured)"]
    entry = entries[0]
    problems: list[str] = []

    def check_hash(label: str, path: Path, claimed: Any) -> None:
        try:
            actual = sha256_file(path)
        except OSError:
            problems.append(f"{label} file is missing: {path}")
            return
        if not isinstance(claimed, str) or actual != claimed:
            problems.append(f"{label} hash mismatch: recorded={claimed} actual={actual}")

    try:
        exe_sha = sha256_file(Path(record.get("exe", "")))
    except OSError:
        problems.append(f"exe file is missing: {record.get('exe')}")
    else:
        if record.get("exe_sha256") != exe_sha:
            problems.append(f"exe hash mismatch: recorded={record.get('exe_sha256')} actual={exe_sha}")
        if entry.get("sha256") != exe_sha:
            problems.append("record entry exe hash disagrees with recomputed hash")
    try:
        scenario_sha = sha256_value(record.get("scenario"))
    except CaptureError as exc:
        problems.append(f"scenario is not deterministic JSON: {exc}")
    else:
        if record.get("scenario_sha256") != scenario_sha:
            problems.append("scenario hash mismatch")
        if entry.get("scenario_sha256") != scenario_sha:
            problems.append("record entry scenario hash disagrees with recomputed hash")

    evidence_raw = str(entry.get("evidence_path", ""))
    if not evidence_raw:
        problems.append("no evidence_path in record")
        return problems
    evidence_path = Path(evidence_raw)
    check_hash("evidence", evidence_path, entry.get("evidence_sha256"))
    if entry.get("evidence_sha256") != record.get("evidence_sha256"):
        problems.append("evidence hash disagrees between record and its entry")
    try:
        detail = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        problems.append("capture detail file is missing or invalid JSON")
        return problems
    if not isinstance(detail, dict) or detail.get("status") != STATUS_CAPTURED:
        problems.append("capture detail is not a CAPTURED object")

    hits = detail.get("hook_hits")
    if not isinstance(hits, list) or hits != record.get("hook_hits"):
        problems.append("detail hook hits disagree with the record")
    if hits is not None and record.get("call_counts") != _count_hits(
            hits, record.get("hooks") or []):
        problems.append("call counts disagree with the recorded hook hits")

    artifacts_dir = record.get("artifacts_dir")
    if not artifacts_dir:
        problems.append("no artifacts_dir in record")
    else:
        base = Path(artifacts_dir)
        listed = detail.get("artifacts")
        if not isinstance(listed, list):
            problems.append("detail artifacts is not a list")
        else:
            for artifact in listed:
                if not isinstance(artifact, dict):
                    problems.append("detail artifacts has a non-object entry")
                    continue
                rel = str(artifact.get("path", ""))
                relative = Path(rel)
                if (relative.is_absolute() or ".." in relative.parts
                        or not relative.parts or rel in ("", ".", "..")):
                    problems.append(f"unsafe artifact path in detail: {rel!r}")
                    continue
                check_hash(f"artifact/{artifact.get('name', rel)}",
                           base / relative, artifact.get("sha256"))
    return problems


def capture(exe: Any, scenario: Any, timeout: int, out: Any, *,
            hooks: list[str] | None = None, frida: str | None = "frida",
            highball: str | None = None, wine: str | None = None,
            endpoint: str | None = None, bottle: Any = None) -> dict:
    """Drive one bounded Frida capture and return the orchestrator record.

    All resources are caller-supplied. Either a real capture is recorded with
    locally recomputed hashes, or an explicit BLOCKED/FAILED record with no
    evidence is returned. This function never raises for an unavailable host:
    availability is evidence, not an exception to be swallowed by a caller.
    """
    out = Path(out)
    exe_path = Path(exe)
    try:
        timeout_int = int(timeout)
    except (TypeError, ValueError):
        timeout_int = 0
    base = _base_record(exe=exe, scenario=scenario, timeout=timeout)
    if timeout_int < 1:
        return _blocked(out, base, "timeout must be a positive integer; NOT TESTED")
    base["timeout_s"] = timeout_int

    try:
        scenario_value = load_scenario(scenario)
        hook_list = _normalise_hooks(hooks, scenario_value)
        _scenario_args(scenario_value)  # validate before any process is spawned
        scenario_sha = sha256_value(scenario_value)
    except (CaptureBlocked, CaptureError) as exc:
        return _blocked(out, base, f"{exc}; NOT TESTED")
    base["scenario"] = scenario_value
    base["hooks"] = hook_list

    if not exe_path.is_file():
        return _blocked(out, base, f"original executable not found: {exe_path}; NOT TESTED")
    try:
        exe_sha = sha256_file(exe_path)
    except OSError:
        return _blocked(out, base, "cannot hash original executable; NOT TESTED")
    base["exe"] = str(exe_path)
    base["frida"] = {"spec": frida}
    source = script_source(hook_list)

    try:
        load_frida(frida)  # fail before spawning a process group
    except CaptureBlocked as exc:
        return _blocked(out, base, f"{exc}")
    try:
        host = resolve_host(highball=highball, wine=wine, endpoint=endpoint,
                            timeout=timeout_int)
    except CaptureBlocked as exc:
        return _blocked(out, base, f"{exc}")
    base["host"] = host
    command = _spawn_command(host, exe_path, scenario_value)

    with tempfile.TemporaryDirectory(prefix="frida-capture-") as td:
        temp = Path(td)
        cfg_path, result_path, pid_path = (
            temp / name for name in ("config.json", "result.json", "pid"))
        cfg = {"source": source, "timeout": timeout_int, "hooks": hook_list,
               "command": command, "runner": host["runner"], "wine": host["wine"],
               "bottle": str(bottle) if bottle else None,
               "frida_spec": frida, "pid_path": str(pid_path), "resume": True}
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, "-c", _WORKER, str(cfg_path), str(result_path)],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True)
        killed = False
        try:
            _, err = proc.communicate(timeout=timeout_int)
        except subprocess.TimeoutExpired:
            killed = True
            try:
                os.killpg(proc.pid, 9)
            except OSError:
                proc.kill()
            try:
                _, err = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                err = b""
            if pid_path.is_file():
                try:
                    os.kill(int(pid_path.read_text().strip()), 9)
                except (OSError, ValueError):
                    pass

        if killed:
            return _blocked(
                out, base,
                f"Frida capture exceeded {timeout_int}s and was killed; NOT TESTED",
                killed_on_timeout=True, worker_stderr=err.decode("utf-8", "replace")[:300])
        if not result_path.is_file():
            return _blocked(
                out, base,
                f"worker produced no result (rc={proc.returncode}); NOT TESTED",
                killed_on_timeout=False)
        try:
            raw = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return _blocked(out, base, "worker result is not valid JSON; NOT TESTED")
        if not isinstance(raw, dict) or raw.get("status") != "OK":
            reason = raw.get("reason", "unknown worker failure") if isinstance(raw, dict) \
                else "invalid worker result"
            return _blocked(out, base, f"Frida capture failed: {reason}; NOT TESTED",
                            killed_on_timeout=False)

    try:
        hits = raw.get("hits")
        if not isinstance(hits, list) or any(
                not isinstance(h, str) or not h.strip() for h in hits):
            raise CaptureError("worker returned invalid hook hits")
        shots = raw.get("screenshots")
        if not isinstance(shots, list):
            raise CaptureError("worker returned invalid screenshots")
        screenshots = [_screenshot_entry(item) for item in shots]
        pid = raw.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool):
            raise CaptureError("worker returned no process id")
        artifacts_dir = out.parent / f"{out.stem}-artifacts"
        artifacts = _persist_artifacts(artifacts_dir, hits, screenshots)
        counts = _count_hits(hits, hook_list)
        detail_path = out.parent / f"{out.stem}-capture.json"
        detail = {
            "schema": SCHEMA_VERSION, "generator": GENERATOR,
            "status": STATUS_CAPTURED, "exe": str(exe_path),
            "exe_sha256": exe_sha, "scenario": scenario_value,
            "scenario_sha256": scenario_sha, "timeout_s": timeout_int,
            "hooks": hook_list, "frida": base["frida"], "host": host,
            "pid": pid, "command": raw.get("command", command),
            "runner": raw.get("runner", host["runner"]),
            "hook_hits": hits, "call_counts": counts,
            "artifacts": artifacts,
        }
        detail_path.parent.mkdir(parents=True, exist_ok=True)
        detail_path.write_bytes(canonical_bytes(detail))
        evidence_sha = sha256_file(detail_path)
    except (CaptureError, OSError, ValueError, binascii.Error) as exc:
        return _blocked(out, base, f"capture failed: {exc}; NOT TESTED",
                        status=STATUS_FAILED)

    screenshots_field = [artifact for artifact in artifacts
                         if artifact["path"].startswith("screenshots/")]
    base.update({
        "status": STATUS_CAPTURED, "verification": VERIFICATION_CAPTURED,
        "reason": None, "exe_sha256": exe_sha, "scenario_sha256": scenario_sha,
        "artifacts_dir": str(artifacts_dir),
        "evidence_path": str(detail_path), "evidence_sha256": evidence_sha,
        "hook_hits": hits, "call_counts": counts,
        "screenshots": screenshots_field, "artifacts": artifacts,
        "killed_on_timeout": False,
        "records": [{
            "generator": GENERATOR, "status": STATUS_CAPTURED,
            "exe": str(exe_path), "sha256": exe_sha,
            "scenario": scenario_value, "scenario_sha256": scenario_sha,
            "evidence_path": str(detail_path), "evidence_sha256": evidence_sha,
            "hook_hits": hits, "call_counts": counts,
            "screenshots": screenshots_field, "artifacts": artifacts,
            "scenario_evidence": (
                f"Frida capture for scenario {scenario_value!r} via "
                f"{raw.get('runner', host['runner'])}: {len(hits)} hook hit(s) "
                f"from the injected session, artifacts hashed locally from the "
                f"bytes that were written. Process-level observation only; "
                "no gameplay, frame or compatibility claim."),
        }],
    })
    _write_record(out, base)
    problems = verify_record(out)
    if problems:
        return _blocked(out, base, "post-write integrity check failed: "
                        + "; ".join(problems), status=STATUS_FAILED)
    return base


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exe", type=Path, help="original Windows executable to instrument")
    ap.add_argument("--scenario", default="",
                    help="scenario name or path to a JSON scenario file")
    ap.add_argument("--timeout", type=int, default=10,
                    help="hard wall-clock deadline in seconds (default: 10)")
    ap.add_argument("--out", type=Path, help="record JSON file to write")
    ap.add_argument("--hook", action="append", default=None,
                    help="API to count, optionally module!function (repeatable); "
                         "defaults to the scenario's hooks")
    ap.add_argument("--frida", default="frida",
                    help="frida module or module:attribute supplied by the caller")
    ap.add_argument("--highball", help="CLI-supplied highball executable (preferred host)")
    ap.add_argument("--wine", help="CLI-supplied Wine executable (labelled fallback)")
    ap.add_argument("--endpoint", help="optional host health URL (GET /health)")
    ap.add_argument("--bottle", type=Path, help="optional WINEPREFIX for the fallback")
    ap.add_argument("--verify", type=Path, metavar="RECORD",
                    help="recompute a record's hashes and exit")
    args = ap.parse_args(argv)

    if args.verify is not None:
        problems = verify_record(args.verify)
        if problems:
            for problem in problems:
                print(f"TAMPERED: {problem}", file=sys.stderr)
            return EXIT_FAILED
        print(f"OK: {args.verify}")
        return EXIT_OK
    missing = [name for name, value in (("--exe", args.exe), ("--out", args.out))
               if value is None]
    if missing:
        ap.error("missing required capture arguments: " + ", ".join(missing))

    record = capture(args.exe, args.scenario, args.timeout, args.out,
                     hooks=args.hook, frida=args.frida, highball=args.highball,
                     wine=args.wine, endpoint=args.endpoint, bottle=args.bottle)
    print(json.dumps(record, indent=2, sort_keys=True))
    return {STATUS_CAPTURED: EXIT_OK, STATUS_FAILED: EXIT_FAILED,
            STATUS_BLOCKED: EXIT_BLOCKED}[record["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
