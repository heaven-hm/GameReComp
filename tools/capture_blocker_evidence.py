#!/usr/bin/env python3
"""Fail-closed join of CLI-supplied capture records (issues #145 / #146).

Four capabilities are required before this tool will emit ``PASS``:

* ``windows-endpoint`` -- a verified **retail-Windows** host/VM endpoint.  A
  Highball/Wine or other emulated endpoint is never accepted.
* ``gui-frames`` -- real, decodable, non-blank GUI frames whose SHA-256 digests
  are recomputed from CLI-supplied byte files; a ``console-only`` scope or an
  incomplete click run is never promoted.
* ``audio`` -- a non-silent PCM capture whose SHA-256 is recomputed from a
  CLI-supplied WAV; an all-zero/silent or hash-less capture is never promoted.
* ``native-hash`` -- a rebuilt native binary hash that matches the expected
  native hash supplied on the CLI.

Every record is a *claim*.  This tool trusts none of them: it recomputes each
digest from the bytes actually supplied, re-decodes frames and WAVs, and joins
only capabilities that share one non-empty ``capture_id`` tied to the real
Windows endpoint.  A console-only or silent capture can never be promoted, and
no flag generates a byte artifact, a hash or a PASS.

Usage::

    python3 -m tools.capture_blocker_evidence \
        --record proof/.../endpoint.json --record proof/.../audio.json \
        --frame /cli/supplied/frame.png --wav /cli/supplied/capture.wav \
        --native-sha256 <expected> --out proof/.../report.json
    python3 -m tools.capture_blocker_evidence --verify proof/.../report.json

Exit status: 0 = PASS, 3 = PARTIAL, 2 = BLOCKED, 1 = failed ``--verify``.
An honest skip is not an error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

try:  # support both `python3 -m tools...` and direct execution
    from tools import audio_capture_adapter as aca
    from tools import frame_evidence_check as fec
except ImportError:  # pragma: no cover - direct script execution only
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools import audio_capture_adapter as aca
    from tools import frame_evidence_check as fec

SCHEMA = 1
GENERATOR = "tools/capture_blocker_evidence.py"

PASS = "PASS"
PARTIAL = "PARTIAL"
BLOCKED = "BLOCKED"
RANK = {BLOCKED: 0, PARTIAL: 1, PASS: 2}

CAPABILITIES = ("windows-endpoint", "gui-frames", "audio", "native-hash")

EXIT_PASS = 0
EXIT_TAMPERED = 1
EXIT_BLOCKED = 2
EXIT_PARTIAL = 3

NOTE = (
    "Byte-level, fail-closed evidence join only. No game or capture process is "
    "run here; a PASS means the supplied bytes survived these checks and share "
    "one capture session. No gameplay, graphics, audio correctness or 100% "
    "compatibility claim is made."
)
WHAT_IT_PROVES = (
    "Only verified retail-Windows bytes, non-blank GUI frames, non-silent audio "
    "and a matching native hash can yield PASS; console-only and silent captures "
    "are never promoted."
)

# Endpoint kinds that are accepted only after an explicit attestation.
REAL_ENDPOINT_KINDS = frozenset({
    "retail-windows", "original-windows", "windows-host", "windows-vm",
})
# Wine/CrossOver/Highball emulation must never satisfy the endpoint gate.
EMULATION_RE = re.compile(
    r"wine|highball|cross.?over|emulat|wineboot|proton|wineskin|darwine|sdl-dummy",
    re.IGNORECASE)
HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")

REASON_TEXT = {
    "NO_RECORD": "no capture record was supplied for this capability",
    "INVALID_RECORD": "capture record is not a usable JSON object",
    "ENDPOINT_NOT_RETAIL": "endpoint kind is missing or not a retail-Windows host/VM",
    "EMULATED_ENDPOINT": "endpoint is Wine/Highball emulation, not the original Windows host",
    "ENDPOINT_UNVERIFIED": "retail-Windows endpoint was not marked verified",
    "ENDPOINT_CONSOLE_SCOPE": "endpoint capture is console-only; no GUI scope was captured",
    "ENDPOINT_OK": "verified retail-Windows endpoint",
    "CONSOLE_ONLY": "capture scope is console-only; GUI/frame evidence is not claimed",
    "NO_FRAMES": "no GUI frame artifacts were supplied",
    "FRAME_NO_HASH": "a GUI frame has no usable 64-hex SHA-256 claim",
    "FRAME_NO_BYTES": "a GUI frame has no CLI-supplied byte file to verify",
    "FRAME_HASH_MISMATCH": "a GUI frame's recomputed SHA-256 does not match its claim",
    "FRAME_UNDECODABLE": "a GUI frame cannot be decoded as BMP/PNG",
    "FRAME_BLANK": "a GUI frame decodes to a single colour (blank)",
    "FRAMES_INCOMPLETE": "the GUI click run completed fewer clicks than expected",
    "FRAMES_OK": "all GUI frames are byte-verified, non-blank and the click run is complete",
    "AUDIO_NO_HASH": "audio capture has no usable 64-hex SHA-256 claim",
    "AUDIO_NO_BYTES": "audio capture has no CLI-supplied WAV to verify",
    "AUDIO_HASH_MISMATCH": "audio WAV's recomputed SHA-256 does not match its claim",
    "AUDIO_INVALID_WAVE": "audio bytes are not a well-formed RIFF/WAVE PCM capture",
    "AUDIO_SYNTHETIC": "audio bytes match a generated synthetic fixture",
    "AUDIO_FRAMES_MISMATCH": "audio sample-frame count does not match the claim",
    "AUDIO_SILENT": "audio is all-zero/silent or constant; non-silent samples are required",
    "AUDIO_OK": "audio WAV is byte-verified and non-silent",
    "NATIVE_HASH_NULL": "no rebuilt native SHA-256 is available (native hash is null)",
    "NO_NATIVE_EXPECTED": "no expected native SHA-256 was supplied on the CLI",
    "NATIVE_HASH_MISMATCH": "native hash does not match the expected native hash",
    "NATIVE_OK": "native hash matches the expected native hash",
    "SESSION_MISSING": "a passing capability has no non-empty capture_id",
    "SESSION_MISMATCH": "passing capabilities do not share one capture_id",
}

HARD_AUDIO = frozenset({
    "AUDIO_NO_HASH", "AUDIO_HASH_MISMATCH", "AUDIO_INVALID_WAVE",
    "AUDIO_SYNTHETIC", "AUDIO_FRAMES_MISMATCH", "AUDIO_SILENT",
})
HARD_FRAMES = frozenset({
    "CONSOLE_ONLY", "NO_FRAMES", "FRAME_HASH_MISMATCH", "FRAME_UNDECODABLE",
})
HARD_NATIVE = frozenset({
    "NATIVE_HASH_NULL", "NO_NATIVE_EXPECTED", "NATIVE_HASH_MISMATCH",
})


def _norm(value: Any) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _valid_hash(value: Any) -> str | None:
    if isinstance(value, str) and HASH_RE.fullmatch(value.strip()):
        return value.strip().lower()
    return None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reason(code: str) -> str:
    return REASON_TEXT.get(code, code)


def load_records(paths: list[Path]) -> list[dict]:
    """Read each CLI record; malformed inputs become BLOCKED rows, never a pass."""
    rows: list[dict] = []
    for path in paths:
        row: dict[str, Any] = {
            "path": str(path), "sha256": None, "capability": None,
            "error": None, "record": None,
        }
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(row)
            continue
        row["sha256"] = sha256_bytes(data)
        try:
            record = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            row["error"] = f"invalid JSON: {type(exc).__name__}: {exc}"
            rows.append(row)
            continue
        if not isinstance(record, dict):
            row["error"] = "record root is not a JSON object"
            rows.append(row)
            continue
        row["capability"] = _norm(record.get("capability")) or None
        row["record"] = record
        rows.append(row)
    return rows


def load_files(paths: list[Path]) -> dict[str, dict]:
    """Index CLI byte files by basename for artifact matching (no inference)."""
    files: dict[str, dict] = {}
    for path in paths:
        try:
            data = Path(path).read_bytes()
        except OSError:
            continue
        files[Path(path).name] = {"path": str(path), "sha256": sha256_bytes(data),
                                  "data": data}
    return files


def _eval(capability: str, row: dict, codes: list[str], detail: dict,
          verdict: str | None = None) -> dict:
    if verdict is None:
        hard = (HARD_FRAMES if capability == "gui-frames" else
                HARD_AUDIO if capability == "audio" else
                HARD_NATIVE if capability == "native-hash" else frozenset())
        verdict = BLOCKED if (hard & set(codes)) else (
            PARTIAL if codes else PASS)
    record = row.get("record") or {}
    return {
        "capability": capability,
        "verdict": verdict,
        "codes": list(dict.fromkeys(codes)),
        "reasons": [_reason(code) for code in dict.fromkeys(codes)],
        "capture_id": record.get("capture_id") if isinstance(record, dict) else None,
        "source_record": row.get("path"),
        "source_record_sha256": row.get("sha256"),
        "error": row.get("error"),
        "detail": detail,
    }


def empty_eval(capability: str) -> dict:
    # No record at all is a hard blocker, never a "partial" evidence claim.
    return _eval(capability, {}, ["NO_RECORD"], {}, verdict=BLOCKED)


def evaluate_endpoint(row: dict) -> dict:
    record = row.get("record") or {}
    ep = record.get("endpoint") if isinstance(record.get("endpoint"), dict) else {}
    kind = _norm(ep.get("kind"))
    label = _norm(ep.get("label"))
    scope = _norm(ep.get("scope"))
    codes: list[str] = []
    if EMULATION_RE.search(f"{kind} {label}"):
        codes.append("EMULATED_ENDPOINT")
    elif kind in REAL_ENDPOINT_KINDS:
        pass
    else:
        codes.append("ENDPOINT_NOT_RETAIL")
    if ep.get("verified") is not True:
        codes.append("ENDPOINT_UNVERIFIED")
    if scope == "console-only":
        codes.append("ENDPOINT_CONSOLE_SCOPE")
    detail = {
        "kind": ep.get("kind"), "label": ep.get("label"), "scope": ep.get("scope"),
        "verified": ep.get("verified"),
        "guest_output_sha256": ep.get("guest_output_sha256"),
        "native_sha256": ep.get("native_sha256"),
    }
    verdict = PASS if not codes else (
        PARTIAL if set(codes) == {"ENDPOINT_CONSOLE_SCOPE"} else BLOCKED)
    return _eval("windows-endpoint", row, codes, detail, verdict=verdict)


def evaluate_frames(row: dict, frame_files: dict[str, dict]) -> dict:
    record = row.get("record") or {}
    scope = _norm(record.get("scope"))
    frames = record.get("frames")
    clicks = record.get("clicks") if isinstance(record.get("clicks"), dict) else {}
    codes: list[str] = []
    if scope == "console-only":
        codes.append("CONSOLE_ONLY")
    if not isinstance(frames, list) or not frames:
        codes.append("NO_FRAMES")
        frames = []
    checked: list[dict] = []
    for frame in frames:
        if not isinstance(frame, dict):
            codes.append("FRAME_NO_BYTES")
            checked.append({"status": "NO_BYTES"})
            continue
        name = frame.get("name")
        claim = _valid_hash(frame.get("sha256"))
        item: dict[str, Any] = {"name": name, "claimed_sha256": claim,
                                "actual_sha256": None, "status": "NO_BYTES"}
        supplied = frame_files.get(name) if isinstance(name, str) else None
        if supplied is None:
            codes.append("FRAME_NO_BYTES")
        else:
            item["actual_sha256"] = supplied["sha256"]
            if claim is None:
                codes.append("FRAME_NO_HASH")
                item["status"] = "NO_HASH"
            elif supplied["sha256"] != claim:
                codes.append("FRAME_HASH_MISMATCH")
                item["status"] = "HASH_MISMATCH"
            else:
                info = fec.frame_info(supplied["data"])
                if "error" in info:
                    codes.append("FRAME_UNDECODABLE")
                    item["status"] = "UNDECODABLE"
                elif info.get("blank"):
                    codes.append("FRAME_BLANK")
                    item["status"] = "BLANK"
                else:
                    item["status"] = "OK"
                    item["distinct_colors"] = info["distinct_colors"]
        checked.append(item)
    completed, expected = clicks.get("completed"), clicks.get("expected")
    if isinstance(completed, int) and isinstance(expected, int) and completed < expected:
        codes.append("FRAMES_INCOMPLETE")
    detail = {
        "scope": record.get("scope"), "source": record.get("source"),
        "clicks": {"completed": completed, "expected": expected},
        "frames": checked,
        "sha256_status": record.get("sha256_status"),
    }
    return _eval("gui-frames", row, codes, detail)


def evaluate_audio(row: dict, wav_files: dict[str, dict]) -> dict:
    record = row.get("record") or {}
    audio = record.get("audio") if isinstance(record.get("audio"), dict) else {}
    artifact = record.get("artifact") if isinstance(record.get("artifact"), dict) else {}
    claim = _valid_hash(artifact.get("sha256"))
    frames_claim = audio.get("frames")
    non_silent_claim = audio.get("non_silent")
    codes: list[str] = []
    detail: dict[str, Any] = {
        "source": record.get("source"), "claimed_sha256": claim,
        "claimed_frames": frames_claim, "claimed_non_silent": non_silent_claim,
        "sha256_computed": None, "frames": None, "peak": None,
        "non_silent": None, "sha256_status": record.get("sha256_status"),
    }
    name = artifact.get("name")
    supplied = wav_files.get(name) if isinstance(name, str) else None
    if claim is None:
        codes.append("AUDIO_NO_HASH")
    if supplied is None:
        codes.append("AUDIO_NO_BYTES")
    else:
        detail["sha256_computed"] = supplied["sha256"]
        if claim is not None and supplied["sha256"] != claim:
            codes.append("AUDIO_HASH_MISMATCH")
        try:
            metrics = aca.parse_capture(supplied["data"])
        except aca.CaptureError as exc:
            codes.append("AUDIO_INVALID_WAVE")
            detail["wave_error"] = str(exc)
        else:
            pcm = supplied["data"][metrics["pcm_offset"]:
                                   metrics["pcm_offset"] + metrics["bytes"]]
            stats = aca.sample_stats(pcm, metrics["sample_width"])
            detail.update(frames=metrics["frames"], peak=stats["peak"],
                          non_silent=stats["non_silent"])
            if supplied["sha256"] in aca.SYNTHETIC_HASHES:
                codes.append("AUDIO_SYNTHETIC")
            if not stats["non_silent"]:
                codes.append("AUDIO_SILENT")
            if isinstance(frames_claim, int) and frames_claim != metrics["frames"]:
                codes.append("AUDIO_FRAMES_MISMATCH")
    # A recorded silent/all-zero claim is itself disqualifying: we never
    # promote a capture that the caller measured as silent.
    if non_silent_claim is False:
        codes.append("AUDIO_SILENT")
    return _eval("audio", row, codes, detail)


def evaluate_native(row: dict, expected: Any) -> dict:
    record = row.get("record") or {}
    native = record.get("native") if isinstance(record.get("native"), dict) else {}
    actual = _valid_hash(native.get("sha256"))
    expected_hash = _valid_hash(expected)
    codes: list[str] = []
    if actual is None:
        codes.append("NATIVE_HASH_NULL")
    elif expected_hash is None:
        codes.append("NO_NATIVE_EXPECTED")
    elif actual != expected_hash:
        codes.append("NATIVE_HASH_MISMATCH")
    detail = {"sha256": native.get("sha256"), "expected_sha256": expected_hash,
              "status": native.get("status"), "source": record.get("source")}
    return _eval("native-hash", row, codes, detail)


def join(record_paths: list[Path], wav_paths: list[Path] | None = None,
         frame_paths: list[Path] | None = None,
         expected_native: Any = None) -> dict:
    """Join all CLI records into a deterministic, fail-closed report."""
    records = load_records(record_paths)
    wav_files = load_files(wav_paths or [])
    frame_files = load_files(frame_paths or [])

    evals: list[dict] = []
    for row in records:
        capability = row["capability"]
        if row["error"] is not None or capability is None:
            evals.append(_eval(capability or "unknown", row, ["INVALID_RECORD"], {},
                               verdict=BLOCKED))
        elif capability == "windows-endpoint":
            evals.append(evaluate_endpoint(row))
        elif capability == "gui-frames":
            evals.append(evaluate_frames(row, frame_files))
        elif capability == "audio":
            evals.append(evaluate_audio(row, wav_files))
        elif capability == "native-hash":
            evals.append(evaluate_native(row, expected_native))
        else:
            evals.append(_eval(capability, row, ["INVALID_RECORD"], {},
                               verdict=BLOCKED))

    capabilities: dict[str, dict] = {}
    for capability in CAPABILITIES:
        candidates = [e for e in evals if e["capability"] == capability]
        if candidates:
            capabilities[capability] = max(candidates, key=lambda e: RANK[e["verdict"]])
        else:
            capabilities[capability] = empty_eval(capability)

    session_codes: list[str] = []
    passing = [c for c in capabilities.values() if c["verdict"] == PASS]
    if len(passing) == len(CAPABILITIES):
        ids = {_norm(c.get("capture_id")) for c in passing}
        if "" in ids:
            session_codes.append("SESSION_MISSING")
        elif len(ids) > 1:
            session_codes.append("SESSION_MISMATCH")

    if all(c["verdict"] == PASS for c in capabilities.values()) and not session_codes:
        overall = PASS
    elif any(c["verdict"] in (PASS, PARTIAL) for c in capabilities.values()) or session_codes:
        overall = PARTIAL
    else:
        overall = BLOCKED

    blockers: list[str] = []
    for capability in CAPABILITIES:
        for code in capabilities[capability]["codes"]:
            blockers.append(f"{capability}:{code}")
    blockers.extend(f"session:{code}" for code in session_codes)

    return {
        "schema": SCHEMA,
        "tool": "capture_blocker_evidence",
        "generator": GENERATOR,
        "verdict": overall,
        "what_it_proves": WHAT_IT_PROVES,
        "inputs": {
            "records": [{"path": r["path"], "sha256": r["sha256"]} for r in records],
            "wavs": [{"path": f["path"], "sha256": f["sha256"]}
                     for f in wav_files.values()],
            "frames": [{"path": f["path"], "sha256": f["sha256"]}
                       for f in frame_files.values()],
            "native_sha256": _valid_hash(expected_native),
        },
        "capabilities": capabilities,
        "session_codes": session_codes,
        "blockers": blockers,
        "requirements": list(CAPABILITIES),
        "note": NOTE,
    }


def canonical(report: dict) -> str:
    return json.dumps(report, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def verify_report(path: Path) -> list[str]:
    """Recompute the whole join from the paths recorded in the report."""
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        return [f"cannot read report: {type(exc).__name__}: {exc}"]
    if not isinstance(report, dict):
        return ["report root is not a JSON object"]
    inputs = report.get("inputs")
    if not isinstance(inputs, dict):
        return ["report has no inputs object"]
    try:
        records = [Path(p["path"]) for p in inputs.get("records", [])
                   if isinstance(p, dict) and isinstance(p.get("path"), str)]
        wavs = [Path(p["path"]) for p in inputs.get("wavs", [])
                if isinstance(p, dict) and isinstance(p.get("path"), str)]
        frames = [Path(p["path"]) for p in inputs.get("frames", [])
                  if isinstance(p, dict) and isinstance(p.get("path"), str)]
    except (TypeError, KeyError) as exc:
        return [f"malformed report inputs: {exc}"]
    expected = inputs.get("native_sha256")
    recomputed = join(records, wavs, frames, expected)
    if canonical(recomputed) == canonical(report):
        return []
    problems: list[str] = []
    for key in ("verdict", "session_codes", "blockers", "inputs", "capabilities"):
        if canonical(recomputed.get(key)) != canonical(report.get(key)):
            problems.append(f"{key} changed since the report was written")
    return problems or ["report differs from a fresh join"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--record", action="append", default=[], type=Path,
                    help="CLI-supplied capture record JSON (repeatable)")
    ap.add_argument("--wav", action="append", default=[], type=Path,
                    help="CLI-supplied audio WAV byte file (repeatable)")
    ap.add_argument("--frame", action="append", default=[], type=Path,
                    help="CLI-supplied GUI frame BMP/PNG byte file (repeatable)")
    ap.add_argument("--native-sha256",
                    help="expected rebuilt native SHA-256 for the native-hash gate")
    ap.add_argument("--out", type=Path, help="write the joined JSON report here")
    ap.add_argument("--verify", type=Path, metavar="REPORT",
                    help="recompute a report from its recorded inputs and exit")
    args = ap.parse_args(argv)

    if args.verify is not None:
        problems = verify_report(args.verify)
        if problems:
            for problem in problems:
                print(f"TAMPERED: {problem}", file=sys.stderr)
            return EXIT_TAMPERED
        print(f"OK: {args.verify}")
        return EXIT_PASS

    report = join(args.record, args.wav, args.frame, args.native_sha256)
    text = json.dumps(report, indent=2, sort_keys=True,
                      ensure_ascii=True, allow_nan=False) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    print(f"overall: {report['verdict']} "
          f"({' '.join(report['blockers']) or 'no blockers'})", file=sys.stderr)
    return {PASS: EXIT_PASS, PARTIAL: EXIT_PARTIAL,
            BLOCKED: EXIT_BLOCKED}[report["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
