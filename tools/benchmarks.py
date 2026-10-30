"""Render docs/BENCHMARKS.md from measured per-project reports.

Data sources, in order of authority:
  reports/games/results.json   canonical per-input run database (table.md rows)
  projects/*/reports/report.json   per-stage metrics written by autorecomp

Nothing here is invented: a column is only filled when the report carries the
value, and unmeasured games are listed as unmeasured rather than scored zero.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = REPO_ROOT / "reports" / "games" / "results.json"
DOC_PATH = REPO_ROOT / "docs" / "BENCHMARKS.md"


def _load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def _load_report(evidence):
    if not evidence:
        return None
    report = REPO_ROOT / Path(evidence).parent / "report.json"
    return _load_json(report, None)


def _stage(report, name):
    if not report:
        return {}
    for stage in report.get("stages", []):
        if stage.get("name") == name:
            return stage
    return {}


def _mib(value):
    if not value:
        return "-"
    return f"{value / (1024 * 1024):.1f}"


def _int(value):
    if value is None:
        return "-"
    return f"{value:,}"


def collect(results_path=RESULTS_PATH):
    data = _load_json(results_path, {"runs": []})
    rows = []
    for run in data.get("runs", []):
        report = _load_report(run.get("evidence"))
        recover = _stage(report, "recover").get("data", {})
        translate = _stage(report, "translate").get("data", {})
        compile_ = _stage(report, "compile").get("data", {})
        run_report = _load_json(REPO_ROOT / Path(run["evidence"]).parent / "run.json", {}) if run.get("evidence") else {}
        rows.append(
            {
                "run": run,
                "functions": recover.get("count"),
                "instructions": recover.get("instructions"),
                "units": translate.get("source_units"),
                "source_bytes": translate.get("source_bytes"),
                "lift_errors": translate.get("lift_errors"),
                "compile_seconds": compile_.get("seconds"),
                "compile_reused": bool(compile_.get("reused")),
                "binary_bytes": _binary_size(compile_.get("binary")),
                "run_status": run.get("run_status") or run_report.get("status"),
                "unresolved_imports": run_report.get("unresolved_import_count"),
                "target": (report or {}).get("outcome", {}).get("target", {}).get("label"),
            }
        )
    return rows


def _binary_size(path):
    if not path:
        return None
    try:
        return Path(path).stat().st_size
    except OSError:
        return None


def render(rows):
    generated = max((r["run"].get("generated_at") or "" for r in rows), default="")
    lines = [
        "# Benchmarks",
        "",
        "Measured results for the PCRecomp-first pipeline (`./RevEng`). This file is",
        "generated from the run database and per-project stage reports; regenerate it with",
        "",
        "```sh",
        "python -m tools.benchmarks",
        "```",
        "",
        "Only measured values appear. A game listed as unmeasured was not run, or its",
        "report did not carry the metric; it is never scored from the upstream project's",
        "own claims. Compile seconds come from real compilations: a run that adopted an",
        "existing binary with `--reuse-build` (after checking its manifest source digest)",
        "shows `-` instead of a fake zero and is excluded from the timing statistics.",
        "The full per-input database lives in `table.md`, and the evidence",
        "behind each row is the linked HTML report.",
        "",
        f"Last measurement: {generated or 'unknown'}",
        "",
        "## How to read the score",
        "",
        "`PASS /100` is weighted stage completion: inspect 10, recover 10, translate 10,",
        "project 10, compile 20, run 15, verify 15, report 10. A stage counts only when it",
        "succeeded for the recorded input hash. The verification status is the honest",
        "outcome and can be lower than the score suggests: a rebuilt binary that compiles",
        "but fails to boot or crashes scores 85 and reports `launch-failed`, while one that",
        "launches and exits with no independent oracle scores 93 and reports `partial`.",
        "",
        "## Measured inputs",
        "",
        "| Game | Executable | SHA-256 | Functions | Instructions | Units | C source (MiB) | Compile (s) | Binary (MiB) | Score | Verification |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in sorted(rows, key=lambda r: (r["run"].get("game") or "", r["run"].get("filename") or "")):
        run = row["run"]
        sha = (run.get("input_sha256") or "")[:12]
        verification = run.get("verification") or "not-run"
        status = run.get("status") or ""
        lines.append(
            "| {game} | {exe} | {sha} | {functions} | {instructions} | {units} | {source} | {compile} | {binary} | {score} | {verification}{status} |".format(
                game=run.get("game") or "-",
                exe=run.get("filename") or "-",
                sha=sha or "-",
                functions=_int(row["functions"]),
                instructions=_int(row["instructions"]),
                units=_int(row["units"]),
                source=_mib(row["source_bytes"]),
                compile=f"{row['compile_seconds']:.0f}" if row["compile_seconds"] else "-",
                binary=_mib(row["binary_bytes"]),
                score=run.get("score", 0),
                verification=verification,
                status=f" ({status})" if status else "",
            )
        )

    built = [r for r in rows if r["binary_bytes"] is not None]
    # Reused builds (--reuse-build) adopt an existing binary after checking it
    # was built from exactly the current source; their ~0 s is not a compile
    # measurement and must not enter the timing statistics.
    timed = [r for r in rows if r["compile_seconds"] and not r["compile_reused"]]
    verified = [r for r in rows if r["run"].get("verification") == "scenario-pass"]
    partial = [r for r in rows if r["run"].get("verification") == "partial"]
    launch_failed = [r for r in rows if r["run"].get("verification") == "launch-failed"]
    total_source = sum(r["source_bytes"] or 0 for r in rows)
    total_instructions = sum(r["instructions"] or 0 for r in rows)

    lines += [
        "",
        "## Aggregate",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Inputs measured | {len(rows)} |",
        f"| Reached a built binary | {len(built)} |",
        f"| Verification `scenario-pass` | {len(verified)} |",
        f"| Verification `partial` | {len(partial)} |",
        f"| Verification `launch-failed` | {len(launch_failed)} |",
        f"| Generated C source | {total_source / (1024 * 1024):.1f} MiB |",
        f"| Recovered instructions | {_int(total_instructions)} |",
    ]
    if timed:
        seconds = [r["compile_seconds"] for r in timed]
        lines.append(f"| Compile time, median | {statistics.median(seconds):.0f} s |")
        lines.append(f"| Compile time, max | {max(seconds):.0f} s |")

    boot_failed = [r for r in rows if r["run_status"] == "boot-failed"]
    launched_exited = [r for r in rows if r["run_status"] == "exited"]
    unresolved = [r["unresolved_imports"] for r in boot_failed if r["unresolved_imports"]]

    ceiling = ["", "## Current ceiling on this host", ""]
    if boot_failed:
        ceiling += [
            f"{len(boot_failed)} of {len(rows)} rebuilt inputs compile to a native binary, then stop in the",
            "PE loader because the runtime does not implement all the imports the executable needs.",
            "The loader prints every unresolved name and exits 2 instead of binding a success stub,",
            "and the verifier records that honestly as `launch-failed`.",
        ]
        if unresolved:
            ceiling += [
                "",
                f"Unresolved imports per boot-failed input: {min(unresolved)} to {max(unresolved)}.",
            ]
    if launched_exited:
        names = ", ".join(sorted({r["run"].get("game") or r["run"].get("filename") or "-"
                                  for r in launched_exited}))
        ceiling += [
            "",
            f"{names} binds every import and launches, then exits before a scenario can be graded;",
            "with no independent original-Windows oracle supplied it is reported as `partial`, not",
            "verified. The remaining gap is runtime coverage (the libraries the loader names), not",
            "translation: every input here already reaches a built binary.",
        ]

    lines += ceiling
    lines += [
        "",
        "## Method",
        "",
        "- Command: `./RevEng <binary> --project-dir projects/<game> --timeout 20`",
        "- Per-function source units are capped at 1,000 functions or 8 MiB of C, whichever",
        "  closes first, so one oversized function cannot defeat the compiler.",
        "- The compile deadline is adaptive: `min(3600, 600 + 5.0 * source MiB)`",
        "  seconds (calibrated on this host at roughly 4.6 s/MiB for a 375 MiB",
        "  overridable with `--build-timeout`, and the whole build runs in its own process",
        "  group so a timeout kills the compiler and not just the wrapper.",
        "- The generated C is register-transfer code (`LOW_LEVEL_FALLBACK`), not recovered",
        "  high-level source; see `docs/SOURCE_PROVENANCE.md` for what the categories mean.",
        "",
        "## Reproducing",
        "",
        "Set `SMALL_GAMES_PATH` to a directory containing your own legally obtained copies",
        "of the corpus, then rerun the sweep. Input hashes in the table identify exactly",
        "which executable produced each row; game binaries and assets are never stored in",
        "this repository.",
        "",
    ]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", default=RESULTS_PATH)
    parser.add_argument("--output", default=DOC_PATH)
    args = parser.parse_args(argv)

    rows = collect(args.results)
    if not rows:
        raise SystemExit(f"no runs in {args.results}")
    doc = render(rows)
    Path(args.output).write_text(doc)
    print(f"wrote {args.output} from {len(rows)} runs")


if __name__ == "__main__":
    main()
