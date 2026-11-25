"""Deterministic guards for the active real-rev.ng GitHub Actions job.

These tests read ``.github/workflows/revng-evidence.yml`` as text with line-based
structural checks only (no YAML library) and never execute the workflow, rev.ng,
a download or a shell command from it. They pin the properties issue #25 needs
from an active Linux job:

* a real x86-64 Linux runner with both ``workflow_dispatch`` and ``push``;
* the exact pinned native archive URL, byte count and SHA-256;
* download, exact-size/hash verification, extraction, ``revng --version``, real
  difftest and multilifter steps, and the fail-closed source-quality tool;
* a summary derived from measured stage return codes and real reports, with an
  ``if: always()`` artifact upload carrying hashes, commands and logs; and
* no fabricated PASS, no issue state change and no emulated substitute.

One test runs only the workflow's embedded summary emitter in a temporary
directory. It is a logic check, not GitHub or rev.ng evidence. Run with::

    python3 -m pytest tests/test_revng_ci_workflow.py -q
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "revng-evidence.yml"
JOB = "revng-evidence"
REPORT_PATH = "reports/revng/revng-ci-summary.json"
QUALITY_PATH = "proof/revng/SOURCE_QUALITY.json"

ARCHIVE_URL = (
    "https://revng-website-downloads.s3.fr-par.scw.cloud/binary-archives/"
    "public/linux-x86-64/revng-distributable-public-demo/default/"
    "none_7361e00740573e8990d13651cedd5314e89e5adc.tar.xz"
)
ARCHIVE_BYTES = "564214476"
ARCHIVE_SHA256 = "f19060db47165388df24343c6c7abc824742ac9063beeb614d474f3c6b764238"
PIN_COMMIT = "7361e00740573e8990d13651cedd5314e89e5adc"

DOWNLOAD_STEP = "Download pinned native rev.ng archive"
VERIFY_STEP = "Verify exact byte count and SHA-256 pin (fail closed)"
EMIT_STEP = "Emit final evidence summary from measured files (fail closed)"
UPLOAD_STEP = "Upload revng evidence (hashes, commands, reports, logs)"
STAGES = (
    "download", "verify", "extract", "fixture", "revng-decompile",
    "compile", "difftest", "multilifter", "source-quality",
)


def _text() -> str:
    assert WORKFLOW.is_file(), f"missing active workflow: {WORKFLOW}"
    return WORKFLOW.read_text(encoding="utf-8")


def _job_block(text: str | None = None) -> str:
    lines = (text or _text()).splitlines()
    start = None
    for index, line in enumerate(lines):
        if line == f"  {JOB}:":
            assert start is None, f"duplicate job key {JOB!r}"
            start = index
    assert start is not None, f"no active {JOB!r} job key"
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line and not line.startswith(" "):
            end = index
            break
        if line.startswith("  ") and not line.startswith("   ") and line.rstrip().endswith(":"):
            end = index
            break
    return "\n".join(lines[start:end])


def _steps(text: str | None = None) -> list[dict[str, str]]:
    """Parse the ``steps:`` list using indentation only (no YAML library)."""
    steps: list[dict[str, str]] = []
    current: dict[str, list[str]] | None = None
    in_run = False

    def finish(item: dict[str, list[str]]) -> dict[str, str]:
        return {"name": item["name"], "head": "\n".join(item["head"]),
                "run": "\n".join(item["run"])}

    for line in (text or _text()).splitlines():
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 6 and stripped.startswith("- "):
            if current is not None:
                steps.append(finish(current))
            current = {"name": "", "head": [], "run": []}
            in_run = False
            rest = stripped[2:]
            if rest.startswith("name:"):
                current["name"] = rest.split(":", 1)[1].strip().strip("\"'")
            else:
                current["head"].append(rest)
        elif current is not None and indent >= 8:
            if stripped.startswith("run: |") or stripped.startswith("run: >"):
                in_run = True
            elif in_run:
                current["run"].append(line[10:] if len(line) >= 10 else "")
            else:
                current["head"].append(stripped)
    if current is not None:
        steps.append(finish(current))
    return steps


def _step(name: str) -> dict[str, str]:
    found = [step for step in _steps() if step["name"] == name]
    assert len(found) == 1, f"expected exactly one step named {name!r}, got {len(found)}"
    return found[0]


def _embedded_scripts(run: str) -> list[str]:
    parts = run.split("<<'PY'")[1:]
    scripts = [part.split("\nPY\n", 1)[0] for part in parts]
    assert scripts, "expected an embedded Python heredoc"
    for script in scripts:
        compile(script, "<revng-evidence>", "exec")  # syntax only, never executed here
    return scripts


def _embedded_script(run: str) -> str:
    scripts = _embedded_scripts(run)
    return scripts[0]


def test_workflow_is_active_and_structurally_parses():
    text = _text()
    assert text.startswith("name: revng-evidence\n")
    assert len(re.findall(rf"^  {JOB}:$", text, flags=re.MULTILINE)) == 1
    job = _job_block(text)
    assert "steps:" in job
    steps = _steps(text)
    assert len(steps) >= 12, f"expected a real multi-stage job, got {len(steps)} steps"
    assert len({step["name"] for step in steps}) == len(steps), "duplicate step names"


def test_job_is_pinned_to_a_real_linux_x86_64_runner():
    job = _job_block()
    assert re.search(r"^    runs-on: ubuntu-latest$", job, flags=re.MULTILINE), (
        "the evidence job must execute on a real Linux x86-64 runner")
    assert "macos-latest" not in job and "windows-latest" not in job
    assert "strategy:" not in job and "matrix:" not in job
    record = _step("Record Linux runner, pins and commands")["run"]
    for token in ('uname -s', 'RUNNER_OS', 'host_os', 'runner_os', 'uname -m'):
        assert token in record, f"host record must verify Linux with {token!r}"
    assert '"Linux"' in record and "no macOS/emulated substitution" in record
    assert "environment.txt" in record and "commands.txt" in record


def test_both_workflow_dispatch_and_push_trigger_the_job():
    text = _text()
    assert re.search(r"^  workflow_dispatch:\s*$", text, flags=re.MULTILINE), (
        "a manual workflow_dispatch trigger is required")
    push = re.search(r"^  push:\n((?:    .*\n)+)", text, flags=re.MULTILINE)
    assert push, "a push trigger is required, not only a manual dispatch"
    assert "branches: [main, develop]" in push.group(1)


def test_exact_archive_url_byte_count_hash_and_commit_pin():
    text = _text()
    assert ARCHIVE_URL in text
    assert PIN_COMMIT in text
    assert ARCHIVE_BYTES in text
    assert ARCHIVE_SHA256 in text
    verify = _step(VERIFY_STEP)["run"]
    assert "checksums.sha256" in verify
    assert f"{ARCHIVE_SHA256}  revng-distributable.tar.xz" in verify
    assert "sha256sum -c checksums.sha256" in verify
    assert f'"564214476"' in verify and "stat -c %s" in verify
    assert "rc=$?" in verify and "verify.rc" in verify


def test_download_extract_and_version_are_real_measured_steps():
    download = _step(DOWNLOAD_STEP)["run"]
    assert "curl -fL --retry 3" in download
    assert ARCHIVE_URL in download
    assert "download.rc" in download and "rc=$?" in download
    extract = _step("Extract pinned archive and record revng --version")["run"]
    for token in ("tar -xJf", "--strip-components=1", ". ./environment",
                  "revng --version", "revng-version.txt", "extract.rc"):
        assert token in extract, f"extract/version step missing {token!r}"


def test_real_fixture_difftest_multilifter_and_source_quality_steps():
    text = _text()
    fixture = _step("Build the real PE32 fixture and the PCRecomp project")["run"]
    assert "differential_gate" in fixture and "build_gate_pe" in fixture
    assert "cargo build" in fixture and "game-recomp-cli" in fixture
    assert "gate.exe" in fixture and "sub_00401000" in fixture
    decompile = _step("Emit real revng C for the fixture")["run"]
    assert "revng quick artifact emit-c-as-single-file" in decompile
    assert "revng.ptml" in decompile and "function_source" in decompile
    difftest = _step("Drive PCRecomp and revng against the original bytes (difftest)")["run"]
    assert "tools/lift/difftest.py --project" in difftest
    assert "--pe" in difftest and "--address 0x401000" in difftest
    assert "--source" in difftest and "--json" in difftest
    assert "baseline-difftest.json" in difftest and "revng-difftest.json" in difftest
    select = _step("Select with the repo multilifter (fail closed)")["run"]
    assert "tools/lift/multilifter.py" in select
    assert "--lifter \"revng=" in select and "--lifter \"pcrecomp=" in select
    assert "decision.json" in select and "multilifter.rc" in select
    quality = _step("Measure source quality against the PCRecomp baseline")["run"]
    assert "tools/revng_source_quality.py" in quality
    assert "--baseline-c" in quality and "--revng-c" in quality
    assert "--baseline-difftest" in quality and "--revng-difftest" in quality
    assert "--decision" in quality and "--revng-compile-rc" in quality
    assert QUALITY_PATH in quality and "source-quality.rc" in quality
    scripts = [step["run"] for step in _steps() if "<<'PY'" in step["run"]]
    count = sum(len(_embedded_scripts(run)) for run in scripts)
    assert count >= 5, "fixture, hash, revng C, adapter and emitter scripts must be present"


def test_summary_path_and_no_fake_pass():
    text = _text()
    emitter = _step(EMIT_STEP)["run"]
    assert f"Path('{REPORT_PATH}')" in emitter, "emitter must write the exact summary path"
    assert "json.dumps" in emitter and "write_text" in emitter
    assert "'status': status" in emitter
    for token in ("platform.system() == 'Linux'", "os.environ.get('RUNNER_OS') == 'Linux'",
                  "host.get('host_os') == 'Linux'", "host.get('runner_os') == 'Linux'"):
        assert token in emitter, f"Linux host guard missing {token!r}"
    assert "source_ok" in emitter and "selected" in emitter and "measured" in emitter
    assert "raise SystemExit(0 if status == 'PASS' else 1)" in emitter
    for fabricated in (
        "status: PASS", "'status': 'PASS'", '"status": "PASS"',
        "verdict: PASS", "verdict: SOURCE_IMPROVED",
        "'source_quality_verdict': 'SOURCE_IMPROVED'", "echo PASS",
        "issue_close", "gh issue close", "fixes #25", "closes #25",
    ):
        assert fabricated not in text, f"fabricated claim in workflow: {fabricated!r}"
    assert "issue #25 stays open" in text.replace("\n", " ").lower()


def test_all_stages_are_measured_and_artifacts_always_uploaded():
    text = _text()
    for stage in STAGES:
        assert f"{stage}.rc" in text, f"stage {stage!r} does not record its rc"
    emitter = _step(EMIT_STEP)["run"]
    assert "except (OSError, ValueError):" in emitter
    assert "rc = 'NOT TESTED'" in emitter
    assert "log = 'NOT TESTED'" in emitter
    upload = _step(UPLOAD_STEP)
    assert upload["head"].count("uses: actions/upload-artifact@v4") == 1
    assert "if: always()" in upload["head"]
    assert "if-no-files-found: warn" in upload["head"]
    assert "revng-evidence" in upload["head"]
    for fragment in (REPORT_PATH, QUALITY_PATH,
                     "${{ runner.temp }}/revng-evidence/"):
        assert fragment in upload["head"], f"artifact upload missing {fragment!r}"
    assert re.search(r"^        if: always\(\)$", text, flags=re.MULTILINE), (
        "the emitter must run even when a stage fails")


def test_emitter_is_fail_closed_without_real_reports():
    """Logic check only: run the embedded emitter in a temporary directory."""
    script = _embedded_script(_step(EMIT_STEP)["run"])
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        evidence = root / "revng-evidence"
        evidence.mkdir()
        env = dict(os.environ, RUNNER_OS="Darwin")
        preamble = "import platform\nplatform.system = lambda: 'Darwin'\n"

        # No stage ran and no reports exist: every rc is NOT TESTED.
        first = subprocess.run(
            [sys.executable, "-c", script, str(evidence)],
            cwd=root, env=env, capture_output=True, text=True,
        )
        assert first.returncode != 0
        summary = json.loads((root / REPORT_PATH).read_text(encoding="utf-8"))
        assert summary["status"] == "NOT TESTED"
        assert [row["rc"] for row in summary["stages"]] == ["NOT TESTED"] * len(STAGES)
        assert summary["source_quality_verdict"] is None

        # Even with every rc measured green, macOS and a missing source-quality
        # report must never yield PASS.
        for stage in STAGES:
            (evidence / f"{stage}.rc").write_text("0\n", encoding="utf-8")
            (evidence / f"{stage}.log").write_text("logic check only\n", encoding="utf-8")
        second = subprocess.run(
            [sys.executable, "-c", preamble + script, str(evidence)],
            cwd=root, env=dict(os.environ, RUNNER_OS="Linux"),
            capture_output=True, text=True,
        )
        assert second.returncode != 0
        summary = json.loads((root / REPORT_PATH).read_text(encoding="utf-8"))
        assert summary["status"] == "NOT TESTED"
        assert summary["linux_host"] is False
        assert [row["rc"] for row in summary["stages"]] == [0] * len(STAGES)

        # A real report file is still not enough: selection must promote revng.
        (evidence / "environment.txt").write_text(
            "host_os: Linux\nrunner_os: Linux\n", encoding="utf-8")
        Path(root, QUALITY_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(root, QUALITY_PATH).write_text(
            json.dumps({"verdict": "SOURCE_IMPROVED", "sha256": "a" * 64}),
            encoding="utf-8")
        (evidence / "decision.json").write_text(
            json.dumps({"decision": "BLOCKED", "chosen_lifter": None}),
            encoding="utf-8")
        linux_preamble = "import platform\nplatform.system = lambda: 'Linux'\n"
        third = subprocess.run(
            [sys.executable, "-c", linux_preamble + script, str(evidence)],
            cwd=root, env=dict(os.environ, RUNNER_OS="Linux"),
            capture_output=True, text=True,
        )
        assert third.returncode != 0
        summary = json.loads((root / REPORT_PATH).read_text(encoding="utf-8"))
        assert summary["status"] == "NOT TESTED"
        assert summary["linux_host"] is True

        # Only a Linux host, all measured rc=0, a promoted revng decision and a
        # SOURCE_IMPROVED verdict yield PASS in the logic check.
        (evidence / "decision.json").write_text(
            json.dumps({"decision": "PROMOTE", "chosen_lifter": "revng"}),
            encoding="utf-8")
        fourth = subprocess.run(
            [sys.executable, "-c", linux_preamble + script, str(evidence)],
            cwd=root, env=dict(os.environ, RUNNER_OS="Linux"),
            capture_output=True, text=True,
        )
        assert fourth.returncode == 0, fourth.stderr
        summary = json.loads((root / REPORT_PATH).read_text(encoding="utf-8"))
        assert summary["status"] == "PASS"
        assert summary["source_quality_verdict"] == "SOURCE_IMPROVED"
        assert "Issue #25 stays OPEN" in summary["claim_scope"]


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
