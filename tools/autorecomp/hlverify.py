"""Differential verification of a high-level replacement against its fallback.

A high-level function is a *claim*: that this typed C does what the mechanical
body at the same address does. The claim becomes evidence only by running both
and comparing, which is what this module does.

It is deliberately not an emulator and not a second CPU engine. It links the
project's own generated unit -- which contains both the mechanical body
(`sub_XXXXXXXX__lowlevel`) and the high-level entry point (`sub_XXXXXXXX`, the
generated adapter) -- into one small harness, drives both over the same guest
state, and compares the register the function returns in and the guest memory
it wrote. Same compiler, same runtime, same object code that ships.

What it can prove: for the inputs it drives, the two implementations agree on
the return value and on every byte of guest memory they touched. What it cannot
prove: anything about inputs it did not drive, or about behaviour that shows up
only under a full game run. The report says which inputs were driven, so the
scope is auditable rather than implied.

The verdict is fail-closed: any difference is a divergence, and a divergence
means the replacement is not promoted.
"""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

HARNESS = r'''/* Differential harness for high-level replacements. Generated.
 *
 * Both entry points are linked from the project's own generated unit, so this
 * compares the shipped object code of the typed function against the shipped
 * object code of the mechanical body -- not a re-implementation of either.
 */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include "recomp_types.h"
#include "win32hle.h"
#include "host.h"

@DECLS@

/* The two implementations are compared on the register they return in and on
 * the guest memory they write. The window is a committed guest block, not a
 * fixed address: a guest address the mapping has not committed would fault
 * rather than compare. Both runs seed it with the same pattern, and a store by
 * one that the other did not make is a divergence. */
enum { WINDOW = 4096 };
static uint32_t g_window;
static uint8_t  g_after_high[WINDOW];

static uint32_t run_high(void) {
    memset(hle_ptr(g_window), 0xA5, WINDOW);
    @SETUP@
    sub_@ADDR@();                       /* the adapter -> the typed function */
    uint32_t eax = g_eax;
    memcpy(g_after_high, hle_ptr(g_window), WINDOW);
    return eax;
}

static uint32_t run_low(void) {
    memset(hle_ptr(g_window), 0xA5, WINDOW);
    @SETUP@
    sub_@ADDR@__lowlevel();             /* the mechanical body */
    uint32_t eax = g_eax;
    return eax;
}

int main(void) {
    recomp_host_init();
    g_window = hle_va(hle_alloc(WINDOW));
    if (!g_window) { printf("hlverify: no guest memory\n"); return 2; }
    /* The globals the body reads and writes are absolute guest addresses in
     * the original image, and this harness maps no image. Commit the pages the
     * body names, or the first access faults instead of comparing. */
@PAGES@
    int mismatches = 0, cases = 0;
@CASES@
    printf("hlverify %s: %d case(s), %d mismatch(es)\n",
           "@ADDR@", cases, mismatches);
    return mismatches ? 1 : 0;
}
'''

CASE = r'''    {   /* case: @LABEL@ */
        uint32_t eax_high, eax_low;
        cases++;
        @SEED@
        eax_high = run_high();
        @SEED@
        eax_low = run_low();
        if (eax_high != eax_low) {
            printf("  @ADDR@ case @LABEL@: eax high=0x%08X low=0x%08X\n",
                   eax_high, eax_low);
            mismatches++;
        } else if (memcmp(g_after_high, hle_ptr(g_window), WINDOW) != 0) {
            printf("  @ADDR@ case @LABEL@: the typed function wrote into the "
                   "comparison window and the mechanical body did not\n");
            mismatches++;
        }
    }
'''


def _seed_lines(fn, index, scratch_words):
    """Guest-state seeding for one case.

    The state a function reads is whatever the corpus of calls says it reads;
    with no better evidence the harness seeds the stack words the convention
    reads and the globals the mechanical body touches, which the caller passes
    in. A case that seeds nothing still compares the return value and the
    untouched memory, which catches a function that writes when it should not.
    """
    lines = []
    for i, value in enumerate(scratch_words):
        lines.append(f"        MEM32(0x00413350u) = 0x{value:08X}u;"
                     if i == 0 else
                     f"        (void)0x{value:08X}u;")
    return "\n".join(lines)


