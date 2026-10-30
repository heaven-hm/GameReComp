"""Generate typed C for a function from the provider's reading plus our evidence.

The input is what rev.ng recovered (structure, types, expressions) and what the
extractor found (which memory, which constants, which calls). The output is typed
C that uses the runtime's guest accessors and the namer's identifiers - the
readable form, generated from evidence alone.

What this deliberately does NOT do: lift. rev.ng already recovered the structure;
re-deriving it from bytes would produce register-transfer C, which is what the
lifter does and what the renaming pass exists to replace. If the provider's
reading is missing, the generator has nothing to say and says so rather than
emitting a mechanical body with nicer names.

The transformation is textual and reversible in the sense that matters: every
named identifier carries its evidence in the file header, and the generated file
is written BESIDE the project, never dispatched to, so it cannot change what the
rebuilt game runs.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.autorecomp.rename_evidence import pe_loaded, collect
from tools.autorecomp import rename_ast
from tools.autorecomp.rename import name_all

GENERIC = {"generic8_t": "uint8_t", "generic16_t": "uint16_t",
           "generic32_t": "uint32_t", "generic64_t": "uint64_t"}


def data_span(pe_path, base):
    """How far past the data base the image actually reaches.

    An offset into the data segment is bounded by the image, not by a constant.
    Doukutsu's .data runs 0x2F048 bytes, so the old fixed 0x10000 refused ordinary
    members of it as "unbounded" -- 20 closures at the last measurement.
    """
    import struct
    d = Path(pe_path).read_bytes()
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    nsec = struct.unpack_from("<H", d, pe + 6)[0]
    optsz = struct.unpack_from("<H", d, pe + 20)[0]
    image_base = struct.unpack_from("<I", d, pe + 24 + 28)[0]
    sec = pe + 24 + optsz
    far = base
    for i in range(nsec):
        off = sec + 40 * i
        vsz, va, rsz, _raw = struct.unpack_from("<IIII", d, off + 8)
        end = image_base + va + max(vsz, rsz)
        far = max(far, end)
    return max(far - base, 0x10000)


def data_base(pe_path):
    for name, va, vsize, roff, rsize in pe_loaded(pe_path)["secs"]:
        if name == ".data":
            return va
    return None


def _balanced(text, start):
    """The balanced parenthesised expression beginning at `start` (which must be
    '('), and the index just past its ')'. Returns (expr, end)."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return text[start + 1:i], i + 1
    return None, start


def typed_expr(expr, base, width_map):
    """Turn EVERY recovered memory access into a guest accessor call.

    The provider's model is that a pointer is a guest address, so
    `*(generic32_t *)(EXPR)` means "the dword at guest address EXPR" whatever
    EXPR is - a constant, a segment offset, or an address computed from another
    load. `&segment_2 + off` is a constant address and becomes the VA; everything
    else is passed through unchanged, so `MEM32(MEM32(0x49CDA8u) * 20u + 0x49CDB8u)`
    comes out of a doubly-indirect access.

    This replaces the first version, which only handled the constant form and so
    silently left pointer arithmetic as host pointer dereferences - correct for
    the two Ikachan functions and wrong for almost every real one."""
    # 1. the segment-relative constants: they are the base of everything else,
    # and an access rewritten in step 2 can expose another one, so alternate
    seg = re.compile(r"\(\s*generic32_t\s*\)\s*&segment_\d+\s*\+\s*(\d+|0x[0-9A-Fa-f]+)U?LL?")
    for _ in range(4):
        expr = seg.sub(lambda m: f"0x{base + int(m.group(1), 0):08X}u", expr)
    # 2. every `*(genericW_t *)(EXPR)` -> MEMw(EXPR), innermost first
    pat = re.compile(r"\*\(generic(\d+)_t \*\)")
    for _ in range(8):                      # nested accesses need several passes
        changed = False
        for m in list(pat.finditer(expr))[::-1]:
            i = m.end()
            # skip the provider's cast decorations before the expression
            while True:
                j = i
                while j < len(expr) and expr[j] in " \t":
                    j += 1
                cm = re.match(r"\(\s*(?:generic\d+_t|uint\d+_t|int\d+_t)\s*\)", expr[j:])
                if cm:
                    i = j + cm.end()
                elif j < len(expr) and expr[j] == '(':
                    inner, end = _balanced(expr, j)
                    if inner is None:
                        break
                    fn = {8: "MEM8", 16: "MEM16", 32: "MEM32", 64: "MEM64"}.get(int(m.group(1)))
                    if fn is None:
                        break
                    expr = expr[:m.start()] + f"{fn}({inner})" + expr[end:]
                    changed = True
                    break
                else:
                    break
        expr = seg.sub(lambda m: f"0x{base + int(m.group(1), 0):08X}u", expr)
        if not changed:
            break
    return expr


