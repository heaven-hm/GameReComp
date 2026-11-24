"""
Translator / Code Generator for 32-bit static recompilation.
Orchestrates the full pipeline: PE analysis -> disassembly -> lifting -> C output.
Generates split source files, dispatch table, and function headers.
"""

import os
import sys
import json
import time
from pathlib import Path

# These three were siblings when this file was written; the tree has since been
# split into pe/, disasm/ and lift/ and the imports never followed, so
# `python -m tools` has been dead. Resolve them by path, the way every other
# consumer of this toolkit does, rather than half-converting the tree to
# packages: lift32 itself still does `from decode16 import ...`, so relative
# imports here would only move the failure one level down.
_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ('pe', 'disasm', 'lift'):
    _p = os.path.join(_TOOLS, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pe_analyze import analyze_pe, build_iat_map, read_bytes_at_va, export_json
from disasm32 import Disassembler, Function
from lift32 import Lifter
import eh32


def load_pe(filepath: str):
    """Load and analyze a PE file, returning everything needed for recompilation."""
    print(f"[*] Analyzing PE: {filepath}")
    info = analyze_pe(filepath)

    with open(filepath, 'rb') as f:
        pe_data = f.read()

    iat_map = build_iat_map(info)
    print(f"[*] Image base: 0x{info.image_base:08X}")
    print(f"[*] Code: 0x{info.code_start:08X} - 0x{info.code_end:08X}")
    print(f"[*] Data: 0x{info.data_start:08X} - 0x{info.data_end:08X}")
    print(f"[*] IAT entries: {len(iat_map)}")

    return info, pe_data, iat_map


def make_read_va(pe_data: bytes, info):
    """Build a read_va(va, size) reader over a PE image's sections in memory."""
    base = info.image_base
    spans = []
    for s in getattr(info, 'sections', ()):
        sva = base + s.virtual_address
        vsz = max(s.virtual_size or 0, s.raw_size or 0)
        spans.append((sva, sva + vsz, s.raw_offset, s.raw_offset + s.raw_size))

    def read_va(va: int, n: int) -> bytes:
        for lo, hi, rlo, rhi in spans:
            if lo <= va < hi:
                off = rlo + (va - lo)
                if off >= rhi:
                    return b'\x00' * n
                chunk = pe_data[off:min(off + n, rhi)]
                return chunk if len(chunk) == n else chunk + b'\x00' * (n - len(chunk))
        return b''

    return read_va


def discover_functions(pe_data: bytes, info, iat_map: dict) -> dict:
    """Discover all functions in the PE's code section.

    The PE entry point and every export are seeded explicitly: they are
    functions whether or not the call/jmp/prologue scan recognises them.
    Without this, a CRT entry that opens with `mov eax, fs:[0]` (the SEH
    prologue) was lost when the prologue scan seeded the inner `push ebp`
    instead, and `recomp_host_run` had no function at the entry VA. disasm32's
    find_functions documents this (`seeds` -- "the PE entry point above all");
    the default pipeline just never passed them.
    """
    disasm = Disassembler(pe_data, info.image_base, info.sections)
    seeds = [info.image_base + info.entry_point_rva]
    seeds += [info.image_base + e.rva for e in getattr(info, 'exports', []) if e.rva]
    functions = disasm.find_functions(info.code_start, info.code_end, iat_map, seeds=seeds)

    # Also recover isolated EH targets (DMD finally blocks / split catch blocks /
    # MSVC catch funclets) reachable only through SEH metadata tables.
    read_va = make_read_va(pe_data, info)
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    extra_work = []
    for func in list(functions.values()):
        blk = func.blocks.get(func.address)
        if not blk or not blk.instructions:
            continue
        aux = eh32.aux_code_targets(md, read_va, blk.instructions[:eh32.PROLOGUE_WINDOW], func.address)
        if not aux:
            continue
        own_insns = {ins.address for b in func.blocks.values() for ins in b.instructions}
        for tgt in aux:
            if not (info.code_start <= tgt < info.code_end) or tgt in functions:
                continue
            # If a catch continuation sits cleanly inside its parent body (and is
            # not a standalone finally/funclet), lift32 emits L_<addr> in-place.
            if tgt in own_insns:
                _, finallies = (set(), set())
                h = eh32._installed_handler(blk.instructions[:eh32.PROLOGUE_WINDOW])
                tbl = eh32.dmd_handlertable(read_va, h, func.address) if h else None
                if tbl:
                    _, finallies = eh32.dmd_targets(read_va, tbl)
                if tgt not in finallies and eh32.funcinfo(read_va, h) is None:
                    continue
            extra_work.append(tgt)
    seen_extra = set()
    while extra_work:
        tgt = extra_work.pop()
        if tgt in seen_extra or tgt in functions or not (info.code_start <= tgt < info.code_end):
            continue
        seen_extra.add(tgt)
        fn = disasm.disassemble_function(tgt, iat_map)
        if fn and fn.blocks:
            functions[tgt] = fn
            for callee in getattr(fn, 'calls_to', ()):
                if callee not in functions and callee not in seen_extra:
                    extra_work.append(callee)
    return functions


def resolve_iat_calls(iat_map: dict, info) -> dict:
    """
    Build a map from IAT slot VA -> function pointer VA.
    For import calls like: call [0x005A9XXX] -> the address in brackets is the IAT slot.
    """
    resolved = {}
    for imp in info.imports:
        va = info.image_base + imp.iat_rva
        name = imp.name if imp.name else f"ordinal_{imp.ordinal}"
        resolved[va] = (imp.dll, name)
    return resolved


MAX_UNIT_BYTES = 8 * 1024 * 1024

# The soft cap can also be tuned from the environment, for sweeps that cannot
# change the command line (the autorecomp CLI pins --split-bytes to 8 MiB). An
# explicit argument always wins; invalid values are ignored with a diagnostic
# rather than silently changing the size of every generated unit.
SOURCE_UNIT_BYTES_ENV = "RECOMP_SOURCE_UNIT_BYTES"

# Kept as one template so the bytes written into every unit and the bytes used
# for cap accounting come from the same string -- the prologue is no longer
# invisible overhead that lets a "capped" unit exceed its cap on disk.
UNIT_PROLOGUE_TEMPLATE = """\
/* Auto-generated by XWA recompiler - DO NOT EDIT */
/* Functions {first} to {last} */
/* Address range: 0x{first_addr:08X} - 0x{last_addr:08X} */

#define RECOMP_GENERATED_CODE
#include "recomp_types.h"
#include "recomp_funcs.h"
#include <math.h>
#include <string.h>

"""


def _utf8_bytes(text: str) -> int:
    """Emitted byte count of text, not its character count."""
    return len(text.encode("utf-8"))


def unit_prologue(first_index, last_index, first_address, last_address):
    """Return the exact prologue written at the head of one source unit."""
    return UNIT_PROLOGUE_TEMPLATE.format(
        first=first_index, last=last_index,
        first_addr=first_address, last_addr=last_address)


def resolve_unit_byte_cap(explicit=None):
    """Resolve the per-unit soft cap: explicit argument > env > default.

    Only positive integers are accepted. No value is ever clamped up or down
    behind the caller's back, so a cap is exactly what the operator asked for.
    """
    if explicit is not None:
        try:
            value = int(explicit)
        except (TypeError, ValueError):
            pass
        else:
            if value > 0:
                return value
        print(f"[!] invalid source-unit byte cap {explicit!r}; using {MAX_UNIT_BYTES}")
        return MAX_UNIT_BYTES
    raw = os.environ.get(SOURCE_UNIT_BYTES_ENV)
    if raw:
        try:
            value = int(raw)
        except ValueError:
            print(f"[!] ignoring invalid {SOURCE_UNIT_BYTES_ENV}={raw!r}")
        else:
            if value > 0:
                return value
            print(f"[!] ignoring non-positive {SOURCE_UNIT_BYTES_ENV}={raw!r}")
    return MAX_UNIT_BYTES


def _human_bytes(nbytes: int) -> str:
    return f"{int(nbytes)} B ({int(nbytes) / (1 << 20):.2f} MiB)"


def build_source_budget(units, cap_bytes):
    """Summarise emitted source units from measured byte counts only.

    `units` is the generator's own manifest list: each entry's `bytes` field is
    an os.path.getsize() observation of the file that was actually written.
    Nothing here is estimated, and no body is truncated to satisfy the cap -- a
    unit that exceeds it is reported so the operator can decide what to do.
    """
    cap_bytes = int(cap_bytes)
    measured = [int(unit.get("bytes", 0)) for unit in units]
    total = sum(measured)
    largest = max(measured, default=0)
    largest_file = ""
    over = []
    for unit, nbytes in zip(units, measured):
        fname = unit.get("file", "?")
        if nbytes == largest and not largest_file:
            largest_file = fname
        if nbytes > cap_bytes:
            functions = int(unit.get("functions", 0))
            over.append({
                "file": fname,
                "bytes": nbytes,
                "functions": functions,
                "over_by_bytes": nbytes - cap_bytes,
                # A single function cannot be split at a function boundary;
                # say so, instead of implying a truncation that never happened.
                "single_oversized_function": functions <= 1,
            })
    return {
        "schema": 1,
        "cap_bytes": cap_bytes,
        "unit_count": len(units),
        "total_bytes": total,
        "largest_unit_bytes": largest,
        "largest_unit_file": largest_file,
        "over_cap_count": len(over),
        "over_cap_units": over,
        "measured_from": "os.path.getsize(recomp_*.c)",
    }


def format_source_budget(budget):
    """Human-readable budget diagnostics, safe to print or write to a file."""
    lines = [
        "GameReComp generated-source budget (measured, not estimated)",
        f"  per-unit soft cap : {_human_bytes(budget['cap_bytes'])}",
        f"  units             : {budget['unit_count']}",
        f"  total emitted     : {_human_bytes(budget['total_bytes'])}",
        f"  largest unit      : {budget['largest_unit_file'] or '-'} "
        f"{_human_bytes(budget['largest_unit_bytes'])}",
        f"  units over cap    : {budget['over_cap_count']}",
    ]
    for unit in budget["over_cap_units"]:
        note = ("single oversized function; kept whole (no truncation)"
                if unit["single_oversized_function"] else
                f"{unit['functions']} functions")
        lines.append(f"  [!] {unit['file']}: {unit['bytes']} B, "
                     f"+{unit['over_by_bytes']} B over cap ({note})")
    return "\n".join(lines) + "\n"


def format_unit_table(units):
    """Per-unit emitted bytes table, one line per file actually written."""
    lines = ["per-unit emitted bytes (os.path.getsize):",
             f"  {'file':<20} {'bytes':>12}  functions"]
    for unit in units:
        lines.append(f"  {unit.get('file', '?'):<20} "
                     f"{int(unit.get('bytes', 0)):>12}  "
                     f"{int(unit.get('functions', 0)):>4}")
    return "\n".join(lines) + "\n"


def write_source_budget_report(output_dir, units, cap_bytes):
    """Write source_budget.txt beside the manifest; return the budget dict."""
    budget = build_source_budget(units, cap_bytes)
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "source_budget.txt"), 'w') as f:
        f.write(format_source_budget(budget))
        f.write("\n")
        f.write(format_unit_table(units))
    return budget


