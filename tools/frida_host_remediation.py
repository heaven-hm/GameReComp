#!/usr/bin/env python3
"""Fail-closed remediation checklist for the Frida attach failure in issue #171.

This tool reads *only* CLI-supplied probe records. It never imports ``frida``,
never attaches to a process, never launches a command, never opens a socket and
never calls ``gh``. It turns recorded error signatures into a ranked checklist
of exact commands with the error signature expected after each attempt.

Its own verdicts are deliberately narrow:

* ``verdict.attach_proven`` is always ``False`` -- a written claim is not an
  attach, and this tool cannot observe one;
* ``verdict.dual_engine`` is always ``NOT PROVEN`` -- a dual-engine result needs
  a real Frida session *and* a real Unicorn result, neither of which is produced
  here;
* issue #171 is always reported ``OPEN``. It stays OPEN until a real attach
  succeeds and is independently verified by the repository's harness. Nothing
  emitted by this tool changes an issue or carries a GitHub auto-closing keyword.

Probe record shape (``schema_version`` 1); one object or an array per file::

    {
      "schema_version": 1,
      "label": "sleep-17.24.3",
      "host": {"os": "macos", "arch": "arm64", "os_version": "27.2.1"},
      "frida": {"version": "17.24.3"},
      "target": {"path": "/bin/sleep", "arch": "arm64e"},
      "attach": {
        "status": "FAILED",
        "error_type": "PermissionDeniedError",
        "error_message": "unable to access process with pid 42 from the current user account"
      }
    }

Only ``schema_version`` and a usable ``attach`` object are required. An unknown
signature, a missing file, a bad schema or an absent failure signature fails
closed: there is no confident checklist and the tool exits non-zero.

Usage::

    python3 -m tools.frida_host_remediation \\
        --probe evidence/frida/sleep-17.24.3.json \\
        --probe evidence/frida/wine-17.27.5.json \\
        --out evidence/frida/remediation.md --json evidence/frida/remediation.json
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
GENERATOR = "tools/frida_host_remediation.py"
ISSUE_NUMBER = 171
ISSUE_STATE = "OPEN"
ATTACH_PROVEN = False
DUAL_ENGINE = "NOT PROVEN"
STATUS_CHECKLIST = "CHECKLIST"
STATUS_MANUAL = "MANUAL_ESCALATION"

EXIT_CHECKLIST = 0
EXIT_MANUAL = 1
EXIT_USAGE = 2

# GitHub auto-closing keywords followed by an issue reference. No report or
# stdout/stderr text may contain any of these.
CLOSING_KEYWORD = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+(?:#\d+|gh-\d+)"
)
# Phrases that would overstate what was proven. Kept explicit so a written
# claim can be echoed as a sanitized "PASS-LIKE" token without repeating it.
FORBIDDEN_CLAIMS = (
    "dual-engine pass",
    "dual engine pass",
    "verdict: pass",
    "attach proven: yes",
    "#171 closed",
    "#171 resolved",
    "issue 171 closed",
    "issue 171 resolved",
)

S_FRIDA_MACOS = "https://frida.re/docs/examples/macos/"
S_FRIDA_TROUBLESHOOTING = "https://frida.re/docs/troubleshooting/"
S_FRIDA_GADGET = "https://frida.re/docs/gadget/"
S_FRIDA_3420 = "https://github.com/frida/frida/issues/3420"
S_FRIDA_3391 = "https://github.com/frida/frida/issues/3391"
S_FRIDA_2772 = "https://github.com/frida/frida/issues/2772"
S_FRIDA_3752 = "https://github.com/frida/frida/issues/3752"
S_Fritap_MACOS = "https://fkie-cad.github.io/friTap/platforms/macos/"
S_YAMA = "https://www.kernel.org/doc/html/latest/admin-guide/LSM/Yama.html"
S_HARDENED = (
    "https://developer.apple.com/documentation/xcode/"
    "configuring-the-hardened-runtime.md"
)

LIMITS = (
    "This tool never attaches to a process, imports frida, launches a command, "
    "opens a socket or calls gh. It reads only the CLI-supplied probe files.",
    "attach_proven is always False and the dual-engine status is always NOT "
    "PROVEN: a written PASS in a probe record is an ignored claim, not evidence.",
    "#171 stays OPEN until a real attach succeeds and is independently verified "
    "by the repository's harness. This tool never changes any issue state.",
    "No GitHub auto-closing keyword is emitted in stdout, JSON or Markdown.",
    "Commands are suggestions to run on a disposable test host; several weaken "
    "macOS security or require a reboot. Their effects are not observed here.",
    "Reports are byte-stable: no clock, environment value or directory walk "
    "order enters them. The same argv and the same bytes yield the same text.",
)


def remedy(rank: int, title: str, commands: list[str], expected_success: str,
           expected_failure: str, risk: str, sources: list[str],
           note: str = "") -> dict[str, Any]:
    """Build one ranked checklist entry with copy-pasteable commands."""
    return {
        "rank": rank,
        "title": title,
        "commands": list(commands),
        "expected_success_signature": expected_success,
        "expected_failure_signature": expected_failure,
        "risk": risk,
        "sources": sorted(set(sources)),
        "note": note,
    }


RULES: list[dict[str, Any]] = [
    {
        "id": "MACOS_FRIDA_HELPER_KILLED",
        "priority": 10,
        "exclusive": True,
        "patterns": [
            r"frida-helper[^\n]{0,120}(?:crash|kill|die|appear)",
            r"oops[^\n]{0,120}frida-helper",
        ],
        "summary": (
            "The kernel killed frida-helper. On Apple silicon this is the "
            "signature of an arm64e platform binary (/bin, /usr/bin, /usr/sbin, "
            "/System): SIP off alone is not enough and taskgated is the wrong "
            "place to look."
        ),
        "remedies": [
            remedy(
                1, "Confirm the target is an Apple platform binary",
                ["codesign -dvvv /bin/sleep 2>&1 | grep -Ei 'flags|platform identifier|identifier'"],
                "codesign prints Platform identifier=<n> (confirms a platform binary)",
                "PermissionDeniedError: oops, frida-helper appears to have crashed. It may have been killed",
                "none: codesign is read-only",
                [S_Fritap_MACOS],
                "If Platform identifier is absent, re-check the task_for_pid rule instead.",
            ),
            remedy(
                2, "Attach to an ad-hoc signed copy, not the platform original",
                [
                    'WORKDIR=$(mktemp -d)',
                    'cp /bin/sleep "$WORKDIR/sleep"',
                    'lipo -thin arm64e "$WORKDIR/sleep" -output "$WORKDIR/sleep-copy" '
                    '2>/dev/null || cp "$WORKDIR/sleep" "$WORKDIR/sleep-copy"',
                    'codesign -f -s - "$WORKDIR/sleep-copy"',
                    '"$WORKDIR/sleep-copy" 300 & PID=$!',
                    'python3 -m tools.frida_probe --pid "$PID" --hook ExitProcess --timeout 5',
                ],
                "frida.attach(pid) returns a session and the script loads with no PermissionDeniedError",
                "NotSupportedError: unexpected error while probing dyld of target process (if translated)",
                "the copy drops the original entitlements; never use it for production behaviour claims",
                [S_Fritap_MACOS],
                "No root and no reboot needed; this is the recommended first live attempt for /bin/sleep.",
            ),
            remedy(
                3, "Use a non-platform build of the target",
                [
                    'command -v gsleep || brew install coreutils',
                    '"$(command -v gsleep)" 300 & PID=$!',
                    'python3 -m tools.frida_probe --pid "$PID" --hook ExitProcess --timeout 5',
                ],
                "frida.attach(pid) returns a session on a Homebrew/non-platform binary",
                "PermissionDeniedError: oops, frida-helper appears to have crashed. It may have been killed",
                "a different build is not bit-identical to the Apple binary",
                [S_Fritap_MACOS],
            ),
            remedy(
                4, "Only if the original binary is mandatory: test-host boot-arg and root",
                [
                    'csrutil status',
                    '# in Recovery: csrutil disable; then reboot normally',
                    'sudo nvram boot-args="-arm64e_preview_abi thid_should_crash=0 tss_should_crash=0"',
                    '# reboot again, then:',
                    'sudo python3 -m tools.frida_probe --pid "$PID" --hook ExitProcess --timeout 5',
                ],
                "frida.attach(pid) returns a session on the original arm64e binary",
                "PermissionDeniedError: oops, frida-helper appears to have crashed. It may have been killed",
                "HIGH: disables SIP, changes boot args, requires two reboots; disposable host only",
                [S_Fritap_MACOS, S_FRIDA_TROUBLESHOOTING],
            ),
            remedy(
                5, "Move the Wine attach to a native Linux x86_64 host",
                [
                    'uname -m',
                    'cat /proc/sys/kernel/yama/ptrace_scope',
                    'sudo sysctl kernel.yama.ptrace_scope=0',
                    'python3 -m tools.frida_probe --pid "$PID" --hook ExitProcess --timeout 5',
                ],
                "attach returns a session with no task_for_pid or frida-helper error",
                "PermissionDeniedError: unable to access process with pid ... due to system restrictions; try `sudo sysctl kernel.yama.ptrace_scope=0`, or run Frida as root",
                "changes a host sysctl; disposable runner only",
                [S_FRIDA_TROUBLESHOOTING, S_YAMA],
            ),
        ],
    },
    {
        "id": "MACOS_DYLD_PROBE_FAILED",
        "priority": 20,
        "exclusive": True,
        "patterns": [
            r"unexpected error while probing dyld",
            r"probing dyld of target process",
            r"unexpectedly timed out while initializing suspended process",
        ],
        "summary": (
            "Frida failed while enumerating dyld in the target. Known causes on "
            "macOS are Rosetta-translated x86_64 targets (Frida 17 cannot probe "
            "them), the dyld hardening shipped in macOS 15.4, and arm64e "
            "platform binaries."
        ),
        "remedies": [
            remedy(
                1, "Remove Rosetta from the path to the target",
                [
                    'file "$TARGET"',
                    'lipo -info "$TARGET"',
                    '# use a native arm64 build; arch -x86_64 / Rosetta is not attachable',
                    'python3 -m tools.frida_probe --pid "$PID" --hook ExitProcess --timeout 5',
                ],
                "attach succeeds against a native arm64 slice",
                "NotSupportedError: unexpected error while probing dyld of target process",
                "a native arm64 build may not match the x86_64 Windows binary under Wine",
                [S_Fritap_MACOS, S_FRIDA_2772],
                "Wine under Rosetta is the likely cause of the recorded Wine failure.",
            ),
            remedy(
                2, "Retry from a clean, current Frida environment",
                [
                    'python3 -m venv .venv-frida',
                    '. .venv-frida/bin/activate',
                    'python3 -m pip install -U --ignore-installed frida frida-tools',
                    'python3 -c "import frida; print(frida.__version__)"',
                    'python3 -m tools.frida_probe --pid "$PID" --hook ExitProcess --timeout 5',
                ],
                "attach succeeds on a rebuilt environment",
                "NotSupportedError: unexpected error while probing dyld of target process",
                "changes only a local virtualenv",
                [S_FRIDA_3420, S_FRIDA_3391],
                "Upstream #3420 was marked completed after an upgrade. Versions 17.24.3 and "
                "17.27.5 already failed on macOS 27.2.1, so an upgrade alone is not "
                "expected to be sufficient here.",
            ),
            remedy(
                3, "Inspect the pip agent's library-validation flag",
                [
                    'python3 -c \'import frida, pathlib; print(pathlib.Path(frida.__file__).parent / "_frida.abi3.so")\'',
                    'codesign -dvvv "$(python3 -c \'import frida, pathlib; print(pathlib.Path(frida.__file__).parent / "_frida.abi3.so")\')" 2>&1 | grep -Ei \'flags|runtime\'',
                ],
                "codesign shows flags without the 0x20000 library-validation bit",
                "flags=0x20002 (library validation set): shared-cache hooks may install but never fire",
                "read-only diagnostic; re-signing the wheel is an upstream matter",
                [S_FRIDA_3752, S_HARDENED],
                "This is a related macOS 26.x+ follow-up, not a proven cause of the dyld probe failure.",
            ),
            remedy(
                4, "Run the Wine attach on a native Linux x86_64 runner",
                [
                    'uname -m',
                    'cat /proc/sys/kernel/yama/ptrace_scope',
                    'sudo sysctl kernel.yama.ptrace_scope=0',
                    'python3 -m tools.frida_probe --pid "$PID" --hook ExitProcess --timeout 5',
                ],
                "attach returns a session with no dyld probing error",
                "PermissionDeniedError: unable to access process with pid ... due to system restrictions; try `sudo sysctl kernel.yama.ptrace_scope=0`, or run Frida as root",
                "provisioning a separate runner is out of scope for this tool",
                [S_FRIDA_TROUBLESHOOTING, S_YAMA],
                "This avoids SIP, arm64e platform binaries and Rosetta at once.",
            ),
            remedy(
                5, "Use Frida Gadget instead of an attach where the target can be relaunched",
                [
                    '# place FridaGadget.dylib and FridaGadget.config (listen, 127.0.0.1:27042, on_load=wait) beside the target',
                    'DYLD_INSERT_LIBRARIES=/path/to/FridaGadget.dylib "$TARGET" &',
                    'frida -H 127.0.0.1:27042 -l attach.js Gadget',
                ],
                "Gadget accepts a connection and the script loads; no attach/dyld probing is used",
                "dyld reports library validation refusal for the injected dylib",
                "target must be relaunchable and loadable; original entitlements may be dropped",
                [S_FRIDA_GADGET, S_HARDENED],
                "Gadget evidence is not the same as a successful attach; label it separately.",
            ),
        ],
    },
    {
        "id": "LINUX_PTRACE_SCOPE",
        "priority": 30,
        "exclusive": True,
        "patterns": [
            r"ptrace_attach failed",
            r"ptrace_scope",
            r"due to system restrictions",
        ],
        "summary": (
            "Linux Yama is refusing PTRACE_ATTACH. Mode 1 allows only descendants "
            "unless CAP_SYS_PTRACE is held; mode 3 cannot be changed."
        ),
        "remedies": [
            remedy(
                1, "Inspect and lower Yama ptrace_scope",
                [
                    'cat /proc/sys/kernel/yama/ptrace_scope',
                    'sudo sysctl kernel.yama.ptrace_scope=0',
                    'python3 -m tools.frida_probe --pid "$PID" --hook ExitProcess --timeout 5',
                ],
                "attach returns a session; ptrace_scope reads 0",
                "PermissionDeniedError: unable to access process with pid ... due to system restrictions; try `sudo sysctl kernel.yama.ptrace_scope=0`, or run Frida as root",
                "lowers a host security control; disposable runner only",
                [S_FRIDA_TROUBLESHOOTING, S_YAMA],
                "If ptrace_scope is 3 the setting is irreversible: use a different runner.",
            ),
            remedy(
                2, "Run Frida as root with CAP_SYS_PTRACE",
                [
                    'sudo -E python3 -m tools.frida_probe --pid "$PID" --hook ExitProcess --timeout 5',
                ],
                "attach returns a session under root/CAP_SYS_PTRACE",
                "SystemError: attach_to_process PTRACE_ATTACH failed: 1",
                "root privileges on the runner",
                [S_FRIDA_TROUBLESHOOTING, S_YAMA],
            ),
            remedy(
                3, "Spawn the target so Frida is the parent",
                [
                    'python3 -m tools.frida_probe --spawn "$WINE" game.exe --hook ExitProcess --timeout 5',
                ],
                "spawn + attach succeeds because the debugger is the parent (Yama mode 1)",
                "SystemError: attach_to_process PTRACE_ATTACH failed: 1",
                "cannot be used to attach to an already-running process",
                [S_YAMA],
            ),
            remedy(
                4, "Use Gadget via LD_PRELOAD for a relaunchable target",
                [
                    'LD_PRELOAD=/path/to/FridaGadget.so "$TARGET" &',
                    'frida -H 127.0.0.1:27042 -l attach.js Gadget',
                ],
                "Gadget connects and the script loads without PTRACE_ATTACH",
                "Gadget does not appear on 127.0.0.1:27042",
                "target must be relaunchable with LD_PRELOAD honoured",
                [S_FRIDA_GADGET],
            ),
        ],
    },
    {
        "id": "MACOS_TASK_FOR_PID_DENIED",
        "priority": 40,
        "exclusive": False,
        "patterns": [
            r"permissiondeniederror",
            r"task_for_pid",
            r"unable to access process with pid",
            r"from the current user account",
            r"operation not permitted",
        ],
        "summary": (
            "taskgated refused task_for_pid. The deciding factors are the "
            "session (GUI prompt vs SSH), SIP, and the target's signature "
            "(hardened runtime, library validation, get-task-allow)."
        ),
        "remedies": [
            remedy(
                1, "Run the probe from a GUI Terminal so taskgate can prompt",
                [
                    '# from Terminal.app as the local user, NOT over SSH',
                    'python3 -m tools.frida_probe --pid "$PID" --hook ExitProcess --timeout 5',
                ],
                "taskgate prompt is answered and frida.attach(pid) returns a session",
                "PermissionDeniedError: unable to access process with pid ... from the current user account",
                "requires interactive access to the host",
                [S_FRIDA_MACOS],
            ),
            remedy(
                2, "Check SIP and the target's signature",
                [
                    'csrutil status',
                    'codesign -d -vvv "$TARGET" 2>&1 | grep -Ei \'flags|runtime|identifier\'',
                    'codesign -d --entitlements - "$TARGET" 2>&1 | grep -i get-task-allow',
                ],
                "codesign shows an adhoc/unsigned target or get-task-allow present",
                "hardened runtime / library validation with no get-task-allow: injection is refused",
                "none: both commands are read-only",
                [S_Fritap_MACOS, S_HARDENED, S_FRIDA_TROUBLESHOOTING],
            ),
            remedy(
                3, "Headless/SSH only: allow the taskport authorization right",
                [
                    '# WARNING: this weakens security; use a disposable host only',
                    'sudo security authorizationdb write system.privilege.taskport allow',
                    'python3 -m tools.frida_probe --pid "$PID" --hook ExitProcess --timeout 5',
                ],
                "attach succeeds after the authorization right is granted",
                "PermissionDeniedError: unable to access process with pid ... from the current user account",
                "HIGH: weakens host security policy; restore the previous right afterwards",
                [S_FRIDA_TROUBLESHOOTING],
            ),
            remedy(
                4, "For a target you control: re-sign with get-task-allow",
                [
                    'codesign -d --entitlements - --xml "$TARGET" > entitlements.plist',
                    '/usr/libexec/PlistBuddy -c "Add :com.apple.security.get-task-allow bool true" entitlements.plist',
                    'codesign -f -s - --entitlements entitlements.plist "$TARGET"',
                    'python3 -m tools.frida_probe --pid "$PID" --hook ExitProcess --timeout 5',
                ],
                "frida.attach(pid) returns a session on the re-signed build",
                "PermissionDeniedError: unable to access process with pid ... from the current user account",
                "re-signing invalidates notarization; test builds only",
                [S_Fritap_MACOS, S_HARDENED],
            ),
            remedy(
                5, "Avoid task_for_pid entirely on a native Linux runner",
                [
                    'cat /proc/sys/kernel/yama/ptrace_scope',
                    'sudo sysctl kernel.yama.ptrace_scope=0',
                    'python3 -m tools.frida_probe --pid "$PID" --hook ExitProcess --timeout 5',
                ],
                "attach returns a session on Linux with no taskgated error",
                "PermissionDeniedError: unable to access process with pid ... due to system restrictions",
                "requires a separate Linux runner",
                [S_FRIDA_TROUBLESHOOTING, S_YAMA],
            ),
        ],
    },
    {
        "id": "FRIDA_IMPORT_FAILED",
        "priority": 50,
        "exclusive": False,
        "patterns": [
            r"modulenotfounderror[^\n]{0,80}frida",
            r"importerror[^\n]{0,80}frida",
            r"no module named ['\"]?frida",
        ],
        "summary": "The Python client cannot import frida at all.",
        "remedies": [
            remedy(
                1, "Install the Frida client in the active interpreter",
                [
                    'python3 -m pip install -U frida frida-tools',
                    'python3 -c "import frida; print(frida.__version__)"',
                    'python3 -m tools.frida_probe --pid "$PID" --hook ExitProcess --timeout 5',
                ],
                "the version is printed and the probe reaches the attach step",
                "ModuleNotFoundError: No module named 'frida'",
                "changes the local Python environment only",
                [S_FRIDA_MACOS],
            ),
        ],
    },
]


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------
def sha256_file(path: Path) -> str | None:
    import hashlib

    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError(f"{path}: cannot read probe JSON: {exc}") from exc


def load_records(paths: list[Path]) -> list[dict[str, Any]]:
    """Read and validate CLI-supplied probe files; bad input fails closed."""
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in paths:
        if not path.is_file():
            raise ValueError(f"probe file not found: {path}")
        key = str(path)
        if key in seen:
            raise ValueError(f"duplicate probe path: {key}")
        seen.add(key)
        data = load_json(path)
        rows = data if isinstance(data, list) else [data]
        if not rows or not all(isinstance(row, dict) for row in rows):
            raise ValueError(f"{path}: expected an object or non-empty array of objects")
        for index, row in enumerate(rows):
            if row.get("schema_version") != SCHEMA_VERSION:
                raise ValueError(
                    f"{path}[{index}]: unsupported schema_version "
                    f"{row.get('schema_version')!r}; expected {SCHEMA_VERSION}"
                )
            attach = row.get("attach")
            if not isinstance(attach, dict):
                raise ValueError(f"{path}[{index}]: attach must be an object")
            label = row.get("label")
            if not isinstance(label, str) or not label.strip():
                label = f"{path.name}[{index}]"
            records.append({
                "label": label.strip(),
                "path": key,
                "sha256": sha256_file(path),
                "host": row.get("host") if isinstance(row.get("host"), dict) else {},
                "frida": row.get("frida") if isinstance(row.get("frida"), dict) else {},
                "target": row.get("target") if isinstance(row.get("target"), dict) else {},
                "attach": attach,
                "raw": row,
            })
    records.sort(key=lambda item: (item["label"], item["path"]))
    return records


def signature_text(attach: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("error_type", "error_message", "reason"):
        value = attach.get(key)
        if isinstance(value, str):
            parts.append(value)
    return " ".join(parts).lower()


OPTIMISTIC_KEYS = frozenset({"verdict", "claimed_verdict", "claim", "status",
                             "dual_engine", "result"})
OPTIMISTIC_WORDS = re.compile(r"(?i)\b(?:pass|passed|success|ok|match|closed|resolved)\b")


def _claim_kind(value: Any) -> str | None:
    if isinstance(value, bool):
        return "TRUE-LIKE" if value else None
    if not isinstance(value, str) or not OPTIMISTIC_WORDS.search(value):
        return None
    lowered = value.lower()
    if "closed" in lowered or "resolved" in lowered:
        return "CLOSED-LIKE"
    return "PASS-LIKE"


def ignored_claims(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    """List optimistic written claims without echoing their forbidden phrasing."""
    found: list[dict[str, str]] = []

    def walk(value: Any, path: str, depth: int) -> None:
        if depth > 4:
            return
        if isinstance(value, dict):
            for key in sorted(value):
                child = value[key]
                field = f"{path}.{key}" if path else key
                if key in OPTIMISTIC_KEYS:
                    kind = _claim_kind(child)
                    if kind:
                        found.append({"record": record["label"], "field": field,
                                      "claim_kind": kind, "action": "IGNORED"})
                walk(child, field, depth + 1)
        elif isinstance(value, list) and len(value) <= 20:
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]", depth + 1)

    for record in records:
        walk(record["raw"], "", 0)
    found.sort(key=lambda item: (item["record"], item["field"], item["claim_kind"]))
    return found


# ---------------------------------------------------------------------------
# Classification and report assembly
# ---------------------------------------------------------------------------
def match_rules(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the matched rules in priority order; exclusive rules stop the scan."""
    matched: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        text = signature_text(record["attach"])
        for rule in sorted(RULES, key=lambda item: item["priority"]):
            if not any(re.search(pattern, text) for pattern in rule["patterns"]):
                continue
            if rule["id"] not in seen:
                seen.add(rule["id"])
                matched.append(rule)
            if rule["exclusive"]:
                break
    matched.sort(key=lambda item: item["priority"])
    return matched


