#!/usr/bin/env python3
"""Deterministic, fail-closed Markdown index over CLI-supplied corpus evidence.

This tool walks an evidence root supplied on the command line, locates the
per-game JSON written by the corpus harness and re-derives every hash it
prints by reading the bytes at the paths supplied inside those JSON files.
Nothing in a JSON file is trusted as evidence about itself:

* ``verification.json`` (``tools/verify_corpus.py``) supplies the original
  executable path and hash and the native binary path and hash.
* ``frame-proof.json`` (``tools/frame_proof.py``) supplies one path and hash
  per captured frame.

A claimed hash that disagrees with the bytes on disk is ``TAMPERED``; a
claimed path whose file is absent is ``BLOCKED``. There is no code path that
assigns PASS: without an original-Windows execution oracle, this indexer only
re-derives identity facts and never claims gameplay equivalence.

Usage:
    python3 -m tools.corpus_evidence_index \
        --evidence-root <dir> --out <index.md> \
        [--json <index.json>] [--label <label>] [--strict]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
GENERATOR = "tools/corpus_evidence_index.py"

# PASS is deliberately present with a permanent zero count: it documents the
# refusal rather than silently omitting the highest verdict from the ladder.
VERDICTS = ("TAMPERED", "PARTIAL", "BUILD ONLY", "FAIL", "BLOCKED", "PASS")
PASS = "PASS"

_HEX = re.compile(r"^[0-9a-fA-F]{64}$")

# The tools that write the JSON consumed below. Listed verbatim so the index
# records the exact commands that produce its inputs; this indexer never runs
# them and never manufactures an evidence file.
PRODUCER_COMMANDS = (
    "python3 -m tools.verify_corpus",
    "python3 -m tools.game_runner --game <NAME> --binary <PATH> "
    "--folder <DIR> --out <DIR>",
    "python3 -m tools.frame_proof --summary <DIR>/summary.json --out <DIR>",
)

# A directory is a candidate game if it holds any per-game harness artifact.
# A missing verification.json must surface as a BLOCKED row, not silently
# disappear from the index, so discovery cannot rely on that one file.
GAME_MARKERS = ("verification.json", "frame-proof.json", "summary.json",
                "status.json", "acceptance.json", "engines.json",
                "macos-to-windows.json", "linux-to-windows.json")

LIMITS = (
    "PASS is never emitted: this indexer has no original-Windows execution "
    "oracle and does not run any game or engine.",
    "Hashes are re-derived only from the bytes at the paths supplied in the "
    "JSON; a claimed hash is never accepted as evidence about itself.",
    "A missing file is BLOCKED and a hash disagreement is TAMPERED; neither "
    "is ever downgraded into a passing verdict.",
    "Frame blank/colour fields are claims. They are counted as non-blank only "
    "after that frame's hash has been independently re-derived and matched.",
    "No timestamps or machine-dependent ordering appear in the output; the "
    "same arguments and bytes always produce a byte-identical index.",
)


# --------------------------------------------------------------------------
# hashing / loading primitives
# --------------------------------------------------------------------------
def sha256_file(path: Path) -> str | None:
    """Exact SHA-256 of a file, or None when it cannot be read (never guessed)."""
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except (OSError, ValueError):
        return None


def is_sha256(value: Any) -> bool:
    """True only for a 64-character hexadecimal digest string."""
    return isinstance(value, str) and bool(_HEX.match(value))


def load_object(path: Path) -> dict:
    """Load a JSON object from a CLI-supplied path; fail on arrays/scalars."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def resolve_supplied(path_text: Any, base: Path) -> Path | None:
    """Resolve a path supplied in JSON; relative paths are relative to its file."""
    if not isinstance(path_text, str) or not path_text.strip():
        return None
    path = Path(path_text)
    return path if path.is_absolute() else base / path