def transform(source, fn_name, base, fn_addr=0):
    """The provider's C for one function, typed and named."""
    text = source
    # ONLY this function's symbol. The first version renamed every
    # function_0x... occurrence, so calls to other lifted functions became calls
    # to the function itself.
    text = re.sub(rf"\bfunction_0x{fn_addr:06x}_Code_x86\b", fn_name, text)
    # memory accesses FIRST: the accessor pattern is written in terms of the
    # provider's genericN_t casts, so replacing the types before it destroys the
    # pattern and leaves bare pointer arithmetic behind
    text = "\n".join(typed_expr(line, base, GENERIC) for line in text.splitlines())
    # the types, after the accesses are gone
    for g, c in GENERIC.items():
        text = re.sub(r'\b' + g + r'\b', c, text)
    # rev.ng's declaration macros are not C
    text = re.sub(r'^_ABI\([^)]*\)\s*\n', '', text, flags=re.M)
    text = re.sub(r"\b_NORETURN\b", "", text)
    # remaining cast noise the accessor call no longer needs
    text = re.sub(r"\(uint32_t\)\s*\(\(uint32_t\)", "(", text)
    # decimal constants the provider printed as ULL become the hex the bytes show
    def hexify(m):
        v = int(m.group(1))
        return f"0x{v:X}u"
    text = re.sub(r"\b(\d{4,})U\b", hexify, text)
    return text


def ungeneratable_reason(body):
    """Why this provider body is outside what the transform can carry, or None.

    This is now a thin pre-check. The AST model is the authority: it synthesizes
    the provider's opaque types, maps its scalar names, rewrites pointers to guest
    VAs and refuses what it cannot express, so the checks that used to live here
    (struct types, pointer variables, provider scalars) would refuse bodies the
    transform now handles - which is exactly the "accepts less than it can"
    failure that the three-number count exposes."""
    return None


def category_for(address, signatures):
    """The manifest category a generated function may claim.

    A call-bearing body can be GENERATED but not CERTIFIED: the oracle drives the
    caller and compares its outputs, while the callee's effect happens inside the
    call - the callee's named addresses are not in the caller's comparison. Until
    the transitive closure is linked AND its named memory unioned (the design in
    docs/VERIFICATION.md), a call-bearing module is HIGH_LEVEL_UNVERIFIED whatever
    its own body would certify as. Presenting a generation count as a coverage win
    is the exact overclaim this project exists to refuse.
    """
    if signatures.get(address, {}).get("calls"):
        return "HIGH_LEVEL_UNVERIFIED"
    return "HIGH_LEVEL_VERIFIED"


def closure_of(address, signatures, cap=256):
    """The caller plus its transitive callees, breadth-first, capped.

    A generated module has to contain everything it calls: a call into a function
    that is not in the module resolves to nothing. The cap protects the harness and
    the diagnosis - a verification that quietly linked 200 functions would be slow
    and would make a divergence hard to localise - and it is recorded in the result
    either way.
    """
    from collections import deque
    seen, order, q = {address}, [address], deque([address])
    capped = False
    while q:
        cur = q.popleft()
        for c in signatures.get(cur, {}).get("calls", []):
            if c not in signatures or c in seen:
                continue
            if len(seen) >= cap:
                capped = True
                continue
            seen.add(c)
            order.append(c)
            q.append(c)
    return order, capped