def _write_unit(filepath, chunk, first_index, last_index):
    """Write one source unit; chunk is a list of (func, lifted_code).

    Returns the byte count of the prologue so the caller can account for the
    repeated preamble when deciding whether to close a unit.
    """
    prologue = unit_prologue(
        first_index, last_index, chunk[0][0].address, chunk[-1][0].address)
    with open(filepath, 'w') as f:
        f.write(prologue)
        for _func, code in chunk:
            f.write(code)
            f.write('\n\n')
    return _utf8_bytes(prologue)


def generate_recomp_files(functions: dict, iat_map: dict, output_dir: str,
                          split_size: int = 1000, func_names: dict = None,
                          max_unit_bytes: int | None = None,
                          highlevel: dict = None, read_va=None):
    """
    Generate recompiled C source files from discovered functions.

    functions: dict of addr -> Function
    iat_map: VA -> (dll, name) for import resolution
    output_dir: directory to write generated files
    split_size: max functions per source file
    func_names: optional addr -> name map for known function names
    max_unit_bytes: soft cap on emitted bytes per source file. None resolves
        RECOMP_SOURCE_UNIT_BYTES then MAX_UNIT_BYTES. A function count alone is
        not a size bound: GeneRally's first 1,000 functions produced a 273 MB
        unit because individual bodies reach 2.25 MB. A single function larger
        than the cap still gets a unit of its own -- emitted whole, never
        truncated -- and is reported as an explicit diagnostic.
    highlevel: address -> replacement entry from tools/autorecomp/highlevel.py.
        A replaced function's mechanical body is emitted under
        `sub_XXXXXXXX__lowlevel` and the dispatch table points at the
        high-level entry point instead, so the fallback stays in the build.
    """
    os.makedirs(output_dir, exist_ok=True)
    max_unit_bytes = resolve_unit_byte_cap(max_unit_bytes)

    # Without `lifted`, lift32 emits a direct call to sub_<target> for every
    # call, even targets outside the image. Discovery now recovers enough
    # garbage calls (noiz2sa, rrootage: 0x1F02ADD, 0x1CCA4D4) that those
    # undeclared names fail the build; the lifted set makes them ICALLs.
    lifter = Lifter(iat_map=iat_map, func_names=func_names or {},
                    lifted=set(functions.keys()))
    eh_md = None
    if read_va is not None:
        from capstone import Cs, CS_ARCH_X86, CS_MODE_32
        eh_md = Cs(CS_ARCH_X86, CS_MODE_32)

    sorted_funcs = sorted(functions.values(), key=lambda f: f.address)
    total = len(sorted_funcs)
    print(f"[*] Generating C code for {total} functions...")

    file_idx = 0
    chunk = []          # list of (func, lifted_code)
    chunk_body_bytes = 0
    chunk_first = 0
    all_func_names = []  # (addr, name) pairs for dispatch table
    units = []

    def flush(last_index):
        nonlocal file_idx, chunk, chunk_body_bytes, chunk_first
        filename = f"recomp_{file_idx:04d}.c"
        filepath = os.path.join(output_dir, filename)
        _write_unit(filepath, chunk, chunk_first, last_index)
        units.append({
            "file": filename,
            "first_function_index": chunk_first,
            "last_function_index": last_index,
            "first_address": f"0x{chunk[0][0].address:08X}",
            "last_address": f"0x{chunk[-1][0].address:08X}",
            "functions": len(chunk),
            "bytes": os.path.getsize(filepath),
        })
        file_idx += 1
        if file_idx % 5 == 0:
            print(f"[*] Generated {last_index + 1}/{total} functions ({file_idx} files)...")
        chunk = []
        chunk_body_bytes = 0
        chunk_first = last_index + 1

    for func_idx, func in enumerate(sorted_funcs):
        # A function with a high-level replacement still gets its mechanical
        # body emitted, under a distinct name: it stays compiled, stays
        # countable and stays available as the comparison baseline, but the
        # dispatch table points at the high-level entry point instead. Dropping
        # it would make the fallback invisible, which is the one thing the
        # source-quality contract does not allow.
        high = highlevel.get(func.address) if highlevel else None
        # The lifter writes the body under func.name, so a replaced function's
        # mechanical body is emitted under its __lowlevel name by renaming the
        # function for the duration of the lift and putting it back after.
        original_name = func.name
        if high:
            func.name = high["low_level_symbol"]
        try:
            eh_resume = None
            if read_va is not None and eh_md is not None and not high:
                blk = func.blocks.get(func.address) if hasattr(func, 'blocks') else None
                if blk and blk.instructions:
                    eh_resume = eh32.resume_points(
                        eh_md, read_va, blk.instructions[:eh32.PROLOGUE_WINDOW], func.address)
            code = lifter.lift_function(func, eh_resume=eh_resume) if eh_resume else lifter.lift_function(func)
            name = func.name
        except Exception as e:
            # An empty stub would let the rebuilt game run with a
            # function silently doing nothing; fail loudly instead.
            symbol = high["low_level_symbol"] if high else f"sub_{func.address:08X}"
            code = (f"/* ERROR lifting sub_{func.address:08X}: {e} */\n"
                    f"void {symbol}(void) {{\n"
                    f'    fprintf(stderr, "[recomp] unresolved function 0x{func.address:08X} called\\n");\n'
                    f"    abort();\n"
                    f"}}\n")
            name = symbol
        finally:
            func.name = original_name
        # What the dispatch table calls: the high-level entry point when one
        # exists, the mechanical body otherwise.
        all_func_names.append((func.address,
                               high["entry_symbol"] if high else name))
        chunk.append((func, code))
        chunk_body_bytes += _utf8_bytes(code) + 2   # +2: the unit's "\n\n" separator
        # Account for the prologue that will actually be written, so the cap
        # is a bound on emitted bytes rather than just on lifted bodies.
        projected = (_utf8_bytes(unit_prologue(
            chunk_first, func_idx, chunk[0][0].address, chunk[-1][0].address))
            + chunk_body_bytes)
        if len(chunk) >= split_size or projected >= max_unit_bytes:
            flush(func_idx)
    if chunk:
        flush(total - 1)

    print(f"[*] Generated {file_idx} source files in {output_dir}")

    # Measure and report what was actually written. The budget is additive
    # metadata: existing consumers of recomp_units.json read only "units".
    budget = write_source_budget_report(output_dir, units, max_unit_bytes)
    with open(os.path.join(output_dir, "recomp_units.json"), "w") as f:
        json.dump({"schema": 1, "units": units, "budget": budget}, f, indent=2)
    print(f"[*] Source budget: {budget['unit_count']} units, "
          f"{budget['total_bytes']} bytes emitted, soft cap "
          f"{max_unit_bytes} B/unit, {budget['over_cap_count']} over cap")
    for unit in budget["over_cap_units"]:
        reason = ("single oversized function; emitted whole (no truncation)"
                  if unit["single_oversized_function"] else
                  f"{unit['functions']} functions")
        print(f"[!] source budget: {unit['file']} is {unit['bytes']} B "
              f"(+{unit['over_by_bytes']} B over cap, {reason})")

    # Generate forward declarations header
    header_path = os.path.join(output_dir, "recomp_funcs.h")
    with open(header_path, 'w') as f:
        f.write("/* Auto-generated function declarations - DO NOT EDIT */\n")
        f.write("#pragma once\n\n")
        f.write("#include <stdint.h>\n\n")
        f.write(f"/* {len(all_func_names)} recompiled functions */\n\n")
        for addr, name in all_func_names:
            f.write(f"void {name}(void);  /* 0x{addr:08X} */\n")
        if highlevel:
            # The mechanical bodies of replaced functions are still compiled
            # and still need declaring.
            for addr, high in sorted(highlevel.items()):
                f.write(f"void {high['low_level_symbol']}(void);"
                        f"  /* 0x{addr:08X} mechanical fallback */\n")

    # Generate dispatch table
    dispatch_path = os.path.join(output_dir, "recomp_dispatch.c")
    with open(dispatch_path, 'w') as f:
        f.write("/* Auto-generated dispatch table - DO NOT EDIT */\n\n")
        f.write('#include "recomp_types.h"\n')
        f.write('#include "recomp_funcs.h"\n\n')
        f.write(f"const recomp_dispatch_entry_t recomp_dispatch_table[] = {{\n")
        for addr, name in sorted(all_func_names, key=lambda x: x[0]):
            f.write(f"    {{ 0x{addr:08X}u, {name} }},\n")
        f.write(f"}};\n\n")
        f.write(f"const uint32_t recomp_dispatch_count = {len(all_func_names)};\n")

    print(f"[*] Generated dispatch table with {len(all_func_names)} entries")
    return all_func_names