def check_claim(path_text: Any, claimed: Any, base: Path) -> dict:
    """Re-derive a claimed hash from the supplied path.

    Statuses: MATCH, MISMATCH, MISSING, NO_CLAIM (no artifact was supplied),
    INVALID_CLAIM (a path was supplied but the hash claim is malformed).
    """
    result = {
        "path": path_text if isinstance(path_text, str) and path_text else None,
        "claimed": claimed if is_sha256(claimed) else None,
        "actual": None,
        "status": "NO_CLAIM",
    }
    if not result["path"]:
        return result
    if not is_sha256(claimed):
        result["status"] = "INVALID_CLAIM"
        return result
    path = resolve_supplied(path_text, base)
    if path is None or not path.is_file():
        result["status"] = "MISSING"
        return result
    actual = sha256_file(path)
    if actual is None:
        result["status"] = "MISSING"
        return result
    result["actual"] = actual
    result["status"] = "MATCH" if actual == claimed.lower() else "MISMATCH"
    return result


# --------------------------------------------------------------------------
# per-game indexing and verdicts
# --------------------------------------------------------------------------
def discover_games(root: Path) -> list[Path]:
    """Every directory under root holding a per-game harness artifact, sorted."""
    if not root.is_dir():
        return []
    found = {p.parent for name in GAME_MARKERS
             for p in root.rglob(name) if p.is_file()}
    return sorted(found, key=lambda p: p.relative_to(root).as_posix())


def index_game(edir: Path) -> dict:
    """Read one per-game evidence dir; recompute its original/native/frame hashes."""
    record: dict[str, Any] = {
        "slug": edir.name,
        "evidence_dir": str(edir),
        "evidence_ref": edir.name,
        "original": {"path": None, "claimed": None, "actual": None,
                     "status": "NO_CLAIM"},
        "native": {"path": None, "claimed": None, "actual": None,
                   "status": "NO_CLAIM"},
        "frames": [],
        "frame_error": None,
        "nonblank_verified": 0,
        "claimed_classification": None,
        "hard_block": None,
        "verdict": "BLOCKED",
        "reason": "",
    }
    vpath = edir / "verification.json"
    if not vpath.is_file():
        record["hard_block"] = (
            "verification.json missing (required per-game corpus JSON)")
        return record
    try:
        verification = load_object(vpath)
    except (OSError, ValueError) as exc:
        record["hard_block"] = f"verification.json unreadable: {exc}"
        return record

    source = verification.get("source")
    source = source if isinstance(source, dict) else {}
    native = verification.get("native")
    native = native if isinstance(native, dict) else {}
    record["original"] = check_claim(verification.get("source_exe"),
                                     source.get("sha256"), edir)
    record["native"] = check_claim(native.get("path"), native.get("sha256"), edir)
    claimed_cls = verification.get("classification")
    record["claimed_classification"] = claimed_cls if isinstance(claimed_cls, str) else None

    fpath = edir / "frame-proof.json"
    if fpath.is_file():
        try:
            proof = load_object(fpath)
            rows = proof.get("frames", [])
            if not isinstance(rows, list):
                rows = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                claim = check_claim(row.get("path"), row.get("sha256"), edir)
                claim["blank_claim"] = (row.get("blank")
                                        if isinstance(row.get("blank"), bool) else None)
                record["frames"].append(claim)
        except (OSError, ValueError) as exc:
            record["frame_error"] = f"frame-proof.json unreadable: {exc}"
    else:
        record["frame_error"] = "frame-proof.json missing; no frame hashes to re-derive"
    return record


