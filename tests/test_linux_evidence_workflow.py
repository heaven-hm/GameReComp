"""Deterministic guards for the *active* Linux-host evidence workflow.

These tests read ``.github/workflows/linux-evidence.yml`` as text. They use only
line-based structural checks (no external YAML library) and never execute the
workflow, the pipeline or a shell command from it. They pin the properties
issue #144 needs from an active job:

* a real, pinned Linux runner (a macOS run is never the Linux result);
* checkout, python deps, the ELF/Mach-O/.NET refusal tests and a native build
  of a small PE32 fixture plus the full pipeline on the Linux host;
* a summary written to the exact ``reports/linux-matrix/linux-summary.json``
  path from measured return codes, with ``NOT TESTED`` for stages that cannot
  run and no fabricated PASS; and
* an ``always()`` artifact upload carrying the summary, logs and native ELF.

One test executes only the summary emitter's embedded Python in a temporary
directory to prove the fail-closed logic; it is a logic check, not Linux-host
evidence. Run with::

    python3 -m pytest tests/test_linux_evidence_workflow.py -q
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "linux-evidence.yml"
REPORT_PATH = "reports/linux-matrix/linux-summary.json"
JOB = "linux-evidence"

REFUSAL_TEST = "tests/test_cli.py::InputFormatRefusal"
FIXTURE_BUILDER = "tests/fixtures/make_pe32_fixture.py"

EMIT_STEP = "Emit Linux summary from measured return codes (fail closed)"
UPLOAD_STEP = "Upload Linux evidence (summary, logs, native binary, host facts)"


def _text():
    assert WORKFLOW.is_file(), f"missing active workflow: {WORKFLOW}"
    return WORKFLOW.read_text(encoding="utf-8")


def _job_block(text=None):
    """Return the two-space-indented body of the single ``linux-evidence`` job."""
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


def _steps(text=None):
    """Parse the ``steps:`` list using indentation only (no YAML library)."""
    steps = []
    current = None
    in_run = False

    def finish(item):
        return {
            "name": item["name"],
            "head": "\n".join(item["head"]),
            "run": "\n".join(item["run"]),
        }

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


def _step(name):
    found = [step for step in _steps() if step["name"] == name]
    assert len(found) == 1, f"expected exactly one step named {name!r}, got {len(found)}"
    return found[0]


def _embedded_script(run):
    assert "<<'PY'" in run, "expected an embedded Python heredoc"
    script = run.split("<<'PY'", 1)[1].split("\nPY\n", 1)[0]
    compile(script, "<linux-evidence>", "exec")  # syntax only, never runs here
    return script


def test_workflow_is_active_and_structurally_parses():
    text = _text()
    assert text.startswith("name: linux-evidence\n")
    assert len(re.findall(rf"^  {JOB}:$", text, flags=re.MULTILINE)) == 1
    job = _job_block(text)
    assert "steps:" in job
    steps = _steps(text)
    assert len(steps) >= 10, f"expected a real multi-stage job, got {len(steps)} steps"
    assert len({step["name"] for step in steps}) == len(steps), "duplicate step names"


def test_job_is_pinned_to_a_real_linux_runner():
    job = _job_block()
    assert re.search(r"^    runs-on: ubuntu-latest$", job, flags=re.MULTILINE), (
        "the evidence job must execute on a real Linux runner")
    for banned in ("macos-latest", "windows-latest", "matrix:", "strategy:",
                   "runs-on: macos", "runs-on: windows"):
        assert banned not in job, f"Linux evidence must not use {banned!r}"
    record = _step("Record Linux host, toolchain and hashes")["run"]
    for token in ("uname -s", '"Linux"', "RUNNER_OS", "host_os", "runner_os"):
        assert token in record, f"host record must verify Linux with {token!r}"
    assert "environment.txt" in record


def test_checkout_deps_refusals_and_native_build_are_real_steps():
    text = _text()
    assert "uses: actions/checkout@v4" in text
    assert "uses: actions/setup-python@v5" in text
    sdl2 = _step("Install SDL2 (Linux native build)")["run"]
    for package in ("libsdl2-dev", "libsdl2-ttf-dev"):
        assert package in sdl2, f"native Linux compile needs {package!r}"
    deps = _step("Install python deps")["run"]
    assert "pip install" in deps
    for dep in ("pytest", "pefile", "capstone", "unicorn"):
        assert dep in deps, f"python deps missing {dep!r}"

    refusals = _step("ELF/Mach-O/.NET refusal tests")["run"]
    assert re.search(rf"pytest\s+{re.escape(REFUSAL_TEST)}", refusals), (
        "the real ELF/Mach-O/.NET refusal tests must run")

    native = _step("Native build of a small PE32 fixture (Linux host)")["run"]
    assert FIXTURE_BUILDER in native
    assert "--out" in native and "--check" in native
    assert "PE\\x00\\x00" in native and "0x014C" in native and "0x010B" in native

    pipeline = _step("Full PE32 pipeline on the Linux host")["run"]
    for token in ("cargo build", "game-recomp-cli", '"$evidence/pe32-minimal.exe"',
                  "--project-dir", "--input-os windows", "--input-arch x86",
                  "--target-os host", "--target-arch host"):
        assert token in pipeline, f"Linux pipeline step missing {token!r}"

    binary = _step("Verify native Linux binary, record hash and show stage logs")["run"]
    for token in ("manifest.json", "input_sha256", "\\x7fELF", "native_sha256", "logs/"):
        assert token in binary, f"native ELF verification missing {token!r}"

    scripts = [s["run"] for s in _steps() if "<<'PY'" in s["run"]]
    assert len(scripts) >= 3, "the host-built fixture, ELF verifier and emitter must be present"
    for run in scripts:
        _embedded_script(run)


def test_summary_is_emitted_at_the_exact_report_path():
    text = _text()
    emitter = _step(EMIT_STEP)["run"]
    assert f"Path('{REPORT_PATH}')" in emitter, "emitter must write the exact report path"
    assert "json.dumps" in emitter and "write_text" in emitter
    upload = _step(UPLOAD_STEP)["head"]
    assert REPORT_PATH in upload, "artifact upload must include the exact report path"
    # The checked-in report path is reports/linux-matrix/linux-summary.json;
    # older docs refer to a different tree that this workflow must not emit.
    assert "reports/linux/linux-summary.json" not in text


def test_rc_values_are_measured_and_not_tested_is_explicit():
    text = _text()
    for name in ("deps", "refusals", "native_build", "pipeline", "native_binary"):
        assert f'{name}.rc' in text, f"stage {name!r} does not record its rc"
    for step in _steps():
        run = step["run"]
        if 'echo "$rc" >' in run:
            assert "rc=$?" in run, f"stage {step['name']!r} rc is not measured"
            assert ".rc" in run
    emitter = _step(EMIT_STEP)["run"]
    assert "int((evidence / (stem + '.rc'))" in emitter
    assert "except (OSError, ValueError):" in emitter
    assert "rc = 'NOT TESTED'" in emitter
    assert "log = 'NOT TESTED'" in emitter
    assert "native = 'NOT TESTED'" in emitter
    for formula in ("'build_rc': by_name['native_build']",
                    "'run_rc': by_name['pipeline']"):
        assert formula in emitter, f"summary field must come from a measured stage: {formula}"
    for fabricated in ("'build_rc': 0", "'run_rc': 0", "build_rc: 0", "run_rc: 0"):
        assert fabricated not in text, f"hard-coded rc {fabricated!r} is not measured"


def test_no_fake_pass_and_fail_closed_status():
    text = _text()
    assert "NOT TESTED" in text
    emitter = _step(EMIT_STEP)["run"]
    assert "ok_codes = {'pipeline': (0, 3)}" in emitter
    assert "measured = all(row['rc'] in ok_codes.get(row['name'], (0,)) for row in rows)" in emitter
    assert "claim_scope" in emitter and "EXIT_UNVERIFIED" in emitter
    assert "'build_rc': by_name['native_build']" in emitter
    for token in ("platform.system() == 'Linux'", "os.environ.get('RUNNER_OS') == 'Linux'",
                  "host.get('host_os') == 'Linux'", "host.get('runner_os') == 'Linux'"):
        assert token in emitter, f"Linux host guard missing {token!r}"
    assert re.search(r"status = 'PASS' if \(measured and linux_host\) else 'NOT TESTED'",
                     emitter), "PASS must be derived from measured rc and a real Linux host"
    assert "raise SystemExit(0 if status == 'PASS' else 1)" in emitter, (
        "a non-PASS summary must fail the job")
    assert re.search(r"^        if: always\(\)$", text, flags=re.MULTILINE), (
        "the emitter must run even when a stage fails")
    for fabricated in (
        "status: PASS", "'status': 'PASS'", '"status": "PASS"',
        "linux_status: PASS", "linux: PASS", "verdict: PASS",
        "echo \"status: PASS\"", "linux_result: PASS",
    ):
        assert fabricated not in text, f"fabricated PASS {fabricated!r} in workflow"


def test_artifact_upload_carries_summary_logs_and_native_binary():
    upload = _step(UPLOAD_STEP)
    assert upload["head"].count("uses: actions/upload-artifact@v4") == 1
    assert "if: always()" in upload["head"]
    assert "if-no-files-found: warn" in upload["head"]
    assert "linux-evidence-summary" in upload["head"]
    for fragment in (REPORT_PATH, "${{ runner.temp }}/linux-evidence/"):
        assert fragment in upload["head"], f"artifact upload missing {fragment!r}"


def test_emitter_is_fail_closed_on_non_linux_and_on_skipped_stages():
    """Logic check only: run the embedded emitter in a temporary directory.

    No workflow step or pipeline is executed. The fake ``platform.system`` and
    ``RUNNER_OS`` values make this assertion identical on every developer host.
    """
    script = _embedded_script(_step(EMIT_STEP)["run"])
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        evidence = root / "linux-evidence"
        evidence.mkdir()
        env = dict(os.environ, RUNNER_OS="Darwin")

        # No stage ran: every rc must be NOT TESTED and the job must fail.
        first = subprocess.run(
            [sys.executable, "-c", script, str(evidence)],
            cwd=root, env=env, capture_output=True, text=True,
        )
        assert first.returncode != 0
        summary = json.loads((root / REPORT_PATH).read_text(encoding="utf-8"))
        assert summary["status"] == "NOT TESTED"
        assert summary["linux_pass_claimed"] is False
        assert summary["build_rc"] == "NOT TESTED"
        assert summary["run_rc"] == "NOT TESTED"
        assert [row["rc"] for row in summary["stages"]] == ["NOT TESTED"] * 5

        # Even with every stage "green", a macOS host must never yield PASS.
        for stem in ("deps", "refusals", "native_build", "pipeline", "native_binary"):
            (evidence / f"{stem}.rc").write_text("0\n", encoding="utf-8")
            (evidence / f"{stem}.log").write_text("logic check only\n", encoding="utf-8")
        macos_env = dict(env, RUNNER_OS="Linux")
        preamble = "import platform\nplatform.system = lambda: 'Darwin'\n"
        second = subprocess.run(
            [sys.executable, "-c", preamble + script, str(evidence)],
            cwd=root, env=macos_env, capture_output=True, text=True,
        )
        assert second.returncode != 0
        summary = json.loads((root / REPORT_PATH).read_text(encoding="utf-8"))
        assert summary["status"] == "NOT TESTED"
        assert summary["linux_pass_claimed"] is False
        assert summary["build_rc"] == 0  # measured, but not a Linux PASS
        assert summary["run_rc"] == 0

        # Logic check only: on a simulated Linux host the pipeline's documented
        # EXIT_UNVERIFIED=3 is recorded verbatim, never rewritten to 0 and never
        # promoted to a gameplay claim.
        (evidence / "environment.txt").write_text(
            "host_os: Linux\nrunner_os: Linux\n", encoding="utf-8")
        (evidence / "pipeline.rc").write_text("3\n", encoding="utf-8")
        linux_preamble = "import platform\nplatform.system = lambda: 'Linux'\n"
        third = subprocess.run(
            [sys.executable, "-c", linux_preamble + script, str(evidence)],
            cwd=root, env=dict(os.environ, RUNNER_OS="Linux"),
            capture_output=True, text=True,
        )
        assert third.returncode == 0
        summary = json.loads((root / REPORT_PATH).read_text(encoding="utf-8"))
        assert summary["status"] == "PASS"
        assert summary["run_rc"] == 3
        assert "not as a gameplay PASS" in summary["claim_scope"]


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