def build_report(records: list[dict[str, Any]]) -> dict[str, Any]:
    matched = match_rules(records)
    checklist: list[dict[str, Any]] = []
    for rule in matched:
        for entry in rule["remedies"]:
            item = dict(entry)
            item["step"] = len(checklist) + 1
            item["rule"] = rule["id"]
            checklist.append(item)
    status = STATUS_CHECKLIST if checklist else STATUS_MANUAL
    if not checklist:
        checklist = [{
            "step": 1,
            "rule": "UNCLASSIFIED_FAILURE",
            "rank": 0,
            "title": "No confident remedy: capture and escalate the exact signature",
            "commands": [
                '# record error_type, error_message, host OS/arch, frida version,',
                '# target path/arch/signature and the exact command that failed;',
                '# then supply those probe records to this tool again',
            ],
            "expected_success_signature": "a recognized signature appears in a new probe record",
            "expected_failure_signature": "unknown or absent error signature",
            "risk": "none: no command is executed by this tool",
            "sources": [S_FRIDA_TROUBLESHOOTING],
            "note": "Failing closed: no ranked remedy is asserted for an unknown signature.",
        }]
    sources = sorted({source for rule in matched for source in {
        s for entry in rule["remedies"] for s in entry["sources"]
    }} | {S_FRIDA_TROUBLESHOOTING})
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "status": status,
        "issue": {
            "number": ISSUE_NUMBER,
            "state": ISSUE_STATE,
            "stays_open_until": "a real attach succeeds and is independently verified",
            "modified_by_tool": False,
        },
        "verdict": {
            "attach_proven": ATTACH_PROVEN,
            "dual_engine": DUAL_ENGINE,
            "reason": (
                "This tool only ranks remedies from recorded error signatures. "
                "It cannot observe an attach or a Unicorn result."
            ),
        },
        "matched_rules": [rule["id"] for rule in matched],
        "rule_summaries": {rule["id"]: rule["summary"] for rule in matched},
        "checklist": checklist,
        "ignored_claims": ignored_claims(records),
        "records": [{
            "label": item["label"],
            "path": item["path"],
            "sha256": item["sha256"],
            "host": item["host"],
            "frida": item["frida"],
            "target": item["target"],
            "error_type": item["attach"].get("error_type"),
            "error_message": item["attach"].get("error_message"),
        } for item in records],
        "sources": sources,
        "limits": list(LIMITS),
    }


