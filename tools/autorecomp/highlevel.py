"""The high-level source bridge: typed C compiled into the same executable.

The pipeline's own output is register-transfer C: every function is
`void sub_00401234(void)` reading `A32(0)`, writing `g_eax`. That is a faithful
mechanical translation and it is not readable source. This module is the seam
where a *typed*, readable function takes over one original address, while the
mechanical body stays in the build as the visible fallback.

The contract, and why it is shaped this way:

- A high-level function is ordinary C: real parameters, real return type, real
  structs, no register names. It is written by a human or produced by a
  structural provider, and it is the thing a reader is meant to read.
- It cannot be called directly from the guest, because the guest calls through
  the Win32/x86 ABI: arguments on the stack, a return address, `eax` for the
  result. So each replacement gets a generated **adapter** with the exact
  `recomp_func_t` signature (`void(void)`) that unpacks the guest frame with
  the layer's own `A32`/`APTR`/`RET` macros, calls the typed function, and
  returns its result through `eax`.
- The adapter is generated from the declaration in `module.json`, never
  hand-written, so the ABI marshalling is in one place and reviewable. Its
  correctness is the whole point: a wrong argument count or a missing `RET`
  purge drifts the guest stack, which is why the adapter is emitted rather than
  left to each author.

A module lives in a directory:

    highlevel/<name>/module.json
    highlevel/<name>/*.c          (the typed implementations)

`module.json`:

    {
      "schema": 1,
      "game": "ikachan",                     # optional, for the report
      "provider": "hand-written",            # who produced the C
      "functions": [
        {
          "address": "0x00401234",
          "symbol": "player_apply_damage",   # the typed function's name
          "category": "HIGH_LEVEL_UNVERIFIED",
          "convention": "stdcall",
          "args": [ {"name": "player", "type": "Player *"},
                    {"name": "amount", "type": "int"} ],
          "returns": "int",
          "provenance": {
            "evidence": "callers pass a Player* in ecx and the field at +0x1C",
            "verified_by": null
          }
        }
      ]
    }

`category` is one of the pipeline's own four, and only `HIGH_LEVEL_VERIFIED` may
be claimed with evidence attached. Nothing here promotes a function on its own:
`readability.py` and the verification gates decide, and this module only carries
the decision into the build.

Generated per function:

    /* recomp_highlevel.c */
    #include "recomp_types.h"
    #include "highlevel_impl.h"
    void sub_00401234(void) {                  /* the guest ABI entry point */
        Player *player = (Player *)hle_ptr(A32(0));
        int amount = (int)A32(1);
        int r = player_apply_damage(player, amount);
        RET(r, 2);
    }

and the low-level body for that address is emitted under
`sub_00401234__lowlevel` instead of `sub_00401234`, so it is still compiled,
still counted, and still available for comparison -- but it is not what the
dispatch table calls.
"""

import json
import re
from pathlib import Path

CATEGORIES = ("HIGH_LEVEL_VERIFIED", "HIGH_LEVEL_UNVERIFIED",
              "LOW_LEVEL_FALLBACK", "UNRESOLVED")

# The calling conventions this bridge can marshal, and how many stack words each
# one pops after the return address. `thiscall` is stdcall-with-this for the
# 32-bit MSVC ABI a game's own methods use.
CONVENTIONS = {
    "cdecl": "caller",
    "stdcall": "callee",
    "fastcall": "callee",
    "thiscall": "callee",
}

_ADDR_RE = re.compile(r"^0x[0-9A-Fa-f]{1,8}$")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ModuleError(ValueError):
    """A module that cannot be trusted to produce a correct adapter."""


def _addr(value, where):
    if isinstance(value, int):
        value = f"0x{value:08X}"
    if not isinstance(value, str) or not _ADDR_RE.match(value):
        raise ModuleError(f"{where}: address must be 0x-prefixed hex, got {value!r}")
    return int(value, 16)


def _ident(value, where):
    if not isinstance(value, str) or not _IDENT_RE.match(value):
        raise ModuleError(f"{where}: {value!r} is not a C identifier")
    return value


