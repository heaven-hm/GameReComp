#!/usr/bin/env python3
"""Honest Windows -> macOS status report from CLI-supplied evidence only.

This tool joins two supplied artifacts and reports what they actually prove:

* ``--corpus-acceptance`` : JSON written by :mod:`tools.corpus_acceptance`
  (``games[].{status,original,native,pipeline,launch,frames,scenario}``).
  Acceptance-matrix style ``rows[]`` with an embedded ``oracle`` are also read.
* ``--pe-facts`` : JSON with PE header facts, e.g. :mod:`tools.corpus.inventory`
  output (``entries[].{sha256,pe_type,architecture,relative_path}``) or plain
  records with the same fields.
* ``--oracle-evidence`` : optional explicit PASS claims (``records[]``), the
  shape used by :mod:`tools.acceptance_matrix`. Oracle claims may also be
  embedded in an acceptance row. This is the *only* source of PASS.

Nothing here is executed, compiled, edited or invented. Every path comes from
the command line; relative evidence paths resolve against the JSON file that
declared them. No game root, corpus, evidence directory or machine path is
built into this module.

Rows are keyed by the PE facts. Only real PE32 x86 facts are *supported*;
PE32+ (x64), CLR/.NET, NE/Win16 and unrecognised formats get an explicit
``DEFERRED`` row even when an oracle claim is attached, so an unsupported
target can never be advertised as working.

A supported row becomes PASS only when **all** of these hold together:

1. an explicit ``oracle_verified`` / ``oracle.verified`` boolean ``true``
   naming a real executed original-Windows process (dummy, headless,
   unexecuted and unknown spellings are refused);
2. the original SHA-256 agrees across the PE fact, the corpus record and the
   oracle claim, and the native SHA-256 agrees between corpus and oracle;
3. a named scenario with evidence text and an existing evidence file whose
   SHA-256 is recomputed here and matches the claimed digest;
4. the corpus record is not FAIL/BLOCKED, has evidenced launch or PARTIAL/PASS
   status, and reports no refusal, failed stage or ambiguous oracle match.

Everything else is capped at PARTIAL / FAIL / BLOCKED / NOT TESTED with an
explicit reason. A written status is advisory only; a build, a frame, a bare
exit code and a fabricated hash never create PASS.

Usage::

    python3 -m tools.win_to_mac_report \
        --corpus-acceptance <acceptance.json> \
        --pe-facts <inventory.json> \
        [--oracle-evidence <oracle.json>] \
        --out <report.json> [--markdown <report.md>] \
        [--generated-at 2026-01-01T00:00:00Z]

``generated_at`` defaults to the current UTC time; pass it explicitly for
byte-for-byte deterministic output in tests and CI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
GENERATOR = "tools/win_to_mac_report.py"
SOURCE_OS = "Windows"
TARGET_OS = "macOS"

STATUSES = ("PASS", "PARTIAL", "FAIL", "BLOCKED", "NOT TESTED", "DEFERRED")
FAMILIES = ("x86", "x64", ".NET", "Win16", "unknown")

# Same allow-list as tools/acceptance_matrix.py and tools/os_capability_matrix.py.
# An empty/unknown entry is safer than accepting a vague "verified" string.
REAL_ORACLES = frozenset({
    "original-windows", "original-windows-process", "windows-oracle",
    "windows-original", "windows-process", "original-process",
    "original-vs-native", "original-game", "retail-windows", "retail-original",
})
NO_ORACLES = frozenset({
    "", "none", "null", "n/a", "na", "unknown", "unspecified", "not-supplied",
    "dummy", "dummy-device", "dummy-device-audio", "headless", "sdl-dummy",
    "stub", "fake", "fake-success", "emulator", "unit-test", "selftest",
    "windows-oracle-unexecuted", "no-oracle",
})

SHA_RE = re.compile(r"\b[0-9a-fA-F]{64}\b")
X86_MACHINES = frozenset({
    "i386", "i486", "i586", "i686", "x86", "x86-32", "x86_32", "ia32",
    "ia-32", "80386", "win32",
})
X64_MACHINES = frozenset({
    "x64", "x86-64", "x86_64", "amd64", "intel64", "ia64", "arm64", "aarch64",
})

LIMITS = [
    "PASS requires an explicit executed-oracle flag, matching original and "
    "native SHA-256 values across supplied sources, and an evidence file whose "
    "hash is recomputed here; a written status is advisory only.",
    "Only PE32 x86 is supported for the Windows -> macOS bridge. x64, .NET, "
    "Win16 and unrecognised formats are DEFERRED, never PASS.",
    "This report proves no macOS execution, gameplay, input, audio or save/load "
    "equivalence; it joins evidence that other tools already recorded.",
    "All paths are CLI-supplied; relative evidence paths resolve against the "
    "JSON file that declared them. No file, build or game is executed here.",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str | None:
    """Exact SHA-256 of a readable file, or None (never guessed)."""
    try:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA_RE.fullmatch(value.strip()))


def norm_sha(value: Any) -> str | None:
    """Normalise a real SHA-256 to lower case; anything else becomes None."""
    return value.strip().lower() if is_sha256(value) else None


def key_of(value: Any) -> str:
    return " ".join(str(value or "").replace("†", "").split()).lower()


def norm_token(value: Any) -> str:
    token = key_of(value).replace("_", "-").replace(" ", "-")
    return re.sub(r"-+", "-", token).strip("-")


def norm_oracle(value: Any) -> str:
    return norm_token(value)


def first(*values: Any) -> Any:
    """First non-empty value, for field aliases without inventing a value."""
    for value in values:
        if value is not None and value != "":
            return value
    return None


def records_from(data: Any) -> list[Any]:
    """Pick the record list of a JSON document, never its bookkeeping lists."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("games", "rows", "entries", "records", "results", "cases",
                "facts", "binaries"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return [data]


