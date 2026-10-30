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
import shlex
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
    #
    # The placeholder form is the one docs/SOURCE_PROVENANCE.md documents
    # (`revng decompile {pe} --output-dir {outdir}`), so it is supported here
    # rather than documented and not implemented: a {pe} or {outdir} anywhere in
    # the command is substituted, and a command with no placeholder gets the PE
    # path appended. Both forms are real; the second is the shorthand.
    custom = os.environ.get("REVNG_DECOMPILE_CMD")
    if custom:
        out_dir = Path(tempfile.mkdtemp(prefix="revng-cmd-"))
        argv_in = shlex.split(custom)
        if any("{pe}" in a or "{outdir}" in a for a in argv_in):
            argv = [a.replace("{pe}", str(pe_path)).replace("{outdir}", str(out_dir))
                    for a in argv_in]
        else:
            argv = [*argv_in, str(pe_path)]
        out = _run(argv, timeout=timeout)
        return {"c": ptml_to_c(out) if "<" in out[:200] else out,
                "command": argv,
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


def selftest():
    """Exercise the command forms the environment variable documents.

    A documented interface with no test is a claim, not a feature: the sed-like
    habit of writing `{pe}` in a manual page and appending it in the code is how
    an escape hatch becomes a trap.
    """
    import subprocess
    import tempfile
    real = os.environ.pop("REVNG_DECOMPILE_CMD", None)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            pe = Path(tmp) / "x.exe"
            pe.write_bytes(b"MZ")
            # A command that prints the argv it received, so the substitution is
            # observable rather than assumed.
            os.environ["REVNG_DECOMPILE_CMD"] = (
                f"{sys.executable} -c \'import sys; print(len(sys.argv))\' {{pe}} tail")
            result = decompile(pe)
            # decompile() resolves the path, and /var is a symlink to /private/var
            # on macOS -- compare against what it actually substituted.
            resolved = str(Path(pe).resolve())
            assert resolved in " ".join(result["command"]), result["command"]
            assert result["command"][-1] == "tail", result["command"]
            assert result["command"].count(resolved) == 1, result["command"]
            os.environ["REVNG_DECOMPILE_CMD"] = (
                f"{sys.executable} -c \'import sys; print(len(sys.argv))\'")
            result = decompile(pe)
            assert result["command"][-1] == str(Path(pe).resolve()), result["command"]
    finally:
        os.environ.pop("REVNG_DECOMPILE_CMD", None)
        if real is not None:
            os.environ["REVNG_DECOMPILE_CMD"] = real
    return 0


def main(argv=None):
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pe", nargs="?", help="the PE to decompile")
    parser.add_argument("--out", help="write the plain C here")
    parser.add_argument("--markup", help="write the raw PTML here")
    parser.add_argument("--timeout", type=float, default=TIMEOUT_SECONDS)
    parser.add_argument("--selftest", action="store_true",
                        help="check the REVNG_DECOMPILE_CMD forms, no binary needed")
    args = parser.parse_args(argv)

    if args.selftest:
        selftest()
        print("selftest: both REVNG_DECOMPILE_CMD forms work")
        return 0

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


def function_source(c_text, address):
    """rev.ng's C for one function, by its original address.

    rev.ng names a function `function_0xADDRESS_Code_ARCH`, so its output is
    address-keyed and can be matched against the recompiler's inventory and
    against a hand-written module. That match is what makes it evidence rather
    than a second opinion nobody can line up.
    """
    # rev.ng does not zero-pad the address: 0x40BA58 is
    # `function_0x40ba58_Code_x86`, not `function_0x0040ba58_...`. Matching a
    # fixed width found nothing, which is exactly the mistake the harness in
    # hlverify made with the generator's own addresses.
    name = rf"function_0x0*{address:x}_Code_"
    start = None
    for m in re.finditer(rf"^\s*([A-Za-z_][\w \*]*?)\s+({name}\w+)\s*\(",
                         c_text, re.M | re.I):
        start = m.start()
        break
    if start is None:
        return None
    # The ABI macro sits on the line above the signature.
    before = c_text.rfind("_ABI(", 0, start)
    if before != -1 and c_text.count("\n", before, start) <= 1:
        start = before
    depth, i = 0, c_text.index("{", start)
    for j in range(i, len(c_text)):
        if c_text[j] == "{":
            depth += 1
        elif c_text[j] == "}":
            depth -= 1
            if depth == 0:
                return c_text[start:j + 1].strip() + "\n"
    return None


def corroborate(module, c_text, out_dir):
    """Write rev.ng's reading of each replaced function beside the module.

    This is a provider's independent opinion, recorded. It is not a proof and it
    is not compared automatically: whether two decompilations agree is a
    judgement, and pretending a string comparison could make it is how a
    verification report starts lying. What it does is make the comparison
    possible without re-running rev.ng, and put the provider's output under
    version control next to the claim it bears on.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for fn in module["functions"]:
        source = function_source(c_text, fn["address"])
        if source is None:
            written.append({"address": f"0x{fn['address']:08X}",
                            "symbol": fn["symbol"], "revng": None,
                            "note": "rev.ng recovered no function at this address"})
            continue
        path = out_dir / f"0x{fn['address']:08X}.c"
        path.write_text(
            f"/* rev.ng's independent reading of 0x{fn['address']:08X}, the\n"
            f" * address {fn['symbol']} replaces. Not a proof and not compared\n"
            f" * automatically: it is the other provider's opinion, recorded so\n"
            f" * the two can be read side by side. */\n{source}")
        written.append({"address": f"0x{fn['address']:08X}",
                        "symbol": fn["symbol"],
                        "revng": f"revng/{path.name}",
                        "bytes": len(source)})
    return written
