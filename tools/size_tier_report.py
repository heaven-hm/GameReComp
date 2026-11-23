#!/usr/bin/env python3
"""Group one-at-a-time game runs into size tiers with honest frame evidence.

Inputs (both supplied on the command line, never hard-coded):

* ``summary.json`` written by :mod:`tools.game_runner` -- the list of runs,
  their binary paths/hashes, exit codes, evidence directories and frame paths.
* ``frame-proof.json`` written by :mod:`tools.frame_proof` -- the exact
  SHA-256, dimensions and distinct-colour count of each decoded frame.

The report groups games into ``small`` / ``medium`` / ``big`` tiers by native
binary size. Only frames that appear in *both* files are given a hash and a
colour count: a frame present only in the summary is ``UNPROVEN``, and a
single-colour frame is ``BLANK``. Games with no frames, or only blank frames,
are marked ``FAIL``. No image is embedded, generated or guessed -- this tool
reports evidence, it does not create a screenshot.

Usage:
    python3 -m tools.size_tier_report --summary <summary.json> \
        --frame-proof <frame-proof.json> --out <report.md> \
        [--html <gallery.html>] [--small-max 4MiB --medium-max 12MiB]

With ``--html`` the tool additionally emits a standalone HTML proof gallery.
The gallery is **native macOS only — no Windows oracle column**: it shows only
frame paths, exact hashes, colour counts and BLANK/UNPROVEN/FAIL markers taken
from the two input records. It references real frame files (never generated or
invented ones) when they still exist at the recorded paths.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

DEFAULT_SMALL_MAX = "4MiB"
DEFAULT_MEDIUM_MAX = "12MiB"
TIERS = ("small", "medium", "big")
TIER_LABELS = {
    "small": "small",
    "medium": "medium",
    "big": "big",
    "unknown": "unknown size (no binary available)",
}

_UNIT_BYTES = {
    "": 1,
    "b": 1,
    "k": 1000, "kb": 1000, "ki": 1024, "kib": 1024,
    "m": 1000 ** 2, "mb": 1000 ** 2, "mi": 1024 ** 2, "mib": 1024 ** 2,
    "g": 1000 ** 3, "gb": 1000 ** 3, "gi": 1024 ** 3, "gib": 1024 ** 3,
    "t": 1000 ** 4, "tb": 1000 ** 4, "ti": 1024 ** 4, "tib": 1024 ** 4,
}
_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([A-Za-z]*)\s*$")


def parse_size(value: Any) -> int:
    """Parse a byte count such as ``4096``, ``4KiB``, ``4MiB`` or ``4MB``."""
    if isinstance(value, bool):
        raise ValueError(f"invalid byte count: {value!r}")
    if isinstance(value, int):
        size = value
    else:
        match = _SIZE_RE.match(str(value))
        if not match:
            raise ValueError(f"invalid byte count: {value!r}")
        unit = match.group(2).lower()
        if unit not in _UNIT_BYTES:
            raise ValueError(f"unknown size unit in {value!r}")
        size = int(float(match.group(1)) * _UNIT_BYTES[unit])
    if size < 0:
        raise ValueError(f"byte count must be non-negative: {value!r}")
    return size


def human_size(size: int | None) -> str:
    if size is None:
        return "size unavailable"
    return f"{size:,} B ({size / (1024 * 1024):.2f} MiB)"


def _norm(path: str) -> str:
    return os.path.normpath(path) if path else path


def tier_of(size: int | None, small_max: int, medium_max: int) -> str:
    """Return the tier for a binary size; unknown sizes are never guessed."""
    if size is None:
        return "unknown"
    if size < small_max:
        return "small"
    if size < medium_max:
        return "medium"
    return "big"


def binary_size(rec: dict) -> tuple[int | None, str]:
    """Read a size recorded in the summary, or stat the binary path.

    Never fabricates a size: returns ``(None, 'unavailable')`` when neither
    source exists, so the game lands in the unknown-size section.
    """
    for key in ("binary_bytes", "size_bytes"):
        value = rec.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value, key
    binary = rec.get("binary")
    if binary:
        try:
            return Path(str(binary)).stat().st_size, "stat of binary path"
        except OSError:
            pass
    return None, "unavailable"


def index_proof(proof: dict | None) -> tuple[dict, dict]:
    """Index proof rows by (game, path) and by path for a fallback match."""
    by_game_path: dict[tuple[str, str], dict] = {}
    by_path: dict[str, list[dict]] = {}
    for row in (proof or {}).get("frames", []):
        key = (str(row.get("game", "")), _norm(str(row.get("path", ""))))
        # Prefer a row that actually carries a hash when a path was proven
        # more than once (e.g. across re-runs).
        if key not in by_game_path or (
            "sha256" in row and "sha256" not in by_game_path[key]
        ):
            by_game_path[key] = row
        by_path.setdefault(_norm(str(row.get("path", ""))), []).append(row)
    return by_game_path, by_path


def join_frames(rec: dict, by_game_path: dict, by_path: dict) -> list[dict]:
    """Attach a proof row to each frame recorded by the runner, if any."""
    joined = []
    game = str(rec.get("game", ""))
    for frame in rec.get("frames", []):
        path = str(frame.get("path", ""))
        key = (game, _norm(path))
        proof_row = by_game_path.get(key)
        if proof_row is None:
            candidates = by_path.get(_norm(path), [])
            # A path-only match is safe only when it is unambiguous.
            if len(candidates) == 1:
                proof_row = candidates[0]
        joined.append({"frame": frame, "path": path, "proof": proof_row})
    return joined


def frame_rows(joined: list[dict]) -> list[dict]:
    """Resolve each joined frame once into report-ready evidence fields.

    ``exists`` records whether the *real* frame file named by the runner is
    still present at generation time. It is used only to decide whether the
    HTML gallery may render an ``<img>`` of a recorded path; it never causes a
    path, hash or colour count to be invented.
    """
    resolved = []
    for row in joined:
        marker, digest, colours = frame_evidence(row)
        resolved.append({"path": row["path"], "marker": marker,
                         "sha256": digest, "colours": colours,
                         "exists": bool(row["path"]) and Path(row["path"]).is_file()})
    return resolved


def frame_evidence(row: dict) -> tuple[str, str | None, Any]:
    """Return (marker, exact sha256, distinct colours) for one joined frame."""
    proof = row["proof"]
    if proof is None:
        return ("UNPROVEN — no matching frame-proof entry; no hash claimed",
                None, None)
    if proof.get("error"):
        return (f"FAIL — proof error: {proof['error']}",
                proof.get("sha256"), proof.get("distinct_colors"))
    digest = proof.get("sha256")
    colours = proof.get("distinct_colors")
    if not digest or not isinstance(colours, int):
        return ("UNPROVEN — frame proof has no hash or colour count", digest, colours)
    name = Path(row["path"]).name
    if proof.get("blank") or colours == 1:
        return (f"BLANK — {name} is single-colour ({colours}); not graphics evidence",
                digest, colours)
    return (f"OK — {colours} distinct colours (render evidence only, not gameplay)",
            digest, colours)


def run_marker(rec: dict) -> str:
    code = rec.get("exit_code", "?")
    if rec.get("status") == "BLOCKED":
        return "FAIL (BLOCKED — never executed)"
    if code == 0:
        return "OK (exit 0)"
    if code == "TIMEOUT":
        return "TIMEOUT (stayed alive; not a pass)"
    return f"FAIL (exit {code})"


def game_verdict(rec: dict, joined: list[dict]) -> tuple[str, str]:
    """Verdict from recorded evidence only: PARTIAL when non-blank, else FAIL."""
    if rec.get("status") == "BLOCKED":
        reason = rec.get("reason") or "no native binary"
        return "FAIL", f"BLOCKED — {reason}"
    if not joined:
        return "FAIL", "no frames captured — no screenshot exists to report"
    markers = [frame_evidence(row)[0] for row in joined]
    if any(marker.startswith("OK") for marker in markers):
        return ("PARTIAL",
                "at least one non-blank frame; no Windows oracle or gameplay acceptance")
    if any(marker.startswith("BLANK") for marker in markers):
        return "FAIL", "every captured frame is BLANK (single colour)"
    return "FAIL", "no proven non-blank frame"


def analyse(summary: dict, proof: dict | None, *,
            small_max: int, medium_max: int) -> list[dict]:
    """Join summary runs with frame proof and attach tier/verdict metadata."""
    by_game_path, by_path = index_proof(proof)
    games = []
    for rec in summary.get("results", []):
        size, size_source = binary_size(rec)
        joined = join_frames(rec, by_game_path, by_path)
        verdict, reason = game_verdict(rec, joined)
        frames = frame_rows(joined)
        log = str(Path(str(rec.get("evidence", ""))) / "run.log") if rec.get("evidence") else ""
        games.append({
            "game": str(rec.get("game", "?")),
            "size": size,
            "size_source": size_source,
            "tier": tier_of(size, small_max, medium_max),
            "binary": str(rec.get("binary", "")),
            "binary_sha256": str(rec.get("binary_sha256", "")),
            "exit_code": rec.get("exit_code", "?"),
            "run_marker": run_marker(rec),
            "status": rec.get("status", ""),
            "log": log,
            "log_exists": bool(log) and Path(log).is_file(),
            "verdict": verdict,
            "reason": reason,
            "frames": frames,
        })
    return games


def _code(text: str) -> str:
    return f"`{text}`"


def render(games: list[dict], *, small_max: int, medium_max: int,
           summary_label: str, proof_label: str | None,
           title: str = "Size-tier screenshot evidence",
           note: str | None = None) -> str:
    """Render the analysis as Markdown (no images, no invented evidence)."""
    out: list[str] = [f"# {title}", ""]
    out += ["Generated by `tools/size_tier_report.py` from:", "",
            f"- Runner summary: {_code(summary_label)}"]
    if proof_label:
        out.append(f"- Frame proof: {_code(proof_label)}")
    else:
        out.append("- Frame proof: **not supplied** — no hash or colour count is claimed")
    out += ["",
            f"Tier thresholds: **small** < {human_size(small_max)}; "
            f"**medium** < {human_size(medium_max)}; "
            f"**big** ≥ {human_size(medium_max)}. Sizes come from the native "
            "binary recorded by the runner (`binary_bytes` when present, "
            "otherwise `stat` of its path). Games whose binary is unavailable "
            "are listed under **unknown size** and are never assigned a tier.",
            "",
            "**No screenshot is embedded, generated or invented in this report.** "
            "Only frames recorded in the summary *and* decoded in the frame "
            "proof receive an exact SHA-256 and colour count. A frame without "
            "a proof entry is `UNPROVEN`; a single-colour frame is `BLANK` and "
            "is not graphics evidence. Logs are recorded paths, not inlined "
            "output. Nothing here establishes a Windows oracle or gameplay "
            "equivalence."]
    if note:
        out += ["", note]

    total_partial = sum(g["verdict"] == "PARTIAL" for g in games)
    total_fail = sum(g["verdict"] == "FAIL" for g in games)
    nonblank = sum(1 for g in games for f in g["frames"]
                   if f["marker"].startswith("OK"))
    blank = sum(1 for g in games for f in g["frames"]
                if f["marker"].startswith("BLANK"))
    unproven = sum(1 for g in games for f in g["frames"]
                   if f["marker"].startswith("UNPROVEN")
                   or f["marker"].startswith("FAIL"))

    for tier in (*TIERS, "unknown"):
        rows = sorted((g for g in games if g["tier"] == tier),
                      key=lambda g: (g["game"].lower(), g["game"]))
        label = TIER_LABELS[tier]
        out += ["", f"## Tier: {label}", ""]
        if not rows:
            out += ["_No games in this tier._", ""]
            continue
        for game in rows:
            out += [f"### {_code(game['game'])} — "
                    f"{human_size(game['size'])} — **{game['verdict']}**", ""]
            out.append(f"- Verdict: **{game['verdict']}** — {game['reason']}")
            out.append(f"- Run: {game['run_marker']}; "
                       f"exit {_code(str(game['exit_code']))}")
            out.append(f"- Size source: {game['size_source']}")
            if game["binary_sha256"]:
                out.append(f"- Binary SHA-256: {_code(game['binary_sha256'])}")
            if game["status"] == "BLOCKED":
                out.append(f"- Status: **FAIL (BLOCKED)** — no native binary to run")
            log_suffix = "" if game["log_exists"] else " (log file not present at generation time)"
            out.append(f"- Log: {_code(game['log']) if game['log'] else 'none recorded'}{log_suffix}")
            out.append("")
            if game["frames"]:
                dirs = {str(Path(f["path"]).parent) for f in game["frames"]}
                for frame_dir in sorted(dirs):
                    out.append(f"- Frames directory: {_code(frame_dir)}")
                out += ["",
                        "| Frame | SHA-256 (exact) | Distinct colours | Marker |",
                        "|---|---|---|---|"]
                for frame in game["frames"]:
                    digest = _code(frame["sha256"]) if frame["sha256"] else "—"
                    colours = str(frame["colours"]) if isinstance(frame["colours"], int) else "—"
                    marker = frame["marker"].replace("|", "\\|")
                    out.append(f"| {_code(Path(frame['path']).name)} | {digest} | "
                               f"{colours} | {marker} |")
            else:
                out += ["_No frames recorded by the runner — there is no "
                        "screenshot to report for this game._"]
            out.append("")

    out += ["## Evidence summary", "",
            f"- Games: {len(games)} — PARTIAL (non-blank proof): {total_partial}; "
            f"FAIL: {total_fail}",
            f"- Frames: non-blank (OK): {nonblank}; BLANK: {blank}; "
            f"UNPROVEN/proof-failed: {unproven}",
            "",
            "## What this report does not claim", "",
            "- No original-Windows frame is compared; no Windows execution "
            "oracle exists in this environment.",
            "- Non-blank frames prove only that the rebuilt binary rendered "
            "pixel content, not correct gameplay, input or audio.",
            "- No hash, colour count, frame path or screenshot is inferred "
            "when the underlying proof record is absent.",
            "",
            "## Regenerate", "",
            "```sh",
            "python3 -m tools.size_tier_report \\",
            "  --summary <summary.json> --frame-proof <frame-proof.json> \\",
            "  --out <report.md> [--html <gallery.html>]",
            "  [--small-max 4MiB --medium-max 12MiB]",
            "```",
            ""]
    return "\n".join(out)


_MARKER_KINDS = ("BLANK", "UNPROVEN", "FAIL", "OK")

_HTML_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       margin: 2rem auto; max-width: 1100px; color: #222; line-height: 1.45; }
h1 { font-size: 1.6rem; }
h2 { font-size: 1.3rem; margin-top: 2rem; border-bottom: 1px solid #ddd; }
h3 { font-size: 1.05rem; }
code { background: #f4f4f4; padding: 0 3px; word-break: break-all; }
.scope { background: #fff8e1; border-left: 4px solid #f0ad4e; padding: .7rem 1rem; }
.badge { display: inline-block; padding: 1px 7px; border-radius: 10px;
         font: 600 11px/1.5 sans-serif; color: #fff; }
.badge.ok { background: #2e7d32; }
.badge.blank { background: #6c757d; }
.badge.unproven { background: #b8860b; }
.badge.fail { background: #b3261e; }
.badge.unknown { background: #555; }
.verdict { font-weight: 600; }
.verdict.partial { color: #b8860b; }
.verdict.fail { color: #b3261e; }
figure { margin: 0 0 1rem; }
figure img { max-width: 240px; height: auto; border: 1px solid #ccc; display: block; }
table { border-collapse: collapse; width: 100%; margin: .5rem 0 1rem; }
th, td { border: 1px solid #ddd; padding: 6px 8px; text-align: left;
         vertical-align: top; font-size: .9rem; }
th { background: #fafafa; }
td.path { max-width: 420px; }
.empty { color: #666; font-style: italic; }
.limits li { margin-bottom: .35rem; }
footer { margin-top: 2rem; color: #777; font-size: .85rem; }
"""


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _marker_kind(marker: str) -> str:
    """Short badge label for a frame marker: BLANK/UNPROVEN/FAIL/OK."""
    for kind in _MARKER_KINDS:
        if marker.startswith(kind):
            return kind
    return "UNKNOWN"


