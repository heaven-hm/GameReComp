"""The marshal: a call across the boundary, from the callee's DECLARED contract.

A call is a value crossing in both directions, so the caller has to know three
things about its callee before it can cross: what it takes, whether it has a guest
stack frame (and how big), and what it returns. All three are in the provider's own
C, which is what makes the contract checkable where it is written rather than at
link time - `expected 2, have 1` was the link-time version of this.

The three members here are the Lead's: a callee without a frame (the special case -
796 of 1524 real call sites carry a frame), a callee with one, and a callee whose
RETURN crosses (the hidden-pointer struct return, 244 of 955 provider functions).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.autorecomp.rename_ast import Unsupportable, rewrite

BASE = 0x0049B000
CALLER = 0x1000


def callee(ret="generic32_t", params=1, frame=None, returns_struct=False):
    return {
        "ret": ret,
        "params": [{"type": "generic32_t", "name": f"a{i}"} for i in range(params)],
        "frame_size": frame,
        "returns_struct": returns_struct,
    }


def caller_body(callee_va, declare_frame=False, extra=""):
    stack = "struct struct_1 stack;\n" if declare_frame else ""
    return (
        "void function_0x1000_Code_x86(generic32_t argument_0) {\n"
        f"    {stack}"
        f"    generic32_t var_0 = function_0x{callee_va:04x}_Code_x86(argument_0);\n"
        f"    {extra}"
        "    return;\n"
        "}\n"
    )


def run(body, sigs, names, frames=False):
    return rewrite(body, BASE, own_va=CALLER, signatures=sigs, callee_names=names,
                   frames=frames)


# ---- member 1: a callee WITHOUT a frame (the special case)
def test_a_call_to_a_frameless_callee_passes_only_its_arguments():
    src, _ = run(caller_body(0x1001), {0x1001: callee(frame=None)},
                 {0x1001: "callee_no_frame"})
    assert "callee_no_frame(argument_0)" in src
    # no frame PARAMETER: the name contains "frame", the signature must not
    assert ", uint32_t frame)" not in src


# ---- member 2: a callee WITH a frame - the caller owes it one, nested below its own
def test_a_call_to_a_frame_callee_nests_the_frame_below_the_callers():
    src, _ = run(caller_body(0x1002, declare_frame=True),
                 {0x1002: callee(frame=8)}, {0x1002: "callee_with_frame"}, frames=True)
    # frame-first: the callee's frame is an argument, and it is the caller's frame
    # MINUS the callee's size, because the guest stack grows down
    assert "callee_with_frame(argument_0, (frame - 8U))" in src


def test_a_caller_without_a_frame_cannot_supply_one_and_is_refused():
    # never defaulted: a default at a boundary that must carry a guest VA is a
    # value crossing unproven, which is the outcome the boundary rule exists to stop
    with pytest.raises(Unsupportable):
        run(caller_body(0x1002), {0x1002: callee(frame=8)}, {0x1002: "callee_with_frame"})


# ---- member 3: the RETURN crossing
def test_a_struct_returning_callee_is_refused():
    with pytest.raises(Unsupportable):
        run(caller_body(0x1003), {0x1003: callee(ret="struct_1", params=0, returns_struct=True)},
            {0x1003: "callee_struct_ret"})


# ---- the contract itself
def test_an_argument_count_that_does_not_match_the_declaration_is_refused():
    with pytest.raises(Unsupportable):
        run(caller_body(0x1001), {0x1001: callee(params=2)}, {0x1001: "callee_two_args"})


def test_a_callee_with_no_declared_contract_is_refused():
    with pytest.raises(Unsupportable):
        run(caller_body(0x1001), {}, {0x1001: "unknown"})


# ---- the manifest category: a call-bearing module is NOT verified
def test_a_call_bearing_body_can_only_claim_high_level_unverified():
    from tools.autorecomp.rename_gen import category_for
    sigs = {0x1001: callee(frame=None), 0x1002: callee(frame=None)}
    sigs[0x1001]["calls"] = [0x1002]
    sigs[0x1002]["calls"] = []
    # the caller can be generated, but the call it makes is inside the boundary the
    # oracle cannot see, so it cannot claim the verified category
    assert category_for(0x1001, sigs) == "HIGH_LEVEL_UNVERIFIED"
    assert category_for(0x1002, sigs) == "HIGH_LEVEL_VERIFIED"