def generate_closure(pe_path, address, revng_dir, names, evidence, signatures,
                     frames=True, cap=256):
    """Every function in the caller's closure, generated as one module.

    Returns ([(va, text)], reason) - all of them or none, because a module that
    contains its caller but not a callee does not compile and would be a worse
    artifact than a refusal.
    """
    order, capped = closure_of(address, signatures, cap)
    if capped:
        return [], f"the closure exceeds the {cap}-function cap; refusing rather than " \
                   f"linking a partial module"
    names_for_module = dict(names.get("functions", {}))
    callee_names = {}
    for va in order:
        fn = names_for_module.get(f'0x{va:08X}')
        if fn and fn.get("name"):
            callee_names[va] = fn["name"]
        else:
            callee_names[va] = f"function_{va:08X}"
    _span = data_span(pe_path, data_base(pe_path))
    out = []
    for va in order:
        text, why = generate(pe_path, va, revng_dir, names, evidence, frames=frames,
                             extra={"signatures": signatures, "callee_names": callee_names,
                                    "segment_span": _span})
        if text is None:
            return [], f"0x{va:08X} (in the closure of 0x{address:08X}): {why}"
        out.append((va, text))
    return out, {"size": len(order), "capped": False}


_REGISTERED_CACHE = {}


def _registered_shim_names():
    """Every import name the runtime's shim tables answer for.

    Read from the tables themselves (`{ "LeaveCriticalSection", k_... }`), because
    the question this answers is precisely "does the runtime know this name at all",
    and the table is the only place that is true.
    """
    if "names" in _REGISTERED_CACHE:
        return _REGISTERED_CACHE["names"]
    import re as _re
    root = Path(__file__).resolve().parents[2] / "runtime" / "win32hle"
    found = set()
    for f in root.glob("*.c"):
        try:
            found |= set(_re.findall(r'\{\s*"([A-Za-z_0-9]+)"\s*,', f.read_text(errors="replace")))
        except OSError:
            continue
    _REGISTERED_CACHE["names"] = found
    return found


_HLE_ENTRY_CACHE = {}


def _hle_entry_points():
    """Every `hle_` symbol the runtime actually provides.

    Read from the runtime's own headers rather than assumed, because the failure
    being prevented is exactly an assumed entry point: a generated module that
    calls `hle_LeaveCriticalSection` when the runtime only has a table-dispatched
    `k_LeaveCriticalSection` links to nothing.
    """
    if "names" in _HLE_ENTRY_CACHE:
        return _HLE_ENTRY_CACHE["names"]
    import re as _re
    root = Path(__file__).resolve().parents[2] / "runtime" / "win32hle"
    found = set()
    for pat in ("*.h", "*.c"):
        for f in root.glob(pat):
            try:
                found |= set(_re.findall(r"\b(hle_[A-Za-z0-9_]+)\s*\(", f.read_text(errors="replace")))
            except OSError:
                continue
    _HLE_ENTRY_CACHE["names"] = found
    return found


