"""What the bytes say a function is, before anyone names it.

The rev.ng renaming pass has one rule: a name must be backed by evidence, and an
identifier with no evidence keeps its mechanical name and says why. This module
produces the evidence, and deliberately produces **no names** — naming is a
separate pass, so the evidence can be reviewed before anything is named.

Seven classes, in descending strength:

1. **Import calls.** `call [IAT]` resolves through the PE import table to a
   module and an export name. A function that calls `ExitProcess` is about
   exiting. Fully mechanical and the strongest evidence there is.
2. **Data references.** Absolute operands with no base or index, classified by
   the section they land in and, for `.rdata`, by what the bytes hold: an ASCII
   string or a word. Evidence is the instruction.
3. **Format strings.** A call to a printf-family or message-box import whose
   format argument is a string in the image gives the argument ROLES: `"%s: %d"`
   says the first vararg is a string and the second a number. Same data as class
   2, read for structure rather than for content.
4. **rev.ng's recovered types.** `*(genericN_t *)(base + off)` in the provider's
   decompilation gives field widths and offsets; the offsets observed are
   recorded, not the provider's whole struct layout.
5. **Call-graph position.** Callees, callers, and whether a caller is a frame
   loop (detected by what that caller imports). An inference from evidence, and
   labelled as one.
6. **Constants.** The immediates the function compares or multiplies against.
7. **Calling convention and argument count.** The function's own signature is in
   its epilogue: `ret N` on an x86 callee means it pops N bytes (stdcall, N/4
   arguments); a bare `ret` means the caller pops (cdecl). For the imports it
   calls, the argument count is only available when the import name is decorated
   (`_Name@N`) or a Windows SDK is present to resolve it - this host has no SDK,
   so any other case is recorded as no_evidence with that reason rather than
   guessed.

Evidence is keyed by IDENTITY, not by "this function's parameter 2": one record
per global VA, one per string, one per function, one per field offset. Two
functions touching the same global produce two evidence items for one identity,
which is what lets a namer give one name to one thing.
"""

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from capstone import Cs, CS_ARCH_X86, CS_MODE_32
from capstone.x86 import X86_OP_MEM, X86_OP_IMM

from tools.lift.difftest import pe_sections

IMAGE_LO, IMAGE_HI = 0x00400000, 0x80000000
FORMAT_IMPORTS = {
    "wsprintfA", "wsprintfW", "sprintf", "sprintf_s", "_snprintf", "printf",
    "fprintf", "vsprintf", "MessageBoxA", "MessageBoxW", "OutputDebugStringA",
}


# ---------------------------------------------------------------- the PE

def parse_imports(pe_path):
    """[(iat_va, module, name)] from the PE import table."""
    data = Path(pe_path).read_bytes()
    e = struct.unpack_from('<I', data, 0x3C)[0]
    coff = e + 4
    nsec = struct.unpack_from('<H', data, coff + 2)[0]
    optsz = struct.unpack_from('<H', data, coff + 16)[0]
    opt = coff + 20
    if struct.unpack_from('<H', data, opt)[0] != 0x10B:
        return []
    image_base = struct.unpack_from('<I', data, opt + 28)[0]
    sec = opt + optsz
    secs = []
    for i in range(nsec):
        s = sec + 40 * i
        vsize, va, rsize, roff = struct.unpack_from('<IIII', data, s + 8)
        secs.append((image_base + va, vsize, rsize, roff))
    dd = opt + 96
    imp_rva, imp_size = struct.unpack_from('<II', data, dd + 8 * 1)   # import directory

    def rva_to_off(rva):
        for va, vsize, rsize, roff in secs:
            if va <= image_base + rva < va + max(vsize, rsize):
                return roff + (image_base + rva - va)
        return None

    out = []
    off = rva_to_off(imp_rva)
    if off is None:
        return out
    while True:
        try:
            ilt, _, _, name_rva, iat = struct.unpack_from('<IIIII', data, off)
        except struct.error:
            break
        if not (ilt or name_rva or iat):
            break
        noff = rva_to_off(name_rva)
        dll = (data[noff:data.index(b'\0', noff)].decode('latin1') if noff is not None else '')
        walk = rva_to_off(ilt or iat)
        slot = iat
        if walk is not None:
            while True:
                try:
                    ent = struct.unpack_from('<I', data, walk)[0]
                except struct.error:
                    break
                if not ent:
                    break
                if not (ent & 0x80000000):
                    hnoff = rva_to_off(ent & 0x7FFFFFFF)
                    nm = data[hnoff + 2:data.index(b'\0', hnoff + 2)].decode('latin1') if hnoff is not None else ''
                else:
                    nm = f"ordinal_{ent & 0xFFFF}"
                out.append((image_base + slot, dll, nm))
                walk += 4
                slot += 4
        off += 20
        if len(out) > 4096:
            break
    return out


