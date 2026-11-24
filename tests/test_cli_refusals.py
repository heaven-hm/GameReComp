"""End-to-end CLI refusals for unsupported input binary formats.

The CLI is fail-closed about the guest binary: it reads the header itself and
names the format it was given instead of guessing or silently producing an
ELF. This file builds tiny synthetic byte templates at runtime (into
``tmp_path``) -- no proprietary game and no checked-in binary is needed --
and drives both the identity helpers and ``cli.main`` over them.

Covered templates: ELF32/ELF64, thin Mach-O 32/64 (both byte orders) and fat
Mach-O, a managed PE carrying CLR directory 14, truncated/invalid MZ, an MZ
without a PE signature, a 64-bit PE, and a valid native PE32 that must pass.

Exit codes come from ``tools.autorecomp.cli``: a refusal is ``EXIT_UNSUPPORTED``
(4), an accepted dry run is 0. No game is run and no live-game result is
claimed here.
"""

import contextlib
import io
import struct
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.autorecomp import cli  # noqa: E402


# --- byte-template helpers -------------------------------------------------

def _elf(byte_class):
    """A minimal ELF identification header (class 1 = ELF32, 2 = ELF64)."""
    return b"\x7fELF" + bytes([byte_class, 1, 1, 0]) + b"\x00" * 56


def _macho(magic):
    """A thin or fat Mach-O magic followed by enough zero bytes to identify."""
    return magic + b"\x00" * 60


def _pe(machine=0x014C, magic=0x010B, clr_rva=0, e_lfanew=0x80, size=0x400):
    """A minimal but structurally complete PE header template.

    ``_pe_identity`` reads at most 4096 header bytes and only looks at the MZ
    magic, ``e_lfanew``, the PE signature, the COFF machine type and (for a
    managed assembly) data directory 14 (COM_DESCRIPTOR). Nothing in this
    template executes; it exists solely to drive those header checks.
    """
    buf = bytearray(size)
    buf[0:2] = b"MZ"
    struct.pack_into("<I", buf, 0x3C, e_lfanew)
    pe = e_lfanew
    buf[pe:pe + 4] = b"PE\x00\x00"
    struct.pack_into("<H", buf, pe + 4, machine)        # COFF machine type
    struct.pack_into("<H", buf, pe + 6, 1)              # NumberOfSections

    pe32 = magic == 0x010B
    opt_size = 0xE0 if pe32 else 0xF0
    struct.pack_into("<H", buf, pe + 20, opt_size)      # SizeOfOptionalHeader
    opt = pe + 24
    struct.pack_into("<H", buf, opt, magic)
    # NumberOfRvaAndSizes lives at +92 in PE32 and +108 in PE32+.
    struct.pack_into("<I", buf, opt + (92 if pe32 else 108), 16)
    # Data directories start at +96 in PE32 and +112 in PE32+; entry 14 is CLR.
    dd = opt + (96 if pe32 else 112)
    if clr_rva:
        struct.pack_into("<II", buf, dd + 14 * 8, clr_rva, 0x48)
    return bytes(buf)


def _mz_without_pe():
    """A well-formed MZ header whose e_lfanew target is not a PE signature."""
    buf = bytearray(_pe())
    pe = struct.unpack_from("<I", buf, 0x3C)[0]
    buf[pe:pe + 4] = b"NE\x00\x00"                      # a legacy NE image
    return bytes(buf)


def _write(tmp_path, name, payload):
    path = tmp_path / (name + ".bin")
    path.write_bytes(payload)
    return path


# --- expected diagnostics from cli._pe_identity / cli._resolve_input --------