def _frame_uri(path: str) -> str:
    """A file:// URI for a recorded native path, escaped for an HTML attribute."""
    uri = path if path.startswith("file://") else "file://" + quote(path, safe="/:")
    return html.escape(uri, quote=True)


def _badge(text: str) -> str:
    kind = _marker_kind(text)
    return (f'<span class="badge {kind.lower()}">{_esc(kind)}</span> '
            f'<small>{_esc(text)}</small>')


def render_html(games: list[dict], *, small_max: int, medium_max: int,
                summary_label: str, proof_label: str | None,
                title: str = "Size-tier frame gallery — native macOS only — no Windows oracle column",
                note: str | None = None) -> str:
    """Render a standalone HTML gallery of *recorded* frame evidence.

    Four columns only — recorded frame path, exact SHA-256, distinct colour
    count and marker. There is deliberately no Windows-original column. Blank,
    unproven and failed frames are given explicit badges, and images are
    linked from the real recorded path only when that file still exists.
    """
    out = ["<!doctype html>", '<html lang="en">', "<head>",
           '<meta charset="utf-8">', f"<title>{_esc(title)}</title>",
           "<style>", _HTML_CSS, "</style>", "</head>", "<body>",
           f"<h1>{_esc(title)}</h1>",
           '<p class="scope"><strong>native macOS only — no Windows oracle column.</strong> '
           "Every path, hash and colour count below comes from the runner summary and frame "
           "proof supplied to this tool. There is no original-Windows capture column because "
           "no Windows execution oracle exists in this environment; Wine is not used as the "
           "final runtime.</p>",
           "<p>Generated by <code>tools/size_tier_report.py</code> from "
           f"runner summary <code>{_esc(summary_label)}</code> and frame proof "
           f"<code>{_esc(proof_label or 'not supplied — no hash or colour count is claimed')}</code>. "
           f"Tiers: <strong>small</strong> &lt; {_esc(human_size(small_max))}; "
           f"<strong>medium</strong> &lt; {_esc(human_size(medium_max))}; "
           f"<strong>big</strong> ≥ {_esc(human_size(medium_max))}. Sizes come from the native "
           "binary recorded by the runner; games without a binary are listed under unknown "
           "size and never assigned a tier.</p>",
           '<p class="play">Frame marker legend: '
           + ", ".join(_badge(k) for k in
                       ("OK — non-blank render evidence only, not gameplay",
                        "BLANK — single-colour frame; not graphics evidence",
                        "UNPROVEN — no matching frame-proof entry, hash or colour count",
                        "FAIL — proof error or missing frame file"))
           + "</p>"]
    if note:
        out += ["<p>", _esc(note), "</p>"]

    total_partial = sum(g["verdict"] == "PARTIAL" for g in games)
    total_fail = sum(g["verdict"] == "FAIL" for g in games)
    nonblank = sum(1 for g in games for f in g["frames"]
                   if f["marker"].startswith("OK"))
    blank = sum(1 for g in games for f in g["frames"]
                if f["marker"].startswith("BLANK"))
    unproven = sum(1 for g in games for f in g["frames"]
                   if f["marker"].startswith("UNPROVEN")
                   or f["marker"].startswith("FAIL"))

    for tier in (*TIERS, "unknown"):
        rows = sorted((g for g in games if g["tier"] == tier),
                      key=lambda g: (g["game"].lower(), g["game"]))
        out.append(f'<section id="tier-{tier}">')
        out.append(f"<h2>Tier: {_esc(TIER_LABELS[tier])}</h2>")
        if not rows:
            out.append('<p class="empty">No games in this tier.</p>')
        for game in rows:
            verdict_cls = game["verdict"].lower()
            out.append('<article>')
            out.append(f"<h3><code>{_esc(game['game'])}</code> — "
                       f"{_esc(human_size(game['size']))} — "
                       f'<span class="verdict {verdict_cls}">{_esc(game["verdict"])}</span></h3>')
            out.append("<ul>")
            out.append(f"<li>Verdict: {_esc(game['verdict'])} — {_esc(game['reason'])}</li>")
            out.append(f"<li>Run: {_esc(game['run_marker'])}; exit <code>{_esc(str(game['exit_code']))}</code></li>")
            out.append(f"<li>Size source: {_esc(game['size_source'])}</li>")
            if game["binary_sha256"]:
                out.append(f"<li>Binary SHA-256: <code>{_esc(game['binary_sha256'])}</code></li>")
            if game["status"] == "BLOCKED":
                out.append("<li>Status: <strong>FAIL (BLOCKED)</strong> — no native binary to run</li>")
            if game["log"]:
                suffix = "" if game["log_exists"] else " (log file not present at generation time)"
                out.append(f"<li>Log: <code>{_esc(game['log'])}</code>{_esc(suffix)}</li>")
            out.append("</ul>")

            if game["frames"]:
                out.append('<p class="scope">Frames shown below are native macOS captures only '
                           '— no Windows oracle column.</p>')
                out.append("<table>")
                out.append("<thead><tr><th>Frame path (recorded)</th><th>SHA-256 (exact)</th>"
                           "<th>Distinct colours</th><th>Marker</th></tr></thead>")
                out.append("<tbody>")
                for frame in game["frames"]:
                    digest = f"<code>{_esc(frame['sha256'])}</code>" if frame["sha256"] else "—"
                    colours = str(frame["colours"]) if isinstance(frame["colours"], int) else "—"
                    image = ""
                    if frame.get("exists"):
                        image = (f'<figure><img src="{_frame_uri(frame["path"])}" '
                                 f'alt="Recorded native frame {_esc(Path(frame["path"]).name)}" '
                                 f'width="240"><figcaption>recorded frame file</figcaption></figure>')
                    missing = "" if frame.get("exists") else (
                        "<br><small>frame file not present at generation time</small>")
                    out.append(
                        '<tr>'
                        f'<td class="path">{image}<code>{_esc(frame["path"])}</code>{missing}</td>'
                        f'<td>{digest}</td>'
                        f'<td>{_esc(colours)}</td>'
                        f'<td>{_badge(frame["marker"])}</td>'
                        '</tr>')
                out.append("</tbody></table>")
            else:
                out.append('<p class="empty">No frames recorded by the runner — there is no '
                           'screenshot to report for this game.</p>')
            out.append("</article>")
        out.append("</section>")

    out += ["<section id=\"summary\">", "<h2>Evidence summary</h2>", "<ul>",
            f"<li>Games: {len(games)} — PARTIAL (non-blank proof): {total_partial}; "
            f"FAIL: {total_fail}</li>",
            f"<li>Frames: non-blank (OK): {nonblank}; BLANK: {blank}; "
            f"UNPROVEN/proof-failed: {unproven}</li>",
            "</ul>", "</section>",
            '<section id="limits" class="limits">',
            "<h2>What this gallery does not claim</h2>", "<ul>",
            "<li>No original-Windows frame is compared; no Windows execution oracle exists "
            "in this environment. This is a native macOS only gallery.</li>",
            "<li>Non-blank frames prove only that the rebuilt binary rendered pixel content, "
            "not correct gameplay, input or audio.</li>",
            "<li>No hash, colour count, frame path or screenshot is inferred when the "
            "underlying proof record is absent.</li>",
            "</ul>", "</section>",
            "<footer>Regenerate with "
            "<code>python3 -m tools.size_tier_report --summary &lt;summary.json&gt; "
            "--frame-proof &lt;frame-proof.json&gt; --out &lt;report.md&gt; "
            "--html &lt;gallery.html&gt;</code>.</footer>",
            "</body>", "</html>", ""]
    return "\n".join(out)


