"""Guards the verdict vocabulary and evidence claims in docs/GAME_MATRIX.md.

The game matrix is the repository's public record of what was actually run.
These tests make sure the Agent 3 addendum keeps explicit, fail-closed statuses
and never presents an invented SHA-256 as evidence.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs" / "GAME_MATRIX.md"

ADDENDUM_HEADING = "## Agent 3"
VERDICTS = ("PASS", "PARTIAL", "FAIL", "BLOCKED", "NOT TESTED")
ABSENT_HASHES = {"", "-", "--", "—", "n/a", "na", "none", "null", "absent", "unknown"}

STATUS_COLUMNS = {
    "Native build",
    "Runtime",
    "Graphics",
    "Input",
    "Audio",
    "Gameplay",
    "Modding",
}


def matrix_text():
    return MATRIX.read_text(encoding="utf-8")


def addendum_section(text):
    """Return only the Agent 3 addendum, up to the next top-level heading."""
    start = text.find(ADDENDUM_HEADING)
    if start == -1:
        return ""
    rest = text[start:]
    following = re.search(r"^## ", rest[len(ADDENDUM_HEADING):], re.MULTILINE)
    if following:
        rest = rest[: len(ADDENDUM_HEADING) + following.start()]
    return rest


def table_cells(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def addendum_rows(section):
    """Return (header, data rows) for the addendum's markdown table."""
    header = None
    rows = []
    for line in section.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = table_cells(line)
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells if cell):
            continue  # separator row
        if header is None:
            header = cells
            continue
        rows.append(cells)
    return header or [], rows


def leading_verdict(cell):
    """Extract a verdict from a status cell such as 'PARTIAL (menu only)'."""
    for verdict in VERDICTS:
        if re.match(r"^%s(?=\b|[: (])" % re.escape(verdict), cell):
            return verdict
    return None


class GameMatrixTest(unittest.TestCase):
    def test_document_states_every_verdict(self):
        text = matrix_text()
        self.assertNotEqual(addendum_section(text), "",
                            "Agent 3 addendum heading is missing")
        for verdict in VERDICTS:
            with self.subTest(verdict=verdict):
                self.assertRegex(
                    text,
                    r"\b%s\b" % re.escape(verdict),
                    "%s is not an explicit verdict in %s" % (verdict, MATRIX),
                )

    def test_addendum_table_has_expected_columns_and_rows(self):
        header, rows = addendum_rows(addendum_section(matrix_text()))
        self.assertIn("SHA-256", header)
        self.assertIn("Evidence / blocker", header)
        status_headers = [name for name in header if name in STATUS_COLUMNS]
        self.assertTrue(status_headers, "no status columns in addendum table")
        self.assertGreaterEqual(len(rows), 10, "addendum looks truncated")

        numbers = []
        for row in rows:
            self.assertRegex(row[0], r"^\d+$", "row has no ordinal: %r" % row)
            numbers.append(int(row[0]))
            self.assertTrue(row[1], "row has no game name: %r" % row)
        self.assertEqual(numbers, sorted(numbers), "addendum rows out of order")
        self.assertEqual(len(numbers), len(set(numbers)), "duplicate row numbers")

    def test_every_addendum_row_has_a_status(self):
        header, rows = addendum_rows(addendum_section(matrix_text()))
        status_indexes = [i for i, name in enumerate(header) if name in STATUS_COLUMNS]
        self.assertTrue(status_indexes)
        for row in rows:
            with self.subTest(game=row[1] if len(row) > 1 else row[0]):
                present = [row[i] for i in status_indexes if i < len(row)]
                self.assertTrue(present, "no status cells in row: %r" % row)
                verdicts = [leading_verdict(cell) for cell in present]
                self.assertTrue(
                    any(verdicts),
                    "row has no PASS/PARTIAL/FAIL/BLOCKED/NOT TESTED status: %r" % row,
                )
                for cell, verdict in zip(present, verdicts):
                    self.assertIsNotNone(
                        verdict,
                        "invented or missing verdict in status cell %r of row %r"
                        % (cell, row),
                    )

    def test_sha256_values_are_sixty_four_hex_digits_or_absent(self):
        header, rows = addendum_rows(addendum_section(matrix_text()))
        sha_index = header.index("SHA-256")
        for row in rows:
            with self.subTest(game=row[1] if len(row) > 1 else row[0]):
                if sha_index >= len(row):
                    continue
                value = row[sha_index].strip().strip("`").strip().lower()
                if value in ABSENT_HASHES:
                    continue
                self.assertRegex(value, r"\A[0-9a-f]{64}\Z",
                                 "invented SHA-256 value: %r" % row[sha_index])

    def test_no_hash_like_string_in_document_has_wrong_length(self):
        # Any run of at least 32 hex digits is presented as a hash; a real
        # SHA-256 is exactly 64 digits. Shorter or longer values are invented.
        for candidate in re.findall(r"\b[0-9a-fA-F]{32,}\b", matrix_text()):
            with self.subTest(candidate=candidate[:16]):
                self.assertEqual(len(candidate), 64,
                                 "hash-like value is not a SHA-256: %s" % candidate)
                self.assertRegex(candidate, r"\A[0-9a-fA-F]{64}\Z")


if __name__ == "__main__":
    unittest.main()
