"""Deterministic guards for the Linux-host job in the canonical CI doc.

These tests only *read* ``docs/ci/github-workflows-ci.yml``. They never run a
workflow, a shell command from it, or the pipeline, and they never invent a
Linux result. They pin the shape of the job that issue #144 asks for:

* an explicit Linux (ubuntu) runner, not a macOS run relabelled as Linux;
* recorded host arch, toolchain versions and SHA-256 hashes;
* the full PE32 fixture pipeline producing a native Linux (ELF) binary plus
  stage logs and reports;
* the ELF/Mach-O/.NET refusal tests; and
* an explicit NOT TESTED policy for hosts with no Linux runner.

Run with: python3 -m pytest tests/test_linux_ci_config.py -q
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI_DOC = ROOT / "docs" / "ci" / "github-workflows-ci.yml"
CLI_TESTS = ROOT / "tests" / "test_cli.py"
FIXTURE = "tests/fixtures/pe32-minimal.exe"
LINUX_JOB = "linux-end-to-end"


def _raw():
    assert CI_DOC.is_file(), f"missing canonical CI doc: {CI_DOC}"
    return CI_DOC.read_text(encoding="utf-8")


def _parsed():
    return yaml.safe_load(_raw())


def _job():
    jobs = _parsed()["jobs"]
    assert LINUX_JOB in jobs, f"no explicit {LINUX_JOB!r} job in {CI_DOC}"
    return jobs[LINUX_JOB]


def _step(job, name):
    found = [s for s in job["steps"] if s.get("name") == name]
    assert len(found) == 1, f"expected exactly one step named {name!r}, got {len(found)}"
    return found[0]


def _run(job, name):
    return _step(job, name)["run"]


def test_ci_doc_parses_and_linux_job_is_explicit():
    jobs = _parsed()["jobs"]
    for existing in ("python", "runtime", "end-to-end", LINUX_JOB):
        assert existing in jobs, f"missing job {existing!r}"
    job = jobs[LINUX_JOB]
    assert "Linux" in job["name"]
    assert job["needs"] == "python"
    assert "$RUNNER_TEMP" in _run(job, "Full pipeline on the PE32 fixture (Linux host)")
    assert len(job["steps"]) >= 8


def test_linux_job_matrix_is_linux_only():
    job = _job()
    assert "matrix.os" in job["runs-on"], "Linux job must run on its Linux matrix"
    matrix = job["strategy"]["matrix"]
    assert matrix["os"] == ["ubuntu-latest"], "Linux job must be pinned to a Linux runner"
    assert all("ubuntu" in os for os in matrix["os"])
    assert job["strategy"]["fail-fast"] is False
    # A macOS run is never the Linux result.
    assert _parsed()["jobs"]["end-to-end"]["runs-on"] == "macos-latest"


def test_job_keys_are_not_duplicated():
    keys = re.findall(r"^  ([a-z0-9-]+):$", _raw(), flags=re.MULTILINE)
    assert keys.count(LINUX_JOB) == 1, "duplicate linux job key silently shadowed by YAML"
    assert len(keys) == len(set(keys)), "duplicate top-level job keys"


def test_records_host_arch_toolchain_and_hashes():
    text = _run(_job(), "Record Linux host, toolchain and hashes")
    for token in (
        "uname -m",          # host arch
        "uname -a",          # full host identity
        "cc --version",      # C toolchain
        "cmake --version",   # build toolchain
        "rustc --version",   # CLI build toolchain
        "python3 --version",
        "git rev-parse HEAD",
        "sha256sum",
        FIXTURE,
        "linux-host/environment.txt",
        "tee",
    ):
        assert token in text, f"host/toolchain/hash record missing {token!r}"


def test_full_pipeline_step_targets_the_host_not_a_foreign_os():
    text = _run(_job(), "Full pipeline on the PE32 fixture (Linux host)")
    for token in (
        "./GameReComp",
        FIXTURE,
        "--project-dir",
        "--timeout",
        "--no-table",
        "--input-os windows",
        "--input-arch x86",
        "--target-os host",
        "--target-arch host",
    ):
        assert token in text, f"pipeline step missing {token!r}"
    for foreign in ("--target-os macos", "--target-os windows", "--target-arch arm64"):
        assert foreign not in text, f"foreign target {foreign!r} would not be a native Linux build"


def test_native_linux_binary_is_verified_and_its_hash_recorded():
    text = _run(_job(), "Verify native Linux binary, record its hash and show stage logs")
    for token in (
        "manifest.json",
        "input_sha256",
        "\\x7fELF",   # ELF magic in the embedded Python verifier
        "native_format: ELF",
        "native_sha256",
        "logs/",      # stage logs
        "cat",
    ):
        assert token in text, f"native-binary verification missing {token!r}"
    # The embedded verifier must at least be syntactically valid Python; this
    # compiles it only, it does not run CI or the pipeline.
    script = text.split("<<'PY'", 1)[1].split("\nPY\n", 1)[0]
    compile(script, "<linux-ci-native-verifier>", "exec")


def test_stage_logs_and_reports_are_uploaded_as_evidence():
    job = _job()
    upload = _step(job, "Upload Linux evidence (stage logs, reports, host/toolchain/hashes)")
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert upload["if"] == "always()"
    assert "linux" in upload["with"]["name"]
    path = upload["with"]["path"]
    for fragment in ("reports/", "logs/", "manifest.json", "linux-host/"):
        assert any(fragment in line for line in path.splitlines()), (
            f"upload evidence is missing {fragment!r}")


def test_elf_macho_dotnet_refusal_tests_are_invoked():
    text = _run(_job(), "ELF/Mach-O/.NET refusal tests")
    assert text.strip() == "python3 -m pytest tests/test_cli.py::InputFormatRefusal -q"
    body = CLI_TESTS.read_text(encoding="utf-8")
    for method in ("test_elf64_is_named", "test_elf32_is_named",
                   "test_macho_thin_and_fat_are_named", "test_dotnet_pe_is_refused"):
        assert f"def {method}(" in body, f"refusal test {method!r} is referenced but absent"


def test_no_linux_result_is_fabricated_without_a_runner():
    raw = _raw()
    assert "NOT TESTED" in raw, "host-without-Linux status must be spelled out"
    assert "never substituted for a Linux result" in raw
    assert "no Linux PASS is recorded here in advance" in raw
    for fabricated in ("linux_status: PASS", "linux: PASS", "linux_result: PASS"):
        assert fabricated not in raw, f"fabricated Linux result {fabricated!r}"
    # The only Linux status the doc writes is a run-time fact, not a claim.
    assert 'echo "linux_status: RUNNING' in raw
    assert "Record Linux host, toolchain and hashes" in raw
