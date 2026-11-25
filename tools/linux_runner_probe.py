#!/usr/bin/env python3
"""Fail-closed CLI probe for a *usable* Linux runner/host.

This tool answers exactly one narrow question: can a native Linux (ELF) binary
be built *and actually executed* here, so that a Linux pipeline result could
ever be produced? It is not a pipeline run and never reports a Linux PASS.
``linux_pipeline_result`` is ``NOT TESTED`` in every report, including the
green one.

Every observation goes through an injectable command executor
(``executor(argv) -> CmdResult``). ``probe_linux_runner()`` deliberately takes
no default live executor: without an injected executor it returns
``NOT TESTED`` without running anything. Only ``main()`` constructs the real
subprocess executor, so tests can prove all four cases (present / absent /
failing / tampered) with fakes and never probe the live host.

What is verified, not merely reported
-------------------------------------
* the runner kernel is Linux (``uname -s`` inside the runner), so a macOS host
  or a foreign runner is never relabelled as a Linux result;
* host arch and kernel are recorded from host ``uname`` commands;
* runner arch, kernel identity and C/CMake/Python versions are recorded;
* a fresh host-generated sentinel is compiled into a tiny C fixture inside the
  runner, the produced artifact's first four bytes must be ELF magic
  (``7f454c46``), a SHA-256 must be printable, the binary must execute with
  exit code 0 and print back that exact sentinel.

A compiler exit code of 0, a claimed "verified" field or any free-form text in
the output is never trusted by itself. Missing tools, failures, timeouts,
non-ELF artifacts, sentinel mismatches and unexpected errors all resolve to a
non-green status with verbatim (bounded) output attached.

Usage
-----
    python3 -m tools.linux_runner_probe
    python3 -m tools.linux_runner_probe --runner docker --image IMAGE --out-json report.json
    python3 -m tools.linux_runner_probe --no-probe     # explicit NOT TESTED, runs nothing

Exit status: 0 only when ``status == USABLE``; 1 for BLOCKED / NOT TESTED;
2 for an invalid option. Exit status 0 means "a runner was available", never
"the pipeline passed on Linux".
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import subprocess
import sys
from dataclasses import dataclass, field, replace
from typing import Callable, Sequence

SCHEMA_VERSION = 1
STATUSES = ("USABLE", "BLOCKED", "NOT TESTED", "FAIL")
DEFAULT_IMAGE = "gamerecomp/linux-runner:local"
ELF_MAGIC_HEX = "7f454c46"
DEFAULT_TIMEOUT = 60
DEFAULT_MAX_OUTPUT = 4000

Executor = Callable[[list[str]], "CmdResult"]


@dataclass(frozen=True)
class CmdResult:
    """Executor return type; fake executors build this directly."""

    command: list[str] = field(default_factory=list)
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "command": [str(c) for c in self.command],
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "error": self.error,
        }


def make_real_executor(timeout: int, max_output: int) -> Executor:
    """Build the live subprocess executor. Only ``main()`` calls this."""

    def execute(argv: Sequence[str]) -> CmdResult:
        cmd = [str(a) for a in argv]
        rec = CmdResult(command=cmd)
        try:
            p = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=timeout, check=False,
            )
            return CmdResult(
                command=cmd, exit_code=p.returncode,
                stdout=p.stdout[-max_output:], stderr=p.stderr[-max_output:],
            )
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout or ""
            err = exc.stderr or ""
            if isinstance(out, bytes):
                out = out.decode("utf-8", "replace")
            if isinstance(err, bytes):
                err = err.decode("utf-8", "replace")
            return CmdResult(
                command=cmd, timed_out=True,
                stdout=("TIMEOUT\n" + out)[-max_output:],
                stderr=err[-max_output:],
            )
        except OSError as exc:
            return CmdResult(
                command=cmd, error=f"{type(exc).__name__}: {exc}"[-max_output:],
            )

    return execute


IDENTITY_SCRIPT = (
    "printf 'KERNEL=%s\\n' \"$(uname -s)\"; "
    "printf 'ARCH=%s\\n' \"$(uname -m)\"; "
    "printf 'UNAME=%s\\n' \"$(uname -a)\"; "
    "printf 'CC=%s\\n' \"$(cc --version 2>/dev/null | head -n 1)\"; "
    "printf 'CMAKE=%s\\n' \"$(cmake --version 2>/dev/null | head -n 1)\"; "
    "printf 'PYTHON=%s\\n' \"$(python3 --version 2>&1 | head -n 1)\""
)


def build_probe_script(token: str) -> str:
    """Self-contained fixture: compile a fresh ELF that echoes ``token``."""
    return f"""set -eu
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT
cat > "$d/probe.c" <<'GRC_LINUX_PROBE_EOF'
#include <stdio.h>
int main(void) {{ puts("{token}"); return 0; }}
GRC_LINUX_PROBE_EOF
cc -O0 -o "$d/probe" "$d/probe.c"
printf 'ELF=%s\\n' "$(od -An -N4 -tx1 "$d/probe" | tr -d ' \\n')"
printf 'SHA256=%s\\n' "$(sha256sum "$d/probe" | cut -d' ' -f1)"
rc=0; out=$("$d/probe" 2>&1) || rc=$?
printf 'RUN=%s\\n' "$out"
printf 'RC=%s\\n' "$rc"
"""


def _fields(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _run(
    executor: Executor, rec: dict, argv: list[str], max_output: int
) -> CmdResult:
    result = executor(argv)
    if not isinstance(result, CmdResult):
        raise TypeError(f"executor returned {type(result).__name__}, expected CmdResult")
    if len(result.stdout) > max_output or len(result.stderr) > max_output:
        result = replace(
            result,
            stdout=result.stdout[-max_output:],
            stderr=result.stderr[-max_output:],
        )
    rec["commands"].append(result.as_dict())
    return result


def _block(rec: dict, reason: str) -> None:
    reasons: list[str] = rec["reasons"]
    if reason not in reasons:
        reasons.append(reason)
    if rec["status"] != "FAIL":
        rec["status"] = "BLOCKED"


def probe_linux_runner(
    executor: Executor | None = None,
    *,
    image: str = DEFAULT_IMAGE,
    runner: str = "auto",
    timeout: int = DEFAULT_TIMEOUT,
    max_output: int = DEFAULT_MAX_OUTPUT,
) -> dict:
    """Probe for a usable Linux runner, fail closed.

    With ``executor=None`` nothing is executed and the status is ``NOT TESTED``.
    ``runner`` is ``auto`` (Linux host, else local Docker image), ``native`` or
    ``docker``. A green ``USABLE`` means only that one fresh native Linux ELF
    was verified and executed here -- never that any pipeline passed.
    """
    rec: dict = {
        "schema_version": SCHEMA_VERSION,
        "tool": "linux_runner_probe",
        "status": "NOT TESTED",
        "runner": {"mode": None, "image": None, "selection": "",
                   "kernel": "", "arch": "", "kernel_identity": ""},
        "host": {"kernel": "", "arch": ""},
        "toolchain": {"cc": "", "cmake": "", "python3": ""},
        "native_binary": {
            "compiled": False, "elf_verified": False, "executed": False,
            "elf_first_bytes": "", "sha256": "", "exit_code": "",
            "sentinel_expected": "", "sentinel_observed": "",
            "output": "", "timed_out": False,
        },
        "commands": [],
        "requested_runner": runner,
        "timeout_seconds": timeout,
        "linux_pipeline_result": "NOT TESTED",
        "not_a_linux_pipeline_pass": True,
        "output_truncated_to": max_output,
        "reasons": [],
    }
    reasons: list[str] = rec["reasons"]

    if executor is None:
        reasons.append(
            "no command executor supplied: live probing disabled; "
            "no Linux result is fabricated"
        )
        return rec
    if runner not in ("auto", "native", "docker"):
        reasons.append(f"invalid runner mode: {runner!r}")
        rec["status"] = "FAIL"
        return rec

    def wrap(script: str) -> list[str]:
        if rec["runner"]["mode"] == "docker":
            return ["docker", "run", "--rm", "--network", "none", image,
                    "sh", "-c", script]
        return ["sh", "-c", script]

    try:
        host_kernel = _run(executor, rec, ["uname", "-s"], max_output)
        host_arch = _run(executor, rec, ["uname", "-m"], max_output)
        rec["host"]["kernel"] = host_kernel.stdout.strip()
        rec["host"]["arch"] = host_arch.stdout.strip()

        is_linux_host = (
            host_kernel.exit_code == 0 and host_kernel.stdout.strip() == "Linux"
        )
        if runner == "native" and not is_linux_host:
            _block(rec, "native runner requested but host kernel is not Linux "
                        f"(got {rec['host']['kernel']!r}); a macOS/other host "
                        "cannot be relabelled as Linux")
            return rec

        if runner == "auto" and is_linux_host:
            rec["runner"]["mode"] = "native"
            rec["runner"]["selection"] = "host uname -s is Linux"
        elif runner in ("auto", "docker"):
            info = _run(executor, rec, ["docker", "info"], max_output)
            if info.exit_code != 0:
                _block(rec, "no Linux host and docker daemon unavailable: "
                            "no usable Linux runner")
                return rec
            inspect = _run(executor, rec, ["docker", "image", "inspect", image],
                           max_output)
            if inspect.exit_code != 0:
                _block(rec, f"Linux runner image {image!r} not present locally; "
                            "no network pull was attempted")
                return rec
            rec["runner"]["mode"] = "docker"
            rec["runner"]["image"] = image
            rec["runner"]["selection"] = "local Docker Linux runner image"

        identity = _run(executor, rec, wrap(IDENTITY_SCRIPT), max_output)
        fields = _fields(identity.stdout)
        rec["commands"][-1]["parsed_fields"] = fields
        rec["runner"]["kernel"] = fields.get("KERNEL", "")
        rec["runner"]["arch"] = fields.get("ARCH", "")
        rec["runner"]["kernel_identity"] = fields.get("UNAME", "")
        rec["toolchain"] = {
            "cc": fields.get("CC", ""),
            "cmake": fields.get("CMAKE", ""),
            "python3": fields.get("PYTHON", ""),
        }
        if identity.timed_out or identity.exit_code != 0:
            _block(rec, f"runner identity probe failed "
                        f"(exit={identity.exit_code}, timed_out={identity.timed_out})")
            return rec
        if rec["runner"]["kernel"] != "Linux":
            _block(rec, "runner kernel is not Linux "
                        f"(got {rec['runner']['kernel']!r}); no Linux result may be "
                        "fabricated from a foreign host")
            return rec
        if not rec["runner"]["arch"]:
            _block(rec, "runner arch was not recorded")
            return rec
        if not rec["toolchain"]["cc"]:
            _block(rec, "no C compiler reported by the runner")
            return rec

        token = secrets.token_hex(16)
        execution = _run(executor, rec, wrap(build_probe_script(token)), max_output)
        observed = _fields(execution.stdout)
        rec["commands"][-1]["parsed_fields"] = observed

        nb = rec["native_binary"]
        nb["sentinel_expected"] = token
        nb["sentinel_observed"] = observed.get("RUN", "")
        nb["elf_first_bytes"] = observed.get("ELF", "")
        nb["sha256"] = observed.get("SHA256", "")
        nb["exit_code"] = observed.get("RC", "")
        nb["output"] = (
            execution.stdout + ("\n" + execution.stderr if execution.stderr else "")
        )[-max_output:]
        nb["timed_out"] = execution.timed_out

        nb["elf_verified"] = nb["elf_first_bytes"].lower() == ELF_MAGIC_HEX
        hash_ok = bool(re.fullmatch(r"[0-9a-f]{64}", nb["sha256"].lower()))
        rc_ok = nb["exit_code"] == "0"
        sentinel_ok = nb["sentinel_observed"] == token
        nb["compiled"] = execution.exit_code == 0 and "ELF" in observed
        nb["executed"] = bool(
            nb["compiled"] and nb["elf_verified"] and hash_ok and rc_ok and sentinel_ok
        )

        if execution.timed_out:
            _block(rec, "native fixture execution timed out")
        if execution.exit_code != 0:
            _block(rec, f"fixture command exit={execution.exit_code}")
        if not nb["compiled"]:
            _block(rec, "native fixture was not compiled inside the runner")
        if not nb["elf_verified"]:
            _block(rec, f"artifact is not a native Linux ELF "
                        f"(first bytes {nb['elf_first_bytes']!r}); "
                        "forged/tampered evidence is never accepted")
        if not hash_ok:
            _block(rec, "artifact SHA-256 is missing or malformed")
        if not rc_ok:
            _block(rec, f"native fixture exited {nb['exit_code']!r}, expected '0'")
        if not sentinel_ok:
            _block(rec, "execution sentinel mismatch: the fresh fixture binary "
                        "did not actually run here")
        rec["status"] = "USABLE" if nb["executed"] else "BLOCKED"
        return rec
    except Exception as exc:  # fail closed on anything unexpected
        _block(rec, f"probe error: {exc!r}")
        rec["status"] = "FAIL"
        return rec


def main(argv: list[str] | None = None, *, executor: Executor | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runner", choices=("auto", "native", "docker"), default="auto")
    ap.add_argument("--image", default=DEFAULT_IMAGE)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--max-output", type=int, default=DEFAULT_MAX_OUTPUT)
    ap.add_argument("--no-probe", action="store_true",
                    help="record NOT TESTED without executing any command")
    ap.add_argument("--out-json", type=str)
    args = ap.parse_args(argv)

    if executor is None and not args.no_probe:
        executor = make_real_executor(args.timeout, args.max_output)
    rec = probe_linux_runner(
        None if args.no_probe else executor, image=args.image, runner=args.runner,
        timeout=args.timeout, max_output=args.max_output,
    )
    text = json.dumps(rec, indent=2) + "\n"
    if args.out_json:
        from pathlib import Path

        out = Path(args.out_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(text)
    print(f"runner_status: {rec['status']} | "
          f"linux_pipeline_result: {rec['linux_pipeline_result']}", file=sys.stderr)
    return 0 if rec["status"] == "USABLE" else 1


if __name__ == "__main__":
    sys.exit(main())
