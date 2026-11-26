"""Guard the two OpenQodex release-security findings."""
import re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github/workflows/release.yml"
LOCK = ROOT / "Cargo.lock"

def test_all_actions_pinned_to_commit_shas():
    text = WF.read_text()
    uses = re.findall(r"uses:\s*([^\s]+)", text)
    assert uses, "no actions found"
    for action in uses:
        repo, _, ref = action.partition("@")
        assert repo and ref, f"unpinned action: {action}"
        assert re.fullmatch(r"[0-9a-f]{40}", ref.split(" #")[0]), f"unpinned action: {action}"

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
            assert "inputs.tag" not in line and "github.ref_name" not in line, f"untrusted input in run script: {line.strip()}"
        elif in_run and not line.startswith(" "):
            in_run = False