def generate(pe_path, address, revng_dir, names, evidence, frames=False, extra=None):
    """(file text, name) for one function, or (None, reason)."""
    rev = Path(revng_dir) / f'0x{address:08X}.c'
    if not rev.is_file():
        return None, f"the provider has no reading for 0x{address:08X} ({rev} missing)"
    fn = names["functions"].get(f'0x{address:08X}')
    if not fn:
        return None, "no evidence record for this address"
    if not fn["name"]:
        # the namer found nothing to call it by, but the body is still the
        # deliverable: a mechanical name is honest, and the certification is
        # what the module's value rests on
        fn = dict(fn, name=f"function_{address:08X}", mechanical_name=True)
    base = data_base(pe_path)
    if base is None:
        return None, "the PE has no .data section"
    body = rev.read_text(errors="replace")
    why = ungeneratable_reason(body)
    if why is None:
        # A provider prototype with fewer parameters than the function READS
        # registers before writing them has lost inputs its body cannot use: the
        # body is wrong even when it compiles. This is the rule the Lead set -
        # a provider signature and our own bytes disagreeing is a refusal, never a
        # reconciliation. It catches the register-convention cases the oracle found
        # (0x00489227 reads ebx) and not every constant-folded one; the oracle's
        # eax-variation is the other half of the measurement.
        try:
            from tools.autorecomp.rename_evidence import entry_registers
            regs = entry_registers(pe_path, address) or []
        except Exception:
            regs = []
        if regs:
            # the provider's parameter count is in the AST output, if we have it
            argc = None
            try:
                import re as _r
                m = _r.search(r"\b\w+\s*\(([^)]*)\)\s*\{", body)
                params = (m.group(1).strip() if m else "")
                argc = 0 if params in ("", "void") else len(params.split(","))
            except Exception:
                argc = None
            if argc is not None and argc < len(regs):
                return None, (f"the provider's prototype takes {argc} argument(s) but the "
                              f"original reads {', '.join(regs)} before writing them: the body "
                              f"has lost its inputs")
    if why:
        return None, why
    # The AST model rewrites every recovered pointer to a guest VA and every
    # deref to a MEMn(); it refuses what it cannot express, which is why the
    # text transform below is only a fallback for bodies without pointers.
    ast_source, ast_notes = None, []
    try:
        _extra = extra or {}
        ast_source, ast_notes = rename_ast.rewrite(
            body, base, own_va=address, frames=frames,
            signatures=_extra.get("signatures"), callee_names=_extra.get("callee_names"),
            segment_span=_extra.get("segment_span") or 0x10000)
    except rename_ast.Unsupportable as e:
        return None, f"the AST model refuses this body: {e}"
    # the provider's comment block stays as provenance, then our header
    typed = ast_source
    # the adapter call the AST output leaves under the provider's name
    typed = typed.replace(f"function_0x{address:06x}_Code_x86", fn["name"])
    # import calls: the provider writes LeaveCriticalSection(), the runtime's
    # shim is hle_LeaveCriticalSection(). The old text transform did this through
    # the evidence; the AST path did not, so those modules failed to compile.
    for rec in evidence["functions"][f'0x{address:08X}']["records"]:
        if rec["kind"] != "import_call":
            continue
        name = rec.get("name") or ""
        if not name or not re.match(r"^[A-Za-z_]\w*$", name):
            continue
        # The substitution below turns `LeaveCriticalSection()` into
        # `hle_LeaveCriticalSection()`. That is only valid if such an entry point
        # EXISTS: the runtime dispatches some shims through its table instead,
        # with a `static void k_LeaveCriticalSection(void)` and no `hle_` wrapper.
        # Substituting blindly INVENTS a name, and the module then fails to link --
        # a call to something that does not exist, one level worse than the
        # eighteen undeclared shims found the same way. Fail here instead, where
        # the reason can be said.
        if name not in _hle_entry_points():
            # Two different refusals, because they need two different fixes. A name
            # the runtime DOES register is reachable through hle_resolve() and
            # recomp_lookup_import() -- the module has to dispatch through the table
            # rather than by name, which is a defined piece of work. A name it does
            # not register at all cannot be called even that way, and is a runtime
            # gap. Reporting one message for both would send the next person to the
            # wrong place.
            if name in _registered_shim_names():
                return None, (f"0x{address:08X} calls the import {name}, which the runtime "
                              f"registers in its shim table but exposes as no hle_{name} "
                              f"entry point -- the module must dispatch through "
                              f"hle_resolve()/recomp_lookup_import(), not by name")
            return None, (f"0x{address:08X} calls the import {name}, which the runtime does "
                          f"not register at all -- no name to dispatch, and no hle_{name}")
        typed = re.sub(rf"(?<![A-Za-z_0-9.h>]){re.escape(name)}\s*\(",
                       f"hle_{name}(", typed)
    local_names = {ident: g["name"] for ident, g in names["globals"].items() if g["name"]}
    typed, decls, refusals = fuse_memory(typed, local_names)
    for note in ast_notes:
        header_note = note
    header = [
        "/* generated by tools/autorecomp/rename_gen.py -- evidence only, never dispatched */",
        "#include <stdint.h>",
        "#include \"win32hle.h\"   /* MEM8/MEM16/MEM32: the guest memory, not a host buffer */",
        # The rev.ng helpers this body calls, DEFINED rather than assumed. An
        # undefined call is the implicit-int defect class exactly - it compiles,
        # takes the wrong arguments, and fails somewhere else entirely. Emitting
        # the definition makes the module self-contained and shows a reader what
        # the provider's intrinsic means instead of asking them to trust a name.
        *([rename_ast.intrinsic_definitions(body)] if rename_ast.intrinsic_definitions(body) else []),
        f'/* address: 0x{address:08X}   symbol: {fn["name"]}   [{fn["kind"]}] */',
        "/* evidence for every identifier:",
        *[f" *   {e}" for e in fn["evidence"][:12]],
        " * names:",
    ]
    touched = {r.get("va") for r in evidence["functions"][f'0x{address:08X}']["records"]
               if r["kind"] == "references_global"}
    for ident, g in sorted(names["globals"].items()):
        if g["name"] and ident.split(":")[1].upper().replace("0X", "0x") in (touched or set()):
            header.append(f' *   {g["name"]}: {ident}')
    if refusals:
        header.insert(-1, f" * fusion: {len(refusals)} access(es) left fused-out: " + refusals[0])
    header.append(" */")
    # The guest ABI entry point. The typed function is plain C: its result is a
    # host value, and the guest reads eax. The adapter is what marshals one into
    # the other, and it is the symbol the dispatch table calls - which is why it
    # is what the oracle certifies, exactly as it does for the shipped module.
    # The adapter's argument count comes from the provider's own parameter list,
    # not from the function's `ret`: the `ret` form gives the *convention* (a bare
    # `ret` is cdecl, so the caller pops), and a cdecl frame's arguments are on
    # the guest stack at esp+4, esp+8, ...
    argc = 0
    frame_note = None
    for note in ast_notes:
        if note.startswith("parameters: "):
            argc = int(note.split(": ")[1])
        if note.startswith("frame: "):
            frame_note = note
    conv_record = next((r for r in evidence["functions"][f'0x{address:08X}']["records"]
                        if r["kind"] == "signature"), {})
    convention = conv_record.get("convention") or "cdecl"
    returns = "uint32_t" if "return" in typed.split("{", 1)[-1] else "void"
    # the GUEST FRAME is not a guest-ABI argument: the adapter derives it from
    # the guest stack pointer. `frame: <type> <size> bytes` carries the extent.
    frame_size = None
    if frame_note:
        import re as _r
        m = _r.search(r"frame: \S+ (\d+) bytes", frame_note)
        frame_size = int(m.group(1)) if m else None
        argc -= 1
    if argc:
        decls = "\n".join(f"    uint32_t argument_{i} = MEM32(g_esp + {4 + 4 * i}u);"
                          for i in range(argc))
        args = ", ".join(f"argument_{i}" for i in range(argc))
    else:
        decls, args = "", ""
    if frame_size:
        decls = (decls + "\n" if decls else "") + f"    uint32_t frame = g_esp - {frame_size}u;"
        args = (args + ", " if args else "") + "frame"
    call = f"{fn['name']}({args})"
    body = (f"{decls}\n    uint32_t result = {call};\n    RETC(result);" if convention == "cdecl"
            else f"{decls}\n    uint32_t result = {call};\n    RET(result, {argc});")
    if returns == "void":
        body = (f"{decls}\n    {call};\n    RETC(0);" if convention == "cdecl"
                else f"{decls}\n    {call};\n    RET(0, {argc});")
    # DECLARE THE CALLEES. A closure links several files; the caller's calls need a
    # prototype in its own translation unit or C invents an implicit-int one and the
    # frame argument is passed wrong. The declaration comes from the same contract
    # the marshal checked against.
    proto_lines = []
    _sig = (extra or {}).get("signatures") or {}
    _names = (extra or {}).get("callee_names") or {}
    for cva, cname in sorted(_names.items()):
        cs = _sig.get(cva)
        if not cs:
            continue
        parts = []
        for i, prm in enumerate(cs["params"]):
            parts.append(f"uint32_t a{i}")
        if cs.get("returns_struct"):
            parts.append("uint32_t out")
        if cs.get("frame_size"):
            parts.append("uint32_t frame")
        ret = "void" if cs.get("returns_struct") else (
            "void" if cs["ret"] == "void" else rename_ast.c_type(cs["ret"]) or "uint32_t")
        proto_lines.append(f"{ret} {cname}({', '.join(parts) if parts else 'void'});")
    prototypes = ("/* callee prototypes for this closure: the caller must see each contract\n"
                  " * or C invents an implicit-int declaration and the frame is passed wrong */\n"
                  + "\n".join(dict.fromkeys(proto_lines)) + "\n\n") if proto_lines else ""
    adapter = (f"\n/* the guest ABI entry point the dispatch table calls (convention: {convention}, "
               f"{argc} argument(s)) */\nvoid sub_{address:08X}(void) {{\n{body}\n}}\n")
    return "\n".join(header) + "\n\n" + prototypes + typed + adapter, None