def load_sources(paths: list[str], kind: str) -> tuple[list[dict], list[dict]]:
    """Read CLI-supplied JSON, recording unreadable inputs fail-closed."""
    loaded: list[dict] = []
    problems: list[dict] = []
    for raw in paths:
        path = Path(raw)
        source = {"kind": kind, "path": str(path), "sha256": sha256_file(path),
                  "status": "ok"}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError) as exc:
            source.update(status="INVALID", error=f"{type(exc).__name__}: {exc}")
            problems.append(source)
            continue
        loaded.append({"data": data, "source": source})
    return loaded, problems


# ---------------------------------------------------------------------------
# PE facts: only PE32 i386 is in scope
# ---------------------------------------------------------------------------

def arch_family(fact: dict) -> tuple[str | None, str]:
    """Classify one PE fact into a bridge family, refusing anything unclear."""
    pe_type = norm_token(first(fact.get("pe_type"), fact.get("type")))
    machine = norm_token(first(fact.get("architecture"), fact.get("machine"),
                               fact.get("arch")))
    family = norm_token(first(fact.get("family"), fact.get("target_arch")))
    status = str(fact.get("status") or "").lower()
    error = str(fact.get("error") or "")
    tokens = {pe_type, machine, family}
    blob = norm_token(" ".join((error, status)))

    managed_flags = ("is_dotnet", "dotnet", "is_clr", "has_clr", "clr",
                     "is_managed", "is_il", "ilonly")
    if any(fact.get(flag) is True for flag in managed_flags) or any(
            token in tokens for token in (".net", "dotnet", "clr", "msil", "il",
                                          "ilonly", "comdescriptor")):
        return ".NET", "CLR/.NET managed assembly; no managed runtime on the macOS target"
    if (pe_type in ("ne", "win16-ne") or machine in ("ne", "win16")
            or family in ("win16", "16-bit", "16bit")
            or "win16" in blob or "16-bit" in blob
            or fact.get("is_win16") is True):
        return "Win16", "NE/Win16 image; no 16-bit loader on the macOS target"
    if (pe_type in ("pe32+", "pe64", "pe32plus") or machine in X64_MACHINES
            or family in ("x64", "amd64", "x86-64")):
        return "x64", ("PE32+ / x64-family image "
                       + (f"({machine}) " if machine else "")
                       + "; x64 feasibility gate is not open")
    if (family in ("x86", "i386", "i686", "ia32") or machine in X86_MACHINES
            or (pe_type == "pe32" and machine in X86_MACHINES)):
        return "x86", "PE32 i386 image; supported bridge family"
    return None, ("unrecognised PE family "
                  f"(pe_type={pe_type or '-'}, machine={machine or '-'}, "
                  f"status={status or '-'}); refusing to claim support")