# ---------------------------------------------------------------------------
# Deterministic rendering and output guards
# ---------------------------------------------------------------------------
def _cell(value: Any) -> str:
    return str(value if value is not None else "-").replace("|", "\\|").replace("\n", " ")


def render_text(report: dict[str, Any]) -> str:
    out: list[str] = [
        f"Frida host remediation — issue #{report['issue']['number']}: "
        f"{report['issue']['state']}",
        f"Attach proven: NO | dual-engine: {report['verdict']['dual_engine']} | "
        f"report: {report['status']}",
        f"#171 stays OPEN until {report['issue']['stays_open_until']}.",
        "",
        f"Matched signatures: {', '.join(report['matched_rules']) or 'none'}",
        "",
        "Ranked checklist:",
    ]
    for item in report["checklist"]:
        out.append(
            f"{item['step']}. [{item['rule']}/{item['rank']}] {item['title']}")
        for command in item["commands"]:
            out.append(f"   $ {command}")
        out.append(f"   success: {item['expected_success_signature']}")
        out.append(f"   still blocked: {item['expected_failure_signature']}")
        out.append(f"   risk: {item['risk']}")
    if report["ignored_claims"]:
        out += ["", "Ignored written claims:"]
        for claim in report["ignored_claims"]:
            out.append(f"- {claim['record']}: {claim['field']} = "
                       f"{claim['claim_kind']} ({claim['action']})")
    out += ["", "Limits:"]
    out.extend(f"- {limit}" for limit in report["limits"])
    out.append("")
    return "\n".join(out)


