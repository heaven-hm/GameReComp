"""Deterministic tests for the InstallShield extractor path confinement (#344).

``tools/assets/isextract.py`` used to build a member's destination straight
from the untrusted ``.hdr`` directory/name strings::

    rel_path = os.path.join(fd.directory.replace('\\\\', os.sep), fd.name)
    out_path = os.path.join(output_dir, rel_path)

That is a classic ZipSlip: ``..`` components walk out of ``output_dir``, an
absolute member name makes ``os.path.join`` discard ``output_dir`` entirely,
and a Windows drive prefix or a symlink inside the tree can redirect a write
anywhere the user can write.

These tests load the real module and assert that:

  * ordinary members still resolve strictly under the extraction root;
  * empty names, NUL bytes, drive prefixes, absolute paths and ``..``
    components are refused with an exact diagnostic;
  * symlink escapes are refused after canonicalization;
  * both ``extract_all`` and ``extract_cab_only`` refuse a malicious member
    before creating a directory or writing a file outside the root.

No game asset, network or live injection is involved.

Run with: python3 -m pytest tests/test_isextract_safety.py -q
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "tools" / "assets" / "isextract.py"

_spec = importlib.util.spec_from_file_location("isextract_safety", SOURCE)
isextract = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = isextract
_spec.loader.exec_module(isextract)


class _FakeFD:
    """Minimal file descriptor for exercising the extraction methods."""

    def __init__(self, name, directory="", compressed=True, invalid=False):
        self.name = name
        self.directory = directory
        self.is_compressed = compressed
        self.is_invalid = invalid
        self.index = 0
        self.expanded_size = 7
        self.compressed_size = 7
        self.data_offset = 0
        self.md5 = b"\x00" * 16
        self.is_obfuscated = False
        self.is_split = False


def _cabinet(fd, cab_file):
    """An ISCabinet shell with one member and a real (empty) volume handle."""
    cabinet = isextract.ISCabinet.__new__(isextract.ISCabinet)
    cabinet.files = [fd]
    cabinet.volume_for = lambda index: str(cab_file)
    return cabinet


def _stub_extract(self, cab_file, fd, out_path):
    """Stand-in for decompression: just prove a write reached out_path."""
    with open(out_path, "wb") as out:
        out.write(b"payload")


def _dummy_cab(root):
    """Create a real, empty volume handle for the extraction methods."""
    root.mkdir(parents=True, exist_ok=True)
    cab_file = root / "dummy.cab"
    cab_file.write_bytes(b"")
    return cab_file


@pytest.fixture
def patched_extract(monkeypatch):
    monkeypatch.setattr(isextract.ISCabinet, "_extract_compressed", _stub_extract)


# --- helper: legitimate paths ------------------------------------------------

def test_safe_output_path_accepts_normal_member(tmp_path):
    root = tmp_path / "extract"
    out = isextract._safe_output_path(str(root), "dir", "sub\\file.txt")
    assert out == os.path.realpath(str(root / "dir" / "sub" / "file.txt"))


def test_safe_output_path_accepts_root_level_member(tmp_path):
    root = tmp_path / "extract"
    out = isextract._safe_output_path(str(root), "", "file.txt")
    assert out == os.path.realpath(str(root / "file.txt"))


# --- helper: exact diagnostics for unsafe paths -----------------------------

@pytest.mark.parametrize("directory,name,message", [
    ("", "", "unsafe archive member: empty file name"),
    ("", "../../evil.txt",
     "unsafe archive member: parent directory component ('..') in path: "
     "'../../evil.txt'"),
    ("..\\..", "evil.txt",
     "unsafe archive member: parent directory component ('..') in path: "
     "'../../evil.txt'"),
    ("dir", "sub/../evil.txt",
     "unsafe archive member: parent directory component ('..') in path: "
     "'dir/sub/../evil.txt'"),
    ("", "/tmp/evil.txt",
     "unsafe archive member: absolute path: '/tmp/evil.txt'"),
    ("\\WINDOWS", "win.ini",
     "unsafe archive member: absolute path: '\\\\WINDOWS'"),
    ("C:\\", "evil.txt",
     "unsafe archive member: drive letter in path: 'C:'"),
    ("", "evil\x00.txt",
     "unsafe archive member: NUL byte in name"),
    ("sneaky\x00dir", "evil.txt",
     "unsafe archive member: NUL byte in directory"),
])
def test_safe_output_path_rejects_with_exact_diagnostic(tmp_path, directory,
                                                        name, message):
    with pytest.raises(ValueError) as exc:
        isextract._safe_output_path(str(tmp_path / "extract"), directory, name)
    assert str(exc.value) == message


def test_safe_output_path_accepts_only_a_file_not_the_root(tmp_path):
    with pytest.raises(ValueError) as exc:
        isextract._safe_output_path(str(tmp_path / "extract"), "", ".")
    assert str(exc.value) == "unsafe archive member: empty file name"


# --- helper: symlink escapes -------------------------------------------------

def test_safe_output_path_rejects_symlinked_directory(tmp_path):
    root = tmp_path / "root"
    output = root / "extract"
    outside = tmp_path / "outside"
    output.mkdir(parents=True)
    outside.mkdir()
    try:
        (output / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(ValueError) as exc:
        isextract._safe_output_path(str(output), "", "link/evil.txt")
    assert str(exc.value).startswith(
        "unsafe archive member: destination escapes extraction root: "
    )
    assert os.path.realpath(str(outside / "evil.txt")) in str(exc.value)


def test_safe_output_path_rejects_symlinked_leaf(tmp_path):
    root = tmp_path / "root"
    output = root / "extract"
    outside = tmp_path / "outside"
    output.mkdir(parents=True)
    outside.mkdir()
    try:
        (output / "victim").symlink_to(outside / "victim.txt")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(ValueError) as exc:
        isextract._safe_output_path(str(output), "", "victim")
    assert str(exc.value).startswith(
        "unsafe archive member: destination escapes extraction root: "
    )


# --- integration: neither extraction method writes outside the root ---------

_METHODS = ["extract_all", "extract_cab_only"]
_TRAVERSALS = [
    ("", "../../outside/evil.txt"),
    ("..\\..", "outside\\evil.txt"),
]


@pytest.mark.parametrize("method", _METHODS)
@pytest.mark.parametrize("directory,name", _TRAVERSALS)
def test_extraction_refuses_dotdot_member(tmp_path, patched_extract, method,
                                          directory, name):
    root = tmp_path / "root"
    output = root / "extract"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "evil.txt"
    sentinel.write_text("untouched")
    cab_file = _dummy_cab(root)

    cabinet = _cabinet(_FakeFD(name, directory), cab_file)
    with pytest.raises(ValueError, match="parent directory component"):
        getattr(cabinet, method)(str(output))

    assert sentinel.read_text() == "untouched"
    assert not (root / "outside").exists()


@pytest.mark.parametrize("method", _METHODS)
def test_extraction_refuses_absolute_member(tmp_path, patched_extract, method):
    root = tmp_path / "root"
    output = root / "extract"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "evil.txt"
    sentinel.write_text("untouched")
    cab_file = _dummy_cab(root)

    cabinet = _cabinet(_FakeFD(str(sentinel)), cab_file)
    with pytest.raises(ValueError, match="absolute path"):
        getattr(cabinet, method)(str(output))

    assert sentinel.read_text() == "untouched"
    assert not (root / "outside").exists()


@pytest.mark.parametrize("method", _METHODS)
def test_extraction_refuses_backslash_traversal(tmp_path, patched_extract,
                                                method):
    root = tmp_path / "root"
    output = root / "extract"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "evil.txt"
    sentinel.write_text("untouched")
    cab_file = _dummy_cab(root)

    # '..\\..\\outside\\evil.txt' is the classic Windows-style ZipSlip spelling.
    cabinet = _cabinet(_FakeFD("..\\..\\outside\\evil.txt"), cab_file)
    with pytest.raises(ValueError, match="parent directory component"):
        getattr(cabinet, method)(str(output))

    assert sentinel.read_text() == "untouched"
    assert not (root / "outside").exists()


@pytest.mark.parametrize("method", _METHODS)
def test_extraction_refuses_symlink_escape(tmp_path, patched_extract, method):
    root = tmp_path / "root"
    output = root / "extract"
    outside = tmp_path / "outside"
    output.mkdir(parents=True)
    outside.mkdir()
    sentinel = outside / "evil.txt"
    sentinel.write_text("untouched")
    cab_file = _dummy_cab(root)
    try:
        (output / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    cabinet = _cabinet(_FakeFD("link/evil.txt"), cab_file)
    with pytest.raises(ValueError, match="destination escapes extraction root"):
        getattr(cabinet, method)(str(output))

    assert sentinel.read_text() == "untouched"


@pytest.mark.parametrize("method", _METHODS)
def test_extraction_still_writes_legitimate_member_inside_root(
        tmp_path, patched_extract, method):
    root = tmp_path / "root"
    output = root / "extract"
    cab_file = _dummy_cab(root)

    cabinet = _cabinet(_FakeFD("ok.txt", "dir\\sub"), cab_file)
    getattr(cabinet, method)(str(output))

    target = output / "dir" / "sub" / "ok.txt"
    assert target.read_bytes() == b"payload"
    assert not (root / "dir").exists()


# --- regression guard on the source itself ----------------------------------

def test_source_guards_both_extraction_methods():
    source = SOURCE.read_text(encoding="utf-8")
    assert "def _safe_output_path(" in source
    assert "os.path.commonpath([base, target])" in source
    assert "os.path.realpath(output_dir)" in source
    for method in ("extract_all", "extract_cab_only"):
        section = source.split(f"def {method}(", 1)[1].split("\n    def ", 1)[0]
        assert "_safe_output_path(output_dir, fd.directory, fd.name)" in section
        assert "fd.directory.replace(" not in section
