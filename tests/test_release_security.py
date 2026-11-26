"""Guard the two OpenQodex release-security findings."""
import re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github/workflows/release.yml"
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))
LOCK = ROOT / "Cargo.lock"

def test_all_actions_in_every_workflow_are_pinned():
    assert WORKFLOWS, "no workflows found"
    for workflow in WORKFLOWS:
        text = workflow.read_text()
        uses = re.findall(r"uses:\s*([^\s]+)", text)
        for action in uses:
            repo, _, ref = action.partition("@")
            assert repo and ref, f"unpinned action in {workflow.name}: {action}"
            assert re.fullmatch(r"[0-9a-f]{40}", ref.split(" #")[0]), f"unpinned action in {workflow.name}: {action}"

def test_url_crate_is_patched():
    text = LOCK.read_text()
    m = re.search(r'\[\[package\]\]\nname = "url"\nversion = "([^"]+)"', text)
    assert m, "url package missing from Cargo.lock"
    major, minor, patch = (int(x) for x in m.group(1).split("."))
    assert (major, minor, patch) >= (2, 5, 9), m.group(1)

def test_no_workflow_input_interpolated_into_run_scripts():
    lines = WF.read_text().splitlines()
    in_run = False
    for line in lines:
        if re.match(r"^\s+run:\s*\|", line):
            in_run = True
            continue
        if in_run and re.match(r"^\s+\S", line) and not line.lstrip().startswith("#"):
            if line.strip() == "":
                continue
            for untrusted in ("inputs.tag", "github.ref_name", "matrix.archive"):
                assert untrusted not in line, f"untrusted expression {untrusted} in run script: {line.strip()}"
        elif in_run and not line.startswith(" "):
            in_run = False


def test_matrix_archive_names_are_static_suffixes():
    text = WF.read_text()
    archives = re.findall(r"archive:\s*([^\s]+)", text)
    assert archives, "no matrix archives found"
    for archive in archives:
        assert "${{" not in archive, f"template expression in matrix archive: {archive}"


def test_release_tag_is_validated_before_use():
    text = WF.read_text()
    v = text.index("Validate release tag")
    first_use = text.index('tag="$TAG"')
    assert v < first_use, "tag is used in a shell before validation"
    assert "^v[0-9]+\\.[0-9]+\\.[0-9]+$" in text, "tag format gate missing"