def section_of(pe_path, va):
    """(name, va, vsize, raw_off, raw_size) containing `va`, or None."""
    base, secs = pe_sections(pe_path)
    for name, sva, vsize, roff, rsize in secs:
        if sva <= va < sva + max(vsize, rsize):
            return name, sva, vsize, roff, rsize
    return None


def bytes_of(pe_path, section, va, n=256):
    name, sva, vsize, roff, rsize = section
    off = roff + (va - sva)
    data = Path(pe_path).read_bytes()
    return data[off:off + n]


def ascii_at(pe_path, va, limit=96):
    """The ASCII string at VA, or None if the bytes are not a printable run."""
    sec = section_of(pe_path, va)
    if not sec:
        return None
    blob = bytes_of(pe_path, sec, va, limit)
    if not blob or blob[0] < 0x20 or blob[0] > 0x7E:
        return None
    end = blob.find(b'\0')
    if end < 2:
        return None
    text = blob[:end].decode('latin1')
    if not all(0x20 <= ord(c) <= 0x7E for c in text):
        return None
    return text


# ---------------------------------------------------------------- the function

def function_instructions(pe_path, va, limit=8192):
    """Disassemble from VA to the function's return, and report the ret form."""
    base, secs = pe_sections(pe_path)
    sec = section_of(pe_path, va)
    if not sec:
        raise SystemExit(f'0x{va:08X} is not in a section')
    name, sva, vsize, roff, rsize = sec
    if 'text' not in name.lower():
        raise SystemExit(f'0x{va:08X} is in {name!r}, which is not code')
    n = min(limit, sva + rsize - va, sva + vsize - va)
    blob = Path(pe_path).read_bytes()[roff + (va - sva): roff + (va - sva) + n]
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    insns, ret_form = [], None
    for insn in md.disasm(blob, va):
        insns.append(insn)
        if insn.mnemonic in ('ret', 'retf', 'retn'):
            ret_form = insn.mnemonic
            if insn.operands:
                ret_form += f' {insn.operands[0].imm}'
            break
    return insns, ret_form