def fact_names(fact: dict) -> set[str]:
    names: set[str] = set()
    for key in ("name", "game", "target", "original_executable", "project"):
        value = fact.get(key)
        if isinstance(value, str) and value.strip():
            names.add(key_of(value))
    for key in ("relative_path", "path", "file", "original", "input"):
        value = fact.get(key)
        if isinstance(value, str) and value.strip():
            names.add(key_of(Path(value).name))
            names.add(key_of(value))
    return {name for name in names if name}


def index_pe_facts(loaded: list[dict]) -> tuple[list[dict], list[dict]]:
    """Flatten PE facts into records keyed by real SHA-256 and names."""
    facts: list[dict] = []
    problems: list[dict] = []
    for wrapped in loaded:
        source = wrapped["source"]
        for index, raw in enumerate(records_from(wrapped["data"])):
            if not isinstance(raw, dict):
                problems.append({"path": source["path"], "index": index,
                                 "reason": "PE fact record is not a JSON object"})
                continue
            sha = norm_sha(first(raw.get("sha256"), raw.get("input_sha256")))
            family, family_reason = arch_family(raw)
            path_value = first(raw.get("relative_path"), raw.get("path"))
            derived_name = Path(str(path_value)).stem if path_value else "?"
            fact = {
                "sha256": sha,
                "name": str(first(raw.get("name"), raw.get("game"),
                                  raw.get("target"), raw.get("original_executable"))
                            or derived_name),
                "names": fact_names(raw),
                "family": family,
                "family_reason": family_reason,
                "pe_type": str(raw.get("pe_type") or raw.get("type") or "") or None,
                "machine": str(raw.get("architecture") or raw.get("machine") or "") or None,
                "status": str(raw.get("status") or "") or None,
                "path": str(first(raw.get("relative_path"), raw.get("path")) or "") or None,
                "source": source,
            }
            facts.append(fact)
    return facts, problems


# ---------------------------------------------------------------------------
# Corpus records and oracle claims
# ---------------------------------------------------------------------------

def normalize_status(value: Any) -> str:
    token = str(value or "").strip().upper()
    if token.startswith("BUILD"):
        return "PARTIAL"  # this report has no BUILD ONLY status
    if token.startswith("NOT"):
        return "NOT TESTED"
    if token in STATUSES:
        return token
    return "NOT TESTED"


def refusal_of(record: dict) -> bool:
    raw = record.get("refusal")
    if isinstance(raw, dict):
        return (raw.get("refused") is True
                or str(raw.get("verdict", "")).upper() == "REFUSED")
    if isinstance(raw, bool):
        return raw
    return str(record.get("verdict", "")).upper() == "REFUSED"