IDENTITY_CASES = [
    ("elf32", _elf(1), "ELF 32 executable supplied"),
    ("elf64", _elf(2), "ELF 64 executable supplied"),
    ("macho-thin-32-le", _macho(b"\xce\xfa\xed\xfe"),
     "Mach-O 32-bit little-endian supplied"),
    ("macho-thin-32-be", _macho(b"\xfe\xed\xfa\xce"),
     "Mach-O 32-bit big-endian supplied"),
    ("macho-thin-64-le", _macho(b"\xcf\xfa\xed\xfe"),
     "Mach-O 64-bit little-endian supplied"),
    ("macho-thin-64-be", _macho(b"\xfe\xed\xfa\xcf"),
     "Mach-O 64-bit big-endian supplied"),
    ("macho-fat", _macho(b"\xca\xfe\xba\xbe"),
     "Mach-O fat/universal binary supplied"),
    ("macho-fat-swapped", _macho(b"\xbe\xba\xfe\xca"),
     "Mach-O fat/universal binary (swapped) supplied"),
    ("managed-pe-clr14", _pe(clr_rva=0x2008), ".NET/CLR assembly supplied"),
    ("truncated-mz", b"MZ", "truncated DOS header"),
    ("invalid-mz", b"ZM" + b"\x00" * 64,
     "not an MZ/PE executable (no Windows PE signature)"),
    ("mz-no-pe-signature", _mz_without_pe(), "MZ header but no PE signature"),
]

# A PE32+ image is recognized as PE, but its architecture is refused by
# _resolve_input rather than _pe_identity.
X64_CASE = ("pe32plus-x64", _pe(machine=0x8664, magic=0x020B),
            "unsupported input architecture: x86_64")

ALL_REFUSALS = IDENTITY_CASES + [X64_CASE]
_IDS = [c[0] for c in ALL_REFUSALS]


def _run_cli(path, tmp_path):
    """Drive cli.main through parsing, target check and input resolution.

    The host is pinned so target resolution cannot mask an input refusal on
    an unusual build machine; identity flags are explicit to keep the usual
    "assumed identity" warning out of stderr.
    """
    stdout, stderr = io.StringIO(), io.StringIO()
    with mock.patch.object(cli.platform, "system", return_value="Darwin"), \
            mock.patch.object(cli.platform, "machine", return_value="arm64"), \
            contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main([
            str(path), "--dry-run",
            "--project-dir", str(tmp_path / "project"),
            "--input-os", "auto", "--input-arch", "auto",
            "--target-os", "host", "--target-arch", "host",
        ])
    return code, stdout.getvalue(), stderr.getvalue()


# --- identity-level assertions --------------------------------------------

@pytest.mark.parametrize("name,payload,diagnostic", IDENTITY_CASES,
                         ids=[c[0] for c in IDENTITY_CASES])
def test_pe_identity_names_the_unsupported_format(tmp_path, name, payload,
                                                   diagnostic):
    identity, error = cli._pe_identity(_write(tmp_path, name, payload))
    assert identity is None
    assert error is not None
    assert diagnostic in error


@pytest.mark.parametrize("name,payload,diagnostic", ALL_REFUSALS, ids=_IDS)
def test_resolve_input_refuses_every_foreign_format(tmp_path, name, payload,
                                                     diagnostic):
    identity, error = cli._resolve_input(_write(tmp_path, name, payload),
                                         "auto", "auto")
    assert identity is None
    assert diagnostic in error


# --- end-to-end CLI assertions ---------------------------------------------

@pytest.mark.parametrize("name,payload,diagnostic", ALL_REFUSALS, ids=_IDS)
def test_cli_exits_unsupported_with_named_diagnostic(tmp_path, name, payload,
                                                      diagnostic):
    path = _write(tmp_path, name, payload)
    code, stdout, stderr = _run_cli(path, tmp_path)
    assert code == cli.EXIT_UNSUPPORTED
    assert stderr.startswith("error: ")
    assert diagnostic in stderr
    # A refusal happens before the stage plan is printed or a project is made.
    assert stdout == ""
    assert not (tmp_path / "project").exists()


def test_cli_accepts_native_pe32(tmp_path):
    path = _write(tmp_path, "native-pe32", _pe(machine=0x014C, magic=0x010B))
    identity, error = cli._pe_identity(path)
    assert error is None
    assert identity == ("windows", "x86")

    code, stdout, stderr = _run_cli(path, tmp_path)
    assert code == 0
    assert "error:" not in stderr
    assert "plan (dry run; nothing was built):" in stdout
    assert "inspect" in stdout
    assert "input os:    windows" in stdout
    assert "input arch:  x86" in stdout
    assert not (tmp_path / "project").exists()  # dry run builds nothing
