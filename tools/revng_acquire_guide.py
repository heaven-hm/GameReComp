#!/usr/bin/env python3
"""Deterministic, read-only rev.ng acquisition guide for issue #25.

This program prints *how* to obtain a pinned rev.ng build and *what* counts as
real ``multilifter --lifter revng=<report>`` evidence. It never installs,
downloads, pulls, executes, imports or probes rev.ng; it makes no network or
``gh`` call; and it emits no PASS verdict. A printed guide is not an installed
tool and not a decompilation result.

All acquisition commands are pinned to exact, recorded artifacts. The container
pin is an immutable OCI digest; the native pin is the exact archive URL that
the official ``master`` installer resolved to when this guide was written. The
unpinned upstream helper is shown only so a reader can recognise it and is
explicitly marked as NOT a version pin.

Usage
-----
    python3 tools/revng_acquire_guide.py
    python3 tools/revng_acquire_guide.py --section container
    python3 tools/revng_acquire_guide.py --json
    python3 tools/revng_acquire_guide.py --out PATH
    python3 tools/revng_acquire_guide.py --claim 'revng is installed here'

Exit codes: 0 = guidance printed, 2 = bad CLI arguments, 3 = a claim was
rejected (or merely offered as evidence; claims are never evidence).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Mapping

SCHEMA_VERSION = 1
GENERATOR = "tools/revng_acquire_guide.py"
ISSUE = "#25"
ISSUE_STATE = "OPEN"

# Authoritative upstream sources, read from the official docs/repository on
# 2026-11-25. The rendered site is https://docs.rev.ng; its source of truth for
# installation is the markdown file cited below.
AUTHORITATIVE_URLS: Mapping[str, str] = {
    "documentation": "https://docs.rev.ng",
    "installation_source": (
        "https://github.com/revng/revng/blob/master/share/doc/revng/"
        "user-manual/initial-setup.md"
    ),
    "repository": "https://github.com/revng/revng",
    "install_script": (
        "https://rev.ng/downloads/revng-distributable/master/install.sh"
    ),
    "container_registry": "https://hub.docker.com/r/revng/revng",
    "licence": "https://github.com/revng/revng/blob/master/LICENSE",
}

# Immutable pins recorded 2026-11-25 from the official distribution points.
# The Docker Hub `master` and `latest` tags both pointed at this digest (pushed
# 2026-11-23T13:37:59Z). The archive URL is what the official `master`
# install.sh resolved to; its path embeds the distributable commit, and the S3
# object reported content-length 564214476 with Last-Modified
# 2026-11-23T12:53:01Z. No sha256 is published for that archive, so the guide
# instructs the operator to compute and record one locally.
PINNED_IMAGE = (
    "revng/revng@sha256:"
    "aaafb2bc57cdc4d6f499b99453a48dfd622ef5611f5eb57927e43b80d03063ac"
)
PINNED_IMAGE_OBSERVED = "2026-11-25 (tag master; image pushed 2026-11-23)"
PINNED_ARCHIVE_URL = (
    "https://revng-website-downloads.s3.fr-par.scw.cloud/binary-archives/"
    "public/linux-x86-64/revng-distributable-public-demo/default/"
    "none_7361e00740573e8990d13651cedd5314e89e5adc.tar.xz"
)
PINNED_ARCHIVE_COMMIT = "7361e00740573e8990d13651cedd5314e89e5adc"
PINNED_ARCHIVE_BYTES = 564214476
PINNED_ARCHIVE_LAST_MODIFIED = "2026-11-23T12:53:01Z"
PINNED_DOC_COMMIT = "1af39407b1826f675ee881faa151a13b3a0a21c9"
PINNED_IMAGE_SIZE_HINT = "~1.17 GB (amd64 image at pin time)"

LICENCE_SUMMARY = (
    "GPL-2.0 as a whole (the project depends on GPLv2 components, in particular "
    "QEMU); individual files are MIT. Keep rev.ng as a separate external "
    "process; do not link it into the mod DLL. This is a provenance note, not "
    "legal advice."
)

HOST_REQUIREMENTS = (
    "Native rev.ng runs on Linux x86-64 only (official initial-setup.md).",
    "macOS, including Apple Silicon/arm64, has no native build: use Docker "
    "(or colima) with `--platform linux/amd64`.",
    "Windows is supported through WSL with a Linux x86-64 userland.",
    f"Disk/network for the pinned image ({PINNED_IMAGE_SIZE_HINT}) or the "
    f"native archive ({PINNED_ARCHIVE_BYTES} bytes).",
    "No game assets are needed: rev.ng analyses the PE as data. The pinned "
    "image is linux/amd64; emulation on arm64 is slower but supported.",
)

CHECKLIST = (
    "Record host facts (`uname -a`, `docker --version`, "
    "`docker image inspect <pinned-image>` or `sha256sum` of the archive). "
    "A probe result of no-core-toolchain-detected is not an installation.",
    "Acquire only through the pinned commands; save pull/download output and "
    "the resolved image digest or archive sha256 in the evidence directory.",
    "Show the binary runs from that pin: `revng --version` and "
    "`revng project artifact --help`; keep the captured output. A help banner "
    "establishes presence, not decompilation quality.",
    "Hash the original PE and each candidate source; record provider version, "
    "function address, ABI adapter version, test-suite hash and case seeds.",
    "Drive the real per-function comparison with "
    "`tools/lift/difftest.py --project --json <report.json>`; original bytes "
    "and candidate must both run with resolved dependencies. Faulted, empty "
    "or untestable comparisons are not PASS.",
    "Select with exactly `python3 tools/lift/multilifter.py --lifter "
    "revng=<report.json> --json <decision.json>`. Require decision `PROMOTE`, "
    "`chosen_lifter` = `revng`, and zero diverged cases on a real report file. "
    "Inline JSON, invented verdicts and copied reports are refused.",
    "If revng diverges or faults, record the reason and test the PCRecomp "
    "fallback independently; never label FAILED, BLOCKED or UNRESOLVED as "
    "verified.",
    "Repeat the comparison and selection and compare decision digests; "
    "determinism is evidence, while correctness stays scoped to driven cases.",
    "Feed sealed reports to `tools/issue25_evidence.py`; its CLOSABLE verdict "
    "is permission to review, not an issue state change. Issue " + ISSUE +
    " stays OPEN until a real revng PASS report exists.",
    "Never hand-edit issue state, never emit a GitHub auto-closing keyword, "
    "and never let this guide stand in for a real provider run.",
)

LIMITS = (
    "This program is read-only: it runs no installer, container, lifter, "
    "`gh` command or network request, and emits no PASS verdict.",
    "The pins record what upstream published on 2026-11-25; re-verify the "
    "digest or archive hash before trusting a later acquisition.",
    "Licence strings are recorded upstream claims, not legal advice.",
    "A CLOSABLE evidence audit is scoped to the reports supplied to it; this "
    "guide cannot establish whole-game equivalence.",
)

# Patterns for claims that must never be accepted or emitted. They are matched
# against caller-supplied text and against every line this program prints.
FAKE_CLAIM_PATTERNS = (
    re.compile(
        r"(?i)\b(?:revng|rev\.ng)\b[\s\S]{0,80}?\b(?:is|was|has been|is now)\s+"
        r"(?:already\s+)?(?:installed|detected|available|present|verified)\b"
    ),
    re.compile(r"(?i)\binstall(?:ed|ing|ation of)\s+(?:revng|rev\.ng)\b"),
    re.compile(r"(?i)\b(?:revng|rev\.ng)\s+(?:passes|passed|verified)\b"),
    re.compile(
        r"(?i)\b(?:revng|rev\.ng)\s+"
        r"(?:achieved|produced|returned|gave)\s+"
        r"(?:a\s+)?(?:PASS|verified\s+result)\b"
    ),
    re.compile(r"(?i)\b(?:PASS|VERIFIED)\s+(?:for|from|by)\s+(?:revng|rev\.ng)\b"),
)

# Mirrors tools/issue25_evidence.py, with extra forms such as "issue 25 is
# closed". Error messages never quote the matched text, so a refusal cannot
# itself emit an auto-closing keyword.
CLOSING_KEYWORD_PATTERNS = (
    re.compile(
        r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+"
        r"(?:(?:issue|pr|pull\s+request)\s*)?#\d+\b"
    ),
    re.compile(
        r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+"
        r"(?:issue|pr|pull\s+request)\s+\d+\b"
    ),
    re.compile(r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+gh-\d+\b"),
    re.compile(
        r"(?i)\b(?:issue|pr)\s*#?\d+\s+(?:is|was|has been)\s+"
        r"(?:closed|fixed|resolved)\b"
    ),
)


class FakeInstallClaim(ValueError):
    """A caller asserted installation or a PASS that no report supports."""


class UnsafeEmission(ValueError):
    """Text contains an auto-closing keyword and must not be printed."""


def closing_keyword(text: str):
    """Return the first auto-closing keyword match, or ``None``."""
    for pattern in CLOSING_KEYWORD_PATTERNS:
        match = pattern.search(text)
        if match:
            return match
    return None


def validate_claim(text: str) -> None:
    """Fail closed on fake installation/PASS claims and closing requests.

    A benign claim still carries no evidentiary weight; callers must not treat
    a successful return as a PASS.
    """
    for pattern in FAKE_CLAIM_PATTERNS:
        if pattern.search(text):
            raise FakeInstallClaim(
                "refusing a claim of installation or PASS: claims are not evidence")
    if closing_keyword(text) is not None:
        raise UnsafeEmission(
            "refusing a request to change issue state: no issue operation is performed")


def assert_emission_safe(text: str) -> None:
    """Guard every output path; never echo the offending text in the error."""
    validate_claim(text)
    if closing_keyword(text) is not None:
        raise UnsafeEmission("refusing to emit an auto-closing keyword")


def _sources() -> str:
    lines = ["## Authoritative sources"]
    lines += [f"- {name}: {url}" for name, url in AUTHORITATIVE_URLS.items()]
    lines.append(
        f"- revng/revng master commit at research time: {PINNED_DOC_COMMIT}")
    return "\n".join(lines)


def _pins() -> str:
    return "\n".join(
        [
            "## Version pins (recorded 2026-11-25)",
            f"- Container: `{PINNED_IMAGE}`",
            f"  Observed: {PINNED_IMAGE_OBSERVED}. Use the digest, not `latest`.",
            f"- Native archive: `{PINNED_ARCHIVE_URL}`",
            f"  Distributable commit: `{PINNED_ARCHIVE_COMMIT}`; "
            f"{PINNED_ARCHIVE_BYTES} bytes; Last-Modified "
            f"{PINNED_ARCHIVE_LAST_MODIFIED}. Compute and record a local "
            "sha256 after download.",
            f"- Documentation source commit: `{PINNED_DOC_COMMIT}`.",
            "- `curl -L -s " + AUTHORITATIVE_URLS["install_script"] +
            " | bash` follows the moving `master` channel: it is NOT a "
            "version pin and is shown only for recognition.",
        ]
    )


def _hosts() -> str:
    return "\n".join(
        ["## Host requirements", *[f"- {item}" for item in HOST_REQUIREMENTS]]
    )


def _container() -> str:
    return "\n".join(
        [
            "## Acquisition A - pinned container (recommended on macOS arm64)",
            "```sh",
            f"docker pull {PINNED_IMAGE}",
            "# Linux x86-64 host:",
            f"docker run --rm -it {PINNED_IMAGE} bash",
            "# macOS / Apple Silicon host (emulated amd64):",
            f"docker run --platform linux/amd64 --rm -it {PINNED_IMAGE} bash",
            "# Inside the container, record the tool version and help output:",
            "revng --version | tee revng-version.txt",
            "revng project artifact --help | tee revng-help.txt",
            "```",
            "The `--platform linux/amd64` flag is required on Apple Silicon "
            "(official initial-setup.md). Image presence is not proof of a "
            "successful lifter run.",
        ]
    )


def _native() -> str:
    return "\n".join(
        [
            "## Acquisition B - pinned native archive (Linux x86-64 only)",
            "```sh",
            f"curl -fL --retry 3 -o revng-distributable.tar.xz '{PINNED_ARCHIVE_URL}'",
            "sha256sum revng-distributable.tar.xz | tee revng-archive.sha256",
            "mkdir -p revng",
            "tar -xJf revng-distributable.tar.xz --strip-components=1 -C revng",
            "cd revng && source ./environment",
            "revng --version | tee revng-version.txt",
            "```",
            "Expect exactly " + str(PINNED_ARCHIVE_BYTES) + " bytes and "
            "Last-Modified " + PINNED_ARCHIVE_LAST_MODIFIED + ". A different "
            "archive is a different pin; stop and re-review.",
        ]
    )


def _checklist() -> str:
    lines = [
        "## Fail-closed checklist for real `multilifter --lifter "
        "revng=<report>` evidence",
    ]
    lines += [f"{index}. {item}" for index, item in enumerate(CHECKLIST, 1)]
    lines += [
        "",
        "NOT PERMITTED:",
        "- Treat this guide or a probe hit as proof of any installation.",
        "- Write an invented report and feed it to multilifter; only a real "
        "difftest report file is accepted.",
        "- Treat a build, a help banner, a synthetic report or a single "
        "arbitrary seed as a PASS verdict.",
        "- Change the state of " + ISSUE + "; this program never calls `gh` "
        "or the network.",
    ]
    return "\n".join(lines)


def _status() -> str:
    return "\n".join(
        [
            "## Status",
            "UNVERIFIED / NO TOOL INSTALLED BY THIS GUIDE",
            f"Issue {ISSUE}: {ISSUE_STATE}. It stays OPEN until a real revng "
            "PASS report exists.",
            "This program prints guidance only. It does not claim revng runs "
            "on any host and it does not produce a PASS verdict.",
            "",
            "Licence: " + LICENCE_SUMMARY,
        ]
    )


def _limits() -> str:
    return "\n".join(["## Limits", *[f"- {item}" for item in LIMITS]])


SECTIONS = {
    "status": _status,
    "sources": _sources,
    "pins": _pins,
    "host_requirements": _hosts,
    "container": _container,
    "native": _native,
    "checklist": _checklist,
    "limits": _limits,
}
SECTION_ORDER = tuple(SECTIONS)


def build_report() -> dict:
    """Machine-readable form of the same facts (no timestamps, no host data)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "status": "UNVERIFIED_NOT_INSTALLED",
        "issue": {
            "number": 25,
            "state": ISSUE_STATE,
            "condition": "stays OPEN until a real revng PASS report exists",
        },
        "authoritative_urls": dict(AUTHORITATIVE_URLS),
        "documentation_commit": PINNED_DOC_COMMIT,
        "version_pins": {
            "container_image": PINNED_IMAGE,
            "container_observed": PINNED_IMAGE_OBSERVED,
            "native_archive_url": PINNED_ARCHIVE_URL,
            "native_archive_commit": PINNED_ARCHIVE_COMMIT,
            "native_archive_bytes": PINNED_ARCHIVE_BYTES,
            "native_archive_last_modified": PINNED_ARCHIVE_LAST_MODIFIED,
        },
        "host_requirements": list(HOST_REQUIREMENTS),
        "licence": LICENCE_SUMMARY,
        "checklist": list(CHECKLIST),
        "limits": list(LIMITS),
    }