def validate_oracle(record: dict, *, base_dir: Path) -> dict:
    """Validate one PASS claim structurally, including its evidence file hash."""
    raw = record.get("oracle") if isinstance(record.get("oracle"), dict) else {}
    hashes = record.get("hashes") if isinstance(record.get("hashes"), dict) else {}
    checks = record.get("checks") if isinstance(record.get("checks"), dict) else {}
    scenario = record.get("scenario") if isinstance(record.get("scenario"), dict) else {}
    top_oracle = record.get("oracle") if not isinstance(record.get("oracle"), dict) else None

    name = norm_oracle(first(raw.get("oracle"), raw.get("name"), raw.get("kind"),
                             top_oracle, record.get("oracle_name")))
    flag = first(raw.get("verified"), raw.get("oracle_verified"),
                 record.get("oracle_verified"), checks.get("has_real_oracle_flag"))
    declared_valid = first(raw.get("valid"), record.get("oracle_valid"))
    input_sha = norm_sha(first(raw.get("input_sha256"), hashes.get("input_sha256"),
                               record.get("input_sha256"), record.get("sha256")))
    native_sha = norm_sha(first(raw.get("native_sha256"), hashes.get("native_sha256"),
                                record.get("native_sha256")))
    scenario_name = first(scenario.get("name"), scenario.get("scenario"),
                          raw.get("scenario"), record.get("scenario_name"),
                          record.get("scenario") if isinstance(record.get("scenario"), str) else None)
    scenario_text = first(scenario.get("evidence"), scenario.get("scenario_evidence"),
                          raw.get("scenario_evidence"), record.get("scenario_evidence"))
    evidence_raw = first(raw.get("evidence_path"), record.get("evidence_path"))
    claimed_sha = norm_sha(first(raw.get("evidence_sha256"),
                                 hashes.get("evidence_sha256"),
                                 record.get("evidence_sha256")))
    game = str(first(record.get("game"), record.get("name"),
                     raw.get("game")) or "").strip()

    evidence_path = None
    if isinstance(evidence_raw, str) and evidence_raw.strip():
        path = Path(evidence_raw.strip())
        evidence_path = path if path.is_absolute() else base_dir / path
    actual_sha = sha256_file(evidence_path) if evidence_path else None

    result_checks = {
        "has_game_name": bool(game),
        "has_real_oracle_flag": flag is True,
        "has_real_oracle_name": name in REAL_ORACLES and name not in NO_ORACLES,
        "has_input_sha256": input_sha is not None,
        "has_native_sha256": native_sha is not None,
        "has_scenario": bool(str(scenario_name or "").strip()),
        "has_scenario_evidence": bool(str(scenario_text or "").strip()),
        "has_evidence_file": evidence_path is not None and actual_sha is not None,
        "evidence_hash_matches": bool(claimed_sha and actual_sha and claimed_sha == actual_sha),
        "not_marked_invalid": declared_valid is not False,
    }
    reasons: list[str] = []
    if not result_checks["has_real_oracle_flag"]:
        reasons.append("oracle_verified is not the boolean true (no executed-oracle flag)")
    if not result_checks["has_real_oracle_name"]:
        reasons.append(f"oracle {name!r} is not an accepted executed original-Windows oracle")
    if not result_checks["has_input_sha256"]:
        reasons.append("oracle claim has no valid original input SHA-256")
    if not result_checks["has_native_sha256"]:
        reasons.append("oracle claim has no valid native binary SHA-256")
    if not result_checks["has_scenario"]:
        reasons.append("oracle claim has no named scenario")
    if not result_checks["has_scenario_evidence"]:
        reasons.append("oracle claim has no scenario evidence text")
    if not result_checks["has_evidence_file"]:
        reasons.append(f"scenario evidence file is missing: {str(evidence_raw or '(none)')}")
    if not result_checks["evidence_hash_matches"]:
        reasons.append("scenario evidence SHA-256 is absent or does not match the file")
    if not result_checks["has_game_name"]:
        reasons.append("oracle claim has no game name")
    if not result_checks["not_marked_invalid"]:
        reasons.append("oracle claim is explicitly marked invalid")

    return {
        "game": game,
        "oracle": name,
        "flag": flag,
        "input_sha256": input_sha,
        "native_sha256": native_sha,
        "scenario": str(scenario_name or "").strip(),
        "scenario_evidence": str(scenario_text or "").strip(),
        "evidence_path": str(evidence_path) if evidence_path else None,
        "claimed_sha256": claimed_sha,
        "actual_sha256": actual_sha,
        "checks": result_checks,
        "reasons": reasons,
        "valid": all(result_checks.values()),
    }


def index_oracles(loaded: list[dict]) -> list[dict]:
    out: list[dict] = []
    for wrapped in loaded:
        base_dir = Path(wrapped["source"]["path"]).parent
        for raw in records_from(wrapped["data"]):
            if isinstance(raw, dict):
                out.append(validate_oracle(raw, base_dir=base_dir))
    return out


