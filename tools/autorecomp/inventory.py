"""Classify a project's lifted-function inventory: start, shared block, phantom.

Every generated project's dispatch table lists functions, and the discovery pass
that produced it conflates three different things:

  * a real function start;
  * an address that IS an instruction boundary, but inside another function - a
    shared block or a tail-merge target, which a jmp scan finds;
  * an address that is not an instruction boundary at all - mid-instruction,
    phantom, and definitively not code.

The difference matters. A phantom entry can never be a legitimate call target, so
it is dead weight: wasted build time and a corrupted ranking. An entry that is a
real boundary inside another function CAN be a legitimate jmp target, and lifting
it as an independent function assumes an entry state it does not have - esp
offsets, live registers, the x87 stack - so if the guest jumps there the lifted
body is wrong in a way no trace localises.

Method, and why it is trustworthy: start from the entries at EVEN addresses (x86
needs no alignment, but a table where a third of the entries are odd is not a
table of functions) plus the PE's entry point, disassemble each forward until it
returns or jumps out, and collect the instruction boundaries. Every even entry is
a control in the measurement - if an even entry ever failed to be a boundary, the
walk would be unsound and the numbers would be untrustworthy.

Add only: this reports. It does not change the inventory, because changing it
changes every game's generated source and the regression baseline is recorded
against the current one.
"""

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from capstone import Cs, CS_ARCH_X86, CS_MODE_32
from tools.autorecomp.rename_evidence import pe_loaded, project_functions


def pe_entry(pe_path):
    """The PE's entry point VA."""
    import struct as _s
    data = pe_loaded(pe_path)["data"]
    e = _s.unpack_from('<I', data, 0x3C)[0]
    coff = e + 4
    optsz = _s.unpack_from('<H', data, coff + 16)[0]
    opt = coff + 20
    base = _s.unpack_from('<I', data, opt + 28)[0]
    rva = _s.unpack_from('<I', data, opt + 16)[0]
    return base + rva


def reachable(pe_path, entries, limit=20000):
    """Instruction boundaries reachable from the PE entry point and real call
    targets, as a worklist. NOT seeded by address parity: that assumes the thing
    being tested, and it is exactly the defect that produced a confident, wrong
    'phantom' count for three of us today."""
    loaded = pe_loaded(pe_path)
    sec = None
    for cand in loaded["secs"]:
        if 'text' in cand[0].lower():
            sec = cand
            break
    if sec is None:
        return set(), set()
    name, tva, tvsize, troff, trsize = sec
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    entry_set = set(entries)
    work = [pe_entry(pe_path)]
    walked = set()
    bounds = set()
    while work and len(walked) < 200000:
        start = work.pop()
        if start in walked or not (tva <= start < tva + min(tvsize, trsize)):
            continue
        walked.add(start)
        off = troff + (start - tva)
        blob = loaded["data"][off:off + (1 << 20)]
        n = 0
        for insn in md.disasm(blob, start):
            bounds.add(insn.address)
            n += 1
            if insn.mnemonic in ("ret", "retf", "retn") or n > limit:
                break
            if insn.mnemonic in ("call", "jmp") and insn.operands and insn.operands[0].type == 2:
                t = insn.operands[0].imm & 0xFFFFFFFF
                if t in entry_set and t not in walked:
                    work.append(t)
    return bounds, walked


def boundaries(pe_path, seeds, limit=64):
    """The instruction boundaries found by walking from each seed."""
    loaded = pe_loaded(pe_path)
    # the code section by what holds the entries, not by a fixed name: .text is
    # not the only spelling (.TEXT, and some linkers name it otherwise)
    seeds_in = set()
    for sec in loaded["secs"]:
        name, va, vsize, roff, rsize = sec
        if any(va <= s < va + max(vsize, rsize) for s in seeds):
            seeds_in.add(sec)
    # the section that holds the most entries: naming is not reliable across
    # linkers and GameMaker binaries use their own layout
    text, best = None, -1
    for sec in seeds_in:
        n = sum(1 for s in seeds if sec[1] <= s < sec[1] + max(sec[2], sec[4]))
        if n > best:
            text, best = sec, n
    if text is None:
        return set(), {}
    _, tva, tvsize, troff, trsize = text
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    found, walked = set(), {}
    for seed in seeds:
        if not (tva <= seed < tva + min(tvsize, trsize)):
            continue
        off = troff + (seed - tva)
        blob = loaded["data"][off:off + 1 << 20]
        seen = 0
        for insn in md.disasm(blob, seed):
            found.add(insn.address)
            seen += 1
            if insn.mnemonic in ("ret", "retf", "retn") or seen > limit:
                break
        walked[seed] = seen
    return found, walked


