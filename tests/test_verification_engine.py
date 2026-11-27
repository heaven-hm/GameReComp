"""Tests for the general Unicorn + Frida verification engine.

These check the parts that must work for ANY project: binary discovery without
game-specific names, address discovery from the recovered function inventory,
and honest handling of a blocked run oracle.
"""
import json
import sys
from pathlib import Path

import pytest

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


def test_addresses_come_from_function_inventory_not_hardcoded(tmp_path):
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "functions.json").write_text(json.dumps({
        "functions": [
            {"address": "0x00402000", "name": "f_small", "calls": 1,
             "instructions": 5, "is_thunk": False},
            {"address": "0x00403000", "name": "f_big", "calls": 20,
             "instructions": 500, "is_thunk": False},
            {"address": "0x00404000", "name": "thunk", "calls": 0,
             "instructions": 2, "is_thunk": True},
        ]
    }))
    addrs = verify.function_addresses(tmp_path, [], 1)
    assert addrs == [0x00402000]


def test_explicit_addresses_are_honoured(tmp_path):
    assert verify.function_addresses(tmp_path, ["0x1234", "0x5678"], 1) == [0x1234]


def test_normalization_hides_user_paths_but_keeps_event_names():
    line = f"RECOMP_EVT {{'name': 'open', 'a0': '{Path.home()}/Downloads/IGI/IGI.EXE'}}"
    out = verify.normalize(line)
    assert "RECOMP_EVT" in out and "'name': 'open'" in out
    assert str(Path.home()) not in out


def test_normalization_hides_aslr_pointers():
    line = 'RECOMP_EVT {"name":"open","a0":"0x104bc9120"}'
    out = verify.normalize(line)
    assert "PTR" in out and "0x104bc9120" not in out


def test_attach_failure_is_blocked_not_a_divergence(tmp_path, monkeypatch):
    pe = tmp_path / "original.exe"
    rebuilt = tmp_path / "rebuilt"
    pe.write_bytes(b"MZ")
    rebuilt.write_bytes(b"\x7fELF")
    monkeypatch.setattr(verify.shutil, "which", lambda _: "/usr/bin/wine")
    monkeypatch.setattr(verify, "windows_runner", lambda: "/usr/bin/wine")
    monkeypatch.setattr(verify, "wine_prefix", lambda: None)

    def fake_trace(argv, timeout, outdir, env_extra=None):
        outdir.mkdir(parents=True, exist_ok=True)
        if "original" in str(outdir):
            log = outdir / "run.log"
            log.write_text("Failed to attach: unable to access process with pid 1\n")
            other = log
        else:
            log = outdir / "run.log"
            log.write_text('RECOMP_EVT {"name":"open","a0":"PTR"}\n')
            other = log
        return log, 1

    monkeypatch.setattr(verify, "frida_trace", fake_trace)
    result = verify.run_verification(pe, rebuilt, [], 5, tmp_path / "frida")
    assert result["blocked"] is True
    assert result["checked"] == 0
    assert "attach denied" in result["reason"]
