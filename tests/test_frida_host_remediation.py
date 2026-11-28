"""Tests for the fail-closed Frida attach remediation CLI.

Every test uses synthetic, CLI-supplied probe records. No test imports frida in
a way that attaches, launches a process, opens a socket, calls gh or changes an
issue. They prove that recorded error signatures map to the right ranked
remedies, that unknown signatures fail closed, that written PASS claims are
ignored, and that no output carries a GitHub auto-closing keyword.

Run with: python3 -m pytest tests/test_frida_host_remediation.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import frida_host_remediation as fhr  # noqa: E402


def _probe(tmp_path: Path, name: str, error_type: str | None,
           error_message: str | None, *, status: str = "FAILED",
           extra: dict | None = None) -> Path:
    attach: dict = {"status": status}
    if error_type is not None:
        attach["error_type"] = error_type
    if error_message is not None:
        attach["error_message"] = error_message
    record = {
        "schema_version": 1,
        "label": name,
        "host": {"os": "macos", "arch": "arm64", "os_version": "27.2.1"},
        "frida": {"version": "17.24.3"},
        "target": {"path": "/bin/sleep", "arch": "arm64e"},
        "attach": attach,
    }
    if extra:
        record.update(extra)
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def _report(path: Path) -> dict:
    return fhr.build_report(fhr.load_records([path]))


def _commands(report: dict) -> list[str]:
    return [command for item in report["checklist"]
            for command in item["commands"]]


def test_permission_denied_current_user_maps_to_task_for_pid_remedies(tmp_path):
    path = _probe(
        tmp_path, "sleep-denied", "PermissionDeniedError",
        "unable to access process with pid 42 from the current user account")
    report = _report(path)

    assert report["matched_rules"] == ["MACOS_TASK_FOR_PID_DENIED"]
    assert report["status"] == fhr.STATUS_CHECKLIST
    assert [item["rank"] for item in report["checklist"]] == [1, 2, 3, 4, 5]
    assert [item["step"] for item in report["checklist"]] == [1, 2, 3, 4, 5]
    commands = _commands(report)
    assert any("csrutil status" in command for command in commands)
    assert any("get-task-allow" in command for command in commands)
    assert any("security authorizationdb write system.privilege.taskport allow"
               in command for command in commands)
    assert any("frida.re/docs/examples/macos" in source
               for source in report["sources"])


def test_helper_crash_maps_to_arm64e_platform_binary_remedies(tmp_path):
    path = _probe(
        tmp_path, "helper-crash", "PermissionDeniedError",
        "oops, frida-helper appears to have crashed. It may have been killed")
    report = _report(path)

    assert report["matched_rules"] == ["MACOS_FRIDA_HELPER_KILLED"]
    commands = _commands(report)
    assert any("codesign -dvvv /bin/sleep" in command for command in commands)
    assert any("mktemp -d" in command for command in commands)
    assert any("codesign -f -s -" in command for command in commands)
    assert any("arm64e_preview_abi" in command for command in commands)
    assert any("frida-helper appears to have crashed" in
               item["expected_failure_signature"] for item in report["checklist"])
    assert any("fkie-cad.github.io/friTap/platforms/macos" in source
               for source in report["sources"])
    # The specialized rule wins over the generic PermissionDeniedError rule.
    assert "MACOS_TASK_FOR_PID_DENIED" not in report["matched_rules"]


def test_dyld_probe_error_maps_to_rosetta_upgrade_linux_and_gadget(tmp_path):
    path = _probe(
        tmp_path, "wine-dyld", "NotSupportedError",
        "unexpected error while probing dyld of target process")
    report = _report(path)

    assert report["matched_rules"] == ["MACOS_DYLD_PROBE_FAILED"]
    commands = _commands(report)
    assert any("lipo -info" in command for command in commands)
    assert any("pip install -U --ignore-installed frida" in command
               for command in commands)
    assert any("sysctl kernel.yama.ptrace_scope=0" in command
               for command in commands)
    assert any("DYLD_INSERT_LIBRARIES" in command for command in commands)
    assert any("unexpected error while probing dyld of target process" in
               item["expected_failure_signature"] for item in report["checklist"])
    assert any("issues/3420" in source for source in report["sources"])
    # Both reported versions already failed, so the upgrade is not a claimed fix.
    assert "17.24.3" in json.dumps(report)
    assert "17.27.5" in json.dumps(report)


def test_linux_ptrace_failure_maps_to_yama_remedies(tmp_path):
    path = _probe(
        tmp_path, "linux-ptrace", "SystemError",
        "attach_to_process PTRACE_ATTACH failed: 1", status="BLOCKED")
    report = _report(path)

    assert report["matched_rules"] == ["LINUX_PTRACE_SCOPE"]
    commands = _commands(report)
    assert any("/proc/sys/kernel/yama/ptrace_scope" in command
               for command in commands)
    assert any("sudo sysctl kernel.yama.ptrace_scope=0" in command
               for command in commands)
    assert any("--spawn" in command for command in commands)
    assert any("Yama.html" in source for source in report["sources"])


def test_missing_client_import_maps_to_install(tmp_path):
    path = _probe(
        tmp_path, "no-frida", "ModuleNotFoundError",
        "No module named 'frida'")
    report = _report(path)

    assert report["matched_rules"] == ["FRIDA_IMPORT_FAILED"]
    assert any("python3 -m pip install -U frida frida-tools" in command
               for command in _commands(report))


def test_unknown_signature_fails_closed(tmp_path, capsys):
    path = _probe(tmp_path, "unknown", "NovelError", "unrecognised host state")
    report = _report(path)

    assert report["matched_rules"] == []
    assert report["status"] == fhr.STATUS_MANUAL
    assert report["checklist"][0]["rule"] == "UNCLASSIFIED_FAILURE"
    assert report["verdict"]["dual_engine"] == "NOT PROVEN"
    assert report["issue"]["state"] == "OPEN"
    assert fhr.main(["--probe", str(path)]) == fhr.EXIT_MANUAL
    assert "No confident remedy" in capsys.readouterr().out


def test_written_pass_claim_is_ignored_and_never_proves_attach(tmp_path):
    path = _probe(
        tmp_path, "claimed-pass", "PermissionDeniedError",
        "unable to access process with pid 42 from the current user account",
        extra={"claimed_verdict": {"status": "PASS",
                                   "dual_engine": "DUAL-ENGINE PASS"},
               "verdict": {"status": "PASS"}})
    report = _report(path)

    assert report["verdict"]["attach_proven"] is False
    assert report["verdict"]["dual_engine"] == "NOT PROVEN"
    assert report["issue"]["state"] == "OPEN"
    assert report["ignored_claims"], "optimistic claims must be recorded as ignored"
    assert {item["claim_kind"] for item in report["ignored_claims"]} == {"PASS-LIKE"}

    text = fhr.render_text(report)
    markdown = fhr.render_markdown(report)
    json_text = json.dumps(report)
    for output in (text, markdown, json_text):
        lowered = output.lower()
        assert "dual-engine pass" not in lowered
        assert "verdict: pass" not in lowered
    assert "#171 stays OPEN" in text
    fhr.assert_no_forbidden_output(text, markdown, json_text)


def test_ok_attach_status_alone_proves_nothing(tmp_path, capsys):
    path = _probe(tmp_path, "claimed-ok", None, None, status="OK")
    report = _report(path)

    assert report["matched_rules"] == []
    assert report["status"] == fhr.STATUS_MANUAL
    assert report["verdict"]["attach_proven"] is False
    assert report["verdict"]["dual_engine"] == "NOT PROVEN"
    assert report["issue"]["state"] == "OPEN"
    assert fhr.main(["--probe", str(path)]) == fhr.EXIT_MANUAL
    capsys.readouterr()


def test_no_issue_closing_keywords_in_any_output_or_docs(tmp_path):
    probes = [
        _probe(tmp_path, "denied", "PermissionDeniedError",
               "unable to access process with pid 42 from the current user account"),
        _probe(tmp_path, "dyld", "NotSupportedError",
               "unexpected error while probing dyld of target process"),
        _probe(tmp_path, "unknown", "NovelError", "unknown state"),
    ]
    for path in probes:
        report = _report(path)
        for output in (fhr.render_text(report), fhr.render_markdown(report),
                       json.dumps(report)):
            assert fhr.CLOSING_KEYWORD.search(output) is None
    docs = (ROOT / "docs" / "FRIDA_HOST_REMEDIATION.md").read_text(encoding="utf-8")
    assert fhr.CLOSING_KEYWORD.search(docs) is None


def test_reports_are_byte_stable(tmp_path):
    path = _probe(
        tmp_path, "stable", "PermissionDeniedError",
        "unable to access process with pid 42 from the current user account")
    first = _report(path)
    second = _report(path)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert fhr.render_text(first) == fhr.render_text(second)
    assert fhr.render_markdown(first) == fhr.render_markdown(second)


def test_malformed_input_fails_closed(tmp_path):
    bad_schema = tmp_path / "bad-schema.json"
    bad_schema.write_text(json.dumps({"schema_version": 99, "attach": {}}),
                          encoding="utf-8")
    try:
        fhr.load_records([bad_schema])
    except ValueError as exc:
        assert "schema_version" in str(exc)
    else:
        raise AssertionError("bad schema must fail closed")

    missing = tmp_path / "missing.json"
    try:
        fhr.load_records([missing])
    except ValueError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("missing probe file must fail closed")

    assert fhr.main(["--probe", str(bad_schema)]) == fhr.EXIT_USAGE
    assert fhr.main([]) == fhr.EXIT_USAGE


def test_cli_writes_reports_and_prints_checklist(tmp_path):
    path = _probe(
        tmp_path, "cli", "NotSupportedError",
        "unexpected error while probing dyld of target process")
    out = tmp_path / "report.md"
    json_out = tmp_path / "report.json"
    result = subprocess.run(
        [sys.executable, "-m", "tools.frida_host_remediation",
         "--probe", str(path), "--out", str(out), "--json", str(json_out)],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    assert "issue #171: OPEN" in result.stdout
    assert "Ranked checklist" in result.stdout
    assert out.is_file() and json_out.is_file()
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["matched_rules"] == ["MACOS_DYLD_PROBE_FAILED"]
    assert report["issue"]["state"] == "OPEN"
    assert report["verdict"]["dual_engine"] == "NOT PROVEN"
    assert fhr.CLOSING_KEYWORD.search(out.read_text(encoding="utf-8")) is None