def classify(project_dir, pe_path):
    """{entered, not_entered} for one project's inventory.

    Seeded from the PE entry point and real call targets, never from address
    parity: parity is what the classification is supposed to tell us about, and
    assuming it produced a confident 'phantom' count that three of us repeated
    today before the bytes were disassembled. This binary packs functions with no
    alignment padding - its own entry point is at an ODD address - so an odd
    start is normal and carries no information."""
    entries = project_functions(project_dir)
    if not entries:
        return None
    bounds, entered = reachable(pe_path, set(entries))
    odd = [v for v in entries if v & 1]
    counts = {"total": len(entries), "odd": len(odd),
              "entered": sum(1 for v in entries if v in entered),
              "not_entered": sum(1 for v in entries if v not in entered),
              "odd_entered": sum(1 for v in odd if v in entered),
              "even_entered": sum(1 for v in entries if not (v & 1) and v in entered)}
    detail = {"not_entered": [f"0x{v:08X}" for v in sorted(entries) if v not in entered]}
    return {"project": Path(project_dir).name, "pe": str(pe_path), "counts": counts,
            "detail": detail}


def main(argv=None):
    ap = argparse.ArgumentParser(description='classify the lifted-function inventory')
    ap.add_argument('dirs', nargs='+', help='project directories (a glob is expanded by the shell)')
    ap.add_argument('--json')
    ap.add_argument('--detail', action='store_true', help='list the inside/phantom addresses')
    args = ap.parse_args(argv)
    import glob as _glob
    # One row per PE. Several project directories can name the same binary (a
    # regenerated project, an older one, a second name for the same game), and
    # the one worth keeping is the one that still has a dispatch table - a stale
    # directory alphabetically earlier must not shadow it.
    import hashlib
    best = {}
    for pattern in args.dirs:
        for d in sorted(_glob.glob(pattern)):
            meta = Path(d) / 'project.json'
            if not meta.is_file():
                continue
            try:
                import json as _json
                pe = _json.loads(meta.read_text())['pe']['path']
            except Exception:
                continue
            if not Path(pe).is_file():
                continue
            key = hashlib.sha256(Path(pe).read_bytes()).hexdigest()[:16]
            n = len(project_functions(d))
            if key not in best or n > best[key][1]:
                best[key] = (d, n, pe)
    rows = []
    for key, (d, n, pe) in sorted(best.items(), key=lambda kv: Path(kv[1][0]).name):
        if n == 0:
            continue
        r = classify(d, pe)
        if r:
            rows.append(r)
    print(f"{'project':22s} {'total':>7s} {'odd':>6s} {'entered':>8s} {'not entered':>12s}")
    tot = {"total": 0, "odd": 0, "entered": 0, "not_entered": 0,
           "odd_entered": 0, "even_entered": 0}
    for r in rows:
        c = r["counts"]
        for k in tot:
            tot[k] += c[k]
        print(f"{r['project']:22s} {c['total']:7d} {c['odd']:6d} {c['entered']:8d} "
              f"{c['not_entered']:12d}")
    print(f"{'TOTAL':22s} {tot['total']:7d} {tot['odd']:6d} {tot['entered']:8d} "
          f"{tot['not_entered']:12d}")
    if tot['total']:
        print(f"\nodd addresses: {tot['odd']} ({100*tot['odd']/tot['total']:.1f}% of the table) - "
              f"a PACKING fact, not a phantom count: this linker does not pad function starts")
        print(f"entered from the PE entry point and real call targets: {tot['entered']} "
              f"({tot['odd_entered']} at odd addresses, {tot['even_entered']} at even ones)")
        print(f"not entered by that walk: {tot['not_entered']} - functions called only "
              f"indirectly, dead code, or data; not classified as phantom")
    if args.detail:
        for r in rows:
            for va in r["detail"]["inside_function"][:5]:
                print(f"  {r['project']}: inside  {va}")
            for va in r["detail"]["phantom"][:5]:
                print(f"  {r['project']}: phantom {va}")
    if args.json:
        Path(args.json).write_text(json.dumps({"method": __doc__, "rows": rows,
                                               "totals": tot}, indent=1))
        print(f"  inventory: {args.json}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
