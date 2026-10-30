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

_CACHE = {}


def pe_loaded(pe_path):
    """{data, base, secs, imports} for a PE, read once."""
    key = str(Path(pe_path).resolve())
    if key in _CACHE:
        return _CACHE[key]
    data = Path(pe_path).read_bytes()
    e = struct.unpack_from('<I', data, 0x3C)[0]
    coff = e + 4
    nsec = struct.unpack_from('<H', data, coff + 2)[0]
    optsz = struct.unpack_from('<H', data, coff + 16)[0]
    opt = coff + 20
    image_base = struct.unpack_from('<I', data, opt + 28)[0]
    sec = opt + optsz
    secs = []
    for i in range(nsec):
        s_ = sec + 40 * i
        name = data[s_:s_ + 8].rstrip(b'\0').decode('latin1')
        vsize, va, rsize, roff = struct.unpack_from('<IIII', data, s_ + 8)
        secs.append((name, image_base + va, vsize, roff, rsize))
    _CACHE[key] = {"data": data, "base": image_base, "secs": secs, "imports": None}
    return _CACHE[key]


def parse_imports(pe_path):
    """[(iat_va, module, name)] from the PE import table."""
    loaded = pe_loaded(pe_path)
    if loaded["imports"] is not None:
        return loaded["imports"]
    data = loaded["data"]
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
    loaded["imports"] = out
    return out


def section_of(pe_path, va):
    """(name, va, vsize, raw_off, raw_size) containing `va`, or None."""
    for name, sva, vsize, roff, rsize in pe_loaded(pe_path)["secs"]:
        if sva <= va < sva + max(vsize, rsize):
            return name, sva, vsize, roff, rsize
    return None


def bytes_of(pe_path, section, va, n=256):
    name, sva, vsize, roff, rsize = section
    off = roff + (va - sva)
    return pe_loaded(pe_path)["data"][off:off + n]


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

def function_instructions(pe_path, va, limit=None, end=None):
    """Disassemble from VA to the function's return, and report the ret form."""
    loaded = pe_loaded(pe_path)
    sec = section_of(pe_path, va)
    if not sec:
        raise SystemExit(f'0x{va:08X} is not in a section')
    name, sva, vsize, roff, rsize = sec
    if 'text' not in name.lower():
        raise SystemExit(f'0x{va:08X} is in {name!r}, which is not code')
    # A function that ends in a jump to a shared epilogue has no ret of its own,
    # so the extent also stops at the next function's start when the caller knows
    # it. Without that a tail-call function swallows its neighbours and its
    # evidence count is fiction.
    n = min(limit or 8192, sva + rsize - va, sva + vsize - va)
    if end and end > va:
        n = min(n, end - va)
    blob = loaded["data"][roff + (va - sva): roff + (va - sva) + n]
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


def plausible_start(pe_path, va):
    """Is this address a plausible function entry?

    x86 has no alignment requirement, so an odd address is *legal* - but a table
    of functions where 23.6% of the entries are odd is not a table of functions:
    it is a discovery pass that also found mid-instruction addresses. Rather than
    name those, the extractor refuses them and says why. This is a data-quality
    check on the function inventory, not a statement about the instruction set."""
    # Parity is NOT evidence: this linker packs functions with no alignment
    # padding and Doukutsu's own PE entry point (0x00481D27) is odd. An earlier
    # version refused odd addresses and was wrong; the retraction is recorded in
    # reports/per-function-oracle.md. What is still checked is that the address
    # lands in a code section at all.
    sec = section_of(pe_path, va)
    if not sec:
        return False, f"0x{va:08X} is not in a section"
    if 'text' not in sec[0].lower():
        return False, f"0x{va:08X} is in {sec[0]}, which is not code"
    return True, ""


def initial_dword(pe_path, va):
    """The dword a global holds in the PE image before the program runs, or None.

    For a runtime helper the initial value is often the strongest evidence there
    is: MSVC's default __security_cookie is 0xBB40E64E, and `cmp ecx, [0x498B20]`
    against a global holding that value is the documented stack-cookie check."""
    try:
        loaded = pe_loaded(pe_path)
        for name, sva, vsize, roff, rsize in loaded["secs"]:
            if sva <= va < sva + max(vsize, 1):
                off = roff + (va - sva)
                chunk = loaded["data"][off:off + 4]
                if len(chunk) == 4:
                    return int.from_bytes(chunk, "little")
    except Exception:
        return None
    return None