def load_module(path):
    """Read and validate one module directory. Raises ModuleError."""
    path = Path(path)
    manifest = path / "module.json"
    if not manifest.is_file():
        raise ModuleError(f"{path}: no module.json")
    try:
        data = json.loads(manifest.read_text())
    except (OSError, ValueError) as exc:
        raise ModuleError(f"{manifest}: {exc}") from exc

    if data.get("schema") != 1:
        raise ModuleError(f"{manifest}: unsupported schema {data.get('schema')!r}")
    entries = data.get("functions")
    if not isinstance(entries, list) or not entries:
        raise ModuleError(f"{manifest}: 'functions' must be a non-empty list")

    functions = []
    seen = set()
    for i, raw in enumerate(entries):
        where = f"{manifest}: functions[{i}]"
        if not isinstance(raw, dict):
            raise ModuleError(f"{where}: not an object")
        address = _addr(raw.get("address"), where)
        if address in seen:
            raise ModuleError(f"{where}: 0x{address:08X} is declared twice")
        seen.add(address)

        symbol = _ident(raw.get("symbol"), where)
        category = raw.get("category")
        if category not in CATEGORIES:
            raise ModuleError(f"{where}: category must be one of {CATEGORIES}, "
                              f"got {category!r}")
        convention = raw.get("convention", "stdcall")
        if convention not in CONVENTIONS:
            raise ModuleError(f"{where}: convention must be one of "
                              f"{sorted(CONVENTIONS)}, got {convention!r}")

        args = raw.get("args", [])
        if not isinstance(args, list):
            raise ModuleError(f"{where}: 'args' must be a list")
        parsed_args = []
        for j, arg in enumerate(args):
            if not isinstance(arg, dict):
                raise ModuleError(f"{where}: args[{j}] must be an object")
            parsed_args.append({
                "name": _ident(arg.get("name"), f"{where}: args[{j}]"),
                "type": str(arg.get("type") or "int"),
            })

        # A verified claim needs the evidence that justifies it. Without this
        # the category would be a label anyone could type.
        provenance = raw.get("provenance") or {}
        if not isinstance(provenance, dict):
            raise ModuleError(f"{where}: 'provenance' must be an object")
        if category == "HIGH_LEVEL_VERIFIED" and not provenance.get("verified_by"):
            raise ModuleError(
                f"{where}: category HIGH_LEVEL_VERIFIED requires "
                f"provenance.verified_by (what proved it), otherwise it is "
                f"HIGH_LEVEL_UNVERIFIED")

        functions.append({
            "address": address,
            "symbol": symbol,
            "category": category,
            "convention": convention,
            "args": parsed_args,
            "returns": str(raw.get("returns") or "void"),
            "provenance": provenance,
            "low_level_symbol": f"sub_{address:08X}__lowlevel",
            "entry_symbol": f"sub_{address:08X}",
        })

    sources = sorted(p for p in path.glob("*.c"))
    if not sources:
        raise ModuleError(f"{path}: no .c file to compile")
    return {
        "path": path,
        "name": data.get("name") or path.name,
        "game": data.get("game"),
        "provider": data.get("provider") or "unspecified",
        "functions": functions,
        "sources": sources,
        "by_address": {f["address"]: f for f in functions},
    }


def load_modules(root):
    """Every module under a directory, keyed by address. Later modules lose to
    earlier ones on a duplicate address, and the clash is an error."""
    root = Path(root)
    if not root.is_dir():
        return [], {}
    modules = []
    by_address = {}
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or not (entry / "module.json").is_file():
            continue
        module = load_module(entry)
        for fn in module["functions"]:
            other = by_address.get(fn["address"])
            if other is not None:
                raise ModuleError(
                    f"0x{fn['address']:08X} is claimed by both "
                    f"{other['module']} and {module['name']}")
            fn = dict(fn, module=module["name"])
            by_address[fn["address"]] = fn
        modules.append(module)
    return modules, by_address