def render_markdown(report: dict[str, Any]) -> str:
    out: list[str] = [
        "# Frida host remediation (issue #171)",
        "",
        f"- Issue: #{report['issue']['number']} — {report['issue']['state']} "
        "(unchanged by this tool)",
        f"- Attach proven: NO",
        f"- Dual-engine status: {report['verdict']['dual_engine']}",
        f"- Report status: {report['status']}",
        f"- Matched signatures: {', '.join(report['matched_rules']) or 'none'}",
        "",
        f"> #171 stays OPEN until {report['issue']['stays_open_until']}. "
        "This tool ranks remedies only; it never attaches or changes an issue.",
        "",
        "## Ranked checklist",
        "",
    ]
    for item in report["checklist"]:
        out += [f"### {item['step']}. {item['title']} "
                f"(`{item['rule']}/{item['rank']}`)", ""]
        for command in item["commands"]:
            out += ["```sh", command, "```", ""]
        out += [
            f"- Expected on success: {_cell(item['expected_success_signature'])}",
            f"- Expected if still blocked: {_cell(item['expected_failure_signature'])}",
            f"- Risk: {_cell(item['risk'])}",
        ]
        if item.get("note"):
            out.append(f"- Note: {_cell(item['note'])}")
        out.append(f"- Sources: {', '.join(item['sources'])}")
        out.append("")
    out += ["## Supplied probe records", "",
            "| Label | File | SHA-256 | Error type |", "|---|---|---|---|"]
    for record in report["records"]:
        out.append(f"| {_cell(record['label'])} | {_cell(record['path'])} | "
                   f"{_cell(record['sha256'])} | {_cell(record['error_type'])} |")
    out += ["", "## Ignored written claims", ""]
    if report["ignored_claims"]:
        out += ["| Record | Field | Kind | Action |", "|---|---|---|---|"]
        for claim in report["ignored_claims"]:
            out.append(f"| {_cell(claim['record'])} | {_cell(claim['field'])} | "
                       f"{_cell(claim['claim_kind'])} | {_cell(claim['action'])} |")
    else:
        out.append("None.")
    out += ["", "## Sources", ""]
    out.extend(f"- {source}" for source in report["sources"])
    out += ["", "## Limits", ""]
    out.extend(f"- {limit}" for limit in report["limits"])
    out.append("")
    return "\n".join(out)