def index_corpus(loaded: list[dict]) -> list[dict]:
    """Flatten corpus acceptance reports into hash-keyed, advisory records."""
    rows: list[dict] = []
    for wrapped in loaded:
        source = wrapped["source"]
        base_dir = Path(source["path"]).parent
        for index, raw in enumerate(records_from(wrapped["data"])):
            if not isinstance(raw, dict):
                continue
            original = raw.get("original") if isinstance(raw.get("original"), dict) else {}
            native = raw.get("native") if isinstance(raw.get("native"), dict) else {}
            pipeline = raw.get("pipeline") if isinstance(raw.get("pipeline"), dict) else {}
            launch = raw.get("launch") if isinstance(raw.get("launch"), dict) else {}
            frames = raw.get("frames") if isinstance(raw.get("frames"), dict) else {}
            hashes = raw.get("hashes") if isinstance(raw.get("hashes"), dict) else {}
            input_sha = norm_sha(first(original.get("sha256"), hashes.get("input_sha256"),
                                       raw.get("input_sha256"), raw.get("sha256")))
            native_sha = norm_sha(first(native.get("sha256"), hashes.get("native_sha256"),
                                        raw.get("native_sha256")))
            pipeline_sha = norm_sha(pipeline.get("native_sha256"))
            example = frames.get("example") if isinstance(frames.get("example"), dict) else {}
            name = str(first(raw.get("game"), raw.get("name"), raw.get("project")) or "")
            names = {key_of(name)} if name else set()
            for key in ("relative_path", "path"):
                value = original.get(key)
                if isinstance(value, str) and value.strip():
                    names.add(key_of(Path(value).name))
                    names.add(key_of(value))
            stages = pipeline.get("stages") if isinstance(pipeline.get("stages"), list) else []
            stage_failed = pipeline.get("stage_failed") or any(
                isinstance(stage, dict) and str(stage.get("status", "")).lower()
                in ("error", "failed", "fail", "crashed") for stage in stages) or None
            embedded = validate_oracle(raw, base_dir=base_dir) if (
                raw.get("oracle") is not None or raw.get("oracle_verified") is not None
                or raw.get("evidence_path") is not None) else None
            rows.append({
                "index": index,
                "name": name,
                "names": {n for n in names if n},
                "status": normalize_status(raw.get("status")),
                "input_sha256": input_sha,
                "native_sha256": native_sha,
                "pipeline_sha256": pipeline_sha,
                "native_hashes_agree": native_sha is None or pipeline_sha is None
                                       or native_sha == pipeline_sha,
                "launched": bool(launch.get("executed")),
                "exit_code": launch.get("exit_code"),
                "nonblank_frame": bool(example) and isinstance(
                    example.get("distinct_colors"), int)
                    and example["distinct_colors"] > 1,
                "stage_failed": stage_failed,
                "refusal": refusal_of(raw),
                "oracle": embedded,
                "source": source,
            })
    return rows


def find_oracle(name: str, input_sha: str | None,
                oracles: list[dict]) -> tuple[dict | None, bool]:
    """Match an oracle by input hash (preferred) or name; refuse ambiguity."""
    by_hash = [o for o in oracles if input_sha and o["input_sha256"] == input_sha]
    if by_hash:
        unique = {(o["native_sha256"], o["evidence_path"], o["oracle"]) for o in by_hash}
        return by_hash[0], len(unique) > 1
    by_name = [o for o in oracles if name and o["game"]
               and key_of(o["game"]) == key_of(name)]
    if by_name:
        unique = {(o["input_sha256"], o["native_sha256"], o["evidence_path"])
                  for o in by_name}
        return by_name[0], len(unique) > 1
    return None, False


# ---------------------------------------------------------------------------
# Classification (the only place PASS is assigned)
# ---------------------------------------------------------------------------