def closure_blockers(pe_path, revng_c, header, signatures, layouts, cap=256):
    """Which refusal class blocks multi-function closures, weighted by caller.

    A closure is only as usable as its WORST member, so the ordering metric is
    closures-unblocked-per-fix, not class size. This is the measurement that decides
    the next piece; it found that fixing the largest class (124 struct returns) moved
    multi-function closures from zero to zero.
    """
    import re as _r
    from collections import Counter, defaultdict
    lines = Path(revng_c).read_text(errors="replace").splitlines()
    bodies = {}
    for i, l in enumerate(lines):
        m = _r.match(r"^[A-Za-z_]\w*[ \t]+function_0x([0-9a-f]+)_Code_x86\s*\(", l)
        if not m or not l.rstrip().endswith("{"):
            continue
        va = int(m.group(1), 16)
        depth, out, opened = 0, [], False
        for l2 in lines[i:]:
            out.append(l2)
            depth += l2.count("{") - l2.count("}")
            if "{" in l2:
                opened = True
            if opened and depth <= 0:
                break
        bodies[va] = "\n".join(out)
    callee_names = {va: f"function_{va:08X}" for va in bodies}

    # The measurement has to use the SAME data base and extent the generator uses,
    # or it measures a machine nobody runs. It used to hard-code 0x0049B000 and no
    # span, so an offset into .data was judged against a base 0x4000 away from the
    # real one and a 64 KB limit -- which is why "unbounded offset" stayed the top
    # class through a fix that had already removed it from the generator's path.
    base = data_base(pe_path)
    span = data_span(pe_path, base)

    def generates(va):
        try:
            rename_ast.rewrite(bodies[va], base, own_va=va, layouts=layouts,
                               frames=True, signatures=signatures,
                               callee_names=callee_names, segment_span=span)
            return True, None
        except rename_ast.Unsupportable as e:
            return False, str(e)
        except Exception as e:                       # noqa: BLE001 - an instrument bug, reported
            return False, f"instrument bug: {type(e).__name__}"

    callers = [va for va in bodies if signatures.get(va, {}).get("calls") and generates(va)[0]]
    blocked_by, sole = defaultdict(set), Counter()
    for va in callers:
        order, _ = closure_of(va, signatures, cap)
        bad = []
        for m in order:
            if m == va:
                continue
            ok, why = generates(m)
            if not ok:
                bad.append((m, (why or "").split(":")[-1].strip()[:64]))
        for m, why in bad:
            blocked_by[why].add(va)
        if len(bad) == 1:
            sole[bad[0][0]] += 1
    return {
        "generating_callers": len(callers),
        "by_reason": [{"reason": why, "closures_blocked": len(cs)}
                      for why, cs in sorted(blocked_by.items(), key=lambda kv: -len(kv[1]))],
        "sole_blockers": [{"address": f"0x{m:08X}", "closures": n}
                          for m, n in sole.most_common() if n],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description='generate typed C from evidence, beside the project')
    ap.add_argument('--pe', required=True)
    ap.add_argument('--address', action='append', default=[])
    ap.add_argument('--revng', required=True)
    ap.add_argument('--closure-blockers', action='store_true',
                    help='print and write the min-over-closure blocker table instead of '
                         'generating: which refusal class blocks the most otherwise-'
                         'generating callers, weighted by closure')
    ap.add_argument('--header', help='types-and-globals.h (for --closure-blockers)')
    ap.add_argument('--revng-c', help='the provider .c (for --closure-blockers)')
    ap.add_argument('--json', help='write the blocker table here (for --closure-blockers)')
    ap.add_argument('--out', required=False,
                    help="output directory. It must be OUTSIDE the repository's highlevel/ tree: "
                         "the pipeline scans that tree for modules to dispatch and a generated "
                         "module there would be applied to the game")
    args = ap.parse_args(argv)
    if args.closure_blockers:
        if not (args.revng_c and args.header):
            ap.error('--closure-blockers needs --revng-c and --header')
        layouts = rename_ast.parse_layouts(Path(args.header).read_text(errors="replace"))
        sigs = rename_ast.parse_signatures(Path(args.revng_c).read_text(errors="replace"),
                                           layouts)
        table = closure_blockers(args.pe, args.revng_c, args.header, sigs, layouts)
        print(f"generating call-bearing callers: {table['generating_callers']}")
        print("\nBY CLOSURES BLOCKED (weighted by caller):")
        for r in table["by_reason"]:
            print(f"  {r['closures_blocked']:4d}  {r['reason']}")
        print("\nSOLE BLOCKERS (fix one function, N closures become multi-function):")
        for b in table["sole_blockers"]:
            print(f"  {b['address']}  {b['closures']} caller(s)")
        if args.json:
            Path(args.json).write_text(json.dumps(table, indent=1))
            print(f"\nwritten: {args.json}")
        return 0
    if not args.out:
        ap.error('--out is required unless --closure-blockers is given')
    addresses = [int(a, 0) for a in args.address]
    if not addresses:
        ap.error('at least one --address is needed')
    evidence = collect(args.pe, addresses, args.revng)
    names = name_all(evidence)
    outdir = Path(args.out)
    # The pipeline scans the repository's highlevel/ tree for modules to dispatch.
    # A generated module there would be APPLIED to the game, which is the one
    # thing a beside-the-project artifact must never do.
    repo_highlevel = (Path(__file__).resolve().parents[2] / "highlevel").resolve()
    if repo_highlevel in outdir.resolve().parents or outdir.resolve() == repo_highlevel:
        sys.exit(f'{outdir}: refusing to write inside {repo_highlevel} -- the pipeline scans it '
                 f'for modules to dispatch, and a generated module there would be applied to a '
                 f'game instead of sitting beside it')
    outdir.mkdir(parents=True, exist_ok=True)
    module = {"schema": 1, "provider": "tools/autorecomp/rename_gen.py (evidence only)",
              "note": "Generated BESIDE the project and never dispatched to: it exists so the "
                      "pipeline can be checked, not so a game can run with it.",
              "functions": []}
    for va in addresses:
        text, why = generate(args.pe, va, args.revng, names, evidence,
                             frames=getattr(args, "frames", False),
                             extra={"segment_span": data_span(args.pe, data_base(args.pe))})
        if text is None:
            print(f'0x{va:08X}: NOT GENERATED - {why}')
            continue
        path = outdir / f'0x{va:08X}.c'
        path.write_text(text)
        fn = names["functions"][f'0x{va:08X}']
        module["functions"].append({"address": f'0x{va:08X}', "symbol": fn["name"],
                                    "category": "HIGH_LEVEL_UNVERIFIED",
                                    "evidence": fn["evidence"], "file": path.name,
                                    "not_yet": "the per-function oracle has not been run on this "
                                               "generated function; until it agrees it stays "
                                               "HIGH_LEVEL_UNVERIFIED"})
        print(f'0x{va:08X}: {fn["name"]} -> {path}')
    (outdir / "module.json").write_text(json.dumps(module, indent=1))
    c = names["counts"]
    print(f'\nnamed_semantic {c["named_semantic"]}, named_structured {c["named_structured"]}, '
          f'no_evidence {c["no_evidence"]}')
    return 0




