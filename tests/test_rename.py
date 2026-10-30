"""The namer's rules, especially the one that stops a name lying about scope.

A function that calls MessageBoxA once in 363 instructions is not
"show_message_box". These tests pin the dominance rule, the documented-fact rule,
the three counts, and the backmap, because each of them is a way a rename pass can
look successful while being wrong.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.autorecomp import rename as rn
from tools.autorecomp import rename_gen as rn_gen


def fn(records, address="0x00401000"):
    return {"records": records, "instructions": len(records), "ret_form": "ret"}


def rec(kind, **kw):
    base = {"kind": kind, "identity": "x", "evidence": "ev"}
    base.update(kw)
    return base


def test_a_wrapper_named_after_its_only_import_is_semantic():
    records = [
        rec("import_call", name="ExitProcess", module="KERNEL32.dll",
            evidence="call [0x40e000] -> KERNEL32.dll!ExitProcess"),
        rec("signature", ret_form="ret", argc=None, convention="cdecl", evidence="ret"),
    ]
    name, kind, ev = rn.name_function(fn(records), {})
    assert kind == "named_semantic"
    assert name.startswith("exit_process")
    assert ev


def test_one_import_in_a_large_function_is_not_named_as_that_import():
    # 14 import calls but 13 internal calls as well: the import is not the
    # behaviour, so the name must say "uses", not "is"
    records = [rec("import_call", name="SendMessageA", module="USER32.dll",
                   evidence="call [0x40e100] -> USER32.dll!SendMessageA")] * 14
    records += [rec("call_target", target="0x00401xxx", evidence="call 0x00401xxx")] * 13
    records.append(rec("signature", ret_form="ret", argc=None, convention="cdecl", evidence="ret"))
    name, kind, _ = rn.name_function(fn(records, "0x0040DF40"), {})
    assert kind == "named_structured"
    assert name.startswith("uses_sendmessagea")
    assert "show" not in name


def test_a_documented_constant_pair_is_semantic():
    records = [
        rec("constant", value="0x000343FD", evidence="imul eax, eax, 0x343fd"),
        rec("constant", value="0x00269EC3", evidence="add eax, 0x269ec3"),
        rec("signature", ret_form="ret", argc=None, convention="cdecl", evidence="ret"),
    ]
    name, kind, ev = rn.name_function(fn(records), {})
    assert kind == "named_semantic"
    assert name.startswith("msvc_rand")
    assert {e["kind"] for e in ev} == {"constant"}


def test_a_constant_store_block_is_structured_not_semantic():
    records = [rec("references_global", va=f"0x00413B{0xE0 + 4 * i:02X}",
                   evidence=f"mov dword ptr [0x413B{0xE0 + 4 * i:02X}], {i}")
               for i in range(6)]
    records.append(rec("signature", ret_form="ret", argc=None, convention="cdecl", evidence="ret"))
    name, kind, _ = rn.name_function(fn(records), {})
    assert kind == "named_structured"
    assert name.startswith("store_block_")


def test_nothing_but_a_signature_is_no_evidence():
    records = [rec("signature", ret_form="ret", argc=None, convention="cdecl", evidence="ret")]
    name, kind, ev = rn.name_function(fn(records), {})
    assert kind == "no_evidence"
    assert name is None and ev == []


def test_the_three_counts_are_kept_apart_and_every_name_is_reversible():
    evidence = {
        "functions": {
            "0x00401000": fn([rec("import_call", name="ExitProcess", module="KERNEL32.dll",
                                  evidence="call ExitProcess")], "0x00401000"),
            "0x00402000": fn([rec("signature", ret_form="ret", argc=None,
                                  convention="cdecl", evidence="ret")], "0x00402000"),
        },
        "identities": {},
    }
    named = rn.name_all(evidence)
    c = named["counts"]
    # never merged: a structured name must not count toward readable source
    assert c["named_semantic"] == 1
    assert c["no_evidence"] == 1
    assert c["named_structured"] == 0
    for name, ev in named["backmap"].items():
        assert ev, f"{name} has no evidence to trace back to"


def test_a_global_is_named_from_the_union_of_its_evidence():
    # two functions touch one global; the union is what the name rests on, and
    # one identity must not become two names
    ident = "global:0x00413350"
    evidence = {
        "functions": {},
        "identities": {ident: [
            {"address": "0x0040BA58", "kind": "references_global", "evidence": "mov eax, [0x413350]"},
            {"address": "0x0040BA58", "kind": "field_access", "evidence": "rev.ng reads generic32_t at 0x413350"},
        ]},
    }
    named = rn.name_all(evidence)
    assert ident in named["globals"]
    assert named["globals"][ident]["name"]
    assert len(named["globals"][ident]["evidence"]) == 2


# ---- memory fusion: the conditions that make it sound rather than usually-right

def test_repeated_accesses_to_one_address_become_one_local():
    body = ("uint32_t f(void) {\n"
            "    MEM32(0x00413350u) = MEM32(0x00413350u) * 0x343FDu + 0x269EC3u;\n"
            "    return MEM32(0x00413350u) >> 16U & 0x7FFFu;\n"
            "}")
    fused, decls, _ = rn_gen.fuse_memory(body, {"global:0x00413350": "state_00413350"})
    assert decls and "uint32_t state_00413350 = MEM32(0x00413350u);" in fused
    # the local carries the value, and the store is the only memory write left
    assert fused.count("MEM32(0x00413350u)") == 2          # the load and the store
    assert "return state_00413350 >> 16U & 0x7FFFu;" in fused


def test_a_call_between_accesses_blocks_the_fusion():
    # a callee may write any address, so a read across a call must reload
    body = ("void f(void) {\n"
            "    MEM32(0x00413350u) = 1;\n"
            "    helper();\n"
            "    MEM32(0x00413350u) = MEM32(0x00413350u) + 1;\n"
            "}")
    fused, decls, refusals = rn_gen.fuse_memory(body, {})
    assert refusals, "the call must be reported as blocking fusion"


def test_different_widths_to_one_address_are_not_the_same_object():
    body = ("void f(void) {\n"
            "    MEM8(0x00413350u) = 1;\n"
            "    MEM32(0x00413350u) = MEM32(0x00413350u) + 1;\n"
            "}")
    fused, decls, _ = rn_gen.fuse_memory(body, {})
    # two locals are fine, two locals with the SAME NAME are a compile error -
    # which is what the first version of the fusion produced
    names = [d.split("=")[0].split()[-1] for d in decls]
    assert len(names) == len(set(names)), names


def test_a_single_access_is_not_fused():
    body = ("uint32_t f(void) {\n"
            "    return MEM32(0x00413350u);\n"
            "}")
    fused, decls, _ = rn_gen.fuse_memory(body, {})
    # one load needs no local; fusing it would add a variable for nothing
    assert decls == [] or fused.count("state_") == 0