def generate_function_list(functions: dict, output_path: str):
    """Export the function list to JSON for external tools."""
    func_list = []
    for addr, func in sorted(functions.items()):
        func_list.append({
            'address': f"0x{addr:08X}",
            'address_int': addr,
            'name': func.name,
            'size': func.size,
            'num_blocks': len(func.blocks),
            'num_instructions': func.num_instructions,
            'is_thunk': func.is_thunk,
            'calls_to': [f"0x{t:08X}" for t in sorted(func.calls_to)],
        })

    with open(output_path, 'w') as f:
        json.dump(func_list, f, indent=2)
    print(f"[*] Exported {len(func_list)} functions to {output_path}")


def generate_import_stubs(iat_map: dict, output_path: str):
    """Generate stub implementations for imported functions."""
    by_dll = {}
    for va, (dll, name) in sorted(iat_map.items()):
        by_dll.setdefault(dll, []).append((va, name))

    with open(output_path, 'w') as f:
        f.write("/* Import bridge stubs - manual implementation needed */\n\n")
        f.write('#include "recomp_types.h"\n\n')

        for dll, imports in sorted(by_dll.items()):
            f.write(f"\n/* ===== {dll} ===== */\n\n")
            for va, name in imports:
                f.write(f"/* 0x{va:08X} */ void bridge_{name}(void) {{\n")
                f.write(f"    /* TODO: implement {dll}::{name} */\n")
                f.write(f"    STUB(\"{dll}::{name}\");\n")
                f.write(f"}}\n\n")

    print(f"[*] Generated import stubs for {sum(len(v) for v in by_dll.values())} imports")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='32-bit PE static recompiler')
    parser.add_argument('pe_file', help='the 32-bit PE to lift')
    parser.add_argument('--output', '-o', default='src/game/recomp/gen',
                        help='Output directory for generated code')
    parser.add_argument('--split', type=int, default=1000,
                        help='Max functions per source file')
    parser.add_argument('--split-bytes', type=int, default=None,
                        help=f'Soft cap on emitted bytes per source file '
                             f'(default {MAX_UNIT_BYTES} bytes, or '
                             f'${SOURCE_UNIT_BYTES_ENV}; never truncates)')
    parser.add_argument('--all', action='store_true',
                        help='Run full pipeline')
    parser.add_argument('--analyze-only', action='store_true',
                        help='Only run PE analysis')
    parser.add_argument('--functions-json', default=None,
                        help='Export function list to JSON')
    parser.add_argument('--stubs', default=None,
                        help='Generate import stub file')
    parser.add_argument('--pe-json', default=None,
                        help='Export PE analysis to JSON')

    args = parser.parse_args()

    start_time = time.time()

    # Step 1: Analyze PE
    info, pe_data, iat_map = load_pe(args.pe_file)

    if args.pe_json:
        export_json(info, args.pe_json)

    if args.analyze_only:
        from pe_analyze import print_summary
        print_summary(info)
        return

    # Step 2: Discover functions
    print(f"\n[*] Phase 2: Function discovery...")
    functions = discover_functions(pe_data, info, iat_map)

    if args.functions_json:
        generate_function_list(functions, args.functions_json)

    if args.stubs:
        generate_import_stubs(iat_map, args.stubs)

    if args.all:
        # Step 3: Generate recompiled C code
        print(f"\n[*] Phase 3: Code generation...")
        generate_recomp_files(
            functions, iat_map, args.output,
            split_size=args.split, max_unit_bytes=args.split_bytes,
        )
        units = [p for p in os.listdir(args.output)
                 if p.startswith("recomp_") and p.endswith(".c")
                 and p != "recomp_dispatch.c"]

        elapsed = time.time() - start_time
        print(f"\n[*] Complete! {len(functions)} functions recompiled in {elapsed:.1f}s")
        print(f"[*] Output: {args.output}")
        print(f"[*] Generated files: {len(units)} source + dispatch + header")

    elapsed = time.time() - start_time
    print(f"\n[*] Total time: {elapsed:.1f}s")


if __name__ == '__main__':
    main()
