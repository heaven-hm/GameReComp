"""Tests for the read-only, version-pinned rev.ng acquisition guide.

Issue #25 cannot be closed by documentation: these tests pin the evidence
discipline instead. They check that the tool and doc cite the authoritative
upstream URLs, carry exact version pins, refuse fake installation/PASS claims,
and never emit a GitHub auto-closing keyword.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import revng_acquire_guide as guide  # noqa: E402

TOOL = ROOT / "tools" / "revng_acquire_guide.py"
DOC = ROOT / "docs" / "REVNG_ACQUISITION.md"

# Independent of the tool's own matcher: mirrors tools/issue25_evidence.py.
CLOSING_KEYWORD = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+"
    r"(?:(?:issue|pr|pull\s+request)\s*)?#?\d+\b"
    r"|\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+gh-\d+\b"
)


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(scope="module")
def guide_text() -> str:
    result = run_cli()
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def guide_json() -> dict:
    result = run_cli("--json")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "url",
    [
        "https://docs.rev.ng",
        "https://github.com/revng/revng/blob/master/share/doc/revng/"
        "user-manual/initial-setup.md",
        "https://github.com/revng/revng",
        "https://rev.ng/downloads/revng-distributable/master/install.sh",
        "https://hub.docker.com/r/revng/revng",
        "https://github.com/revng/revng/blob/master/LICENSE",
    ],
)
def test_authoritative_url_is_named_by_tool_and_doc(url, guide_text, guide_json, doc_text):
    assert url in guide_text
    assert url in doc_text
    assert url in guide_json["authoritative_urls"].values()


def test_container_pin_is_an_exact_oci_digest(guide_text, guide_json, doc_text):
    matches = re.findall(r"revng/revng@sha256:([0-9a-f]{64})", guide_text)
    assert matches, "no 64-hex container digest pin in guide"
    assert set(matches) == {
        "aaafb2bc57cdc4d6f499b99453a48dfd622ef5611f5eb57927e43b80d03063ac"
    }
    assert guide_json["version_pins"]["container_image"] == guide.PINNED_IMAGE
    assert guide.PINNED_IMAGE in doc_text
    assert "latest" in guide_text and "Use the digest, not `latest`" in guide_text


def test_native_archive_pin_is_exact(guide_text, guide_json, doc_text):
    assert guide.PINNED_ARCHIVE_URL in guide_text
    assert guide.PINNED_ARCHIVE_URL in doc_text
    assert guide_json["version_pins"]["native_archive_url"] == guide.PINNED_ARCHIVE_URL
    assert "7361e00740573e8990d13651cedd5314e89e5adc" in guide_text
    assert str(guide.PINNED_ARCHIVE_BYTES) in guide_text
    assert guide_json["version_pins"]["native_archive_bytes"] == 564214476


def test_moving_master_installer_is_not_presented_as_a_pin(guide_text, doc_text):
    assert "NOT a version pin" in guide_text
    assert "**NOT a version pin**" in doc_text


@pytest.mark.parametrize(
    "claim",
    [
        "revng is installed on this host",
        "revng was detected on the runner",
        "revng has been installed successfully",
        "installed revng from source",
        "revng passed the differential gate",
        "PASS for revng is recorded",
    ],
)
def test_fake_install_or_pass_claim_rejected_by_api(claim):
    with pytest.raises(guide.FakeInstallClaim):
        guide.validate_claim(claim)


def test_fake_install_claim_rejected_by_cli_without_echoing_it():
    result = run_cli("--claim", "revng is installed on this host")
    assert result.returncode == 3
    assert result.stdout.startswith("REJECTED:")
    assert "installed on this host" not in result.stdout
    assert "stays OPEN" in result.stdout
    assert CLOSING_KEYWORD.search(result.stdout) is None


def test_benign_claim_is_still_not_accepted_as_evidence():
    result = run_cli("--claim", "I ran a build")
    assert result.returncode == 3
    assert "NOT_ACCEPTED_AS_EVIDENCE" in result.stdout
    assert "stays OPEN" in result.stdout


@pytest.mark.parametrize("section", guide.SECTION_ORDER + ("all",))
def test_no_closing_keyword_in_any_section(section):
    result = run_cli("--section", section)
    assert result.returncode == 0, result.stderr
    assert CLOSING_KEYWORD.search(result.stdout) is None


def test_no_closing_keyword_in_json_or_doc(guide_text, guide_json, doc_text):
    json_text = json.dumps(guide_json)
    for text in (guide_text, json_text, doc_text):
        assert CLOSING_KEYWORD.search(text) is None


def test_issue_state_change_request_is_rejected_without_leaking_keyword():
    result = run_cli("--claim", "fixes #25")
    assert result.returncode == 3
    assert result.stdout.startswith("REJECTED:")
    assert "#25" in result.stdout  # only the generic refusal, not the verb
    assert CLOSING_KEYWORD.search(result.stdout) is None


def test_no_install_or_pass_claim_in_guide_or_doc(guide_text, doc_text):
    for text in (guide_text, doc_text):
        guide.assert_emission_safe(text)
    assert "UNVERIFIED / NO TOOL INSTALLED BY THIS GUIDE" in guide_text
    assert "emits no PASS verdict" in guide_text
    assert "Issue #25 stays OPEN until a real revng PASS report exists." in doc_text


def test_json_issue_state_remains_open(guide_json):
    assert guide_json["schema_version"] == 1
    assert guide_json["status"] == "UNVERIFIED_NOT_INSTALLED"
    assert guide_json["issue"] == {
        "number": 25,
        "state": "OPEN",
        "condition": "stays OPEN until a real revng PASS report exists",
    }


def test_checklist_requires_real_multilifter_report(guide_text, doc_text):
    for text in (guide_text, doc_text):
        assert "`multilifter --lifter revng=<report>`" in text or (
            "`multilifter --lifter " in text and "revng=<report.json>" in text
        )
        assert "tools/lift/difftest.py --project --json" in text
        assert "sha256sum" in text
        assert "Inline JSON, invented verdicts and copied reports are refused" in text
        assert "chosen_lifter` = `revng`" in text
        assert "zero diverged cases" in text


def test_host_requirements_and_licence_are_stated(guide_text, doc_text):
    for text in (guide_text, doc_text):
        assert "Linux x86-64 only" in text
        assert "--platform linux/amd64" in text
        assert "colima" in text
        assert "GPL-2.0" in text
        assert "MIT" in text
        assert "QEMU" in text


def test_output_is_deterministic_and_offline(guide_text):
    assert run_cli().stdout == guide_text
    assert run_cli("--json").stdout == run_cli("--json").stdout
    assert "\n" not in guide.PINNED_IMAGE_OBSERVED
    source = TOOL.read_text(encoding="utf-8")
    assert "import subprocess" not in source
    assert "import urllib" not in source
    assert "import socket" not in source


def test_out_file_matches_stdout_and_sections_are_consistent(tmp_path, guide_text):
    out = tmp_path / "guide.md"
    result = run_cli("--out", str(out))
    assert result.returncode == 0
    assert out.read_text(encoding="utf-8") == result.stdout == guide_text
    section = run_cli("--section", "container")
    assert section.returncode == 0
    body = guide.SECTIONS["container"]()
    assert body in guide_text
    assert section.stdout.rstrip().endswith(body.strip())
    with pytest.raises(ValueError, match="unknown section"):
        guide.render_text("not-a-section")
