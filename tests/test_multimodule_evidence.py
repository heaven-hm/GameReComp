"""Tests for the fail-closed multi-module boot evidence joiner.

Every artifact here is synthetic and written under tmp_path (except the final
case, which joins the committed Chocolate Doom proof and frame bytes). The
tests prove each verdict (VERIFIED / PARTIAL / BLOCKED), that a fake module or
fake frame cannot be VERIFIED, and every tamper path: an unattested module, a
self-declared verdict, contradictory counts, altered proof/frame bytes, a
blank image and a missing companion mapping. No game or native is executed.

Run with: python3 -m pytest tests/test_multimodule_evidence.py -q
"""
from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from tools import multimodule_evidence

ROOT = Path(__file__).resolve().parents[1]
EXE_SHA = "ab" * 32
NATIVE_SHA = "cd" * 32


def make_bmp24(pixels: list[list[tuple[int, int, int]]]) -> bytes:
    """Minimal 24-bpp uncompressed BMP with the given RGB rows."""
    height, width = len(pixels), len(pixels[0])
    stride = ((width * 3 + 3) // 4) * 4
    data = bytearray(54 + stride * height)
    data[:2] = b'BM'
    struct.pack_into('<I', data, 10, 54)
    struct.pack_into('<I', data, 14, 40)
    struct.pack_into('<ii', data, 18, width, height)
    struct.pack_into('<H', data, 26, 1)
    struct.pack_into('<H', data, 28, 24)
    for file_row, image_row in enumerate(reversed(pixels)):
        off = 54 + file_row * stride
        for x, (red, green, blue) in enumerate(image_row):
            data[off + x * 3:off + x * 3 + 3] = bytes((blue, green, red))
    return bytes(data)


NONBLANK = [[(255, 0, 0), (0, 255, 0)], [(0, 0, 255), (255, 255, 255)]]
BLANK = [[(7, 7, 7), (7, 7, 7)], [(7, 7, 7), (7, 7, 7)]]


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def make_case(tmp_path: Path, name: str = "", *, picture=NONBLANK,
              exe_imports: int = 166, companion_imports: int = 34,
              unresolved: int = 0, frame_count: int = 1):
    """Write a consistent proof + boot + frame; return their paths."""
    suffix = name.replace("-", "_")
    frame = tmp_path / f"frame{suffix}.bmp"
    frame.write_bytes(make_bmp24(picture))
    frame_sha = hashlib.sha256(frame.read_bytes()).hexdigest()
    proof_path = tmp_path / f"proof{suffix}.json"
    proof = {
        "schema": 1, "issue": 28,
        "originals": [{"name": "chocolate-doom.exe", "sha256": EXE_SHA}],
        "rebuild": {"native_binary_sha256": NATIVE_SHA},
        "run": {
            "imports_bound": exe_imports,
            "companion_sdl_net_imports_bound": companion_imports,
            "unresolved_imports": unresolved, "frame_count": frame_count,
        },
        "frames": [{"path": str(frame), "sha256": frame_sha,
                    "distinct_colors": 4, "blank": len(set(
                        c for row in picture for c in row)) <= 1}],
    }
    write_json(proof_path, proof)
    boot = boot_for(proof_path, frame, frame_sha)
    boot_path = tmp_path / f"boot{suffix}.json"
    write_json(boot_path, boot)
    return frame, proof_path, boot_path


def boot_for(proof_path: Path, frame: Path, frame_sha: str):
    """Build a boot record whose claims agree with the supplied proof bytes."""
    proof_hash = hashlib.sha256(proof_path.read_bytes()).hexdigest()
    proof = json.loads(proof_path.read_text())
    run = proof["run"]
    return {
        "schema_version": 1,
        "label": "synthetic chocolate-doom.exe + SDL_net.dll boot",
        "source": {"path": str(proof_path), "sha256": proof_hash},
        "native_binary": {"sha256": proof["rebuild"]["native_binary_sha256"]},
        "modules": [
            {"name": "chocolate-doom.exe", "role": "exe",
             "sha256": proof["originals"][0]["sha256"],
             "imports_bound": run["imports_bound"],
             "unresolved_imports": run["unresolved_imports"]},
            {"name": "SDL_net.dll", "role": "companion",
             "companion_of": "chocolate-doom.exe",
             "imports_bound": run["companion_sdl_net_imports_bound"],
             "unresolved_imports": run["unresolved_imports"],
             "mapping": {
                 "source_key": "companion_sdl_net_imports_bound",
                 "imports_bound": run["companion_sdl_net_imports_bound"],
             }},
        ],
        "frames": [{"path": str(frame), "sha256": frame_sha}],
    }


def invoke(tmp_path: Path, *, boot=None, proof=None, frames=None, binary=None,
           name: str = ""):
    """Supply evidence through the real CLI and return (exit code, verdict)."""
    args = ["--out", str(tmp_path / f"verdict{name}.json")]
    proof_arg = None
    if proof is not None:
        if isinstance(proof, Path):
            proof_arg = proof  # preserve the committed bytes verbatim
        else:
            proof_arg = tmp_path / f"proof-arg{name}.json"
            write_json(proof_arg, proof)
        args += ["--proof", str(proof_arg)]
    if boot is not None:
        if isinstance(boot, Path):
            path = boot
        else:
            path = tmp_path / f"boot-arg{name}.json"
            if (proof_arg is not None and isinstance(boot.get("source"), dict)
                    and not multimodule_evidence.suffix_match(
                        str(boot["source"].get("path", "")), proof_arg)):
                # The claim must name the proof bytes actually supplied.
                boot = dict(boot)
                boot["source"] = dict(boot["source"], path=str(proof_arg))
            write_json(path, boot)
        args += ["--boot", str(path)]
    for frame in frames or []:
        args += ["--frame", str(frame)]
    if binary is not None:
        args += ["--binary", str(binary)]
    code = multimodule_evidence.main(args)
    doc = json.loads((tmp_path / f"verdict{name}.json").read_text())
    return code, doc


def codes(doc) -> set[str]:
    return {item["code"] for item in doc["reasons"]}


def module(doc, name: str) -> dict:
    return next(item for item in doc["modules"] if item["name"] == name)


def full_case(tmp_path: Path, name: str = "", **kwargs):
    frame, proof_path, boot_path = make_case(tmp_path, name, **kwargs)
    return invoke(tmp_path, boot=json.loads(boot_path.read_text()),
                  proof=json.loads(proof_path.read_text()),
                  frames=[frame], name=name)


# ---------------------------------------------------------------------------
# verdicts
# ---------------------------------------------------------------------------
def test_full_two_module_evidence_verified(tmp_path):
    code, doc = full_case(tmp_path)
    assert code == 0
    assert doc["verdict"] == "VERIFIED"
    assert doc["reasons"] == []
    assert doc["summary"] == {"modules": 2, "verified": 2, "partial": 0,
                              "blocked": 0, "nonblank_frames": 1}
    exe = module(doc, "chocolate-doom.exe")
    companion = module(doc, "SDL_net.dll")
    assert exe["verdict"] == companion["verdict"] == "VERIFIED"
    assert exe["facts"]["imports_bound"] == 166
    assert exe["facts"]["hash_attested"] is True
    assert companion["facts"]["mapping_attested"] is True
    assert companion["facts"]["imports_bound"] == 34
    assert doc["evidence"]["source"]["hash_verified"] is True
    assert doc["evidence"]["frames"]["nonblank"] == 1
    assert doc["evidence"]["frames"]["verified"][0]["distinct_colors"] == 4


def test_no_inputs_is_blocked(tmp_path):
    code, doc = invoke(tmp_path)
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert {"NO_EVIDENCE", "BOOT_INVALID", "PROOF_UNREADABLE"} <= codes(doc)


def test_companion_without_mapping_is_partial(tmp_path):
    frame, proof_path, boot_path = make_case(tmp_path)
    boot = json.loads(boot_path.read_text())
    del boot["modules"][1]["mapping"]
    code, doc = invoke(tmp_path, boot=boot,
                       proof=json.loads(proof_path.read_text()), frames=[frame])
    assert code == 1
    assert doc["verdict"] == "PARTIAL"
    assert "COMPANION_MAPPING_MISSING" in codes(doc)
    assert module(doc, "SDL_net.dll")["verdict"] == "PARTIAL"
    assert module(doc, "chocolate-doom.exe")["verdict"] == "PARTIAL"
    assert "GLOBAL_EVIDENCE_INCOMPLETE" in {
        item["code"] for item in module(doc, "chocolate-doom.exe")["reasons"]}


def test_no_frame_bytes_is_partial(tmp_path):
    frame, proof_path, boot_path = make_case(tmp_path)
    code, doc = invoke(tmp_path, boot=json.loads(boot_path.read_text()),
                       proof=json.loads(proof_path.read_text()))
    assert code == 1
    assert doc["verdict"] == "PARTIAL"
    assert "FRAME_FILES_MISSING" in codes(doc)
    assert module(doc, "SDL_net.dll")["verdict"] == "PARTIAL"


def test_no_frame_claims_is_partial(tmp_path):
    frame, proof_path, boot_path = make_case(tmp_path)
    boot = json.loads(boot_path.read_text())
    boot["frames"] = []
    code, doc = invoke(tmp_path, boot=boot,
                       proof=json.loads(proof_path.read_text()), frames=[frame])
    assert code == 1
    assert doc["verdict"] == "PARTIAL"
    assert "FRAME_PROOF_MISSING" in codes(doc)


# ---------------------------------------------------------------------------
# fake module / fake frame are never VERIFIED
# ---------------------------------------------------------------------------
def test_fake_module_not_attested_by_proof_blocks(tmp_path):
    frame, proof_path, boot_path = make_case(tmp_path)
    boot = json.loads(boot_path.read_text())
    boot["modules"].append({
        "name": "totally-fake.dll", "role": "companion",
        "companion_of": "chocolate-doom.exe", "imports_bound": 999,
        "unresolved_imports": 0,
        "mapping": {"source_key": "companion_totally_fake_imports_bound",
                    "imports_bound": 999},
    })
    code, doc = invoke(tmp_path, boot=boot,
                       proof=json.loads(proof_path.read_text()), frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "MAPPING_NOT_ATTESTED" in codes(doc)
    fake = module(doc, "totally-fake.dll")
    assert fake["verdict"] == "BLOCKED"
    assert "MAPPING_NOT_ATTESTED" in {item["code"] for item in fake["reasons"]}


def test_fake_exe_with_invented_hash_blocks(tmp_path):
    frame, proof_path, boot_path = make_case(tmp_path)
    boot = json.loads(boot_path.read_text())
    boot["modules"][0]["sha256"] = "11" * 32
    code, doc = invoke(tmp_path, boot=boot,
                       proof=json.loads(proof_path.read_text()), frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "MODULE_HASH_MISMATCH" in codes(doc)


@pytest.mark.parametrize("claim", [
    {"verdict": "VERIFIED"}, {"status": "PASS"}, {"pass": True},
])
def test_self_declared_verdict_in_boot_is_tampering(tmp_path, claim):
    frame, proof_path, boot_path = make_case(tmp_path)
    boot = json.loads(boot_path.read_text())
    boot.update(claim)
    code, doc = invoke(tmp_path, boot=boot,
                       proof=json.loads(proof_path.read_text()), frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert {"FAKE_PASS_CLAIM", "EVIDENCE_TAMPERED"} <= codes(doc)


def test_self_declared_verdict_in_module_is_tampering(tmp_path):
    frame, proof_path, boot_path = make_case(tmp_path)
    boot = json.loads(boot_path.read_text())
    boot["modules"][1]["module_verdict"] = "VERIFIED"
    code, doc = invoke(tmp_path, boot=boot,
                       proof=json.loads(proof_path.read_text()), frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "FAKE_PASS_CLAIM" in codes(doc)


def test_blank_frame_is_not_render_evidence(tmp_path):
    frame, proof_path, boot_path = make_case(tmp_path, picture=BLANK)
    code, doc = invoke(tmp_path, boot=json.loads(boot_path.read_text()),
                       proof=json.loads(proof_path.read_text()), frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "FRAME_BLANK" in codes(doc)
    assert doc["evidence"]["frames"]["nonblank"] == 0


def test_fake_frame_that_is_not_an_image_blocks(tmp_path):
    frame, proof_path, boot_path = make_case(tmp_path)
    frame.write_bytes(b"this is not a BMP or PNG")
    code, doc = invoke(tmp_path, boot=json.loads(boot_path.read_text()),
                       proof=json.loads(proof_path.read_text()), frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "FRAME_HASH_MISMATCH" in codes(doc)


def test_frame_not_attested_by_proof_blocks(tmp_path):
    frame, proof_path, boot_path = make_case(tmp_path)
    boot = json.loads(boot_path.read_text())
    boot["frames"][0]["path"] = str(tmp_path / "unattested.bmp")
    code, doc = invoke(tmp_path, boot=boot,
                       proof=json.loads(proof_path.read_text()), frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "FRAME_NOT_ATTESTED" in codes(doc)


# ---------------------------------------------------------------------------
# tamper detection
# ---------------------------------------------------------------------------
def test_frame_byte_tamper_detected(tmp_path):
    frame, proof_path, boot_path = make_case(tmp_path)
    boot = json.loads(boot_path.read_text())
    raw = frame.read_bytes()
    frame.write_bytes(raw[:-1] + bytes([raw[-1] ^ 0xFF]))
    code, doc = invoke(tmp_path, boot=boot,
                       proof=json.loads(proof_path.read_text()), frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "FRAME_HASH_MISMATCH" in codes(doc)


def test_proof_hash_tamper_detected(tmp_path):
    frame, proof_path, boot_path = make_case(tmp_path)
    boot = json.loads(boot_path.read_text())
    proof = json.loads(proof_path.read_text())
    proof["run"]["imports_bound"] = 999  # alter the committed proof bytes
    code, doc = invoke(tmp_path, boot=boot, proof=proof, frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "PROOF_HASH_MISMATCH" in codes(doc)


def test_import_count_tamper_detected(tmp_path):
    frame, proof_path, boot_path = make_case(tmp_path)
    boot = json.loads(boot_path.read_text())
    boot["modules"][0]["imports_bound"] = 999
    code, doc = invoke(tmp_path, boot=boot,
                       proof=json.loads(proof_path.read_text()), frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "IMPORT_COUNT_MISMATCH" in codes(doc)


def test_unresolved_imports_are_negative_evidence(tmp_path):
    frame, proof_path, boot_path = make_case(tmp_path, unresolved=2)
    code, doc = invoke(tmp_path, boot=json.loads(boot_path.read_text()),
                       proof=json.loads(proof_path.read_text()), frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "UNRESOLVED_IMPORTS_PRESENT" in codes(doc)


def test_unresolved_count_mismatch_blocks(tmp_path):
    frame, proof_path, boot_path = make_case(tmp_path)
    boot = json.loads(boot_path.read_text())
    boot["modules"][0]["unresolved_imports"] = 3
    code, doc = invoke(tmp_path, boot=boot,
                       proof=json.loads(proof_path.read_text()), frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "UNRESOLVED_COUNT_MISMATCH" in codes(doc)


def test_native_binary_hash_mismatch_blocks(tmp_path):
    frame, proof_path, boot_path = make_case(tmp_path)
    boot = json.loads(boot_path.read_text())
    boot["native_binary"]["sha256"] = "44" * 32
    code, doc = invoke(tmp_path, boot=boot,
                       proof=json.loads(proof_path.read_text()), frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "NATIVE_BINARY_HASH_MISMATCH" in codes(doc)


def test_native_binary_bytes_are_rehashed_when_supplied(tmp_path):
    frame, proof_path, boot_path = make_case(tmp_path)
    binary = tmp_path / "native.bin"
    binary.write_bytes(b"native bytes that do not match the proof hash")
    code, doc = invoke(tmp_path, boot=json.loads(boot_path.read_text()),
                       proof=json.loads(proof_path.read_text()),
                       frames=[frame], binary=binary)
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "NATIVE_BINARY_HASH_MISMATCH" in codes(doc)
    assert doc["evidence"]["native_binary"]["actual_sha256"] == \
        hashlib.sha256(binary.read_bytes()).hexdigest()


def test_mapping_count_tamper_detected(tmp_path):
    frame, proof_path, boot_path = make_case(tmp_path)
    boot = json.loads(boot_path.read_text())
    boot["modules"][1]["mapping"]["imports_bound"] = 999
    code, doc = invoke(tmp_path, boot=boot,
                       proof=json.loads(proof_path.read_text()), frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "MAPPING_COUNT_MISMATCH" in codes(doc)


def test_mapping_cannot_borrow_another_modules_key(tmp_path):
    frame, proof_path, boot_path = make_case(tmp_path)
    boot = json.loads(boot_path.read_text())
    boot["modules"][1]["name"] = "evil.dll"
    code, doc = invoke(tmp_path, boot=boot,
                       proof=json.loads(proof_path.read_text()), frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "MAPPING_NOT_ATTESTED" in codes(doc)


def test_companion_of_must_name_a_boot_exe(tmp_path):
    frame, proof_path, boot_path = make_case(tmp_path)
    boot = json.loads(boot_path.read_text())
    boot["modules"][1]["companion_of"] = "missing.exe"
    code, doc = invoke(tmp_path, boot=boot,
                       proof=json.loads(proof_path.read_text()), frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "COMPANION_OF_INVALID" in codes(doc)


def test_duplicate_module_names_block(tmp_path):
    frame, proof_path, boot_path = make_case(tmp_path)
    boot = json.loads(boot_path.read_text())
    boot["modules"].append(dict(boot["modules"][0]))
    code, doc = invoke(tmp_path, boot=boot,
                       proof=json.loads(proof_path.read_text()), frames=[frame])
    assert code == 1
    assert doc["verdict"] == "BLOCKED"
    assert "MODULE_DUPLICATE" in codes(doc)


# ---------------------------------------------------------------------------
# determinism, CLI and the real committed evidence
# ---------------------------------------------------------------------------
def test_output_is_byte_identical_for_same_evidence(tmp_path):
    frame, proof_path, boot_path = make_case(tmp_path)
    code_a, _ = invoke(tmp_path, boot=boot_path, proof=proof_path,
                       frames=[frame], name="-a")
    code_b, _ = invoke(tmp_path, boot=boot_path, proof=proof_path,
                       frames=[frame], name="-b")
    a = (tmp_path / "verdict-a.json").read_bytes()
    b = (tmp_path / "verdict-b.json").read_bytes()
    assert code_a == code_b == 0
    assert a == b


def test_module_cli_exit_status(tmp_path):
    frame, proof_path, boot_path = make_case(tmp_path)
    base = [sys.executable, "-m", "tools.multimodule_evidence"]
    full = base + ["--boot", str(boot_path), "--proof", str(proof_path),
                   "--frame", str(frame), "--out", str(tmp_path / "out.json")]
    done = subprocess.run(full, cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["verdict"] == "VERIFIED"
    blocked = subprocess.run(base + ["--out", str(tmp_path / "empty.json")],
                             cwd=ROOT, capture_output=True, text=True)
    assert blocked.returncode == 1
    assert json.loads(blocked.stdout)["verdict"] == "BLOCKED"


def test_committed_chocolate_doom_evidence_verified(tmp_path):
    """The real issue #28 proof + BMP bytes join into a VERIFIED boot."""
    proof = ROOT / "proof/issue-28/issue28-proof.json"
    doc = json.loads(proof.read_text())
    boot = {
        "schema_version": 1,
        "label": "issue #9: chocolate-doom.exe + SDL_net.dll multi-module boot",
        "source": {"path": "proof/issue-28/issue28-proof.json",
                   "sha256": hashlib.sha256(proof.read_bytes()).hexdigest()},
        "native_binary": {"sha256": doc["rebuild"]["native_binary_sha256"]},
        "modules": [
            {"name": "chocolate-doom.exe", "role": "exe",
             "sha256": doc["originals"][0]["sha256"],
             "imports_bound": doc["run"]["imports_bound"],
             "unresolved_imports": doc["run"]["unresolved_imports"]},
            {"name": "SDL_net.dll", "role": "companion",
             "companion_of": "chocolate-doom.exe",
             "imports_bound": doc["run"]["companion_sdl_net_imports_bound"],
             "unresolved_imports": doc["run"]["unresolved_imports"],
             "mapping": {
                 "source_key": "companion_sdl_net_imports_bound",
                 "imports_bound": doc["run"]["companion_sdl_net_imports_bound"],
             }},
        ],
        "frames": [{"path": item["path"], "sha256": item["sha256"]}
                   for item in doc["frames"]],
    }
    code, verdict = invoke(
        tmp_path, boot=boot, proof=proof,
        frames=[ROOT / item["path"] for item in doc["frames"]], name="-real")
    assert code == 0
    assert verdict["verdict"] == "VERIFIED"
    assert verdict["summary"] == {"modules": 2, "verified": 2, "partial": 0,
                                  "blocked": 0, "nonblank_frames": 3}
    assert module(verdict, "chocolate-doom.exe")["facts"]["imports_bound"] == 166
    assert module(verdict, "SDL_net.dll")["facts"]["imports_bound"] == 34
    assert module(verdict, "SDL_net.dll")["facts"]["mapping_attested"] is True
    assert all(item["distinct_colors"] == 3468 and item["blank"] is False
               for item in verdict["evidence"]["frames"]["verified"])