def assert_no_forbidden_output(*texts: str) -> None:
    """Fail closed if any output would overstate a proof or close an issue."""
    for text in texts:
        match = CLOSING_KEYWORD.search(text)
        if match:
            raise ValueError(f"refusing auto-closing keyword: {match.group(0)!r}")
        lowered = text.lower()
        for phrase in FORBIDDEN_CLAIMS:
            if phrase in lowered:
                raise ValueError(f"refusing forbidden claim phrase: {phrase!r}")


def make_command(args: argparse.Namespace) -> str:
    parts = ["python3", "-m", "tools.frida_host_remediation"]
    for path in args.probe:
        parts += ["--probe", shlex.quote(str(path))]
    if args.probe_list is not None:
        parts += ["--probe-list", shlex.quote(str(args.probe_list))]
    if args.out is not None:
        parts += ["--out", shlex.quote(str(args.out))]
    if args.json_out is not None:
        parts += ["--json", shlex.quote(str(args.json_out))]
    return " ".join(parts)


def expand_probe_list(path: Path) -> list[Path]:
    data = load_json(path)
    if not isinstance(data, list) or not all(
            isinstance(item, str) and item.strip() for item in data):
        raise ValueError(f"{path}: expected an array of non-empty path strings")
    return [Path(item) if Path(item).is_absolute() else path.parent / Path(item)
            for item in data]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", action="append", default=[], type=Path,
                    help="probe record JSON (object or array; repeatable)")
    ap.add_argument("--probe-list", type=Path,
                    help="JSON array of probe record paths (alternative to repeats)")
    ap.add_argument("--out", type=Path, help="write the Markdown checklist here")
    ap.add_argument("--json", dest="json_out", type=Path,
                    help="write the machine-readable report here")
    args = ap.parse_args(argv)

    try:
        paths = list(args.probe)
        if args.probe_list is not None:
            paths.extend(expand_probe_list(args.probe_list))
        if not paths:
            raise ValueError("supply at least one --probe or --probe-list")
        for path in paths:
            if not path.is_file():
                raise ValueError(f"probe file not found: {path}")
        records = load_records(paths)
        if not records:
            raise ValueError("no probe records supplied")
        report = build_report(records)
        report["command"] = make_command(args)
        text = render_text(report)
        markdown = render_markdown(report)
        json_text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        assert_no_forbidden_output(text, markdown, json_text)
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(markdown, encoding="utf-8", newline="\n")
        if args.json_out is not None:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(json_text, encoding="utf-8", newline="\n")
    except ValueError as exc:
        print(f"frida_host_remediation: {exc}", file=sys.stderr)
        return EXIT_USAGE

    print(text, end="")
    return EXIT_CHECKLIST if report["status"] == STATUS_CHECKLIST else EXIT_MANUAL


if __name__ == "__main__":
    sys.exit(main())