def classify(fact: dict | None, corpus: dict | None, oracle: dict | None,
             *, oracle_ambiguous: bool = False) -> dict:
    """Join one PE fact with corpus/oracle evidence into a fail-closed row."""
    reasons: list[str] = []
    name = (corpus["name"] if corpus else None) or (fact["name"] if fact else "?")
    family = fact["family"] if fact else None
    input_sha = fact["sha256"] if fact else (corpus["input_sha256"] if corpus else None)
    checks: dict[str, Any] = {
        "has_pe_fact": fact is not None,
        "supported_arch": family == "x86",
        "has_corpus_record": corpus is not None,
        "has_oracle_record": oracle is not None,
        "has_input_sha256": is_sha256(input_sha),
        "hashes_agree_across_sources": True,
        "native_hashes_agree": True,
        "launch_evidenced": bool(corpus and corpus["launched"]),
        "no_refusal": not bool(corpus and corpus["refusal"]),
        "no_failed_stage": not bool(corpus and corpus["stage_failed"]),
        "oracle_hashes_match": False,
        "evidence_hash_matches": bool(oracle and oracle["checks"]["evidence_hash_matches"]),
        "pass_eligible": False,
    }
    if oracle:
        for key, value in oracle["checks"].items():
            checks.setdefault(key, value)
    hashes: dict[str, Any] = {
        "input_sha256": input_sha,
        "native_sha256": corpus["native_sha256"] if corpus else None,
        "pipeline_sha256": corpus["pipeline_sha256"] if corpus else None,
        "pe_sha256": fact["sha256"] if fact else None,
        "oracle_input_sha256": oracle["input_sha256"] if oracle else None,
        "oracle_native_sha256": oracle["native_sha256"] if oracle else None,
        "claimed_evidence_sha256": oracle["claimed_sha256"] if oracle else None,
        "actual_evidence_sha256": oracle["actual_sha256"] if oracle else None,
    }

    if fact is None:
        reasons.append("no PE facts supplied for this corpus record; original identity "
                       "is unverified (Windows -> macOS cannot be assessed)")
        checks["hashes_agree_across_sources"] = False
        return _row(name, family, fact, corpus, oracle, "BLOCKED", reasons, checks, hashes)

    if family != "x86":
        reasons.append(f"{family or 'unrecognised PE family'} is DEFERRED for the "
                       "Windows -> macOS bridge; no supported execution path, never PASS")
        reasons.append(fact["family_reason"])
        if oracle is not None:
            reasons.append("attached oracle claim is ignored for an unsupported architecture")
        checks["supported_arch"] = False
        checks["pass_eligible"] = False
        return _row(name, family, fact, corpus, oracle, "DEFERRED", reasons, checks, hashes)

    corpus_sha = corpus["input_sha256"] if corpus else None
    native_sha = corpus["native_sha256"] if corpus else None
    hash_conflict = bool(corpus_sha and corpus_sha != input_sha)
    if corpus and not corpus["native_hashes_agree"]:
        checks["native_hashes_agree"] = False
        hash_conflict = True
    input_match = bool(oracle and oracle["input_sha256"] == input_sha)
    native_match = bool(oracle and native_sha and oracle["native_sha256"] == native_sha)
    if oracle and oracle["input_sha256"] and not input_match:
        hash_conflict = True
    if oracle and oracle["native_sha256"] and native_sha and not native_match:
        hash_conflict = True
    checks["hashes_agree_across_sources"] = not hash_conflict
    checks["oracle_hashes_match"] = bool(input_match and native_match)
    if hash_conflict:
        reasons.append("PASS withheld: original/native SHA-256 conflicts across PE facts, "
                       "corpus record and oracle claim")
    if oracle_ambiguous:
        reasons.append("PASS withheld: more than one distinct oracle claim matches this row")

    evidence_tampered = bool(oracle and oracle["claimed_sha256"]
                             and not checks["evidence_hash_matches"])
    if evidence_tampered:
        reasons.append("FAIL: scenario evidence file hash does not match the claimed "
                       "digest (tampered or stale evidence)")
    if oracle:
        reasons.extend("PASS withheld: " + text for text in oracle["reasons"])

    status: str
    if hash_conflict or evidence_tampered:
        status = "FAIL"
    elif corpus and corpus["status"] == "FAIL":
        status = "FAIL"
        reasons.append("corpus acceptance status is FAIL; no evidence to promote")
    elif corpus and (corpus["status"] == "BLOCKED" or corpus["refusal"]):
        status = "BLOCKED"
        reasons.append("corpus record is BLOCKED or carries a refusal")
    elif not corpus and not oracle:
        status = "NOT TESTED"
        reasons.append("PE identity present but no corpus acceptance or oracle evidence")
    elif corpus and (corpus["launched"] or corpus["nonblank_frame"] or native_sha
                     or corpus["status"] in ("PARTIAL", "PASS")):
        status = "PARTIAL"
        reasons.append("build/launch/frame or advisory evidence only; no validated oracle")
    else:
        status = "NOT TESTED"
        reasons.append("no launch or build evidence to report")

    pass_gate = (
        not hash_conflict and not evidence_tampered and not oracle_ambiguous
        and oracle is not None and oracle["valid"]
        and input_match and native_match
        and corpus is not None and corpus["status"] not in ("FAIL", "BLOCKED")
        and (corpus["launched"] or corpus["status"] in ("PARTIAL", "PASS"))
        and not corpus["refusal"] and not corpus["stage_failed"]
        and corpus["native_hashes_agree"]
        and all(oracle["checks"].values())
    )
    if pass_gate:
        status = "PASS"
        reasons = [
            f"validated executed oracle {oracle['oracle']!r} for scenario "
            f"{oracle['scenario']!r}",
            f"original SHA-256 {input_sha} and native SHA-256 {native_sha} agree "
            "across PE facts, corpus record and oracle claim",
            f"scenario evidence {oracle['evidence_path']} SHA-256 "
            f"{oracle['claimed_sha256']} recomputed and matched",
            "scope: evidence join only; no macOS execution or gameplay equivalence",
        ]
    checks["pass_eligible"] = status == "PASS"
    return _row(name, family, fact, corpus, oracle, status, reasons, checks, hashes)


