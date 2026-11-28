"""Tests for the same-input runtime verification engine.

Reconstruction is done once by the pipeline; this engine only runs the two
built binaries with identical inputs and compares their observable output.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.autorecomp import verify  # noqa: E402


def test_binary_discovery_from_manifest_uses_its_recorded_path(tmp_path):
    (tmp_path / "reports").mkdir()
    exe = tmp_path / "build" / "WhateverName"
    exe.parent.mkdir()
    magic = verify.host_binary_magic()
    exe.write_bytes(magic[0] if magic else b"\x7fELF")
    exe.chmod(0o755)
    (tmp_path / "manifest.json").write_text(json.dumps({
        "binary": {"path": str(exe), "sha256": "abcd"}
    }))
    found = verify.rebuilt_binary(tmp_path, {})
    assert found is not None
    assert found.name == "WhateverName"


def test_binary_discovery_does_not_accept_a_directory(tmp_path):
    (tmp_path / "build").mkdir()
    assert verify.rebuilt_binary(tmp_path, {}) is None


def test_source_parity_is_read_not_recomputed(tmp_path):
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "verify.json").write_text(json.dumps({"source_percent": 75.0}))
    src = verify.recorded_source_verification(tmp_path)
    assert src["percent"] == 75.0
    assert "no re-lifting" in src["engine"]


def test_source_parity_missing_is_blocked(tmp_path):
    src = verify.recorded_source_verification(tmp_path)
    assert src["blocked"] is True
    assert src["percent"] == 0.0


def test_normalization_hides_user_paths_and_aslr_pointers():
    line = f"orig {Path.home()}/Downloads/IGI/IGI.EXE 0x104bc9120"
    out = verify.normalize(line)
    assert str(Path.home()) not in out
    assert "PTR" in out


def test_runtime_parity_compares_both_outputs(tmp_path, monkeypatch):
    pe = tmp_path / "original.exe"
    rebuilt = tmp_path / "rebuilt"
    pe.write_bytes(b"MZ")
    rebuilt.write_bytes(b"\x7fELF")
    monkeypatch.setattr(verify, "windows_runner", lambda: ["/usr/bin/wine"])
    monkeypatch.setattr(verify, "wine_prefix", lambda: None)

    def fake_capture(argv, timeout, outdir, env_extra=None):
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "run.log").write_text("ok")
        if argv[0] == "/usr/bin/wine":
            return 0, "boot ok\n", ""
        return 0, "boot ok\n", ""

    monkeypatch.setattr(verify, "run_process_capture", fake_capture)
    result = verify.runtime_verification(pe, rebuilt, [], 5, tmp_path / "runtime")
    assert result["blocked"] is False
    assert result["verified"] is True
    assert result["percent"] == 100.0
    assert result["checked"] == 3  # exit code + stdout + stderr


def test_runtime_parity_detects_divergence(tmp_path, monkeypatch):
    pe = tmp_path / "original.exe"
    rebuilt = tmp_path / "rebuilt"
    pe.write_bytes(b"MZ")
    rebuilt.write_bytes(b"\x7fELF")
    monkeypatch.setattr(verify, "windows_runner", lambda: ["/usr/bin/wine"])
    monkeypatch.setattr(verify, "wine_prefix", lambda: None)

    def fake_capture(argv, timeout, outdir, env_extra=None):
        outdir.mkdir(parents=True, exist_ok=True)
        if argv[0] == "/usr/bin/wine":
            return 0, "original\n", ""
        return 1, "rebuilt\n", ""

    monkeypatch.setattr(verify, "run_process_capture", fake_capture)
    result = verify.runtime_verification(pe, rebuilt, [], 5, tmp_path / "runtime")
    assert result["verified"] is False
    assert "stdout" in result["reason"] and "exit_code" in result["reason"]