def assign_verdict(record: dict) -> tuple[str, str]:
    """Assign exactly one fail-closed verdict. PASS is structurally unreachable."""
    if record.get("hard_block"):
        return "BLOCKED", record["hard_block"]
    mismatches: list[str] = []
    for name, claim in (("original", record["original"]), ("native", record["native"])):
        if claim["status"] == "MISMATCH":
            mismatches.append(
                f"{name} hash mismatch (claimed {claim['claimed']}, "
                f"recomputed {claim['actual']})")
        elif claim["status"] == "INVALID_CLAIM":
            mismatches.append(f"{name} path supplied without a valid SHA-256 claim")
    for index, claim in enumerate(record["frames"]):
        if claim["status"] == "MISMATCH":
            mismatches.append(
                f"frame[{index}] hash mismatch (claimed {claim['claimed']}, "
                f"recomputed {claim['actual']})")
        elif claim["status"] == "INVALID_CLAIM":
            mismatches.append(f"frame[{index}] path supplied without a valid SHA-256 claim")
    if mismatches:
        return "TAMPERED", "; ".join(mismatches) + "; evidence not trusted"

    if record["original"]["status"] == "NO_CLAIM":
        return "BLOCKED", "no original executable path supplied"
    if record["original"]["status"] == "MISSING":
        return "BLOCKED", "original executable path supplied but file missing; hash not re-derived"
    if record["frame_error"]:
        return "BLOCKED", record["frame_error"]
    if any(c["status"] == "MISSING" for c in record["frames"]):
        return "BLOCKED", "a frame path was supplied but its file is missing"
    if record["native"]["status"] == "NO_CLAIM":
        return "BLOCKED", "no native binary claimed (no build to index)"
    if record["native"]["status"] == "MISSING":
        return "BLOCKED", "native binary path supplied but file missing; hash not re-derived"

    verified = [c for c in record["frames"] if c["status"] == "MATCH"]
    nonblank = sum(1 for c in verified if c["blank_claim"] is False)
    record["nonblank_verified"] = nonblank
    if nonblank:
        return ("PARTIAL",
                f"{nonblank} re-derived non-blank frame(s); no Windows oracle, PASS not claimed")
    if verified:
        return "FAIL", "all re-derived frames are blank; not render evidence"
    if (record["original"]["status"] == "MATCH"
            and record["native"]["status"] == "MATCH"):
        return "BUILD ONLY", "original and native hashes re-derived; no frame evidence"
    return "BLOCKED", "insufficient verifiable evidence"


def build_index(evidence_root: Path, *, label: str, command: str,
                template: str) -> dict:
    """Build the machine-readable index; no clock, no environment, no network."""
    games: list[dict] = []
    for edir in discover_games(evidence_root):
        try:
            rel = edir.relative_to(evidence_root).as_posix()
        except ValueError:
            rel = str(edir)
        record = index_game(edir)
        record["evidence_ref"] = rel
        verdict, reason = assign_verdict(record)
        record["verdict"] = verdict
        record["reason"] = reason
        if verdict == PASS:  # defensive: never allow a pass to leak in
            record["verdict"] = "BLOCKED"
            record["reason"] = "PASS refused: no Windows oracle; " + reason
        games.append(record)
    games.sort(key=lambda item: item["slug"])
    counts = {verdict: 0 for verdict in VERDICTS}
    for game in games:
        counts[game["verdict"]] += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "label": label,
        "evidence_root": str(evidence_root),
        "counts": counts,
        "games": games,
        "commands": {
            "exact": command,
            "template": template,
            "producers": list(PRODUCER_COMMANDS),
        },
        "limits": list(LIMITS),
    }


# --------------------------------------------------------------------------
# deterministic Markdown rendering
# --------------------------------------------------------------------------
def md_cell(value: Any) -> str:
    """Escape a value for a GFM table cell."""
    text = "" if value is None else str(value)
    text = text.replace("\\", "\\\\").replace("|", "\\|")
    return text.replace("\r", " ").replace("\n", " ")


def _fence(value: str) -> str:
    """Backtick fence longer than any run inside the value."""
    return "`" * (max((len(run) for run in re.findall(r"`+", value)), default=0) + 1)


def md_code(value: str) -> str:
    """Inline code span that survives embedded backticks."""
    fence = _fence(value)
    pad = " " if value.startswith("`") or value.endswith("`") else ""
    return f"{fence}{pad}{value}{pad}{fence}"


