#!/usr/bin/env python3
"""Read-only feasibility probe for rev.ng / Anvill / Remill / Rellic / LLVM.

This module answers exactly one question: *are these toolchains reachable from
this host, and if not, why?* It deliberately does not clone, build, invoke,
import or spawn a process for any of them. A green result means "the binary is
on PATH", not "decompilation works" and not "we integrated it".
The audit in docs/REVNG_INTEGRATION_AUDIT.md explains why that distinction is
load-bearing.

Usage
-----
    python3 -m tools.revng_probe
    python3 -m tools.revng_probe --tools-file tools/revng_tools.json --out report.json
    python3 -m tools.revng_probe --path /opt/bin:/usr/bin --compact

``--tools-file`` reads a JSON *list*. Each entry is either

* a string -- an extra binary name, grouped under ``user-supplied``; or
* an object with ``tool`` (group name) and ``binaries`` (list of names).
  Optional ``env_var``, ``upstream``, ``licence``, ``role``, ``caveat`` and
  ``primary`` fields override the defaults for that group.

The report is JSON on stdout (or ``--out``) with a ``schema_version`` so
consumers can fail loudly on a breaking change. Exit status is 0 for a
successful probe and 2 for an invalid input file; tool absence is reported in
the JSON, never as a process failure, because absence is a normal finding.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = 1

# These are facts about upstream projects, recorded during the read-only audit
# (docs/REVNG_INTEGRATION_AUDIT.md). They are strings in the report on purpose:
# a consumer must not mistake this probe for a licence or capability verdict.
TOOL_REGISTRY: tuple[dict[str, Any], ...] = (
    {
        "tool": "revng",
        "binaries": ("revng",),
        "primary": "revng",
        "env_var": "REVNG_BIN",
        "upstream": "https://github.com/revng/revng",
        "licence": "GPL-2.0 as a whole (depends on GPLv2 QEMU); individual files MIT",
        "role": "LLVM/QEMU binary analysis framework and decompiler",
        "caveat": (
            "Upstream runs on Linux x86-64; other hosts need the revng/revng "
            "container. GPLv2 as a whole means keep it an external process, "
            "not a linked library."
        ),
    },
    {
        "tool": "anvill",
        "binaries": ("anvill", "anvill-specify-bitcode"),
        "primary": "anvill",
        "env_var": "ANVILL_BIN",
        "upstream": "https://github.com/lifting-bits/anvill",
        "licence": "AGPL-3.0",
        "role": "machine-code lifting primitives on top of Remill",
        "caveat": (
            "AGPL-3.0 has a network-use clause. The master branch consumes "
            "protobuf specs from an in-development, closed-source Ghidra "
            "plugin; only tag binja-final-version ships open spec generation."
        ),
    },
    {
        "tool": "remill",
        "binaries": ("remill", "remill-lift"),
        "primary": "remill",
        "env_var": "REMILL_BIN",
        "upstream": "https://github.com/lifting-bits/remill",
        "licence": "Apache-2.0",
        "role": "static lifter: machine instructions to LLVM bitcode",
        "caveat": (
            "Remill is primarily a CMake library, not a CLI; absence of a "
            "binary on PATH does not prove the library is absent. Check "
            "<prefix>/lib/cmake/remill separately."
        ),
    },
    {
        "tool": "rellic",
        "binaries": ("rellic-decomp", "rellic-headergen", "rellic-xref"),
        "primary": "rellic-decomp",
        "env_var": "RELLIC_BIN",
        "upstream": "https://github.com/lifting-bits/rellic",
        "licence": "Apache-2.0",
        "role": "goto-free C output from LLVM bitcode",
        "caveat": (
            "Needs LLVM 16 and Z3 4.7.1+; bitcode must be emitted by the "
            "matching LLVM version or decompilation is meaningless."
        ),
    },
    {
        "tool": "llvm",
        "binaries": (
            "llvm-objdump",
            "llvm-dis",
            "llvm-config",
            "opt",
            "clang",
            "objdump",
        ),
        "primary": "llvm-objdump",
        "env_var": None,
        "upstream": "https://llvm.org/",
        "licence": "Apache-2.0 with LLVM Exception",
        "role": "disassembly / bitcode utilities that support the above",
        "caveat": (
            "LLVM utilities are not decompilers. Their presence only means "
            "support tooling exists; version mismatches still break bitcode."
        ),
    },
)

LIMITATIONS: tuple[str, ...] = (
    "PATH (and optional *_BIN overrides) are inspected; no binary is executed.",
    "A found binary is not version-checked; output fidelity is not established.",
    "Library-only installs (notably Remill) cannot be detected through PATH.",
    "Container runtimes and images are not probed; a missing native binary "
    "does not rule out container-based use (e.g. revng/revng).",
    "Licence strings are upstream claims recorded in the audit, not legal advice.",
)


class ProbeInputError(ValueError):
    """The optional JSON tool list is malformed."""


def _as_names(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
        return list(value)
    raise ProbeInputError("'binaries' must be a string or a list of strings")


def load_extra_tools(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Load an optional JSON list of extra tool/binary specifications.

    Raises ProbeInputError (never JSONDecodeError) so callers can give one
    consistent, actionable error for every bad-input shape.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProbeInputError(f"cannot read tools file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ProbeInputError(f"invalid JSON in tools file {path}: {exc}") from exc
    if not isinstance(raw, list):
        raise ProbeInputError("tools file must contain a JSON list")

    grouped: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(raw):
        if isinstance(entry, str):
            spec: dict[str, Any] = {"tool": "user-supplied", "binaries": [entry]}
        elif isinstance(entry, Mapping):
            spec = dict(entry)
            if "binaries" not in spec:
                raise ProbeInputError(f"entry {index} is missing 'binaries'")
            spec["binaries"] = _as_names(spec["binaries"])
            name = spec.get("tool", "user-supplied")
            if not isinstance(name, str) or not name:
                raise ProbeInputError(f"entry {index} has an invalid 'tool' name")
            spec["tool"] = name
        else:
            raise ProbeInputError(f"entry {index} must be a string or an object")
        spec["binaries"] = _as_names(spec["binaries"])

        group = spec["tool"]
        if group in grouped:
            grouped[group]["binaries"].extend(spec["binaries"])
            continue
        spec.setdefault("primary", spec["binaries"][0] if spec["binaries"] else "")
        spec.setdefault("env_var", None)
        spec.setdefault("upstream", "")
        spec.setdefault("licence", "unspecified (user-supplied)")
        spec.setdefault("role", "user-supplied tool")
        spec.setdefault("caveat", "User-supplied entry; provenance is the caller's responsibility.")
        grouped[group] = spec
    return list(grouped.values())


def _which(candidate: str, path_env: str | None) -> str | None:
    """shutil.which with an explicit path so tests can pin PATH without globals."""
    return shutil.which(candidate, path=path_env)


def probe_tool(
    spec: Mapping[str, Any],
    path_env: str | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Probe one tool group. Pure detection: no execution, no file mutation."""
    env = os.environ if env is None else env
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    primary = spec.get("primary") or (spec.get("binaries") or [None])[0]

    for name in spec.get("binaries", ()):
        resolved = _which(name, path_env)
        if resolved:
            found.append({"name": name, "path": resolved, "source": "path"})
            seen.add(name)

    env_var = spec.get("env_var")
    env_value = env.get(env_var) if isinstance(env_var, str) else None
    if env_value:
        resolved = _which(env_value, path_env) or (
            env_value if os.path.isfile(env_value) and os.access(env_value, os.X_OK) else None
        )
        if resolved:
            # An env override is an alternative location for the primary
            # binary, so it satisfies the primary rather than looking like an
            # unrelated extra tool.
            override_name = primary if primary and primary not in seen else env_value
            if override_name not in seen:
                found.append({"name": override_name, "path": resolved, "source": "env"})
                seen.add(override_name)

    missing = [name for name in spec.get("binaries", ()) if name not in seen]
    found_names = {item["name"] for item in found}

    if found and primary in found_names:
        status = "available"
    elif found:
        status = "partial"
    else:
        status = "missing"

    if found:
        reason = "found " + ", ".join(f"{i['name']} at {i['path']} ({i['source']})" for i in found)
        if missing:
            reason += "; missing " + ", ".join(missing)
    else:
        reason = "no matching binary on PATH" + (
            f" and {env_var} is unset" if env_value is None and env_var else ""
        )
        reason += f"; expected one of: {', '.join(spec.get('binaries', ())) or '(none)'}"
    reason += f". {spec.get('caveat', '').strip()}".rstrip()

    return {
        "tool": spec["tool"],
        "status": status,
        "role": spec.get("role", ""),
        "upstream": spec.get("upstream", ""),
        "licence": spec.get("licence", ""),
        "found": found,
        "missing": missing,
        "reason": reason,
    }