def _row(name: str, family: str | None, fact: dict | None, corpus: dict | None,
         oracle: dict | None, status: str, reasons: list[str], checks: dict,
         hashes: dict) -> dict:
    return {
        "target": name or "?",
        "family": family or "unknown",
        "supported": family == "x86",
        "deferred": status == "DEFERRED",
        "status": status,
        "reason": "; ".join(reasons),
        "reasons": reasons,
        "checks": checks,
        "hashes": hashes,
        "pe": ({
            "pe_type": fact["pe_type"], "machine": fact["machine"],
            "path": fact["path"], "source": fact["source"]["path"],
            "family_reason": fact["family_reason"],
        } if fact else None),
        "corpus_status": corpus["status"] if corpus else None,
        "corpus_source": corpus["source"]["path"] if corpus else None,
        "oracle": ({
            "name": oracle["oracle"], "verified": oracle["flag"] is True,
            "valid": oracle["valid"], "scenario": oracle["scenario"],
            "evidence_path": oracle["evidence_path"],
            "source_claim": bool(oracle["reasons"] or oracle["valid"]),
        } if oracle else None),
        "source_kinds": sorted({item["kind"] for item in (
            fact["source"] if fact else None, corpus["source"] if corpus else None)
            if item}),
    }


def build_report(facts: list[dict], corpus_rows: list[dict], oracles: list[dict],
                 problems: list[dict], inputs: list[dict], generated_at: str) -> dict:
    """Build the deterministic Windows -> macOS report from supplied evidence."""
    used: set[int] = set()
    rows: list[dict] = []

    def resolve(corpus: dict | None, name: str, sha: str | None
                ) -> tuple[dict | None, bool]:
        found, ambiguous = find_oracle(name, sha, oracles)
        if found is None and corpus and corpus.get("oracle") is not None:
            return corpus["oracle"], False
        return found, ambiguous

    for fact in facts:
        matches: list[dict] = []
        if fact["sha256"]:
            matches = [row for row in corpus_rows
                       if row["input_sha256"] == fact["sha256"]]
        if not matches:
            matches = [row for row in corpus_rows
                       if row["names"] & fact["names"]]
        corpus = matches[0] if matches else None
        if corpus is not None:
            used.add(corpus["index"])
        oracle, ambiguous = resolve(
            corpus, (corpus["name"] if corpus else fact["name"]), fact["sha256"])
        rows.append(classify(fact, corpus, oracle, oracle_ambiguous=ambiguous))

    for corpus in corpus_rows:
        if corpus["index"] in used:
            continue
        oracle, ambiguous = resolve(corpus, corpus["name"], corpus["input_sha256"])
        rows.append(classify(None, corpus, oracle, oracle_ambiguous=ambiguous))

    rows.sort(key=lambda row: (row["target"].lower(), row["target"],
                               row["hashes"]["input_sha256"] or ""))
    by_status = {status: sum(1 for row in rows if row["status"] == status)
                 for status in STATUSES}
    by_family = {family: sum(1 for row in rows if row["family"] == family)
                 for family in FAMILIES}
    return {
        "schema": SCHEMA_VERSION,
        "generator": GENERATOR,
        "generated_at": generated_at,
        "source_os": SOURCE_OS,
        "target_os": TARGET_OS,
        "policy": {
            "supported_family": "x86",
            "deferred_families": ["x64", ".NET", "Win16", "unknown"],
            "pass_requires": [
                "executed original-windows oracle flag",
                "matching original and native SHA-256 across supplied sources",
                "named scenario with an evidence file whose hash is recomputed here",
            ],
            "written_status_is_advisory_only": True,
            "unsupported_arch_is_never_pass": True,
        },
        "inputs": sorted(inputs + problems,
                         key=lambda item: (item["kind"], item["path"])),
        "rows": rows,
        "limits": list(LIMITS),
        "summary": {
            "total": len(rows),
            "by_status": by_status,
            "by_family": by_family,
            "supported": by_family["x86"],
            "deferred": sum(by_family[family] for family in FAMILIES if family != "x86"),
            "problem_inputs": len(problems),
        },
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# Windows -> macOS status report",
        "",
        f"Generated: {report['generated_at']}  ",
        f"Schema: {report['schema']}  ",
        f"Source: {report['source_os']}  ",
        f"Target: {report['target_os']}",
        "",
        "PASS requires an executed-oracle flag, matching hashes across supplied "
        "sources and scenario evidence whose hash is recomputed here. Written "
        "statuses are advisory; only PE32 x86 is supported, every other family "
        "is DEFERRED.",
        "",
        "## Inputs",
        "",
        "| Kind | Path | Status | SHA-256 |",
        "| --- | --- | --- | --- |",
    ]
    for item in report["inputs"]:
        lines.append(f"| {item['kind']} | `{item['path']}` | {item['status']} | "
                     f"`{item.get('sha256') or '-'}` |")
        if item.get("error"):
            lines[-1] += f" ({item['error']})"

    supported = [row for row in report["rows"] if row["supported"]]
    deferred = [row for row in report["rows"] if row["deferred"]]
    other = [row for row in report["rows"] if not row["supported"] and not row["deferred"]]

    lines += ["", "## Supported PE32 x86 rows", "",
              "| Target | Original SHA-256 | PE | Status | Reason |",
              "| --- | --- | --- | --- | --- |"]
    if not supported:
        lines.append("| _none_ | - | - | - | no supported PE fact supplied |")
    for row in supported:
        pe = row["pe"] or {}
        lines.append(f"| {row['target']} | `{row['hashes']['input_sha256'] or '-'}` "
                     f"| {pe.get('pe_type') or '-'} / {pe.get('machine') or '-'} "
                     f"| **{row['status']}** | {row['reason']} |")

    lines += ["", "## Deferred rows (unsupported architectures)", "",
              "| Target | Family | Status | Reason |",
              "| --- | --- | --- | --- |"]
    if not deferred:
        lines.append("| _none_ | - | - | - |")
    for row in deferred:
        lines.append(f"| {row['target']} | {row['family']} | **{row['status']}** "
                     f"| {row['reason']} |")

    if other:
        lines += ["", "## Rows without PE facts", "",
                  "| Target | Status | Reason |", "| --- | --- | --- |"]
        for row in other:
            lines.append(f"| {row['target']} | **{row['status']}** | {row['reason']} |")

    lines += ["", "## Limits", ""]
    lines.extend(f"- {limit}" for limit in report["limits"])
    counts = report["summary"]["by_status"]
    lines += ["", "## Summary", "",
              "- " + "; ".join(f"{status}: {counts[status]}" for status in STATUSES),
              f"- Supported PE32 x86 rows: {report['summary']['supported']}  ",
              f"- Deferred/unsupported rows: {report['summary']['deferred']}",
              ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus-acceptance", action="append", default=[], metavar="JSON",
                    help="corpus-acceptance/acceptance-matrix JSON (repeatable)")
    ap.add_argument("--pe-facts", action="append", default=[], metavar="JSON",
                    help="PE facts / inventory JSON (repeatable)")
    ap.add_argument("--oracle-evidence", action="append", default=[], metavar="JSON",
                    help="explicit oracle PASS claims (repeatable)")
    ap.add_argument("--out", required=True, type=Path, help="write the JSON report here")
    ap.add_argument("--markdown", type=Path, help="optionally write a Markdown report here")
    ap.add_argument("--generated-at", default=None,
                    help="ISO UTC timestamp override for deterministic output")
    args = ap.parse_args(argv)

    if not any((args.corpus_acceptance, args.pe_facts, args.oracle_evidence)):
        ap.error("supply at least one evidence input")

    inputs: list[dict] = []
    problems: list[dict] = []
    corpus_loaded: list[dict] = []
    facts_loaded: list[dict] = []
    oracle_loaded: list[dict] = []
    for kind, paths, target in (
            ("corpus-acceptance", args.corpus_acceptance, corpus_loaded),
            ("pe-facts", args.pe_facts, facts_loaded),
            ("oracle-evidence", args.oracle_evidence, oracle_loaded)):
        found, failures = load_sources(paths, kind)
        target.extend(found)
        problems.extend(failures)
        inputs.extend(item["source"] for item in found)

    facts, fact_problems = index_pe_facts(facts_loaded)
    problems.extend(fact_problems)
    report = build_report(
        facts, index_corpus(corpus_loaded), index_oracles(oracle_loaded),
        problems, inputs, generated_at=args.generated_at or utc_now())

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(report), encoding="utf-8")
    counts = report["summary"]["by_status"]
    print("summary: " + "; ".join(f"{key}: {value}" for key, value in counts.items()))
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