def evidence_for_function(pe_path, va, imports, revng_dir=None):
    """The evidence records for one function, keyed by identity."""
    insns, ret_form = function_instructions(pe_path, va)
    iat = {a: (dll, nm) for a, dll, nm in imports}
    recs = []

    for insn in insns:
        at = insn.address
        mnem = insn.mnemonic
        # 1. calls through the IAT
        if mnem in ('call', 'jmp') and len(insn.operands) == 1:
            op = insn.operands[0]
            if op.type == X86_OP_MEM and op.mem.base == 0 and op.mem.index == 0:
                tgt = op.mem.disp & 0xFFFFFFFF
                if tgt in iat:
                    dll, nm = iat[tgt]
                    recs.append({"kind": "import_call", "identity": f"import:{dll}!{nm}",
                                 "at": f"0x{at:08X}", "module": dll, "name": nm,
                                 "evidence": f"{mnem} [{tgt:#010x}] -> {dll}!{nm}"})
            elif op.type == X86_OP_IMM:
                recs.append({"kind": "call_target", "identity": f"function:0x{op.imm & 0xFFFFFFFF:08X}",
                             "at": f"0x{at:08X}", "target": f"0x{op.imm & 0xFFFFFFFF:08X}",
                             "evidence": f"{mnem} 0x{op.imm & 0xFFFFFFFF:08X}"})
        # 2/3/6. absolute data references and constants
        for op in insn.operands:
            if op.type == X86_OP_MEM and op.mem.base == 0 and op.mem.index == 0:
                tgt = op.mem.disp & 0xFFFFFFFF
                if IMAGE_LO <= tgt < IMAGE_HI and tgt not in iat:
                    text = ascii_at(pe_path, tgt)
                    if text:
                        recs.append({"kind": "references_string", "identity": f"string:{text}",
                                     "at": f"0x{at:08X}", "va": f"0x{tgt:08X}", "value": text,
                                     "evidence": f"{mnem} {insn.op_str}"})
                    else:
                        recs.append({"kind": "references_global", "identity": f"global:0x{tgt & ~3:08X}",
                                     "at": f"0x{at:08X}", "va": f"0x{tgt & ~3:08X}",
                                     "width": 2 ** ((tgt - (tgt & ~3))),
                                     "evidence": f"{mnem} {insn.op_str}"})
            elif op.type == X86_OP_IMM:
                v = op.imm & 0xFFFFFFFF
                if v > 0xFFFF and not (IMAGE_LO <= v < IMAGE_HI):
                    recs.append({"kind": "constant", "identity": f"constant:0x{v:08X}",
                                 "at": f"0x{at:08X}", "value": f"0x{v:08X}",
                                 "evidence": f"{mnem} {insn.op_str}"})

    # 3. format strings: a printf-family call whose format argument is a string
    for i, insn in enumerate(insns):
        if insn.mnemonic != 'call':
            continue
        if not insn.operands or insn.operands[0].type != X86_OP_MEM:
            continue
        dll, nm = iat.get(insn.operands[0].mem.disp & 0xFFFFFFFF, ('', ''))
        if nm not in FORMAT_IMPORTS:
            continue
        for back in range(1, min(4, i + 1)):
            prev = insns[i - back]
            for op in prev.operands:
                if op.type == X86_OP_IMM and IMAGE_LO <= (op.imm & 0xFFFFFFFF) < IMAGE_HI:
                    text = ascii_at(pe_path, op.imm & 0xFFFFFFFF)
                    if text and '%' in text:
                        recs.append({"kind": "format_string", "identity": f"format:{text}",
                                     "at": f"0x{insn.address:08X}", "import": nm, "format": text,
                                     "argument_roles": format_roles(text),
                                     "evidence": f"{nm} called with {text!r} (pushed at 0x{prev.address:08X})"})

    # 7. the function's own convention and argument count, from its epilogue
    argc = None
    if ret_form and ' ' in ret_form:
        try:
            argc = int(ret_form.split()[1]) // 4
        except ValueError:
            argc = None
    recs.append({"kind": "signature", "identity": f"function:0x{va:08X}",
                 "ret_form": ret_form, "argc": argc,
                 "convention": ("stdcall" if argc is not None else "cdecl"),
                 "evidence": f"the function ends with `{ret_form}`"})

    # 4. rev.ng's recovered types, where the provider was run
    if revng_dir:
        rev = Path(revng_dir) / f'0x{va:08X}.c'
        if rev.is_file():
            import re
            text = rev.read_text(errors='replace')
            # rev.ng addresses memory as `&segment_N + off`. The segment is the
            # section's base, so the offset becomes a VA here and the record
            # joins the same identity as the byte-level global evidence - two
            # views of one thing must not become two identities.
            seg_base = data_section_base(pe_path)
            seen = set()
            for m in re.finditer(r'\*\(generic(\d+)_t \*\)[^=]*?\+\s*(0x[0-9A-Fa-f]+|\d+)', text):
                width = int(m.group(1))
                off = int(m.group(2), 0)
                target = (seg_base + off) & ~3 if seg_base else None
                indent = f"global:0x{target:08X}" if target else f"field:revng:+0x{off:X}"
                key = (width, indent)
                if key in seen:
                    continue
                seen.add(key)
                recs.append({"kind": "field_access", "identity": indent,
                             "width": width,
                             "offset": f"0x{off:X}",
                             "va": f"0x{target:08X}" if target else None,
                             "evidence": f"rev.ng reads a generic{width}_t at "
                                         + (f"0x{target:08X}" if target else f"base+0x{off:X}")})
    return recs, insns, ret_form