def _pipeline(name: str, ready: bool, reason: str) -> dict[str, Any]:
    return {"name": name, "feasible": ready, "reason": reason}


def build_report(
    *,
    path_env: str | None = None,
    env: Mapping[str, str] | None = None,
    extra_tools: Iterable[Mapping[str, Any]] | None = None,
    now: datetime | None = None,
    host: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build the complete machine-readable feasibility report."""
    env = os.environ if env is None else env
    specs = list(TOOL_REGISTRY) + [dict(s) for s in (extra_tools or ())]
    tools = {s["tool"]: probe_tool(s, path_env=path_env, env=env) for s in specs}

    def state(name: str) -> str:
        entry = tools.get(name)
        return entry["status"] if entry else "missing"

    core = ("revng", "anvill", "remill", "rellic")
    available_core = [name for name in core if state(name) != "missing"]
    pipelines = [
        _pipeline(
            "revng-decompilation",
            state("revng") != "missing",
            tools["revng"]["reason"] if "revng" in tools else "revng is not in the registry",
        ),
        _pipeline(
            "remill-rellic-lifting",
            state("remill") != "missing" and state("rellic") != "missing",
            (
                f"remill: {state('remill')}; rellic: {state('rellic')}. "
                "Both a lifter and a C emitter are required for this pipeline."
            ),
        ),
        _pipeline(
            "anvill-specification-lifting",
            state("anvill") != "missing",
            (
                tools["anvill"]["reason"] + " AGPL-3.0 and the closed-source "
                "spec generator on master are separate adoption blockers."
                if "anvill" in tools
                else "anvill is not in the registry"
            ),
        ),
        _pipeline(
            "llvm-utility-support",
            state("llvm") != "missing",
            tools["llvm"]["reason"] if "llvm" in tools else "llvm is not in the registry",
        ),
    ]

    feasible_pipelines = [p["name"] for p in pipelines if p["feasible"]]
    if available_core:
        overall = "partial-toolchain-detected"
        overall_reason = (
            "At least one core decompiler/lifter is reachable: "
            + ", ".join(available_core)
            + ". This is a detection result only; fidelity is unproven."
        )
    else:
        overall = "no-core-toolchain-detected"
        overall_reason = (
            "None of revng, anvill, remill or rellic was found on PATH. "
            "Only supporting tools (if any) are present; a native integration "
            "would introduce an unsatisfied runtime dependency on this host."
        )

    default_host = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "host": dict(default_host if host is None else host),
        "tools": tools,
        "summary": {
            "overall": overall,
            "overall_reason": overall_reason,
            "available_core_tools": available_core,
            "feasible_pipelines": feasible_pipelines,
            "pipelines": pipelines,
        },
        "limitations": list(LIMITATIONS),
    }


def format_report(report: Mapping[str, Any], *, compact: bool = False) -> str:
    return json.dumps(report, indent=None if compact else 2, sort_keys=True) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", help="write JSON here instead of stdout")
    parser.add_argument("--tools-file", help="optional JSON list of extra tools/binaries")
    parser.add_argument("--path", dest="path_env", help="override PATH used for detection")
    parser.add_argument("--compact", action="store_true", help="single-line JSON")
    args = parser.parse_args(argv)

    try:
        extras = load_extra_tools(args.tools_file) if args.tools_file else []
    except ProbeInputError as exc:
        print(f"revng_probe: {exc}", file=sys.stderr)
        return 2

    report = build_report(path_env=args.path_env, extra_tools=extras)
    text = format_report(report, compact=args.compact)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
