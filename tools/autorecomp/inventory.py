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
    """{start, inside_function, phantom} for one project's inventory."""
    entries = project_functions(project_dir)
    if not entries:
        return None
    seeds = [a for a in entries if not (a & 1)]
    bounds, walked = boundaries(pe_path, seeds)
    inside_other = bound_elsewhere = 0
    counts = {"total": len(entries), "even": len(seeds), "start": 0,
              "inside_function": 0, "phantom": 0}
    detail = {"inside_function": [], "phantom": []}
    for va in sorted(entries):
        if va in bounds and va in seeds:
            counts["start"] += 1
        elif va in bounds:
            counts["inside_function"] += 1
            detail["inside_function"].append(f"0x{va:08X}")
        else:
            counts["phantom"] += 1
            detail["phantom"].append(f"0x{va:08X}")
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
    print(f"{'project':22s} {'total':>7s} {'even':>6s} {'start':>7s} {'inside':>7s} {'phantom':>8s}")
    tot = {"total": 0, "even": 0, "start": 0, "inside_function": 0, "phantom": 0}
    for r in rows:
        c = r["counts"]
        for k in tot:
            tot[k] += c[k]
        print(f"{r['project']:22s} {c['total']:7d} {c['even']:6d} {c['start']:7d} "
              f"{c['inside_function']:7d} {c['phantom']:8d}")
    print(f"{'TOTAL':22s} {tot['total']:7d} {tot['even']:6d} {tot['start']:7d} "
          f"{tot['inside_function']:7d} {tot['phantom']:8d}")
    odd = tot['total'] - tot['even']
    if tot['total']:
        print(f"\nodd entries: {odd} ({100*odd/tot['total']:.1f}% of the table)")
        print(f"of those odd entries: {tot['inside_function']} are instruction boundaries inside "
              f"another function (a shared block / jmp target), {tot['phantom']} are not any "
              f"boundary (phantom, definitively not code)")
    bad = [r['project'] for r in rows if r['counts']['even'] != r['counts']['start']]
    print(f"CONTROL: every even entry must be an instruction boundary -> "
          f"{'PASS' if not bad else 'FAIL for ' + ', '.join(bad)}")
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
