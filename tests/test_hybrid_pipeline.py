"""Deterministic, fail-closed tests for the hybrid pipeline orchestrator.

Run with: python3 -m pytest tests/test_hybrid_pipeline.py -q

Every fixture lives in tmp_path. The adapters below are *fake* in the sense
that they do not run a real Frida/Unicorn engine, but they still have to supply
real, independently verified evidence: original/native/input digests are
recomputed by the orchestrator from CLI-supplied files, and frame/evidence
hashes are recomputed from real files. No host path, game or corpus is read.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import hybrid_pipeline as hp

FIXED_TIME = "2026-11-24T00:00:00Z"
ORIGINAL_BYTES = b"MZ original PE bytes for hybrid pipeline tests"
NATIVE_BYTES = b"ELF native binary bytes for hybrid pipeline tests"
FRAME_BYTES = b"\x89PNG frame evidence bytes"
REPLAY_BYTES = b"replay evidence: frame 12 state matches original"
CAPTURE_BYTES = b"frida capture: CreateWindowExA called 3 times"
EVENTS = [
    {"event": "key_down", "key": "W", "frame": 10},
    {"event": "mouse_move", "x": 42, "y": 17, "frame": 11},
    {"event": "key_up", "key": "W", "frame": 12},
]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def write_json(path: Path, data: object) -> Path:
    return write(path, (json.dumps(data, indent=2) + "\n").encode())


class World:
    """A complete, evidence-backed input set that legitimately reaches PASS."""

    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.out = tmp_path / "out"
        self.original = write(tmp_path / "original.exe", ORIGINAL_BYTES)
        self.native = write(tmp_path / "native.bin", NATIVE_BYTES)
        self.frame = write(tmp_path / "frame12.png", FRAME_BYTES)
        self.replay_evidence = write(tmp_path / "replay.json", REPLAY_BYTES)
        self.capture_evidence = write(tmp_path / "capture.json", CAPTURE_BYTES)
        self.hashes = {
            "original_sha256": sha(ORIGINAL_BYTES),
            "native_sha256": sha(NATIVE_BYTES),
            "input_sha256": "",  # filled after the scenario is written
        }
        self.scenario = {
            "name": "boot-to-menu-with-input",
            "os": "Windows",
            "arch": "x86",
            "events": EVENTS,
            "replay": {
                "executed": True,
                "events": EVENTS,
                "frames": [{
                    "index": 12,
                    "path": str(self.frame),
                    "sha256": sha(FRAME_BYTES),
                }],
                "state": {"screen": "menu", "health": 100, "frame": 12},
                "evidence_path": str(self.replay_evidence),
                "evidence_sha256": sha(REPLAY_BYTES),
            },
        }
        self.scenario_path = write_json(tmp_path / "scenario.json", self.scenario)
        self.hashes["input_sha256"] = sha(self.scenario_path.read_bytes())

        self.frida_adapter = self.make_python_adapter("frida", self.frida_evidence())
        self.unicorn_adapter = self.make_python_adapter("unicorn", self.unicorn_evidence())

    def sync_adapters(self):
        """Re-echo the hash of the (possibly edited) scenario file so a replay
        test isolates its replay gate instead of tripping the hash gate."""
        self.hashes["input_sha256"] = sha(self.scenario_path.read_bytes())
        self.frida_adapter = self.make_python_adapter("frida", self.frida_evidence())
        self.unicorn_adapter = self.make_python_adapter("unicorn", self.unicorn_evidence())

    def frida_evidence(self, **overrides) -> dict:
        doc = {
            "status": "OK",
            "oracle": {"name": "original-windows", "verified": True, "executed": True},
            "hashes": dict(self.hashes),
            "capture": {
                "evidence": "CreateWindowExA/ShowWindow observed in original process",
                "calls": ["CreateWindowExA", "ShowWindow"],
                "path": str(self.capture_evidence),
                "sha256": sha(CAPTURE_BYTES),
            },
            "evidence": "frida hooked the real original process",
            "evidence_path": str(self.capture_evidence),
            "evidence_sha256": sha(CAPTURE_BYTES),
        }
        doc.update(overrides)
        return doc

    def unicorn_evidence(self, **overrides) -> dict:
        doc = {
            "status": "OK",
            "hashes": dict(self.hashes),
            "agreement": True,
            "evidence": "eax signature matched original bytes across 5 seeds",
            "evidence_path": str(self.replay_evidence),
            "evidence_sha256": sha(REPLAY_BYTES),
        }
        doc.update(overrides)
        return doc

    def make_python_adapter(self, name: str, evidence: dict) -> Path:
        path = self.root / f"{name}_adapter.py"
        payload = json.dumps(evidence)
        path.write_text(
            "EVIDENCE = " + repr(payload) + "\n"
            "def collect(context):\n"
            "    import json\n"
            "    return json.loads(EVIDENCE)\n",
            encoding="utf-8",
        )
        return path

    def make_json_adapter(self, name: str, evidence: dict) -> Path:
        return write_json(self.root / f"{name}_evidence.json",
                          {name: evidence})

    def argv(self, *, frida=None, unicorn=None, timeout=5):
        return [
            "--original", str(self.original),
            "--native", str(self.native),
            "--input-scenario", str(self.scenario_path),
            "--out", str(self.out),
            "--frida-adapter", "" if frida is None else str(frida),
            "--unicorn-adapter", "" if unicorn is None else str(unicorn),
            "--timeout", str(timeout),
            "--generated-at", FIXED_TIME,
        ]

    def run(self, *, frida=None, unicorn=None, timeout=5):
        adapter_f = self.frida_adapter if frida is None else frida
        adapter_u = self.unicorn_adapter if unicorn is None else unicorn
        code = hp.main(self.argv(frida=adapter_f, unicorn=adapter_u,
                                 timeout=timeout))
        report = json.loads((self.out / "report.json").read_text())
        return code, report


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


# ---------------------------------------------------------------------------
# PASS is reachable, scoped and deterministic
# ---------------------------------------------------------------------------

def test_full_evidence_yields_scoped_pass(world):
    code, report = world.run()
    assert code == 0
    assert report["verdict"] == "PASS"
    assert all(report["checks"].values()), report["checks"]
    assert report["hashes"] == {
        "original_sha256": sha(ORIGINAL_BYTES),
        "native_sha256": sha(NATIVE_BYTES),
        "input_sha256": sha(world.scenario_path.read_bytes()),
        "adapter_claims": report["hashes"]["adapter_claims"],
    }
    assert report["target"] == {"os": "Windows", "arch": "x86"}
    assert "all gates satisfied" in report["reasons"][0]
    # Only the evidenced pair is PASS; every other cell is DEFERRED/BLOCKED.
    cells = report["scope"]["cells"]
    assert len(cells) == len(hp.OSES) * len(hp.ARCHES)
    evidenced = [c for c in cells if c["evidenced"]]
    assert len(evidenced) == 1
    assert evidenced[0]["key"] == "Windows|x86"
    for cell in cells:
        if not cell["evidenced"]:
            assert cell["status"] == "DEFERRED"
            assert "BLOCKED for support claims" in cell["reason"]
    assert "No universal OS/game support" in report["claim"]


def test_json_adapters_can_also_reach_pass(world):
    code, report = world.run(frida=world.make_json_adapter("frida", world.frida_evidence()),
                             unicorn=world.make_json_adapter("unicorn", world.unicorn_evidence()))
    assert code == 0
    assert report["verdict"] == "PASS"
    assert report["engines"]["frida"]["method"] == "json-evidence"
    assert report["engines"]["unicorn"]["method"] == "json-evidence"


def test_report_is_deterministic_with_fixed_timestamp(world):
    _, first = world.run()
    _, second = world.run()
    assert first == second


def test_markdown_report_is_written(world):
    argv = world.argv(frida=world.frida_adapter,
                      unicorn=world.unicorn_adapter) + ["--markdown", "summary.md"]
    assert hp.main(argv) == 0
    text = (world.out / "summary.md").read_text()
    assert "Verdict: **PASS**" in text
    assert "| real_executed_oracle_flag | PASS |" in text
    assert "| macOS | x64 | DEFERRED | no |" in text


# ---------------------------------------------------------------------------
# PASS is impossible without every gate (fake-adapter tampering tests)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("override,expected_reason", [
    ({"oracle": {"name": "original-windows", "verified": False, "executed": True}},
     "real_executed_oracle_flag"),
    ({"oracle": {"name": "dummy-device", "verified": True, "executed": True}},
     "real_executed_oracle_flag"),
    ({"oracle": {"name": "original-windows", "verified": True, "executed": False}},
     "real_executed_oracle_flag"),
    ({"oracle": None}, "real_executed_oracle_flag"),
    ({"capture": {"evidence": "", "calls": []}}, "frida_capture_evidence"),
    ({"capture": None}, "frida_capture_evidence"),
    ({"status": "BLOCKED", "reason": "frida attach refused by taskgated"},
     "frida_capture_evidence"),
    ({"hashes": {}}, "hashes_match_evidence"),
])
def test_frida_gate_cannot_be_faked(world, override, expected_reason):
    evidence = world.frida_evidence(**override)
    adapter = world.make_python_adapter("frida_bad", evidence)
    code, report = world.run(frida=adapter)
    assert code != 0
    assert report["verdict"] != "PASS"
    assert expected_reason in report["missing_checks"]
    assert any("PASS withheld" in reason for reason in report["reasons"])


@pytest.mark.parametrize("override,expected_reason", [
    ({"agreement": False}, "unicorn_agreement"),
    ({"agreement": None}, "unicorn_agreement"),
    ({"evidence": ""}, "unicorn_agreement"),
    ({"status": "BLOCKED", "reason": "unicorn unmapped read"}, "unicorn_agreement"),
    ({"hashes": {}}, "hashes_match_evidence"),
    ({"evidence_sha256": "0" * 64}, "unicorn_agreement"),
])
def test_unicorn_gate_cannot_be_faked(world, override, expected_reason):
    adapter = world.make_python_adapter("unicorn_bad",
                                        world.unicorn_evidence(**override))
    code, report = world.run(unicorn=adapter)
    assert code != 0
    assert report["verdict"] != "PASS"
    assert expected_reason in report["missing_checks"]


@pytest.mark.parametrize("change,expected_reason", [
    ("no_events", "scripted_events"),
    ("events_mismatch", "events_match"),
    ("not_executed", "replayed_input_scenario"),
    ("no_frames", "frames_evidenced"),
    ("fake_frame_hash", "frames_evidenced"),
    ("no_state", "state_evidenced"),
    ("no_evidence_hash", "replayed_input_scenario"),
    ("wrong_evidence_hash", "replayed_input_scenario"),
    ("no_target", "target_os_arch_evidenced"),
])
def test_replay_gate_cannot_be_faked(world, change, expected_reason):
    scenario = json.loads(json.dumps(world.scenario))
    replay = scenario["replay"]
    if change == "no_events":
        scenario.pop("events")
    elif change == "events_mismatch":
        replay["events"] = [{"event": "different"}]
    elif change == "not_executed":
        replay["executed"] = False
    elif change == "no_frames":
        replay["frames"] = []
    elif change == "fake_frame_hash":
        replay["frames"][0]["sha256"] = "d" * 64
    elif change == "no_state":
        replay.pop("state")
    elif change == "no_evidence_hash":
        replay.pop("evidence_sha256")
    elif change == "wrong_evidence_hash":
        replay["evidence_sha256"] = "e" * 64
    elif change == "no_target":
        scenario.pop("os")
        scenario.pop("arch")
    write_json(world.scenario_path, scenario)
    world.sync_adapters()
    code, report = world.run()
    assert code != 0
    assert report["verdict"] != "PASS"
    assert expected_reason in report["missing_checks"]


def test_mismatched_file_hash_never_passes(world):
    # A fake adapter parroting a made-up original hash cannot satisfy the gate
    # because the orchestrator recomputes the digest from the CLI-supplied file.
    evidence = world.frida_evidence()
    evidence["hashes"]["original_sha256"] = "f" * 64
    adapter = world.make_python_adapter("frida_fake_hash", evidence)
    code, report = world.run(frida=adapter)
    assert code != 0
    assert report["verdict"] == "FAIL"
    assert any("conflicting" in reason or "does not match" in reason
               or "not a real SHA-256" in reason for reason in report["reasons"])


# ---------------------------------------------------------------------------
# Missing adapters and malformed inputs block / fail closed
# ---------------------------------------------------------------------------

def test_no_adapters_is_blocked(world):
    code, report = world.run(frida="", unicorn="")
    assert code != 0
    assert report["verdict"] == "BLOCKED"
    assert "no adapter supplied" in " ".join(report["reasons"])
    evidenced = [c for c in report["scope"]["cells"] if c["evidenced"]]
    assert len(evidenced) == 1 and evidenced[0]["status"] == "BLOCKED"
    assert all(c["status"] == "DEFERRED" for c in report["scope"]["cells"]
               if not c["evidenced"])


@pytest.mark.parametrize("which", ["frida", "unicorn"])
def test_missing_adapter_path_is_blocked(world, which):
    missing = world.root / "does-not-exist.py"
    kwargs = {which: missing}
    code, report = world.run(**kwargs)
    assert code != 0
    assert report["verdict"] == "BLOCKED"
    assert f"{which} adapter not found" in " ".join(report["reasons"])


def test_adapter_that_raises_is_blocked(world):
    path = world.root / "boom.py"
    path.write_text("def collect(context):\n    raise RuntimeError('no engine here')\n")
    code, report = world.run(frida=path)
    assert code != 0
    assert report["verdict"] == "BLOCKED"
    assert "RuntimeError" in " ".join(report["reasons"])


def test_adapter_without_collect_is_blocked(world):
    path = world.root / "empty.py"
    path.write_text("VALUE = 1\n")
    code, report = world.run(unicorn=path)
    assert code != 0
    assert report["verdict"] == "BLOCKED"
    assert "exposes no collect" in " ".join(report["reasons"])


def test_adapter_timeout_is_blocked(world):
    path = world.root / "slow.py"
    path.write_text(
        "import time\n"
        "def collect(context):\n"
        "    time.sleep(30)\n"
        "    return {}\n"
    )
    code, report = world.run(frida=path, timeout=1)
    assert code != 0
    assert report["verdict"] == "BLOCKED"
    assert "--timeout 1s" in " ".join(report["reasons"])


def test_invalid_scenario_json_is_not_tested(world):
    write(world.scenario_path, b"{ not valid json")
    code, report = world.run()
    assert code != 0
    assert report["verdict"] in ("BLOCKED", "FAIL")
    assert "PASS withheld" in " ".join(report["reasons"])


def test_missing_original_file_blocks(world):
    code, report = world.run()
    assert report["verdict"] == "PASS"
    world.original.unlink()
    code, report = world.run()
    assert code != 0
    assert report["verdict"] == "BLOCKED"
    assert any("original" in reason for reason in report["reasons"])


def test_non_object_adapter_evidence_is_blocked(world):
    adapter = world.root / "list_adapter.py"
    adapter.write_text("def collect(context):\n    return [1, 2, 3]\n")
    code, report = world.run(frida=adapter)
    assert code != 0
    assert report["verdict"] != "PASS"
    assert "not a JSON object" in " ".join(report["reasons"])


# ---------------------------------------------------------------------------
# Status ladder: PARTIAL / BUILD ONLY / FAIL / NOT TESTED
# ---------------------------------------------------------------------------

def test_partial_when_evidence_runs_but_a_gate_is_missing(world):
    evidence = world.unicorn_evidence(agreement=False)
    adapter = world.make_python_adapter("unicorn_partial", evidence)
    code, report = world.run(unicorn=adapter)
    assert code != 0
    assert report["verdict"] == "PARTIAL"


def test_build_only_when_native_exists_without_engine_runs(world):
    scenario = json.loads(json.dumps(world.scenario))
    scenario["replay"]["executed"] = False
    write_json(world.scenario_path, scenario)
    world.sync_adapters()
    frida = world.root / "noop_frida.py"
    unicorn = world.root / "noop_unicorn.py"
    for path in (frida, unicorn):
        path.write_text("def collect(context):\n    return {}\n")
    code, report = world.run(frida=frida, unicorn=unicorn)
    assert code != 0
    # Inputs were computed, but no engine adapter supplied positive evidence.
    assert report["verdict"] == "BUILD ONLY"
    assert report["hashes"]["native_sha256"] == sha(NATIVE_BYTES)


def test_fail_when_adapter_reports_failure(world):
    frida = world.make_python_adapter(
        "frida_fail", world.frida_evidence(status="FAIL", reason="capture crashed"))
    code, report = world.run(frida=frida)
    assert code != 0
    assert report["verdict"] == "FAIL"
    assert "capture crashed" in " ".join(report["reasons"])


def test_not_tested_without_a_target_and_no_engine_evidence(world):
    scenario = {"name": "unspecified-target"}
    write_json(world.scenario_path, scenario)
    world.sync_adapters()
    frida = world.root / "noop.py"
    frida.write_text("def collect(context):\n    return {}\n")
    code, report = world.run(frida=frida, unicorn=frida)
    assert code != 0
    assert report["verdict"] == "NOT TESTED"
    assert "target_os_arch_evidenced" in report["missing_checks"]
    assert all(c["status"] == "DEFERRED" for c in report["scope"]["cells"])


def test_missing_input_file_is_blocked(world):
    write(world.original, b"")
    code, report = world.run()
    assert code != 0
    assert report["verdict"] == "BLOCKED"
    assert "original file is empty" in " ".join(report["reasons"])


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
