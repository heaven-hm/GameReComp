"""Name a function from its evidence, and never from what it probably means.

Step 2 of the renaming pass. `rename_evidence.py` produces evidence; this turns
evidence into identifiers, under one rule:

    a name may describe what was OBSERVED, and must never state what it MEANS.

Every word in a generated name must be traceable to an evidence record. A name
that cannot be traced does not get generated - the identifier keeps its
mechanical name and the reason is recorded. That is why there are three counts
and not two:

  named_semantic    the evidence establishes meaning (calls ExitProcess ->
                    `exit_process`; the constants are the documented MSVC LCG
                    pair -> `msvc_rand_next`)
  named_structured  the evidence establishes shape or identity only
                    (`store_block_413BE0`): a reader learns what it looks like,
                    not what it is for
  no_evidence       stays mechanical, with the reason in a histogram

A structured name counted with a semantic one would let a pass look successful by
renaming everything, so the two are never merged and the headline for readable
source is `named_semantic`.

The dominance rule. A function that calls `MessageBoxA` once in 363 instructions
is not "show_message_box": naming it after one import it happens to call lies
about scope. An import may only name a function when the calls are all imports,
or the only call is an import immediately before a return - both mechanical
tests. Otherwise the import is recorded as evidence with "uses" phrasing and the
name comes from something else.

Every generated name maps back to the records that produced it, so a reader can
recover the evidence from the identifier (the `backmap` in the output).
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.autorecomp.rename_evidence import collect

# Documented facts a name may cite, with where the fact comes from. A pair here
# is not a guess: these constants together are the specific generator, and the
# citation is what makes the word in the name traceable.
DOCUMENTED = [
    {
        "constants": (0x000343FD, 0x00269EC3),
        "word": "msvc_rand",
        "citation": "0x343FD and 0x269EC3 are the multiplier and increment of the "
                    "Microsoft C runtime's linear congruential generator",
        "shape": "step",       # the pair appears in a multiply-then-add over a global
    },
]

VERB_OF = {
    "ExitProcess": "exit_process",
    "CreateFileA": "open_file", "CreateFileW": "open_file",
    "GetFileVersionInfoA": "read_version_info",
    "MessageBoxA": "use_messagebox", "MessageBoxW": "use_messagebox",
    "wsprintfA": "format_string_text",
    "PlaySoundA": "play_sound", "sndPlaySoundA": "play_sound",
    "RegOpenKeyExA": "open_registry_key",
    "GetModuleFileNameA": "get_module_path",
}


def _records(fn):
    return fn["records"]


def _kinds(records):
    return [r["kind"] for r in records]


def _call_counts(records):
    imports = sum(1 for r in records if r["kind"] == "import_call")
    targets = sum(1 for r in records if r["kind"] == "call_target")
    return imports, imports + targets


def _last_is_import_before_ret(records):
    """Is the final call an import? (a wrapper's shape: call, return)"""
    calls = [r for r in records if r["kind"] in ("import_call", "call_target")]
    return bool(calls) and calls[-1]["kind"] == "import_call"


def documented_match(records):
    """A documented constant set that appears together in this function."""
    values = {int(r["value"], 16) for r in records if r["kind"] == "constant"}
    for doc in DOCUMENTED:
        if all(c in values for c in doc["constants"]):
            return doc
    return None


def _block_shape(records):
    """A run of stores of constants into one contiguous block, and nothing read."""
    globals_ = [r for r in records if r["kind"] == "references_global"]
    if not globals_:
        return None
    stores = [r for r in globals_ if r["evidence"].startswith("mov ") and "ptr [" in r["evidence"]
              and ", " in r["evidence"]]
    if not stores or len(stores) != len(globals_):
        return None
    vas = sorted(int(r["va"], 16) for r in stores)
    if vas[-1] - vas[0] > 256:
        return None
    return vas[0]


def name_function(fn, identities, va=None):
    """(name, kind, evidence records) for one function."""
    va = va or fn.get("address", "0x00000000")
    records = _records(fn)
    imports, calls = _call_counts(records)
    doc = documented_match(records)
    block = _block_shape(records)

    # A documented constant set is meaning, not shape: the constants ARE the
    # generator, and the citation says so.
    if doc:
        return (f"{doc['word']}_{doc['shape']}", "named_semantic",
                [r for r in records if r["kind"] == "constant"])

    # An import may name the function only when the import is the behaviour:
    # every call is an import, or the only call is an import before the return.
    dominant = calls > 0 and (imports == calls or (calls == 1 and _last_is_import_before_ret(records)))
    if dominant:
        first = next(r for r in records if r["kind"] == "import_call")
        word = VERB_OF.get(first["name"])
        if word:
            return (f"{word}_{va[2:].lower()}", "named_semantic",
                    [r for r in records if r["kind"] == "import_call"])
        # the import is the behaviour but no documented verb exists for it: a
        # structured name that still says which call it is
        return (f"call_{_slug(first['name'])}_{va[2:].lower()}", "named_structured",
                [r for r in records if r["kind"] == "import_call"])

    # Not dominant: say that it uses the import, do not claim to be it.
    if imports:
        first = next(r for r in records if r["kind"] == "import_call")
        return (f"uses_{_slug(first['name'])}_{va[2:].lower()}", "named_structured",
                [r for r in records if r["kind"] == "import_call"])

    if block is not None:
        return (f"store_block_{block:08x}", "named_structured",
                [r for r in records if r["kind"] == "references_global"])

    strings = [r for r in records if r["kind"] == "references_string"]
    if strings:
        return (f"uses_str_{_slug(strings[0]['value'])[:24]}_{va[2:].lower()}",
                "named_structured", strings)

    if any(r["kind"] == "field_access" for r in records):
        return (f"typed_access_{va[2:].lower()}", "named_structured",
                [r for r in records if r["kind"] == "field_access"])

    return (None, "no_evidence", [])


def _slug(text):
    s = re.sub(r'[^A-Za-z0-9]+', '_', text).strip('_').lower()
    return s or 'unnamed'


def name_globals(identities):
    """One name per global identity, from the union of every function's evidence.

    The union is the point: a global written by one function and compared by
    another is better named with both facts than with either.
    """
    out = {}
    for ident, items in sorted(identities.items()):
        if not ident.startswith("global:"):
            continue
        va = int(ident.split(":")[1], 16)
        kinds = [i["kind"] for i in items]
        names = [i.get("name") for i in items if i["kind"] == "import_call"]
        if "constant" in kinds or any(i["kind"] == "field_access" for i in items):
            out[ident] = {"name": f"state_{va:08x}", "kind": "named_structured",
                          "evidence": items}
        elif names:
            out[ident] = {"name": f"{_slug(names[0])}_data", "kind": "named_structured",
                          "evidence": items}
        else:
            out[ident] = {"name": None, "kind": "no_evidence", "evidence": items}
    return out


def name_all(evidence):
    """Every generated identifier, with its evidence and a reversible backmap."""
    functions, backmap = {}, {}
    counts = {"named_semantic": 0, "named_structured": 0, "no_evidence": 0,
              "no_evidence_reasons": {}}
    for va, fn in evidence["functions"].items():
        name, kind, records = name_function(fn, evidence["identities"], va)
        functions[va] = {"name": name, "kind": kind,
                         "evidence": [r["evidence"] for r in records],
                         "instructions": fn["instructions"]}
        counts[kind] += 1
        if kind == "no_evidence":
            reason = ("only the signature class fired: the function has shape but the "
                      "evidence establishes nothing a name could describe")
            counts["no_evidence_reasons"][reason] = counts["no_evidence_reasons"].get(reason, 0) + 1
        elif name:
            backmap[name] = [r["evidence"] for r in records]
    globals_ = name_globals(evidence["identities"])
    for ident, g in globals_.items():
        counts[g["kind"]] += 1
        if g["name"]:
            backmap[g["name"]] = [r["evidence"] for r in g["evidence"]]
        else:
            reason = "the global's only evidence is that something touches it"
            counts["no_evidence_reasons"][reason] = counts["no_evidence_reasons"].get(reason, 0) + 1
    return {"functions": functions, "globals": globals_, "counts": counts, "backmap": backmap}


def main(argv=None):
    ap = argparse.ArgumentParser(description='name functions from evidence, with the three counts')
    ap.add_argument('--pe', required=True)
    ap.add_argument('--address', action='append', default=[])
    ap.add_argument('--revng')
    ap.add_argument('--json')
    args = ap.parse_args(argv)
    addresses = [int(a, 0) for a in args.address]
    if not addresses:
        ap.error('at least one --address is needed')
    evidence = collect(args.pe, addresses, args.revng)
    named = name_all(evidence)
    for va, fn in named["functions"].items():
        print(f'{va}: {fn["name"] or "(mechanical)"}  [{fn["kind"]}]')
        for e in fn["evidence"]:
            print(f'    evidence: {e}')
    c = named["counts"]
    print(f'\nnamed_semantic {c["named_semantic"]}, named_structured {c["named_structured"]}, '
          f'no_evidence {c["no_evidence"]}')
    if args.json:
        Path(args.json).write_text(json.dumps(named, indent=1))
        print(f'  names: {args.json}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