def data_section_base(pe_path):
    """The VA of .data: the base rev.ng's `&segment_2` stands for."""
    base, secs = pe_sections(pe_path)
    for name, va, vsize, roff, rsize in secs:
        if name == '.data':
            return va
    return None


def format_roles(fmt):
    """What each conversion says about its argument's type."""
    roles = []
    i = 0
    while i < len(fmt):
        if fmt[i] != '%':
            i += 1
            continue
        i += 1
        if i < len(fmt) and fmt[i] == '%':
            i += 1
            continue
        mods = ''
        while i < len(fmt) and fmt[i] in '-+ #0123456789.*hlL':
            mods += fmt[i]
            i += 1
        if i >= len(fmt):
            break
        conv = fmt[i]
        kind = {'s': 'string', 'c': 'char', 'd': 'integer', 'i': 'integer', 'u': 'unsigned',
                'x': 'unsigned', 'X': 'unsigned', 'p': 'pointer', 'f': 'float',
                'e': 'float', 'g': 'float'}.get(conv, 'unknown')
        roles.append({"conversion": f"%{mods}{conv}", "role": kind})
        i += 1
    return roles


def collect(pe_path, addresses, revng_dir=None):
    """The evidence for a set of functions, identities shared across them."""
    imports = parse_imports(pe_path)
    per_func, identities = {}, {}
    for va in addresses:
        recs, insns, ret_form = evidence_for_function(pe_path, va, imports, revng_dir)
        per_func[f'0x{va:08X}'] = {"records": recs, "instructions": len(insns), "ret_form": ret_form}
        for r in recs:
            identities.setdefault(r["identity"], []).append({"address": f'0x{va:08X}', **r})
    counts = {"functions": len(addresses), "records": sum(len(v["records"]) for v in per_func.values()),
              "identities": len(identities)}
    return {"pe": str(pe_path), "imports_parsed": len(imports),
            "counts": counts, "functions": per_func, "identities": identities}


def main(argv=None):
    ap = argparse.ArgumentParser(description='what the bytes say a function is, before naming')
    ap.add_argument('--pe', required=True, help='the original PE')
    ap.add_argument('--address', action='append', default=[], help='a function VA (repeatable)')
    ap.add_argument('--revng', help='the provider directory for this game (highlevel/<game>/revng)')
    ap.add_argument('--json', help='write the evidence here')
    ap.add_argument('--identities', action='store_true', help='also print the shared identity table')
    args = ap.parse_args(argv)
    addresses = [int(a, 0) for a in args.address]
    if not addresses:
        ap.error('at least one --address is needed')
    data = collect(args.pe, addresses, args.revng)
    for va, fn in data["functions"].items():
        print(f'{va}: {fn["instructions"]} instruction(s), ends with `{fn["ret_form"]}`, '
              f'{len(fn["records"])} evidence record(s)')
        for r in fn["records"]:
            print(f'    {r["kind"]:<18} {r["evidence"]}')
    print(f'{data["counts"]["records"]} record(s) over {data["counts"]["identities"]} identity(ies)')
    if args.identities:
        for ident, items in sorted(data["identities"].items()):
            print(f'  {ident}: {len(items)} record(s) from {sorted({i["address"] for i in items})}')
    if args.json:
        Path(args.json).write_text(json.dumps(data, indent=1))
        print(f'  evidence: {args.json}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