def adapter_source(fn):
    """The generated guest-ABI entry point for one high-level function.

    The marshalling is mechanical and is the only place the guest ABI appears:
    `A32(i)` reads stack word i, `APTR(i)` turns it into a host pointer into
    guest memory, and RET ends the shim with the callee's own purge. A `cdecl`
    function leaves its arguments for the caller (RETC), everything else pops
    them (RET).
    """
    args = fn["args"]
    conv = fn["convention"]
    lines = [
        f"/* Generated adapter for {fn['symbol']} at 0x{fn['address']:08X}.",
        f" * Convention: {conv}. Do not edit: the marshalling is the ABI. */",
        f"void {fn['entry_symbol']}(void) {{",
    ]
    call_args = []
    for i, arg in enumerate(args):
        ctype = arg["type"]
        name = arg["name"]
        if "*" in ctype:
            lines.append(f"    {ctype} {name} = ({ctype})hle_ptr(A32({i}));")
        else:
            lines.append(f"    {ctype} {name} = ({ctype})A32({i});")
        call_args.append(name)

    call = f"{fn['symbol']}({', '.join(call_args)})"
    argc = len(args)
    if fn["returns"] == "void":
        lines.append(f"    {call};")
        lines.append(f"    RETC(0);" if conv == "cdecl" else f"    RETV({argc});")
    else:
        lines.append(f"    {fn['returns']} result = {call};")
        if conv == "cdecl":
            lines.append("    RETC(result);")
        else:
            lines.append(f"    RET(result, {argc});")
    lines.append("}")
    return "\n".join(lines) + "\n"


def emit_unit(modules, output_dir):
    """Write the high-level unit(s) and return what the build needs.

    One unit file per module, so a module's sources and its adapters stay
    together and a reader can see both halves side by side.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for module in modules:
        lines = [
            "/* High-level source unit: the typed functions and the generated",
            f" * adapters that expose them through the guest ABI.",
            f" * Provider: {module['provider']}. Module: {module['name']}.",
            " * Compiled into the same executable as the low-level fallback; the",
            " * dispatch table points at these entry points. */",
            '/* win32hle.h is where the guest ABI lives: A32/APTR read the',
            ' * stdcall frame, hle_ptr turns a guest address into a host pointer,',
            " * and RET/RETC end the entry point with the callee's own purge. */",
            '#include "win32hle.h"',
            '#include "highlevel_impl.h"',
            "",
        ]
        for fn in module["functions"]:
            lines.append(f"/* 0x{fn['address']:08X}  {fn['category']}  "
                         f"provider={module['provider']} */")
            lines.append(adapter_source(fn))
        path = output_dir / f"recomp_highlevel_{module['name']}.c"
        path.write_text("\n".join(lines))
        written.append(path)
    return written


def header_source(modules):
    """Declarations for every high-level implementation, so the adapters can
    call them and the compiler checks the signature the author wrote."""
    lines = ["/* Declarations for the high-level implementations. Generated. */",
             "#pragma once",
             '#include "recomp_types.h"',
             ""]
    for module in modules:
        lines.append(f"/* module {module['name']} (provider: {module['provider']}) */")
        for fn in module["functions"]:
            params = ", ".join(f"{a['type']} {a['name']}" for a in fn["args"]) or "void"
            lines.append(f"{fn['returns']} {fn['symbol']}({params});")
        lines.append("")
    return "\n".join(lines)


def summary(modules, by_address, functions):
    """Category counts over the whole function set, from the actual build."""
    categories = {c: 0 for c in CATEGORIES}
    replaced = []
    for address, fn in functions.items():
        high = by_address.get(address)
        if high:
            categories[high["category"]] += 1
            replaced.append(high)
        else:
            categories["LOW_LEVEL_FALLBACK"] += 1
    return {
        "source_categories": categories,
        "high_level_functions": sorted(replaced, key=lambda f: f["address"]),
        "high_level_count": len(replaced),
        "function_count": len(functions),
    }
