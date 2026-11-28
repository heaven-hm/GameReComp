"""Deterministic guards for the hybrid job in the canonical CI doc.

These tests only *read* ``docs/ci/github-workflows-ci.yml`` and the source files
the job names. They never run a workflow, a shell command from it, Frida, Wine or
the pipeline, and they never invent a hybrid result. They pin the shape of the
job that runs the hybrid deterministic self-checks (fake adapters) and the
Unicorn paired difftest, and they reject any fabricated PASS for a host-dependent
result such as a live Windows capture.

Run with: python3 -m pytest tests/test_hybrid_ci_config.py -q
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI_DOC = ROOT / "docs" / "ci" / "github-workflows-ci.yml"
HYBRID_JOB = "hybrid-deterministic"

SELF_CHECK_STEP = "Hybrid pipeline deterministic self-checks (fake adapters)"
PAIRED_STEP = "Unicorn paired difftest (CPU-level, no Wine/Frida)"
SCOPE_STEP = "Record NOT TESTED scope (no live Windows capture)"
UPLOAD_STEP = "Upload hybrid deterministic scope record"

FAKE_ADAPTER_TESTS = (
    "tests/test_hybrid_diff.py",
    "tests/test_frida_probe.py",
    "tests/test_proof_checker.py",
)
DIFFTEST_TOOL = "tools/lift/difftest.py"


def _raw():
    assert CI_DOC.is_file(), f"missing canonical CI doc: {CI_DOC}"
    return CI_DOC.read_text(encoding="utf-8")


def _parsed():
    doc = yaml.safe_load(_raw())
    assert isinstance(doc, dict), "CI doc must parse to a mapping"
    return doc


def _jobs():
    jobs = _parsed()["jobs"]
    assert isinstance(jobs, dict)
    return jobs


def _job():
    jobs = _jobs()
    assert HYBRID_JOB in jobs, f"no explicit {HYBRID_JOB!r} job in {CI_DOC}"
    return jobs[HYBRID_JOB]


def _step(job, name):
    found = [s for s in job["steps"] if s.get("name") == name]
    assert len(found) == 1, f"expected exactly one step named {name!r}, got {len(found)}"
    return found[0]


def _run(job, name):
    return _step(job, name)["run"]


def test_ci_doc_parses_and_hybrid_job_is_explicit():
    jobs = _jobs()
    for existing in ("python", "runtime", "end-to-end", "linux-end-to-end", HYBRID_JOB):
        assert existing in jobs, f"missing job {existing!r}"
    job = jobs[HYBRID_JOB]
    assert "hybrid" in job["name"].lower()
    assert "deterministic" in job["name"].lower()
    assert job["needs"] == "python"
    assert len(job["steps"]) >= 7


def test_job_keys_are_not_duplicated():
    keys = re.findall(r"^  ([a-z0-9-]+):$", _raw(), flags=re.MULTILINE)
    assert keys.count(HYBRID_JOB) == 1, "duplicate hybrid job key silently shadowed by YAML"
    assert len(keys) == len(set(keys)), "duplicate top-level job keys"


def test_hybrid_job_runs_on_portable_runners():
    job = _job()
    assert "matrix.os" in job["runs-on"], "hybrid job must use its OS matrix"
    matrix = job["strategy"]["matrix"]
    assert set(matrix["os"]) == {"ubuntu-latest", "macos-latest"}
    assert job["strategy"]["fail-fast"] is False


def test_install_step_has_all_deterministic_test_deps():
    text = _run(_job(), "Install deterministic test deps")
    for dep in ("pytest", "unicorn", "capstone", "pyyaml", "pefile"):
        assert dep in text, f"missing deterministic test dep {dep!r}"
    assert "pip install" in text


def test_fake_adapter_self_check_command_is_exact_and_host_independent():
    text = _run(_job(), SELF_CHECK_STEP).strip()
    expected = (
        "set -eu\n"
        "python3 -m pytest \\\n"
        "  tests/test_hybrid_diff.py \\\n"
        "  tests/test_frida_probe.py \\\n"
        "  tests/test_proof_checker.py \\\n"
        "  -q"
    )
    assert text == expected, f"unexpected fake-adapter self-check command:\n{text}"
    for banned in ("--live-frida", "highball", "wine", "--exe", "RECOMP_P6_EXE",
                   "RECOMP_P6_NATIVE", "GameReComp", "Downloads"):
        assert banned not in text, f"self-check must not use live-engine input {banned!r}"


def test_paired_difftest_command_is_exact_and_cpu_level_only():
    text = _run(_job(), PAIRED_STEP).strip()
    expected = (
        "set -eu\n"
        "python3 tools/lift/difftest.py -k paired --cases 2"
    )
    assert text == expected, f"unexpected Unicorn paired difftest command:\n{text}"
    for banned in ("--live-frida", "wine", "highball", "--exe", "--project",
                   "RECOMP_P6_EXE"):
        assert banned not in text, f"paired difftest must stay CPU-level: {banned!r}"
    assert "-k paired" in text, "the difftest must be filtered to the paired cases"


def test_named_test_files_exist_and_are_fake_engine_checks():
    body = (ROOT / "tests" / "test_hybrid_diff.py").read_text(encoding="utf-8")
    assert "mock.patch" in body, "hybrid gate self-check must use fake engines"
    frida = (ROOT / "tests" / "test_frida_probe.py").read_text(encoding="utf-8")
    assert "--dry-run" in frida, "Frida self-check must not need a live session"
    for path in FAKE_ADAPTER_TESTS:
        assert (ROOT / path).is_file(), f"CI self-check names missing test {path!r}"


def test_paired_difftest_tool_really_has_paired_cases():
    body = (ROOT / DIFFTEST_TOOL).read_text(encoding="utf-8")
    paired = re.findall(r"Case\('[^']*paired[^']*'", body)
    assert len(paired) >= 8, "paired difftest command would select too few real cases"
    assert "def main(" in body, "difftest must expose a CLI entry point"


def test_not_tested_scope_step_is_explicit_for_every_host_gap():
    text = _run(_job(), SCOPE_STEP)
    assert 'echo "job: hybrid-deterministic"' in text
    assert 'echo "runner_os: $RUNNER_OS"' in text
    assert 'echo "git_head: $(git rev-parse HEAD)"' in text
    assert 'echo "claim: deterministic gate/CPU checks only; no live Windows capture claimed"' in text
    for key in (
        "live_windows_capture",
        "live_frida_attach",
        "wine_original_run",
        "linux_native_binary",
        "windows_game_compatibility",
    ):
        assert re.search(rf'echo "{key}: NOT TESTED', text), (
            f"host gap {key!r} must be recorded as NOT TESTED")
    assert "tee" in text, "scope record must be written to a file artifact"


def test_no_pass_is_fabricated_for_host_dependent_results():
    raw = _raw()
    assert "No PASS is fabricated" in raw, "header must forbid fabricated PASS"
    for fabricated in (
        "live_windows_capture: PASS",
        "live_frida_attach: PASS",
        "wine_original_run: PASS",
        "linux_native_binary: PASS",
        "windows_game_compatibility: PASS",
        "hybrid_status: PASS",
        "hybrid_result: PASS",
    ):
        assert fabricated not in raw, f"fabricated PASS result {fabricated!r}"
    # The scope step writes facts and NOT TESTED only; it never echoes a PASS.
    scope = _run(_job(), SCOPE_STEP)
    assert "PASS" not in scope, "scope record must not promote a gate result to PASS"


def test_live_capture_is_never_claimed_anywhere_in_the_hybrid_job():
    job = _job()
    for step in job["steps"]:
        text = step.get("run", "")
        assert "live Windows capture claimed" not in text.replace(
            "no live Windows capture claimed", ""), "must not claim a live capture"
        assert "status: PASS" not in text
        assert "verdict: PASS" not in text
    raw = _raw()
    assert "Neither path attaches Frida, starts Wine or" in raw
    assert "no live Windows capture is claimed" in raw


def test_scope_artifact_is_uploaded():
    job = _job()
    upload = _step(job, UPLOAD_STEP)
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert upload["if"] == "always()"
    assert upload["with"]["name"] == "hybrid-deterministic-scope"
    assert "hybrid-deterministic-scope.txt" in upload["with"]["path"]


def test_hybrid_job_does_not_replace_linux_evidence_job():
    jobs = _jobs()
    linux = jobs["linux-end-to-end"]
    assert "Linux" in linux["name"]
    assert linux["strategy"]["matrix"]["os"] == ["ubuntu-latest"]
    assert jobs["end-to-end"]["runs-on"] == "macos-latest"
    assert HYBRID_JOB not in str(linux), "hybrid job must not be substituted for Linux evidence"