def render_text(section: str = "all") -> str:
    """Render one section, or the whole guide, as deterministic markdown."""
    if section not in SECTIONS and section != "all":
        raise ValueError(f"unknown section {section!r}")
    names = SECTION_ORDER if section == "all" else (section,)
    header = (
        "# rev.ng acquisition guide (read-only)"
        "\n\nThis program does not install rev.ng and emits no PASS verdict.\n"
    )
    return header + "\n\n".join(SECTIONS[name]() for name in names) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="read-only, version-pinned rev.ng acquisition guide (issue #25)")
    parser.add_argument("--section", choices=("all",) + SECTION_ORDER, default="all")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="emit the machine-readable guide")
    parser.add_argument("--out", metavar="PATH", help="also write the guide to PATH")
    parser.add_argument("--claim", metavar="TEXT",
                        help="test a claim; claims are never accepted as evidence")
    args = parser.parse_args(argv)

    if args.claim is not None:
        try:
            validate_claim(args.claim)
        except (FakeInstallClaim, UnsafeEmission):
            refusal = (
                "REJECTED: no installation, PASS or issue-state claim is "
                "accepted as evidence. A real revng PASS report is required; "
                + ISSUE + " stays OPEN."
            )
            assert_emission_safe(refusal)
            print(refusal)
            return 3
        refusal = (
            "NOT_ACCEPTED_AS_EVIDENCE: a claim is not a provider run. "
            + ISSUE + " stays OPEN until a real revng PASS report exists."
        )
        assert_emission_safe(refusal)
        print(refusal)
        return 3

    text = (
        json.dumps(build_report(), indent=2, sort_keys=True) + "\n"
        if args.as_json else render_text(args.section)
    )
    assert_emission_safe(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
