#!/usr/bin/env python3
"""Fail-closed readiness recorder for a real pinned rev.ng container run (issue #25).

This tool is the *operator-side* counterpart to the read-only
``tools/revng_acquire_guide.py``. It does not manufacture evidence: its default
mode is a read-only assessment of whether the pinned image could honestly be
acquired on this host. A pull is only possible when the caller passes
``--allow-pull`` **and** the disk guard passes; a decompilation is only recorded
when a real container process really exited successfully.

Nothing this tool prints is a differential verdict. It never calls
``tools/lift/multilifter.py``, ``tools/issue25_evidence.py`` or ``gh``, and it
never emits a PASS for a provider. A successful ``docker run`` establishes that
a binary ran, not that its output is correct; that claim needs a real
``difftest.py --project --json`` report driven separately.

Usage
-----
    python3 tools/revng_container_runner.py --out proof/revng/container_attempt.json
    python3 tools/revng_container_runner.py --allow-pull --out proof/revng/container_attempt.json

The report shape is versioned so evidence consumers can fail loudly. The most
important fields are ``verdict`` (always ``NO_REVNG_PASS``), ``reason_codes``
(why the run could not happen) and ``host.disk`` (the exact free-space reading
the guard used). Missing tools, an unusable image or an insufficient disk all
produce a BLOCKED report with an actionable reason; no exit status is a PASS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

SCHEMA_VERSION = 1
GENERATOR = "tools/revng_container_runner.py"
ISSUE = 25

# Do not duplicate the acquisition pin: importing it keeps the runner tied to
# the digest recorded by tools/revng_acquire_guide.py (and its tests).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.revng_acquire_guide import PINNED_IMAGE  # noqa: E402

# A real pull of the pinned amd64 image on this macOS/arm64 host consumed about
# 5.8 GiB of host disk before the registry fetch stalled; the guide's compressed
# size hint (~1.17 GB) is NOT a safe host-disk budget. The guard therefore keeps
# a 6 GiB floor and refuses to start when it cannot be met.
MIN_FREE_BYTES = 6 * 1024 ** 3
PULL_DISK_BUDGET_BYTES = 6 * 1024 ** 3

DEFAULT_FIXTURE = "tests/fixtures/pe32-minimal.exe"
DEFAULT_TIMEOUT = 300
COMMAND_TIMEOUT = 60

CLOSING_KEYWORD = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+(?:#\d+|gh-\d+)")


class CommandResult:
    """A captured command outcome; failures stay data, never exceptions."""

    def __init__(self, argv: Sequence[str], rc: int, stdout: str, stderr: str):
        self.argv = list(argv)
        self.rc = rc
        self.stdout = stdout
        self.stderr = stderr

    def to_dict(self) -> dict[str, Any]:
        return {"argv": self.argv, "returncode": self.rc,
                "stdout": self.stdout[-4000:], "stderr": self.stderr[-4000:]}


def run_command(argv: Sequence[str], timeout: int = COMMAND_TIMEOUT) -> CommandResult:
    """Run a read-only host command and capture its outcome without raising."""
    try:
        proc = subprocess.run(list(argv), capture_output=True, text=True,
                              timeout=timeout, check=False)
        return CommandResult(argv, proc.returncode, proc.stdout, proc.stderr)
    except (OSError, subprocess.SubprocessError) as exc:
        return CommandResult(argv, 127, "", f"{type(exc).__name__}: {exc}")


def sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except (OSError, ValueError):
        return None


def disk_state(path: Path) -> dict[str, Any]:
    """Exact free-space reading used by the guard, rounded only for display."""
    try:
        usage = shutil.disk_usage(str(path))
    except OSError as exc:
        return {"path": str(path), "free_bytes": None, "total_bytes": None,
                "free_gib": None, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "path": str(path),
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "free_gib": round(usage.free / (1024 ** 3), 3),
        "error": None,
    }


def disk_guard(disk: Mapping[str, Any]) -> tuple[bool, str | None]:
    """Return (allowed, reason). Never guesses a free-space value."""
    free = disk.get("free_bytes")
    if not isinstance(free, int) or isinstance(free, bool) or free < 0:
        return False, "disk free space could not be measured"
    if free < MIN_FREE_BYTES:
        return (False,
                f"only {free} bytes ({disk.get('free_gib')} GiB) free; "
                f"refusing to start a pull that needs up to {PULL_DISK_BUDGET_BYTES} bytes "
                f"({PULL_DISK_BUDGET_BYTES / 1024 ** 3:g} GiB) and requires a "
                f"{MIN_FREE_BYTES / 1024 ** 3:g} GiB floor")
    return True, None


def image_state(image: str, runner: Callable[..., CommandResult] = run_command) -> dict[str, Any]:
    """Inspect the pinned image by digest; a missing image is data, not a crash."""
    probe = runner(["docker", "image", "inspect", image,
                    "--format", "{{.Id}}|{{json .RepoDigests}}|{{.Size}}"])
    digests: list[str] = []
    if probe.rc == 0:
        parts = probe.stdout.strip().split("|")
        if len(parts) == 3:
            try:
                parsed = json.loads(parts[1])
                if isinstance(parsed, list):
                    digests = [str(d) for d in parsed]
            except (ValueError, TypeError):
                pass
    pinned = image.split("@", 1)[1] if "@" in image else None
    usable = probe.rc == 0 and bool(digests) and any(
        d.endswith("@" + pinned) for d in digests) if pinned else probe.rc == 0
    return {
        "pin": image,
        "pinned_digest": pinned,
        "inspect_returncode": probe.rc,
        "repo_digests": digests,
        "image_id": probe.stdout.strip().split("|")[0] if probe.rc == 0 and probe.stdout.strip() else None,
        "usable": bool(usable),
        "inspect_stderr_tail": probe.stderr[-1000:],
    }


def host_state(path: Path, image: str,
               runner: Callable[..., CommandResult] = run_command) -> dict[str, Any]:
    """Read-only host facts; all commands are version/inspection only."""
    version = runner(["docker", "--version"])
    info = runner(["docker", "info", "--format", "{{.ServerVersion}}"])
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "docker_client": version.stdout.strip() if version.rc == 0 else None,
        "docker_server": info.stdout.strip() if info.rc == 0 else None,
        "docker_available": version.rc == 0 and info.rc == 0,
        "disk": disk_state(path),
        "image": image_state(image, runner),
    }


def build_report(*, image: str, host: Mapping[str, Any], disk_path: Path,
                 allow_pull: bool, pull: CommandResult | None,
                 version_probes: Sequence[CommandResult] | None,
                 fixture: Mapping[str, Any] | None) -> dict[str, Any]:
    """Assemble a report from observed facts; no verdict higher than NO_PASS."""
    reasons: list[str] = []
    if not host.get("docker_available"):
        reasons.append("NO_DOCKER_RUNTIME")
    allowed, disk_reason = disk_guard(host["disk"])
    if not allowed:
        reasons.append("DISK_GUARD_BLOCKED")
    if not host["image"]["usable"]:
        reasons.append("PINNED_IMAGE_NOT_USABLE")
    if not allow_pull:
        reasons.append("PULL_NOT_AUTHORISED")
    if pull is not None and pull.rc != 0:
        reasons.append("PULL_FAILED")
    if version_probes is not None and any(p.rc != 0 for p in version_probes):
        reasons.append("VERSION_PROBE_FAILED")
    if fixture is None:
        reasons.append("NO_REVNG_C_OUTPUT")

    if reasons or not (version_probes and fixture):
        decision = "BLOCKED"
    else:
        decision = "ATTEMPTED"
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "issue": ISSUE,
        "pin": image,
        # Deliberately not PASS/PROMOTE/VERIFIED: running a binary and even
        # printing C is not a differential verdict.
        "verdict": "NO_REVNG_PASS",
        "decision": decision,
        "reason_codes": reasons,
        "disk_guard": {
            "minimum_free_bytes": MIN_FREE_BYTES,
            "pull_budget_bytes": PULL_DISK_BUDGET_BYTES,
            "allowed": allowed,
            "reason": disk_reason,
        },
        "host": dict(host),
        "pull": pull.to_dict() if pull is not None else None,
        "version_probes": [p.to_dict() for p in (version_probes or [])],
        "fixture": dict(fixture) if fixture is not None else None,
        "what_it_proves": "host facts and the exact reason no real rev.ng run happened",
        "what_it_does_not_prove": (
            "any decompiler quality claim, any differential agreement, any issue state "
            "change, or any acceptance criterion of issue #25"),
        "workspace_disk_path": str(disk_path),
    }


def attempt(image: str = PINNED_IMAGE, disk_path: Path | None = None, *,
            allow_pull: bool = False, timeout: int = DEFAULT_TIMEOUT,
            runner: Callable[..., CommandResult] = run_command) -> dict[str, Any]:
    """Assess (and optionally perform) a real pinned-container attempt."""
    disk_path = disk_path or Path(__file__).resolve().parents[1]
    host = host_state(disk_path, image, runner)
    allowed, _ = disk_guard(host["disk"])
    pull: CommandResult | None = None
    probes: list[CommandResult] = []
    fixture: dict[str, Any] | None = None

    if allow_pull and host["docker_available"] and allowed:
        pull = runner(["docker", "pull", "--platform", "linux/amd64", image],
                      timeout=timeout)
        if pull.rc == 0:
            host["image"] = image_state(image, runner)
            if host["image"]["usable"]:
                for argv in (
                    ["docker", "run", "--platform", "linux/amd64", "--rm", image,
                     "revng", "--version"],
                    ["docker", "run", "--platform", "linux/amd64", "--rm", image,
                     "revng", "project", "artifact", "--help"],
                ):
                    probes.append(runner(argv, timeout=timeout))
                pe = Path(__file__).resolve().parents[1] / DEFAULT_FIXTURE
                digest = sha256_file(pe)
                if digest is not None:
                    repo = Path(__file__).resolve().parents[1]
                    compiled = runner(
                        ["docker", "run", "--platform", "linux/amd64", "--rm",
                         "-v", f"{repo}:/work", image, "revng", "quick", "artifact",
                         "emit-c-as-single-file", "/work/" + DEFAULT_FIXTURE],
                        timeout=timeout)
                    fixture = {
                        "pe": DEFAULT_FIXTURE,
                        "pe_sha256": digest,
                        "returncode": compiled.rc,
                        "stdout_tail": compiled.stdout[-4000:],
                        "stderr_tail": compiled.stderr[-2000:],
                    }
    return build_report(image=image, host=host, disk_path=disk_path,
                        allow_pull=allow_pull, pull=pull,
                        version_probes=probes or None, fixture=fixture)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True,
                        help="write the versioned JSON attempt report here")
    parser.add_argument("--disk-path", type=Path, default=None,
                        help="filesystem to measure for the disk guard (default: repo root)")
    parser.add_argument("--allow-pull", action="store_true",
                        help="explicitly authorise a pull; still refused by the disk guard")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help="per-command timeout in seconds (default: %(default)s)")
    args = parser.parse_args(argv)

    report = attempt(disk_path=args.disk_path, allow_pull=args.allow_pull,
                     timeout=args.timeout)
    text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    assert CLOSING_KEYWORD.search(text) is None, "refusing to emit an auto-closing keyword"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    codes = ", ".join(report["reason_codes"]) or "none"
    print(f"wrote {args.out}: verdict={report['verdict']} "
          f"decision={report['decision']} reason_codes=[{codes}]")
    # Exit 0 means the assessment ran; it is never a provider PASS.
    return 0


if __name__ == "__main__":
    sys.exit(main())
