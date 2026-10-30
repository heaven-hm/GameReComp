"""The per-function oracle's own pieces, without running a whole comparison.

The end-to-end verdict needs Unicorn, a C compiler and a generated project, so
that belongs in the tool's own run (see reports/per-function-oracle.md). What is
tested here is the part that turns a PE and an address into a case, because a
wrong byte or a wrong memory set would make every verdict meaningless while
still looking like a result.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lift import difftest

PE = Path("/Users/haseeb-mir/Downloads/recomp-apps/games/ikachan/dxIka.exe")
HAVE = PE.is_file()


def test_pe_sections_reads_the_pe32_layout():
    if not HAVE:
        pytest.skip("the corpus is not present")
    base, secs = difftest.pe_sections(PE)
    assert base == 0x00400000
    names = [s[0] for s in secs]
    assert ".text" in names
    text = next(s for s in secs if s[0] == ".text")
    # the PE32 optional header carries the image base at +28 and the section
    # table follows it; a wrong offset would put .text somewhere implausible
    assert 0x00400000 <= text[1] < 0x00410000


def test_bytes_at_a_va_come_from_that_va():
    if not HAVE:
        pytest.skip("the corpus is not present")
    blob, section = difftest.pe_bytes_at(PE, 0x0040BA58, 32)
    assert section == ".text"
    # a1 50 33 41 00 = mov eax, [0x413350]; 69 c0 .. = imul eax, eax, 0x343FD
    assert blob[:5] == bytes([0xA1, 0x50, 0x33, 0x41, 0x00])
    assert blob[5:7] == bytes([0x69, 0xC0])


def test_a_va_outside_the_image_is_refused():
    if not HAVE:
        pytest.skip("the corpus is not present")
    with pytest.raises(SystemExit):
        difftest.pe_bytes_at(PE, 0x90000000, 16)


def test_named_memory_stops_at_the_return():
    if not HAVE:
        pytest.skip("the corpus is not present")
    blob, _ = difftest.pe_bytes_at(PE, 0x0040BA58, 4096)
    named = difftest.named_addrs_from_code(blob, 0x0040BA58)
    # msvc_rand_next touches exactly one global: the LCG state at 0x413350.
    # Disassembling past its ret would collect the next function's globals and
    # compare memory this function never touches.
    assert named == [0x00413350]


def test_the_seed_varies_per_case():
    # both sides use the same seed function, so it must vary - a constant made
    # every case the same case, and every comparison a divergence
    seed_of = lambda c: (0x9E3779B9 * (c + 1)) & 0xFFFFFFFF
    seeds = [seed_of(c) for c in range(8)]
    assert len(set(seeds)) == 8
    assert seed_of(0) == 0x9E3779B9


def test_flags_are_excluded_by_default_for_a_plain_c_body():
    # a typed C function does not model the guest's flag state, so comparing the
    # flags would compare stale globals against the real register; the default
    # excludes them and the report says why
    assert difftest.ONLY_DF == ("DF",)
    assert set(difftest.NO_FLAGS) == {"CF", "PF", "AF", "ZF", "SF", "DF", "OF"}
    plain = difftest.Case(name="x", code=b"\x90", undef=difftest.NO_FLAGS)
    lifted = difftest.Case(name="x", code=b"\x90", undef=difftest.ONLY_DF)
    assert "ZF" in plain.undef and "ZF" not in lifted.undef


def test_the_comparator_catches_a_flag_only_difference():
    # the property the exclusion must not destroy: when flags ARE compared (a
    # lifted body), a flag-only difference is a divergence
    lifted = {"regs": {n: 0 for n in difftest.REGS}, "eflags": 0x202,
              "mem": {}, "fp_depth": 0, "st": []}
    real = {"regs": {n: 0 for n in difftest.REGS}, "eflags": 0x246,
            "mem": {}, "fp_depth": 0, "st": []}
    fields = difftest.diff(difftest.Case(name="x", code=b"\x90",
                                         undef=difftest.ONLY_DF), lifted, real)
    assert {f[0] for f in fields} == {"PF", "ZF"}
