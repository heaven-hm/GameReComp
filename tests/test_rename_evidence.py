"""The evidence extractor's rules, which decide what a name may be based on.

The extraction itself needs the corpus PE; what is tested here is the part that
turns bytes into evidence classes and format strings into argument roles,
because a wrong rule would make every name it backs look justified.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.autorecomp import rename_evidence as re_ev
from tools.autorecomp import rename as rn

PE = Path("/Users/haseeb-mir/Downloads/recomp-apps/games/ikachan/dxIka.exe")
HAVE = PE.is_file()


def test_format_roles_read_argument_types():
    # "%s: %d" says the first vararg is a string and the second a number - that
    # is the evidence class, not a guess
    roles = re_ev.format_roles("%s: %d")
    assert [r["role"] for r in roles] == ["string", "integer"]
    assert re_ev.format_roles("100%% done") == []
    assert [r["role"] for r in re_ev.format_roles("%08X %p %f")] == ["unsigned", "pointer", "float"]


def test_the_import_table_gives_iat_addresses():
    if not HAVE:
        pytest.skip("the corpus is not present")
    imports = re_ev.parse_imports(PE)
    assert imports, "the PE has imports"
    modules = {dll.lower() for _, dll, _ in imports}
    # Ikachan is a DirectDraw/DirectInput game, not an SDL one - the test says
    # what is true of this binary rather than what the tool would like
    assert {"kernel32.dll", "user32.dll", "ddraw.dll"} <= modules, modules
    # every entry names where it lives, which is what a `call [iat]` resolves by
    assert all(0x00400000 <= va < 0x80000000 for va, _, _ in imports)


def test_a_functions_signature_comes_from_its_own_ret():
    if not HAVE:
        pytest.skip("the corpus is not present")
    insns, ret_form = re_ev.function_instructions(PE, 0x0040BA58)
    assert ret_form == "ret"          # cdecl: the caller pops
    assert len(insns) == 7            # ret included, and the extent stops there


def test_evidence_is_keyed_by_identity_across_views():
    if not HAVE:
        pytest.skip("the corpus is not present")
    data = re_ev.collect(PE, [0x0040BA58], Path("highlevel/ikachan/revng"))
    # the byte-level read, write and rev.ng's field access are ONE global, not
    # three; if they were three identities the namer would give three names to
    # one thing
    assert "global:0x00413350" in data["identities"]
    assert len(data["identities"]["global:0x00413350"]) >= 2


def test_an_address_that_is_not_code_is_refused():
    if not HAVE:
        pytest.skip("the corpus is not present")
    with pytest.raises(SystemExit):
        re_ev.function_instructions(PE, 0x90000000)


def test_an_odd_address_is_refused_as_a_function_start():
    # x86 has no alignment requirement, but 23.6% of the corpus's table entries
    # being odd means the discovery is finding mid-instruction addresses; naming
    # those would generate C for garbage
    if not HAVE:
        pytest.skip("the corpus is not present")
    ok, why = re_ev.plausible_start(PE, 0x0040BA59)      # one byte into a real function
    assert not ok and "odd address" in why
    ok, why = re_ev.plausible_start(PE, 0x0040BA58)
    assert ok, why


def test_a_refused_entry_is_not_named():
    if not HAVE:
        pytest.skip("the corpus is not present")
    ev = re_ev.collect(PE, [0x0040BA59])
    named = rn.name_all(ev)
    fn = named["functions"]["0x0040BA59"]
    assert fn["kind"] == "not_a_function_start"
    assert fn["name"] is None
    assert named["counts"]["not_a_function_start"] == 1
