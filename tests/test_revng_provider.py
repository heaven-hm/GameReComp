"""The escape hatch's command forms are a documented interface, so they are tested.

REVNG_DECOMPILE_CMD appears in docs/SOURCE_PROVENANCE.md as
`revng decompile {pe} --output-dir {outdir}`, and the code used to append the PE
path instead of substituting. A manual page and an implementation disagreeing is
worse than either alone: the user follows the doc and gets a command that runs
something else. Both forms are now real and both are checked here.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.autorecomp import revng


@pytest.fixture
def pe(tmp_path):
    p = tmp_path / "x.exe"
    p.write_bytes(b"MZ")
    return p


@pytest.fixture
def restore_cmd():
    old = os.environ.pop("REVNG_DECOMPILE_CMD", None)
    yield
    os.environ.pop("REVNG_DECOMPILE_CMD", None)
    if old is not None:
        os.environ["REVNG_DECOMPILE_CMD"] = old


def test_placeholder_form_substitutes(pe, restore_cmd):
    os.environ["REVNG_DECOMPILE_CMD"] = (
        f"{sys.executable} -c 'import sys; print(len(sys.argv))' {{pe}} tail")
    result = revng.decompile(pe)
    resolved = str(pe.resolve())
    assert resolved in result["command"]
    assert result["command"][-1] == "tail", result["command"]
    assert result["command"].count(resolved) == 1


def test_appended_form_is_the_shorthand(pe, restore_cmd):
    os.environ["REVNG_DECOMPILE_CMD"] = f"{sys.executable} -c 'import sys; print(1)'"
    result = revng.decompile(pe)
    assert result["command"][-1] == str(pe.resolve())


def test_quoted_arguments_survive(pe, restore_cmd):
    """str.split() broke any command with a quoted argument; shlex does not."""
    os.environ["REVNG_DECOMPILE_CMD"] = (
        f"{sys.executable} -c 'import sys; print(len(sys.argv))' {{pe}}")
    result = revng.decompile(pe)
    joined = " ".join(result["command"])
    assert "import sys; print(len(sys.argv))" in joined, result["command"]


def test_outdir_placeholder(pe, restore_cmd, tmp_path):
    os.environ["REVNG_DECOMPILE_CMD"] = (
        f"{sys.executable} -c 'import sys; print(1)' --dir {{outdir}}")
    result = revng.decompile(pe)
    assert any(a.startswith("/") and "revng-cmd-" in a for a in result["command"])
