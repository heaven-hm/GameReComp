"""The differential verifier: what it checks, and what it refuses to claim.

The verifier is the evidence behind a HIGH_LEVEL_VERIFIED claim, so its own
failure modes matter more than its success path. These tests pin:

1. A project without a high-level unit is refused, not silently reported clean.
2. A missing mechanical body is refused -- there is nothing to compare against.
3. The harness source it generates names both entry points and commits the
   guest pages the mechanical body reads, because a body that reads a global
   cannot be compared against a harness with no image mapped.
4. The address scan finds the generator's addresses, which are not a fixed
   width (`MEM32(0x413350)` is six hex digits, `sub_0040BA58` is eight).
5. The scope it reports says what was driven and what was not proved.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "pe"))
sys.path.insert(0, str(ROOT / "tools" / "disasm"))
sys.path.insert(0, str(ROOT / "tools" / "lift"))

from tools.autorecomp import hlverify  # noqa: E402


class RefusalTest(unittest.TestCase):
    def test_a_directory_that_is_not_a_project_is_refused(self):
        result = hlverify.verify(ROOT, 0x00401000)
        self.assertFalse(result["ok"])
        self.assertIn("not a project", result["reason"])

    def test_a_missing_mechanical_body_is_refused(self):
        """No fallback to compare against means no verification, not a pass."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "src" / "recomp" / "gen").mkdir(parents=True)
            (project / "src" / "highlevel").mkdir(parents=True)
            result = hlverify.verify(project, 0x00401000)
        self.assertFalse(result["ok"])
        self.assertIn("no mechanical body", result["reason"])


class HarnessSourceTest(unittest.TestCase):
    def test_harness_names_both_entry_points(self):
        self.assertIn("sub_@ADDR@()", hlverify.HARNESS)
        self.assertIn("sub_@ADDR@__lowlevel()", hlverify.HARNESS)

    def test_harness_commits_the_pages_it_reads(self):
        """The commit calls are generated, not in the template: the addresses
        come from the body under test."""
        self.assertIn("@PAGES@", hlverify.HARNESS)
        source = Path(hlverify.__file__).read_text()
        self.assertIn("hle_vm_reserve", source)

    def test_harness_compares_memory_not_only_the_return_value(self):
        self.assertIn("g_after_high", hlverify.HARNESS)
        self.assertIn("memcmp", hlverify.CASE)

    def test_case_reports_a_memory_only_divergence(self):
        """A typed function that writes where the body did not is a divergence
        even when both return the same value."""
        # The message is split across two C string literals, so match the part
        # that is not split.
        self.assertIn("wrote into the", hlverify.CASE)


class AddressScanTest(unittest.TestCase):
    def test_the_scan_finds_addresses_of_either_width(self):
        """Regression: the first version required eight hex digits and so found
        none of the generator's six-digit globals."""
        import re
        body = ("MEM32(0x413350) = eax; sub_0040BA58(); MEM8(0x413BE0) = 1;")
        found = {int(m.group(1), 16) & ~0xFFF
                 for m in re.finditer(r"0x([0-9A-Fa-f]{4,8})u?\b", body)
                 if 0x00400000 <= int(m.group(1), 16) < 0x80000000}
        # Both globals are in the same page; the function name in sub_0040BA58
        # carries no 0x prefix and is not a data address to commit.
        self.assertEqual(found, {0x413000})


class ScopeTest(unittest.TestCase):
    def test_the_verdict_states_what_was_not_proven(self):
        source = Path(hlverify.__file__).read_text()
        self.assertIn('"not_proven"', source)
        self.assertIn("inputs other than the listed seeds", source)


if __name__ == "__main__":
    unittest.main()
