#!/usr/bin/env python3
"""Differential integration gate: prove the flag/memory comparator is not vacuous.

The per-function oracle in ``tools/lift/difftest.py`` has already been wrong in a
way that no ordinary pass could reveal: the reference machine's EFLAGS were hard
coded to zero, so every flag comparison passed because the named bits happened
to be clear on both sides. A run that reports ``agree`` is only evidence if the
thing being compared can actually differ. This gate drives real machine code
through the real reference machine and then *deliberately breaks* one observable
at a time, asserting that each break is detected:

* flags -- the synthetic function's ``add`` sets CF/PF/AF/ZF/SF; a one-bit ZF
  flip on the candidate side must be reported as a ZF divergence, and must be
  invisible when flags are excluded (the plain-C default).
* memory -- the function writes a named global; a one-byte flip on the candidate
  side must be reported as that address's divergence.
* selection -- the verdicts are emitted in difftest's JSON shape and passed to
  ``tools/lift/multilifter.py``: the clean body must PROMOTE, each broken body
  must FAILED. A vacuous pass would silently promote a broken body.

Everything is self-contained: the PE is derived from the checked-in synthetic
fixture (no game corpus, no provider, no SDL2), and Unicorn + capstone are the
only native dependencies. The gate is opt-in through ``sh verify.sh
--differential``; the assertions are also collected by the default pytest run.

    python3 tests/diff/differential_gate.py
    python3 tests/diff/differential_gate.py --json reports/diff-gate.json

Exit 0 = every check passed, 1 = at least one check failed, 2 = the gate could
not run (missing dependency / malformed fixture). See docs/DIFF_GATE.md.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.fixtures.make_pe32_fixture import build_pe  # noqa: E402
from tools.lift import difftest, multilifter  # noqa: E402

# Where the fixture's .text begins (VA 0x00401000, file offset 0x200). Both are
# read from the PE headers below; these constants are only the expected values
# the gate asserts, never the bytes it drives.
ENTRY_VA = 0x00401000
GLOBAL_IN = 0x00404100
GLOBAL_OUT = 0x00404104

# mov eax, [GLOBAL_IN]; add eax, -2; mov [GLOBAL_OUT], eax; ret
# The add is the flag subject (cases 0/1 give SF, ZF, CF, AF, PF distinct values)
# and the mov is the memory subject. Ending in ret makes named_addrs_from_code
# stop here instead of collecting the next function's globals.
CODE = (
    bytes.fromhex("A100414000")       # mov eax, [0x00404100]
    + bytes.fromhex("05FEFFFFFF")     # add eax, 0xFFFFFFFE (-2)
    + bytes.fromhex("A304414000")     # mov [0x00404104], eax
    + bytes.fromhex("C3")             # ret
)
CASES = 8
SEED_OF = lambda c: (c + 1) & 0xFFFFFFFF  # noqa: E731 - mirrors difftest.oracle
FLAGS_UNDER_TEST = ("CF", "PF", "AF", "ZF", "SF")


class GateUnavailable(Exception):
    """The gate could not be driven; this is not a comparison failure."""


def build_gate_pe() -> bytes:
    """Return the synthetic PE32 whose single function is the gate subject.

    Derived from the checked-in fixture so the PE headers stay valid. The only
    changes are the function bytes and growing .text to a full page, so the
    disassembler and Unicorn both see the trailing ``ret``. Deterministic: the
    same bytes for every call.
    """
    image = bytearray(build_pe())
    if len(image) < 0x500:
        raise GateUnavailable("synthetic fixture is shorter than its declared sections")
    e_lfanew = int.from_bytes(image[0x3C:0x40], "little")
    coff = e_lfanew + 4
    optsz = int.from_bytes(image[coff + 16:coff + 18], "little")
    opt = coff + 20
    sec = opt + optsz
    if image[sec:sec + 5] != b".text":
        raise GateUnavailable("synthetic fixture's first section is not .text")
    virtual_size, va, raw_size, raw_off = struct.unpack_from("<IIII", image, sec + 8)
    # The fixture keeps SizeOfRawData at one file page but sets VirtualSize to
    # the exact code length; pe_image() maps min(raw, virtual), so without
    # growing VirtualSize the trailing ret (and the second global) are lost.
    if (va, virtual_size, raw_size, raw_off) != (0x1000, 13, 0x200, 0x200):
        raise GateUnavailable("synthetic fixture layout changed; re-derive the gate offsets")
    image[raw_off:raw_off + len(CODE)] = CODE
    page = 0x200
    struct.pack_into("<I", image, sec + 8, page)     # VirtualSize
    struct.pack_into("<I", image, opt + 4, page)     # SizeOfCode
    return bytes(image)


def _reference(pe_path: Path) -> tuple[list[int], list[dict[str, Any]], bytes]:
    """Drive the ORIGINAL bytes once; return (named addresses, results, blob)."""
    blob, _ = difftest.pe_bytes_at(str(pe_path), ENTRY_VA, 64)
    lo, size = difftest.oracle_window(str(pe_path))
    named = difftest.named_addrs_from_code(blob, ENTRY_VA, lo, lo + size)
    if not named:
        raise GateUnavailable("no named guest addresses were found in the gate function")
    real = difftest.run_unicorn_function(str(pe_path), ENTRY_VA, named, SEED_OF, CASES)
    if len(real) != CASES:
        raise GateUnavailable(f"reference machine returned {len(real)}/{CASES} cases")
    return named, real, blob


def _flag_bits(eflags: int) -> dict[str, int]:
    return {name: (eflags >> bit) & 1 for name, bit in difftest.FLAGS}


def _break_flags(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A candidate body with ZF inverted on every case; everything else correct."""
    zf_bit = dict(difftest.FLAGS)["ZF"]
    broken = copy.deepcopy(results)
    for row in broken:
        row["eflags"] ^= 1 << zf_bit
    return broken


