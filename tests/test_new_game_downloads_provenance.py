"""Real provenance gates for the P8 batch of freshly downloaded PE32 games.

The documentation section and the committed evidence JSON must agree, and every
entry must carry an official URL, a byte size and a full SHA-256 digest.  This
does not run any game; it only prevents provenance records with missing or
mismatched hashes from landing.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "NEW_GAME_DOWNLOADS.md"
EVIDENCE = ROOT / "evidence" / "p8-new-games" / "provenance.json"
RUN_EVIDENCE = ROOT / "evidence" / "p8-new-games" / "evidence.json"
ALLOWED_CLASSIFICATIONS = {"PASS", "PARTIAL", "BUILD-ONLY", "BLOCKED"}

EXPECTED_FILES = {
    "tetris-win-32-jp.exe",
    "tetris-win-32-ch.exe",
    "chocolate-heretic-3.1.0-win32.zip",
    "chocolate-hexen-3.1.0-win32.zip",
}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
SECTION_HEADING = "## P8 batch"
OFFICIAL_HOSTS = ("https://github.com/OUCC/tetris/releases/download/v1.3/",
                  "https://github.com/chocolate-doom/chocolate-doom/releases/download/")


def p8_section() -> str:
    text = DOC.read_text(encoding="utf-8")
    assert SECTION_HEADING in text, "P8 provenance section missing from docs"
    section = text.split(SECTION_HEADING, 1)[1]
    return section.split("\n## ", 1)[0]


def parse_rows(section: str) -> dict:
    rows = {}
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or cells[0].startswith("---") or cells[0] == "File":
            continue
        paths = re.findall(r"`([^`]+)`", cells[0])
        urls = re.findall(r"`([^`]+)`", cells[1])
        hashes = re.findall(r"`([^`]+)`", cells[3])
        if not (paths and urls and hashes):
            continue
        rows[paths[0]] = {
            "url": urls[0],
            "size": int(cells[2].replace(",", "")),
            "sha256": hashes[0],
        }
    return rows


class NewGameDownloadsProvenanceTest(unittest.TestCase):
    def test_documented_entries_have_official_urls_sizes_and_hashes(self):
        rows = parse_rows(p8_section())
        self.assertEqual(set(rows), EXPECTED_FILES)
        for name, row in rows.items():
            self.assertTrue(row["url"].startswith(OFFICIAL_HOSTS),
                            f"{name}: non-official URL {row['url']}")
            self.assertGreater(row["size"], 0, f"{name}: missing size")
            self.assertRegex(row["sha256"], HASH_RE, f"{name}: missing SHA-256")

    def test_documentation_matches_committed_evidence(self):
        data = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        evidence = {e["file"]: e for e in data["downloads"]}
        rows = parse_rows(p8_section())
        self.assertEqual(set(evidence), EXPECTED_FILES)
        for name, row in rows.items():
            self.assertEqual(evidence[name]["url"], row["url"], name)
            self.assertEqual(evidence[name]["size_bytes"], row["size"], name)
            self.assertEqual(evidence[name]["sha256"], row["sha256"], name)
            self.assertTrue(evidence[name]["redownload_sha256_match"], name)
            self.assertRegex(evidence[name]["inner_exe_sha256"] or "0" * 64,
                             HASH_RE, name)

    def test_every_hash_in_p8_section_is_backed_by_evidence(self):
        doc_hashes = set(re.findall(r"\b[0-9a-f]{64}\b", p8_section()))
        self.assertTrue(doc_hashes, "P8 section has no hashes")
        backing = EVIDENCE.read_text(encoding="utf-8") + \
            RUN_EVIDENCE.read_text(encoding="utf-8")
        for digest in doc_hashes:
            self.assertIn(digest, backing, f"hash in docs has no evidence: {digest}")

    def test_run_evidence_carries_real_hashes_and_honest_classifications(self):
        doc = json.loads(RUN_EVIDENCE.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(doc["games"]), 2)
        for game in doc["games"]:
            name = game["name"]
            self.assertIn(game["classification"], ALLOWED_CLASSIFICATIONS, name)
            self.assertRegex(game["provenance"]["sha256"], HASH_RE, name)
            self.assertRegex(game["rebuild"]["native_binary_sha256"], HASH_RE, name)
            self.assertEqual(game["rebuild"]["lift_errors"], 0, name)
            for frame in game["frame_proof"]:
                self.assertRegex(frame["source_bmp_sha256"], HASH_RE, name)
            self.assertTrue(game["committed_screenshots"], name)
            for shot in game["committed_screenshots"]:
                self.assertRegex(shot["committed_png_sha256"], HASH_RE, name)
                self.assertLessEqual(shot["size_bytes"], 100_000, name)
        # Provenance/evidence indexes only: no game binary or archive is committed.
        for path in (RUN_EVIDENCE.parent).rglob("*"):
            if path.is_file():
                self.assertNotIn(path.suffix.lower(), {".exe", ".zip", ".bmp"},
                                 str(path))


if __name__ == "__main__":
    unittest.main()