def build_html_report(summary: dict, proof: dict | None, *,
                      small_max: int, medium_max: int,
                      summary_label: str, proof_label: str | None,
                      title: str = "Size-tier frame gallery — native macOS only — no Windows oracle column",
                      note: str | None = None) -> str:
    """Convenience wrapper: analyse the JSON dicts and render the HTML gallery."""
    games = analyse(summary, proof, small_max=small_max, medium_max=medium_max)
    return render_html(games, small_max=small_max, medium_max=medium_max,
                       summary_label=summary_label, proof_label=proof_label,
                       title=title, note=note)


def build_report(summary: dict, proof: dict | None, *,
                 small_max: int, medium_max: int,
                 summary_label: str, proof_label: str | None,
                 title: str = "Size-tier screenshot evidence",
                 note: str | None = None) -> str:
    """Convenience wrapper: analyse the JSON dicts and render the Markdown."""
    games = analyse(summary, proof, small_max=small_max, medium_max=medium_max)
    return render(games, small_max=small_max, medium_max=medium_max,
                  summary_label=summary_label, proof_label=proof_label,
                  title=title, note=note)


def load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--summary", required=True, type=Path,
                    help="game_runner summary.json (path supplied by caller)")
    ap.add_argument("--frame-proof", type=Path,
                    help="tools.frame_proof frame-proof.json; omit to claim no hashes")
    ap.add_argument("--out", type=Path,
                    help="write Markdown here; omit to print to stdout")
    ap.add_argument("--html", type=Path,
                    help="also write the native-macOS-only HTML frame gallery here")
    ap.add_argument("--html-title",
                    default="Size-tier frame gallery — native macOS only — no Windows oracle column",
                    help="title for the HTML gallery")
    ap.add_argument("--small-max", default=DEFAULT_SMALL_MAX,
                    help=f"upper bound of the small tier (default: {DEFAULT_SMALL_MAX})")
    ap.add_argument("--medium-max", default=DEFAULT_MEDIUM_MAX,
                    help=f"upper bound of the medium tier (default: {DEFAULT_MEDIUM_MAX})")
    ap.add_argument("--title", default="Size-tier screenshot evidence")
    ap.add_argument("--note", help="optional provenance note appended after the header")
    args = ap.parse_args(argv)

    try:
        small_max = parse_size(args.small_max)
        medium_max = parse_size(args.medium_max)
        if small_max >= medium_max:
            raise ValueError("--small-max must be smaller than --medium-max")
        summary = load_json(args.summary)
        proof = load_json(args.frame_proof) if args.frame_proof else None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"size_tier_report: {exc}", file=sys.stderr)
        return 2

    report = build_report(
        summary, proof, small_max=small_max, medium_max=medium_max,
        summary_label=str(args.summary),
        proof_label=str(args.frame_proof) if args.frame_proof else None,
        title=args.title, note=args.note)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(report)
    if args.html:
        gallery = build_html_report(
            summary, proof, small_max=small_max, medium_max=medium_max,
            summary_label=str(args.summary),
            proof_label=str(args.frame_proof) if args.frame_proof else None,
            title=args.html_title, note=args.note)
        args.html.parent.mkdir(parents=True, exist_ok=True)
        args.html.write_text(gallery, encoding="utf-8")
        print(f"wrote {args.html} (native macOS only — no Windows oracle column)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