def md_block(value: str) -> str:
    """Fenced code block that survives backticks in a CLI-supplied path."""
    longest = max((len(run) for run in re.findall(r"`+", value)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}sh\n{value}\n{fence}"


def hash_cell(claim: dict) -> str:
    if claim["status"] == "MATCH":
        return md_code(claim["actual"])
    return claim["status"]


def render_markdown(index: dict) -> str:
    """Render a byte-stable Markdown index from an already-built index dict."""
    out: list[str] = [
        "# Corpus evidence index",
        "",
        f"- Label: {md_cell(index['label'])}",
        f"- Evidence root: {md_code(index['evidence_root'])}",
        f"- Generator: {md_code(index['generator'])} (schema {index['schema_version']})",
        "",
        "All hashes below were re-derived from bytes at the paths supplied in "
        "the JSON. Claimed hashes are never trusted and PASS is never emitted.",
        "",
        "## Verdict counts",
        "",
        "| Verdict | Count |",
        "|---|---:|",
    ]
    for verdict in VERDICTS:
        out.append(f"| {verdict} | {index['counts'][verdict]} |")
    out += [
        f"| **Total** | **{len(index['games'])}** |",
        "",
        "## Games",
        "",
        "| Game | Verdict | Original SHA-256 | Native SHA-256 | Frames | Evidence | Reason |",
        "|---|---|---|---|---|---|---|",
    ]
    for game in index["games"]:
        frames = game["frames"]
        verified = sum(1 for c in frames if c["status"] == "MATCH")
        frame_text = (f"{verified}/{len(frames)} re-derived, "
                      f"{game['nonblank_verified']} non-blank") if frames else "—"
        evidence = md_code(f"{game['evidence_ref']}/verification.json")
        out.append("| {game} | {verdict} | {original} | {native} | {frames} | "
                   "{evidence} | {reason} |".format(
                       game=md_cell(md_code(game["slug"])),
                       verdict=md_cell(game["verdict"]),
                       original=md_cell(hash_cell(game["original"])),
                       native=md_cell(hash_cell(game["native"])),
                       frames=md_cell(frame_text),
                       evidence=md_cell(evidence),
                       reason=md_cell(game["reason"]),
                   ))
    out += [
        "",
        "## Reproduction commands",
        "",
        "Exact invocation used for this index (run from the repository root):",
        "",
        md_block(index["commands"]["exact"]),
        "",
        "Template with placeholders for a fresh evidence root:",
        "",
        md_block(index["commands"]["template"]),
        "",
        "Inputs are written by the corpus harness; this indexer does not run them "
        "and never fabricates an evidence file:",
        "",
        md_block("\n".join(index["commands"]["producers"])),
        "",
        "## Limits",
        "",
    ]
    out.extend(f"- {limit}" for limit in index["limits"])
    out.append("")
    return "\n".join(out)


def make_command(argv: list[str]) -> str:
    """Rebuild the exact indexer invocation from parsed CLI values."""
    parts = ["python3", "-m", "tools.corpus_evidence_index"]
    parts += ["--evidence-root", shlex.quote(str(argv[0]))]
    parts += ["--out", shlex.quote(str(argv[1]))]
    if argv[2] is not None:
        parts += ["--json", shlex.quote(str(argv[2]))]
    if argv[3] is not None:
        parts += ["--label", shlex.quote(str(argv[3]))]
    if argv[4]:
        parts.append("--strict")
    return " ".join(parts)


TEMPLATE_COMMAND = (
    "python3 -m tools.corpus_evidence_index "
    "--evidence-root <EVIDENCE_ROOT> --out <INDEX.md> "
    "[--json <INDEX.json>] [--label <LABEL>] [--strict]"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--evidence-root", required=True, type=Path,
                        help="directory to walk for per-game corpus JSON")
    parser.add_argument("--out", required=True, type=Path,
                        help="write the deterministic Markdown index here")
    parser.add_argument("--json", dest="json_out", type=Path,
                        help="also write the machine-readable index JSON here")
    parser.add_argument("--label", default="CLI-supplied corpus evidence",
                        help="human-readable label recorded in the index")
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 if any game is TAMPERED")
    args = parser.parse_args(argv)

    if not args.evidence_root.is_dir():
        print(f"corpus_evidence_index: not a directory: {args.evidence_root}",
              file=sys.stderr)
        return 2
    games = discover_games(args.evidence_root)
    if not games:
        print("corpus_evidence_index: no per-game evidence under "
              f"{args.evidence_root}; refusing to emit an empty index",
              file=sys.stderr)
        return 2

    command = make_command([args.evidence_root, args.out, args.json_out,
                            args.label, args.strict])
    index = build_index(args.evidence_root, label=args.label,
                        command=command, template=TEMPLATE_COMMAND)
    markdown = render_markdown(index)
    try:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(markdown, encoding="utf-8")
        if args.json_out is not None:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(
                json.dumps(index, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8")
    except OSError as exc:
        print(f"corpus_evidence_index: cannot write index: {exc}", file=sys.stderr)
        return 2

    counts = ", ".join(f"{key}={value}" for key, value in index["counts"].items())
    print(f"wrote {args.out} ({len(index['games'])} games; {counts})")
    if args.strict and index["counts"]["TAMPERED"]:
        print("corpus_evidence_index: TAMPERED evidence found (--strict)",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