# ------------------------------------------------------------- memory fusion
#
# A reader should not have to hold "the same address three times" in their head.
# The transform fuses repeated guest-memory accesses into a typed local, and the
# conditions are what make it sound rather than usually-right:
#
#   * same address AND same width - different widths are different objects;
#   * no intervening call - a call may write anything, so a read across it must
#     be a fresh load (this is the condition that decides correctness);
#   * no intervening write through an unknown pointer - same reason;
#   * a write to the address updates the local rather than discarding it, so a
#     read after the write reads the new value.

MEM_ACCESS = re.compile(r"MEM(\d+)\(0x([0-9A-Fa-f]+)u\)")
CALL_IN = re.compile(r"\b(?!MEM\d+\b|return\b|if\b|while\b|for\b)([A-Za-z_]\w*)\s*\(")
UNKNOWN_STORE = re.compile(r"\*\s*\([^)]*\)\s*[A-Za-z_]\w*\s*=|MEM\d+\(\s*[^0]")   # a store through a computed address


def _width_type(bits):
    return {8: "uint8_t", 16: "uint16_t", 32: "uint32_t", 64: "uint64_t"}.get(int(bits), "uint32_t")


def fuse_memory(body, local_names=None):
    """(fused body, locals introduced, list of refusals with reasons)."""
    lines = body.splitlines()
    locals_ = {}            # (width, addr) -> {"name": str, "live": bool}
    decls, refusals = [], []
    out = []

    def current(width, addr):
        ent = locals_.get((width, addr))
        return ent["name"] if ent and ent["live"] else None

    def invalidate_all(reason):
        for ent in locals_.values():
            if ent["live"]:
                ent["live"] = False

    # Which widths touch each address: two widths at one address are two
    # objects, and they must not end up sharing a local's name.
    widths_at = {}
    for line in lines:
        for w, a in MEM_ACCESS.findall(line):
            widths_at.setdefault(int(a, 16), set()).add(int(w))

    def local_for(width, addr):
        base = (local_names or {}).get(f"global:0x{addr:08X}", f"mem_{addr:08X}")
        return base if len(widths_at.get(addr, {width})) == 1 else f"{base}_u{width // 8}"

    idx = 0
    for line in lines:
        stripped = line.strip()
        # a call ANYWHERE invalidates every live local, including a bare call
        # statement - a callee may write any address, which is what makes a read
        # across it unsound to fuse
        if stripped and not stripped.rstrip().endswith("{") and MEM_ACCESS.search(stripped) is None:
            bare_calls = [c for c in CALL_IN.findall(stripped)
                          if c not in ("MEM8", "MEM16", "MEM32", "MEM64", "return", "if", "while", "for")]
            if bare_calls:
                invalidate_all("a call may write any address")
                refusals.append(f"line {idx + 1}: a call ({bare_calls[0]}) between accesses, not fused")
                out.append(line)
                idx += 1
                continue
        if not stripped.startswith(("MEM", "return", "if", "while", "for", "}", "{")) and \
           not stripped.startswith(("uint", "void", "int")) and "=" not in stripped:
            out.append(line)
            continue
        # the function's own signature is not a call it makes
        if stripped.rstrip().endswith("{") and "(" in stripped and not MEM_ACCESS.search(stripped):
            out.append(line)
            idx += 1
            continue
        # a call anywhere in the statement invalidates every live local
        calls = [c for c in CALL_IN.findall(stripped) if c not in ("MEM8", "MEM16", "MEM32", "MEM64")]
        if calls:
            invalidate_all("a call may write any address")
            refusals.append(f"line {idx + 1}: a call ({calls[0]}) between accesses, not fused")
            out.append(line)
            idx += 1
            continue
        if UNKNOWN_STORE.search(stripped) and "MEM" not in stripped.split("=")[0]:
            invalidate_all("a store through a computed address may write any address")
        full = stripped
        m = MEM_ACCESS.match(full)
        if m and "=" in full and full.split("=")[0].strip().startswith(f"MEM{m.group(1)}(0x{m.group(2)}u)"):
            # a write to a known address: the local (if any) becomes the new value
            width, addr = int(m.group(1)), int(m.group(2), 16)
            lhs, rhs = full.split("=", 1)
            rhs = rhs.rstrip().rstrip(";")
            name = current(width, addr)
            key = (width, addr)
            if name is None:
                name = local_for(width, addr)
                decls.append(f"    {_width_type(width)} {name} = MEM{width}(0x{addr:08X}u);")
                locals_[key] = {"name": name, "live": True}
            fused_rhs = MEM_ACCESS.sub(lambda mm: name if (int(mm.group(1)) == width and int(mm.group(2), 16) == addr) else mm.group(0), rhs).strip()
            out.append(f"    {name} = {fused_rhs};")
            out.append(f"    MEM{width}(0x{addr:08X}u) = {name};")
            idx += 1
            continue
        if stripped.startswith("return "):
            expr = stripped[len("return "):].rstrip(";")
            used = []
            def sub(mm):
                width, addr = int(mm.group(1)), int(mm.group(2), 16)
                name = current(width, addr)
                if name:
                    used.append((name, width, addr))
                    return name
                used.append((None, width, addr))
                return mm.group(0)
            fused = MEM_ACCESS.sub(sub, expr)
            for name, width, addr in used:
                if name is None:
                    local = local_for(width, addr)
                    decls.append(f"    {_width_type(width)} {local} = MEM{width}(0x{addr:08X}u);")
                    locals_[(width, addr)] = {"name": local, "live": True}
            # rename the fresh loads the substitution left behind into their locals
            for name, width, addr in used:
                if name:
                    continue
                local = local_for(width, addr)
                fused = fused.replace(f"MEM{width}(0x{addr:08X}u)", local, 1)
            out.append(f"    return {fused};")
            idx += 1
            continue
        out.append(line)
        idx += 1

    if not decls:
        return body, [], refusals
    # declarations go after the opening brace of the function
    merged, inserted = [], False
    for line in out:
        merged.append(line)
        if not inserted and "{" in line and line.strip().endswith("{"):
            merged.extend(decls)
            inserted = True
    return "\n".join(merged), decls, refusals


if __name__ == '__main__':
    sys.exit(main())