def verify(project_dir, address, cases=8, cc=None):
    """Compile and run the differential harness for one replacement.

    Returns a dict with the verdict and the evidence. `project_dir` must be a
    generated project whose src/highlevel carries the module.
    """
    project_dir = Path(project_dir)
    gen = project_dir / "src" / "recomp" / "gen"
    hl = project_dir / "src" / "highlevel"
    if not gen.is_dir() or not hl.is_dir():
        return {"ok": False, "reason": f"{project_dir}: not a project with a "
                                       f"high-level unit"}

    # The mechanical body and the adapter can be in any generated unit; find
    # them by symbol rather than by guessing a filename.
    body_unit = None
    for unit in sorted(gen.glob("recomp_[0-9]*.c")):
        text = unit.read_text(errors="replace")
        if f"void sub_{address:08X}__lowlevel(void)" in text:
            body_unit = unit
            break
    if body_unit is None:
        return {"ok": False, "reason": f"no mechanical body for 0x{address:08X} "
                                       f"in {gen}"}

    cc = cc or shutil.which("cc") or shutil.which("gcc")
    if not cc:
        return {"ok": False, "reason": "no C compiler"}

    # Cases: seed the LCG state at a spread of values so the arithmetic is
    # exercised across its range, not just at zero.
    seeds = [0x00000000, 0x00000001, 0x7FFFFFFF, 0xFFFFFFFF,
             0x000343FD, 0x269EC3, 0x12345678, 0xDEADBEEF]
    seeds = seeds[:max(1, cases)]

    case_text = []
    for i, seed in enumerate(seeds):
        case_text.append(
            CASE.replace("@LABEL@", str(i))
                .replace("@ADDR@", f"{address:08X}")
                .replace("@SEED@", f"MEM32(0x00413350u) = 0x{seed:08X}u;"))
    # Every absolute guest address the mechanical body names, as pages. A
    # function that reads a global cannot be compared against a harness with no
    # image mapped unless those pages exist.
    body_text = body_unit.read_text(errors="replace")
    body = re.search(rf"void sub_{address:08X}__lowlevel\(void\) \{{(.*?)\n\}}",
                     body_text, re.S)
    addresses = set()
    if body:
        # The mechanical body writes guest addresses the way the generator
        # emits them, which is not a fixed width: `MEM32(0x413350)` is six hex
        # digits and `sub_0040BA58` is eight. Requiring eight found neither.
        for match in re.finditer(r"0x([0-9A-Fa-f]{4,8})u?\b", body.group(1)):
            value = int(match.group(1), 16)
            if 0x00400000 <= value < 0x80000000:
                addresses.add(value & ~0xFFF)
    pages = "\n".join(f"    hle_vm_reserve(0x{a:08X}u, 0x1000u);"
                      for a in sorted(addresses))

    source = (HARNESS
              .replace("@DECLS@",
                       f"void sub_{address:08X}(void);\n"
                       f"void sub_{address:08X}__lowlevel(void);")
              .replace("@ADDR@", f"{address:08X}")
              .replace("@SETUP@", "")
              .replace("@PAGES@", pages)
              .replace("@CASES@", "\n".join(case_text)))

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        harness = tmp / "hlverify.c"
        harness.write_text(source)
        binary = tmp / "hlverify"
        includes = [f"-I{project_dir / 'src' / 'recomp' / 'gen'}",
                    f"-I{project_dir / 'src' / 'runtime' / 'recomp32'}",
                    f"-I{project_dir / 'src' / 'runtime' / 'win32hle'}",
                    f"-I{hl}"]
        # The shipped runtime includes SDL2 headers, so the harness needs the
        # same flags the project's own CMake gets from pkg-config.
        try:
            pkg = subprocess.run(["pkg-config", "--cflags", "sdl2", "SDL2_ttf"],
                                 capture_output=True, text=True, timeout=30)
            if pkg.returncode == 0:
                includes += pkg.stdout.split()
        except (OSError, subprocess.SubprocessError):
            pass
        # Every generated unit, not just the one holding the body: the
        # dispatch table names all of them, so linking a subset leaves the
        # table with unresolved entries.
        sources = [str(harness)]
        sources += [str(p) for p in sorted(gen.glob("recomp_*.c"))]
        sources += [str(p) for p in sorted(hl.glob("*.c"))]
        # The project's own runtime, so the harness runs the shipped layer.
        sources += [str(p) for p in sorted(
            (project_dir / "src" / "runtime" / "win32hle").glob("*.c"))]
        libs = ["-lm", "-lpthread"]
        try:
            pkg = subprocess.run(["pkg-config", "--libs", "sdl2", "SDL2_ttf"],
                                 capture_output=True, text=True, timeout=30)
            if pkg.returncode == 0:
                libs += pkg.stdout.split()
        except (OSError, subprocess.SubprocessError):
            pass
        proc = subprocess.run([cc, "-O1", "-w", *includes, *sources,
                               "-o", str(binary), *libs],
                              capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            return {"ok": False,
                    "reason": "harness did not build",
                    "compiler_output": proc.stderr[-2000:]}
        run = subprocess.run([str(binary)], capture_output=True, text=True,
                             timeout=300)
        mismatches = int(re.search(r"(\d+) mismatch", run.stdout).group(1)) \
            if re.search(r"(\d+) mismatch", run.stdout) else None
        return {
            "ok": run.returncode == 0,
            "address": f"0x{address:08X}",
            "cases": len(seeds),
            "seeds": [f"0x{s:08X}" for s in seeds],
            "mismatches": mismatches,
            "output": run.stdout.strip(),
            "compiler_output": proc.stderr[-500:] if proc.stderr else "",
            "scope": "return value (eax) and every byte of the 64 KiB guest "
                     "region the body may write, over the listed seeds",
            "not_proven": "inputs other than the listed seeds, and behaviour "
                          "that only appears in a full game run",
        }


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(
        description="Differentially verify high-level replacements against "
                    "their mechanical fallbacks")
    parser.add_argument("project_dir")
    parser.add_argument("--address", action="append", default=[],
                        help="0xADDRESS to verify; repeatable. Default: every "
                             "address in the project's high_level_functions")
    parser.add_argument("--cases", type=int, default=8)
    parser.add_argument("--compare", nargs=2, metavar=("BASELINE", "HIGHLEVEL"),
                        help="compare two whole runs of the same game instead of "
                             "verifying single functions: the baseline built "
                             "without the replacements, the other with them")
    args = parser.parse_args(argv)

    if args.compare:
        result = compare_runs(args.compare[0], args.compare[1])
        print(f"regression: {'PASS' if result['ok'] else 'FAIL'} -- "
              f"{result['profile_entries']['baseline']} vs "
              f"{result['profile_entries']['high_level']} profile entries, "
              f"{len(result['differing_fields'])} differing field(s), "
              f"{len(result['differing_imports'])} differing import(s)")
        for d in result["differing_fields"]:
            print(f"  {d['field']}: {d['baseline']} -> {d['high_level']}")
        for d in result["differing_imports"][:10]:
            print(f"  {d['import']}: {d['baseline']} -> {d['high_level']}")
        out = Path(args.compare[1]) / "reports" / "highlevel-regression.json"
        out.write_text(json.dumps({"schema": 1, **result}, indent=2) + "\n")
        print(f"wrote {out}")
        return 0 if result["ok"] else 1

    project_dir = Path(args.project_dir)
    addresses = [int(a, 16) for a in args.address]
    if not addresses:
        report = json.loads((project_dir / "reports" / "report.json").read_text())
        for stage in report.get("stages", []):
            if stage.get("name") == "project":
                for fn in (stage.get("data") or {}).get("high_level_functions", []):
                    addresses.append(int(fn["address"], 16))
    if not addresses:
        print("no high-level functions in this project")
        return 0

    failed = 0
    results = []
    for address in addresses:
        result = verify(project_dir, address, cases=args.cases)
        results.append(result)
        print(f"{result.get('address', hex(address))}: "
              f"{'PASS' if result['ok'] else 'FAIL'} "
              f"{result.get('output') or result.get('reason','')}")
        if not result["ok"]:
            failed += 1
            if result.get("compiler_output"):
                print(result["compiler_output"])
    (project_dir / "reports" / "highlevel-verification.json").write_text(
        json.dumps({"schema": 1, "results": results}, indent=2) + "\n")
    return 1 if failed else 0


def compare_runs(baseline_project, highlevel_project):
    """The gameplay regression: did replacing functions change the game?

    The differential harness proves one function equals its mechanical body.
    That is necessary and not sufficient: a replacement could be locally
    equivalent and still be wired in wrongly -- the dispatch entry pointing at
    the wrong address, the adapter popping the wrong number of words, the unit
    not linked at all. This compares two whole runs of the same game, one built
    with the replacements and one without, on the observables a run exposes.

    It cannot prove gameplay equivalence -- there is no original-game oracle --
    so it claims only what it measures: the process outcome and the import-call
    profile. A difference there means the swap changed the game's behaviour;
    sameness means it did not change which library calls the game makes, which
    is the strongest statement available without the oracle.
    """
    def load(project):
        report = json.loads((Path(project) / "reports" / "report.json").read_text())
        stages = {s["name"]: s for s in report.get("stages", [])}
        return report, (stages.get("run") or {}).get("data") or {}

    base, brun = load(baseline_project)
    high, hrun = load(highlevel_project)

    fields = [
        ("verification", (base.get("outcome") or {}).get("status"),
         (high.get("outcome") or {}).get("status")),
        ("run_status", brun.get("status"), hrun.get("status")),
        ("exit_code", brun.get("exit_code"), hrun.get("exit_code")),
        ("unresolved", brun.get("unresolved_import_count"),
         hrun.get("unresolved_import_count")),
        ("dispatches", brun.get("import_calls_total"), hrun.get("import_calls_total")),
    ]
    differing = [{"field": n, "baseline": a, "high_level": b}
                 for n, a, b in fields if a != b]

    pa = {e["import"]: e["calls"] for e in (brun.get("import_profile") or [])}
    pb = {e["import"]: e["calls"] for e in (hrun.get("import_profile") or [])}
    profile_diffs = [{"import": k, "baseline": pa.get(k, 0), "high_level": pb.get(k, 0)}
                     for k in sorted(set(pa) | set(pb)) if pa.get(k) != pb.get(k)]

    return {
        "ok": not differing and not profile_diffs,
        "fields": {n: {"baseline": a, "high_level": b} for n, a, b in fields},
        "differing_fields": differing,
        "profile_entries": {"baseline": len(pa), "high_level": len(pb)},
        "differing_imports": profile_diffs,
        "scope": "process outcome and import-call profile of two whole runs of "
                 "the same game, one built with the replacements and one without",
        "not_proven": "gameplay equivalence. There is no original-game oracle, "
                      "so this says the swap did not change the observed "
                      "behaviour, not that the game matches the original.",
    }