def _break_memory(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A candidate body with one written global byte wrong on every case."""
    broken = copy.deepcopy(results)
    for row in broken:
        if GLOBAL_OUT not in row["mem"]:
            raise GateUnavailable(f"reference did not capture memory at 0x{GLOBAL_OUT:08X}")
        row["mem"][GLOBAL_OUT] ^= 0x01
    return broken


def _verdict(real: list[dict[str, Any]], candidate: list[dict[str, Any]],
             undef: tuple[str, ...], blob: bytes) -> dict[str, Any]:
    """Compare with difftest's own diff() and emit its --project JSON row shape."""
    case = difftest.Case(name=f"gate 0x{ENTRY_VA:08X}", code=blob, undef=undef)
    rows, diverged = [], 0
    for i, (got, want) in enumerate(zip(candidate, real)):
        fields = difftest.diff(case, got, want)
        if fields:
            diverged += 1
        rows.append({"case": i, "seed": f"0x{SEED_OF(i):08X}", "agree": not fields,
                     "fields": [{"field": n, "project": a, "original": b}
                                for n, a, b in fields]})
    return {
        "address": f"0x{ENTRY_VA:08X}",
        "verdict": "agree" if diverged == 0 else "diverge",
        "pe": "tests/diff (synthetic)", "section": ".text",
        "compares": "the named arithmetic flags and the named guest memory",
        "diverged_cases": diverged, "cases": rows,
    }


def _field_names(report: dict[str, Any]) -> set[str]:
    return {f["field"] for row in report["cases"] for f in row["fields"]}


def run_gate() -> dict[str, Any]:
    """Run every gate check. Never raises for a comparison failure; see GateUnavailable."""
    image = build_gate_pe()
    with tempfile.TemporaryDirectory(prefix="diff-gate-") as tmp:
        pe_path = Path(tmp) / "gate.exe"
        pe_path.write_bytes(image)
        named, real, blob = _reference(pe_path)

    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    # 1. The memory subject must be discovered from the original bytes. If this
    #    fails, a memory comparison would silently compare nothing.
    check("named-memory-from-original-bytes",
          named == [GLOBAL_IN, GLOBAL_OUT],
          f"named={[f'0x{a:08X}' for a in named]}")

    # 2. The reference's flags must really vary. This is the guard the historical
    #    bug lacked: a hard-coded zero EFLAGS would make every value identical.
    per_case = [_flag_bits(row["eflags"]) for row in real]
    varying = {name: sorted({bits[name] for bits in per_case})
               for name in FLAGS_UNDER_TEST}
    check("reference-flags-nonvacuous",
          all(values == [0, 1] for values in varying.values()),
          "; ".join(f"{n}={v}" for n, v in varying.items()))

    clean = copy.deepcopy(real)

    # 3. A flag-only break is invisible with flags excluded (plain-C default)
    #    and detected when flags are compared (lifted body).
    broken_flags = _break_flags(real)
    excluded = _verdict(real, broken_flags, difftest.NO_FLAGS, blob)
    included = _verdict(real, broken_flags, difftest.ONLY_DF, blob)
    check("flag-break-hidden-when-flags-excluded",
          excluded["verdict"] == "agree" and _field_names(excluded) == set(),
          f"verdict={excluded['verdict']}, fields={sorted(_field_names(excluded))}")
    check("flag-break-detected-when-flags-compared",
          included["verdict"] == "diverge" and _field_names(included) == {"ZF"},
          f"verdict={included['verdict']}, fields={sorted(_field_names(included))}")

    # 4. A memory-only break is detected at the exact named address.
    broken_memory = _break_memory(real)
    memory_report = _verdict(real, broken_memory, difftest.NO_FLAGS, blob)
    expected_addr = f"[{GLOBAL_OUT:08X}]"  # diff()'s own field spelling
    check("memory-break-detected-at-named-address",
          memory_report["verdict"] == "diverge"
          and _field_names(memory_report) == {expected_addr},
          f"verdict={memory_report['verdict']}, fields={sorted(_field_names(memory_report))}")

    # 5. A correct candidate agrees: the comparator is not trigger-happy.
    clean_report = _verdict(real, clean, difftest.ONLY_DF, blob)
    check("clean-body-agrees",
          clean_report["verdict"] == "agree" and _field_names(clean_report) == set(),
          f"verdict={clean_report['verdict']}, fields={sorted(_field_names(clean_report))}")

    # 6. The full pipeline: emitted verdicts drive the multi-lifter selector.
    decisions = {
        "clean_body": multilifter.select([("clean", clean_report)]).decision,
        "flag_bug": multilifter.select([("flag-bug", included)]).decision,
        "memory_bug": multilifter.select([("memory-bug", memory_report)]).decision,
    }
    check("multilifter-promotes-only-clean-body",
          decisions == {"clean_body": multilifter.DECISION_PROMOTE,
                        "flag_bug": multilifter.DECISION_FAILED,
                        "memory_bug": multilifter.DECISION_FAILED},
          json.dumps(decisions, sort_keys=True))

    ok = all(c["ok"] for c in checks)
    return {
        "tool": "tests/diff/differential_gate.py",
        "what_it_proves": ("the per-function oracle's flag and named-memory comparisons are "
                           "non-vacuous on real machine code, and a broken body is not promoted"),
        "what_it_does_not_prove": ("anything about other entry states, the C compile/link path, "
                                   "other functions, or the game as a whole"),
        "fixture": {
            "entry": f"0x{ENTRY_VA:08X}", "code": CODE.hex(),
            "named": [f"0x{a:08X}" for a in named], "cases": CASES,
            "pe_sha256": hashlib.sha256(image).hexdigest(),
        },
        "checks": checks,
        "decisions": decisions,
        "ok": ok,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", metavar="PATH", help="also write the gate report as JSON")
    parser.add_argument("--quiet", action="store_true", help="do not print the report")
    args = parser.parse_args(argv)

    try:
        report = run_gate()
    except GateUnavailable as exc:
        print(f"DIFF-GATE UNAVAILABLE: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, IndexError) as exc:
        print(f"DIFF-GATE UNAVAILABLE: broken fixture: {exc}", file=sys.stderr)
        return 2

    text = json.dumps(report, indent=2)
    if not args.quiet:
        print(text)
    if args.json:
        Path(args.json).write_text(text + "\n", encoding="utf-8")
    if report["ok"]:
        print(f"DIFF-GATE PASS: {len(report['checks'])} check(s) passed")
        return 0
    failed = [c["name"] for c in report["checks"] if not c["ok"]]
    print(f"DIFF-GATE FAIL: {', '.join(failed)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