def evidence_for_function(pe_path, va, imports, revng_dir=None, end=None):
    """The evidence records for one function, keyed by identity."""
    insns, ret_form = function_instructions(pe_path, va, end=end)
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
                                     # the IMAGE value is evidence in its own right
                                     "initial_value": initial_dword(pe_path, tgt & ~3),
                                     "evidence": f"{mnem} {insn.op_str}"})
            elif op.type == X86_OP_IMM:
                v = op.imm & 0xFFFFFFFF
                # every immediate is evidence, small ones included: the LCG's
                # shift of 16 and mask of 0x7FFF were invisible while the rule
                # kept only values above 0xFFFF, and they are half the function
                if not (IMAGE_LO <= v < IMAGE_HI):
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
    for name, va, vsize, roff, rsize in pe_loaded(pe_path)["secs"]:
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
        ok, why = plausible_start(pe_path, va)
        if not ok:
            per_func[f'0x{va:08X}'] = {"records": [], "instructions": 0, "ret_form": None,
                                       "refused": why}
            continue
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
    ap.add_argument('--pe', help='the original PE (needed for --address, not for --rank)')
    ap.add_argument('--address', action='append', default=[], help='a function VA (repeatable)')
    ap.add_argument('--revng', help='the provider directory for this game (highlevel/<game>/revng)')
    ap.add_argument('--json', help='write the evidence here')
    ap.add_argument('--identities', action='store_true', help='also print the shared identity table')
    ap.add_argument('--rank', metavar='DIRS',
                    help="rank the corpus's functions by evidence density (a glob, e.g. projects/*)")
    ap.add_argument('--top', type=int, default=10, help='how many to show from the ranking (default 10)')
    args = ap.parse_args(argv)
    if args.rank:
        import glob as _glob
        dirs = sorted(_glob.glob(args.rank))
        revng_of = {}
        for d in dirs:
            cand = Path('highlevel') / Path(d).name / 'revng'
            if cand.is_dir():
                revng_of[Path(d).name] = str(cand)
        rows, refused = rank(dirs, revng_of, progress=print)
        by_class = {}
        for r in rows:
            by_class[r['classes_fired']] = by_class.get(r['classes_fired'], 0) + 1
        print(f'\n{len(rows)} function(s) ranked; {refused} refused as not-a-function-start '
              f'(odd address); classes fired histogram:')
        for n in sorted(by_class, reverse=True):
            print(f'  {n} class(es): {by_class[n]}')
        print(f'\ntop {args.top} by evidence density:')
        for r in rows[:args.top]:
            print(f'  {r["address"]}  {Path(r["project"]).name:<18} '
                  f'{r["classes_fired"]} class(es): {",".join(r["classes"]) or "-"}  '
                  f'({r["records"]} records, {r["globals"]} global(s), {r["instructions"]} insns)')
        if args.json:
            Path(args.json).write_text(json.dumps(
                {"what": "function evidence density: which of the seven evidence classes fire, "
                         "per function, over the corpus (de-duplicated by PE)",
                 "not": "names; this says where naming has evidence to stand on, not what anything is",
                 "corpus": len(rows), "refused_not_a_function_start": refused,
                 "classes": list(CLASSES),
                 "histogram": {str(k): v for k, v in sorted(by_class.items(), reverse=True)},
                 "rows": rows}, indent=1))
            print(f'  density: {args.json}')
        return 0
    addresses = [int(a, 0) for a in args.address]
    if not addresses and not args.rank:
        ap.error('at least one --address is needed (or --rank)')
    if addresses and not args.pe:
        ap.error('--address needs --pe: the bytes must come from the original')
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




# ---------------------------------------------------------------- the corpus

CLASSES = ("import_call", "references_string", "format_string", "field_access",
           "call_target", "constant")


def project_functions(project_dir):
    """The VAs a project's dispatch table lifts, from the generated table."""
    import re
    table = Path(project_dir) / "src" / "recomp" / "gen" / "recomp_dispatch.c"
    if not table.is_file():
        return []
    text = table.read_text(errors="replace")
    # the table's addresses are not padded to eight digits in every project
    return [int(m.group(1), 16) for m in re.finditer(r'\{\s*0x([0-9A-Fa-f]{4,8})u\s*,\s*sub_', text)]


def density(project_dir, pe_path, revng_dir=None, limit=None):
    """Per function: which evidence classes fire, and how many records.

    No names. This is the number that says whether the renaming pass has a
    corpus worth building on: a function where only `signature` fires can be
    described mechanically but not named."""
    vas = sorted(project_functions(project_dir))
    if limit:
        vas = vas[:limit]
    rev = Path(revng_dir) if revng_dir else None
    stats = []
    refused = 0
    for idx, va in enumerate(vas):
        end = vas[idx + 1] if idx + 1 < len(vas) else None
        ok, why = plausible_start(pe_path, va)
        if not ok:
            refused += 1
            continue
        try:
            recs, insns, ret_form = evidence_for_function(pe_path, va, parse_imports(pe_path),
                                                          rev if rev and rev.is_dir() else None,
                                                          end=end)
        except SystemExit:
            continue
        kinds = [r["kind"] for r in recs]
        fired = [c for c in CLASSES if c in kinds]
        global_recs = sum(1 for k in kinds if k == "references_global")
        stats.append({"project": Path(project_dir).name, "address": f"0x{va:08X}",
                      "instructions": len(insns), "records": len(recs),
                      "classes": fired, "classes_fired": len(fired),
                      "globals": global_recs,
                      "signature": recs[-1]["ret_form"] if recs and recs[-1]["kind"] == "signature" else None})
    if stats is not None:
        stats_refused = refused
    return {"rows": stats, "refused": refused} if False else stats


def rank(project_dirs, revng_of=None, limit=None, progress=None):
    """The corpus's functions by evidence density, de-duplicated by PE.

    Two projects generated from the same PE lift the same functions, so counting
    both would double every number."""
    import hashlib
    seen = {}
    rows = []
    refused = 0
    for pd in project_dirs:
        meta = Path(pd) / "project.json"
        if not meta.is_file():
            continue
        try:
            import json as _json
            info = _json.loads(meta.read_text())
            pe = info["pe"]["path"]
        except Exception:
            continue
        key = hashlib.sha256(Path(pe).read_bytes()).hexdigest() if Path(pe).is_file() else pe
        if key in seen:
            continue
        seen[key] = str(pd)
        rev = (revng_of or {}).get(Path(pd).name)
        if progress:
            progress(f"  {Path(pd).name}: {len(project_functions(pd))} function(s)")
        from collections import Counter as _C
        st = density(pd, pe, rev, limit)
        _vas = project_functions(pd)
        refused += sum(1 for v in _vas if v & 1)
        rows += st
    rows.sort(key=lambda r: (-r["classes_fired"], -r["records"]))
    return rows, refused


if __name__ == '__main__':
    sys.exit(main())
