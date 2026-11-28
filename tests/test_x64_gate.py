"""Deterministic guards for the Issue #148 x64 lifter feasibility gate.

This module does NOT implement or claim x64 support. It proves three things:

  (a) ``docs/feasibility/X64_LIFTER_GATE.md`` exists with the required sections
      and the exact refusal strings of the fail-closed CLI;
  (b) the CLI refuses a *synthetic* PE32+ header with a named refusal and a
      non-zero exit code, so the boundary is reproducible without a game binary;
  (c) no x64 support claim is reachable without a built native binary: the
      refusal auditor refuses a binary-less x64 project, and the CLI cannot emit
      a native binary from x64 input even when a binary file is present.

Run with: python3 -m pytest tests/test_x64_gate.py -q
"""

import json
import struct
import subprocess
import sys
from pathlib import Path

from tools.autorecomp.refusals import collect_refusals

ROOT = Path(__file__).resolve().parents[1]
GATE_DOC = ROOT / "docs" / "feasibility" / "X64_LIFTER_GATE.md"

# (a) The doc must carry every one of these headings, verbatim.
REQUIRED_SECTIONS = (
    "# X64 lifter feasibility gate (Issue #148)",
    "## 1. Scope and fail-closed refusal boundary",
    "## 2. Inspect local upstream checkouts (exact commands)",
    "## 3. Licence constraints",
    "## 4. Benchmark a representative x64 function through a candidate lifter",
    "## 5. Go/no-go checklist",
    "## 6. No x64 support claim without a built and executed native binary",
    "## 7. Reproduce the refusal (exact commands and expected output)",
    "## 8. Limits of this gate",
)

# Exact refusal strings, pinned to tools/autorecomp/cli.py::_resolve_input.
EXACT_REFUSALS = (
    "unsupported input architecture: x86_64",
    "64-bit guest support is planned, not implemented",
    "--input-arch x86 but the binary's header says x86_64; refusing to reconstruct on a guess",
)

# (c) Exact rule the gate doc must state about native binaries.
CLAIM_RULE = (
    "no x64 support claim may be made without a built and executed native binary"
)

EXIT_UNSUPPORTED = 4  # tools/autorecomp/cli.py::EXIT_UNSUPPORTED


def _x64_probe(path):
    """Write a minimal but structurally valid PE32+ header (Machine 0x8664).

    Only fields read by ``cli._pe_identity`` are needed to reach the refusal:
    MZ, e_lfanew, PE\\0\\0, COFF machine, optional-header size and PE32+ magic.
    Data directories are left zero so the .NET/CLR check does not fire first.
    No game bytes are synthesized -- this is a detection probe, not a game.
    """
    data = bytearray(2048)
    data[0:2] = b"MZ"
    pe = 0x80
    struct.pack_into("<I", data, 0x3C, pe)          # e_lfanew
    data[pe:pe + 4] = b"PE\x00\x00"                  # PE signature
    struct.pack_into("<H", data, pe + 4, 0x8664)     # IMAGE_FILE_MACHINE_AMD64
    struct.pack_into("<H", data, pe + 20, 0x00F0)    # SizeOfOptionalHeader (PE32+)
    struct.pack_into("<H", data, pe + 24, 0x020B)    # PE32+ optional magic
    path = Path(path)
    path.write_bytes(data)
    return path


def _run_cli(probe, extra):
    """Invoke the real CLI as a subprocess and capture (rc, combined output)."""
    cmd = [sys.executable, "-m", "tools.autorecomp", str(probe),
           "--input-os", "windows", "--out-mode", "source-only",
           "--no-table", "--no-progress"] + list(extra)
    proc = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, timeout=60,
                          check=False)
    return proc.returncode, proc.stdout or ""


def _fake_x64_project(base, with_binary):
    """A project that would *look* like an x64 claim but has no execution proof."""
    project = Path(base) / "claimed_x64"
    project.mkdir(parents=True, exist_ok=True)
    binary = project / "build" / "claimed_x64"
    if with_binary:
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b"\x7fELF")  # existence is all the auditor checks
    (project / "manifest.json").write_text(json.dumps({
        "game": "claimed_x64.exe",
        "input_arch": "x86_64",
        "binary": {"path": str(binary)},
    }))
    return project


# --- (a) documentation guardrails ------------------------------------------

def test_gate_doc_exists_with_required_sections():
    assert GATE_DOC.is_file(), f"missing gate document: {GATE_DOC}"
    text = GATE_DOC.read_text(encoding="utf-8")
    for heading in REQUIRED_SECTIONS:
        assert heading in text, f"gate doc missing required section: {heading}"


def test_gate_doc_contains_exact_refusal_strings():
    text = GATE_DOC.read_text(encoding="utf-8")
    for refusal in EXACT_REFUSALS:
        assert refusal in text, f"gate doc missing exact refusal: {refusal}"


def test_gate_doc_contains_no_claim_without_binary_rule():
    text = GATE_DOC.read_text(encoding="utf-8")
    assert CLAIM_RULE in text, "gate doc must forbid x64 claims without a built native binary"


# --- (b) named, non-zero refusal on a synthetic x64 PE header --------------

def test_x64_auto_arch_is_refused_named_and_nonzero(tmp_path):
    probe = _x64_probe(tmp_path / "probe64.exe")
    rc, out = _run_cli(probe, ["--input-arch", "auto",
                               "--target-os", "host", "--target-arch", "host"])
    assert rc == EXIT_UNSUPPORTED, (rc, out)
    assert rc != 0
    assert EXACT_REFUSALS[0] in out, out
    assert EXACT_REFUSALS[1] in out, out


def test_forcing_x86_on_x64_is_refused(tmp_path):
    probe = _x64_probe(tmp_path / "probe64.exe")
    rc, out = _run_cli(probe, ["--input-arch", "x86",
                               "--target-os", "host", "--target-arch", "host"])
    assert rc == EXIT_UNSUPPORTED, (rc, out)
    assert EXACT_REFUSALS[2] in out, out


# --- (c) no x64 claim without a built native binary ------------------------

def test_binaryless_x64_project_is_refused_by_auditor(tmp_path):
    project = _fake_x64_project(tmp_path, with_binary=False)
    report = collect_refusals(project)
    assert report["fail_closed"] is True
    assert report["refused"] is True
    assert report["verdict"] == "REFUSED"
    assert report["counts"]["MISSING_NATIVE_BINARY"] >= 1


def test_x64_input_never_yields_a_native_binary(tmp_path):
    """A binary on disk is necessary but not sufficient: the CLI still refuses.

    The auditor clears MISSING_NATIVE_BINARY once a file exists, but the CLI
    refuses the x64 input before any build, so this pipeline cannot turn an x64
    header into a native binary. Together the two facts leave no reachable
    'x64 supported / accepted' state for this repository.
    """
    project = _fake_x64_project(tmp_path, with_binary=True)
    with_binary = collect_refusals(project)
    assert with_binary["counts"]["MISSING_NATIVE_BINARY"] == 0

    probe = _x64_probe(tmp_path / "probe64.exe")
    rc, out = _run_cli(probe, ["--input-arch", "auto",
                               "--target-os", "host", "--target-arch",
                               "host", "--out-mode", "binary",
                               "--project-dir", str(tmp_path / "built")])
    assert rc == EXIT_UNSUPPORTED, (rc, out)
    assert EXACT_REFUSALS[0] in out, out
    # No generated native artifact can be claimed from that refusal.
    built = tmp_path / "built"
    assert not built.is_dir() or not any(built.rglob("claimed_x64*"))
