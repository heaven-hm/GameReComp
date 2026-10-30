"""rev.ng as the high-level source provider.

Issue #1 makes rev.ng the required primary structural provider, not an optional
extra: it decompiles the original PE independently of the recompiler and gives
typed C, structured control flow and recovered data layout, which is the raw
material for readable source. This module runs it and brings its output back in
a form the rest of the pipeline can use.

Two ways to reach it, in this order:

1. A native install: `REVNG_BIN`, or `revng` on PATH. rev.ng runs on Linux
   x86-64 only, so this is the path on a Linux host.
2. The upstream container `revng/revng` through Docker or Podman, with
   `--platform linux/amd64`. On an Apple Silicon Mac that is the only way to run
   it at all, and it works: the container is emulated, so it is slow, and the
   host has to have a container runtime already.

`REVNG_DOCKER` overrides the runtime command (e.g. `podman`), `REVNG_IMAGE` the
image.

What it produces, measured on the synthetic fixture (i386 PE32, one function
that calls ExitProcess):

    #include "types-and-globals.h"
    #include "helpers.h"
    _ABI(Microsoft_x86_cdecl)
    generic32_t function_0x401000_Code_x86(void) {
        generic32_t var_0 = 0U;
        ExitProcess(0U);
        return 0U;
    }

Note what that is and is not. It is real decompiled C: rev.ng identified the
calling convention, named the function after its address, resolved the import by
name, and recovered a local. It is NOT human-readable source in the sense this
project means -- `generic32_t`, `var_0`, `function_0x401000_Code_x86` are
mechanical names, and turning them into something a person would write is the
deterministic renaming and structuring work that sits on top. rev.ng supplies
the structure; it does not supply the naming.

The output is PTML, rev.ng's marked-up C. `ptml_to_c` strips it to plain C. The
markup is not decoration: it carries per-token source locations back to the
original instructions, which is what a later pass needs to prove that a renamed
identifier still refers to the same code. So the stripped text is what gets
compiled and the markup is kept beside it.
"""

import html
import os
import sys
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

DEFAULT_IMAGE = "revng/revng:latest"
TIMEOUT_SECONDS = 3600


class RevngUnavailable(RuntimeError):
    """rev.ng cannot be reached, with the reason a user can act on."""


def _runtime_command():
    for candidate in (os.environ.get("REVNG_DOCKER"), "docker", "podman"):
        if candidate and shutil.which(candidate):
            return candidate
    return None


def discover():
    """How rev.ng can be reached, or why it cannot.

    Returns a dict rather than raising, because the caller's job is to report
    the reason in the run's evidence -- a pipeline that cannot produce readable
    source must say so specifically, not fail vaguely.
    """
    native = os.environ.get("REVNG_BIN") or shutil.which("revng")
    if native:
        return {"available": True, "kind": "native", "command": native,
                "reason": None}

    runtime = _runtime_command()
    if not runtime:
        return {
            "available": False, "kind": "none", "command": None,
            "reason": "rev.ng not found and no container runtime to run it in. "
                      "rev.ng runs on Linux x86-64 only. On Linux, install it "
                      "and set REVNG_BIN; on macOS or Windows, install Docker "
                      "(or Podman) and the pipeline will use the upstream "
                      "revng/revng image with --platform linux/amd64. "
                      "Alternatively set REVNG_DECOMPILE_CMD to any command "
                      "that prints C for a PE on stdout.",
        }
    return {"available": True, "kind": "container", "command": runtime,
            "reason": None, "image": os.environ.get("REVNG_IMAGE", DEFAULT_IMAGE)}


def _run(argv, timeout=TIMEOUT_SECONDS):
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-800:]
        raise RevngUnavailable(f"{' '.join(argv[:3])}... failed "
                               f"(exit {proc.returncode}): {tail}")
    return proc.stdout


def version():
    """The rev.ng version, for the pin the report has to carry."""
    found = discover()
    if not found["available"]:
        return None
    try:
        if found["kind"] == "native":
            out = _run([found["command"], "system-info"], timeout=300)
        else:
            out = _run([found["command"], "run", "--platform", "linux/amd64",
                        "--rm", found["image"], "revng", "system-info"],
                       timeout=600)
    except (RevngUnavailable, subprocess.SubprocessError, OSError):
        return None
    first = next((ln.strip() for ln in out.splitlines() if ln.strip()), None)
    return first


def ptml_to_c(text):
    """rev.ng's marked-up C to plain C.

    PTML wraps every token in a span and every line in a div, and carries the
    original instruction address on each. Stripping the tags and keeping the
    line breaks gives back the C rev.ng emitted; the indentation is
    reconstructed from brace depth because PTML's own indentation is the
    renderer's, not the code's.
    """
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    out, depth = [], 0
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith(("}", ")")) or stripped.startswith("}"):
            depth = max(0, depth - 1)
        out.append("    " * depth + stripped)
        if stripped.endswith("{"):
            depth += 1
        elif stripped.endswith("}") and not stripped.startswith("}"):
            depth = max(0, depth - 1)
    return "\n".join(out) + "\n"


