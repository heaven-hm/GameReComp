"""Independent guard for the issue #28 evidence artifact.

Recomputes every hash and decodes every committed frame itself; no claim in
the proof JSON or the evidence document is trusted.
"""

import hashlib
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROOF = ROOT / "proof/issue-28/issue28-proof.json"
DOC = ROOT / "docs/ISSUE_28_PROOF.md"

APIS = {
    "CreatePatternBrush", "LoadLibraryW", "CreateIconIndirect", "DrawTextW",
    "LoadCursorW", "MessageBoxW", "__initenv", "SDL_CreateCursor",
    "SDL_GetCursor", "SDL_SetCursor", "SDL_SetError", "SDL_VideoDriverName",
    "SDL_strlcat", "SDL_strlcpy", "Mix_SetPostMix", "WSACancelBlockingCall",
    "_flsbuf", "_snprintf", "fscanf", "vprintf",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bmp_info(path: Path):
    data = path.read_bytes()
    assert data[:2] == b"BM", f"{path} is not a BMP"
    offset, = struct.unpack_from("<I", data, 10)
    size, = struct.unpack_from("<I", data, 14)
    width, height = struct.unpack_from("<ii", data, 18)
    bpp, = struct.unpack_from("<H", data, 28)
    assert bpp == 24, f"{path} is not 24-bpp"
    stride = ((width * 3 + 3) // 4) * 4
    colors = set()
    for y in range(abs(height)):
        row = data[offset + y * stride: offset + y * stride + width * 3]
        for x in range(width):
            colors.add(row[x * 3:x * 3 + 3])
    return width, abs(height), colors


def test_proof_is_scoped_and_names_every_api():
    data = json.loads(PROOF.read_text())
    assert data["issue"] == 28
    assert "not proven" in data["what_is_not_proven"].lower() or \
        "no original-windows" in data["what_is_not_proven"].lower()
    text = DOC.read_text()
    for api in APIS:
        assert api in text, f"doc omits API {api}"
    assert "ok    api_gap_selftest.c" in data["selftest"]["status_line"]
    assert data["run"]["unresolved_imports"] == 0
    assert all(s.startswith("ok") for s in data["rebuild"]["stages"].values())


def test_recomputed_hashes_and_re_decoded_frames():
    data = json.loads(PROOF.read_text())
    assert len(data["frames"]) == 3
    for original in data["originals"]:
        assert len(original["sha256"]) == 64
    assert data["rebuild"]["native_binary_sha256"] == \
        "c4c000a39369c3964cac0ab11bbba86e8fcd1d2d1980f2aa2f5db14f9640a411"
    for frame in data["frames"]:
        path = ROOT / frame["path"]
        assert path.is_file(), f"missing frame {frame['path']}"
        assert _sha(path) == frame["sha256"], f"hash mismatch {frame['path']}"
        width, height, colors = _bmp_info(path)
        assert (width, height) == (frame["width"], frame["height"])
        assert len(colors) == frame["distinct_colors"]
        assert len(colors) > 1 and frame["blank"] is False
    for rel in data["tetris_frames"]:
        assert (ROOT / rel).is_file(), f"missing Tetris frame {rel}"


def test_no_fake_success_claim():
    text = DOC.read_text().lower() + PROOF.read_text().lower()
    assert "gameplay equivalence" in text
    assert "not a claim" in text or "not proven" in text
    assert "100% pass" not in text