def decompile(pe_path, timeout=TIMEOUT_SECONDS):
    """Decompile one PE and return the C, the command, and the raw markup.

    Raises RevngUnavailable with an actionable reason when rev.ng cannot run.
    """
    pe_path = Path(pe_path).resolve()
    if not pe_path.is_file():
        raise RevngUnavailable(f"{pe_path}: not a file")

    # REVNG_DECOMPILE_CMD: any command that prints C for a PE on stdout. The
    # escape hatch for a host that reaches rev.ng some other way (a remote
    # runner, a wrapper script, a future native macOS build).
    custom = os.environ.get("REVNG_DECOMPILE_CMD")
    if custom:
        out = _run([*custom.split(), str(pe_path)], timeout=timeout)
        return {"c": ptml_to_c(out) if "<" in out[:200] else out,
                "command": [*custom.split(), str(pe_path)],
                "markup": out if "<" in out[:200] else None,
                "kind": "custom"}

    found = discover()
    if not found["available"]:
        raise RevngUnavailable(found["reason"])

    if found["kind"] == "native":
        argv = [found["command"], "quick", "artifact", "emit-c-as-single-file",
                str(pe_path)]
        out = _run(argv, timeout=timeout)
        return {"c": ptml_to_c(out), "command": argv, "markup": out,
                "kind": "native"}

    # The container needs the binary inside it. The repository is mounted at
    # /work, which is where the corpus and the fixtures already are, so the path
    # is passed through unchanged when it is under the repository and copied in
    # otherwise.
    runtime = found["command"]
    repo = Path(__file__).resolve().parents[2]
    # The container sees only what is mounted. A host temp directory is not
    # shared with the VM (a /tmp path inside the container simply does not
    # exist), so a binary from outside the repository is staged inside it for
    # the duration of the run.
    staged = None
    try:
        inside = "/work/" + str(pe_path.relative_to(repo))
    except ValueError:
        stage = repo / ".revng-in"
        stage.mkdir(exist_ok=True)
        staged = stage / pe_path.name
        shutil.copy2(pe_path, staged)
        inside = f"/work/.revng-in/{pe_path.name}"
    try:
        argv = [runtime, "run", "--platform", "linux/amd64", "--rm",
                "-v", f"{repo}:/work", found["image"],
                "revng", "quick", "artifact", "emit-c-as-single-file", inside]
        out = _run(argv, timeout=timeout)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
    return {"c": ptml_to_c(out), "command": argv, "markup": out,
            "kind": "container", "image": found["image"]}


def functions_in(c_text):
    """The functions rev.ng recovered, by the name it gave them.

    Its names are `function_0xADDRESS_Code_x86`, so the address is recoverable
    and can be matched against the recompiler's own function inventory. That
    match is what lets a candidate replace a specific mechanical body.
    """
    found = []
    for m in re.finditer(r"^\s*_ABI\(([^)]+)\)\s*$", c_text, re.M):
        abi = m.group(1)
        after = c_text[m.end():]
        sig = re.search(r"^\s*([A-Za-z_][\w \*]*?)\s+"
                        r"(function_0x([0-9A-Fa-f]+)_Code_\w+)\s*\(",
                        after, re.M)
        if sig:
            found.append({
                "return_type": sig.group(1).strip(),
                "symbol": sig.group(2),
                "address": int(sig.group(3), 16),
                "abi": abi,
            })
    return found


def main(argv=None):
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pe")
    parser.add_argument("--out", help="write the plain C here")
    parser.add_argument("--markup", help="write the raw PTML here")
    parser.add_argument("--timeout", type=float, default=TIMEOUT_SECONDS)
    args = parser.parse_args(argv)

    found = discover()
    print(f"rev.ng: {'available via ' + found['kind'] if found['available'] else 'UNAVAILABLE'}")
    if not found["available"]:
        print(f"  {found['reason']}", file=sys.stderr)
        return 2
    print(f"  version: {version()}")
    try:
        result = decompile(args.pe, timeout=args.timeout)
    except RevngUnavailable as exc:
        print(f"  {exc}", file=sys.stderr)
        return 2
    if args.out:
        Path(args.out).write_text(result["c"])
        print(f"wrote {args.out} ({len(result['c'])} bytes of C)")
    else:
        print(result["c"])
    if args.markup:
        Path(args.markup).write_text(result["markup"] or "")
    funcs = functions_in(result["c"])
    print(f"functions recovered: {len(funcs)}")
    for fn in funcs[:10]:
        print(f"  0x{fn['address']:08X}  {fn['symbol']}  {fn['abi']}  "
              f"-> {fn['return_type']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
