"""Tests for the read-only toolchain feasibility probe.

PATH is mocked by pointing the probe at a synthetic directory of fake
executables (``--path`` / ``path_env``), so these tests are deterministic and
never depend on -- or execute -- a real rev.ng/Anvill/Remill/Rellic install.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import revng_probe as probe  # noqa: E402


def fake_binaries(directory: Path, *names: str) -> None:
    """Create executable stubs; shutil.which only checks existence + X bit."""
    for name in names:
        target = directory / name
        target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        target.chmod(0o755)


class RevngProbeTest(unittest.TestCase):
    # -- registry / safety ------------------------------------------------
    def test_registry_has_exactly_the_five_documented_groups(self):
        names = {spec["tool"] for spec in probe.TOOL_REGISTRY}
        self.assertEqual(names, {"revng", "anvill", "remill", "rellic", "llvm"})
        by_name = {s["tool"]: s for s in probe.TOOL_REGISTRY}
        self.assertIn("GPL-2.0", by_name["revng"]["licence"])
        self.assertEqual(by_name["anvill"]["licence"], "AGPL-3.0")
        self.assertEqual(by_name["remill"]["licence"], "Apache-2.0")
        self.assertEqual(by_name["rellic"]["licence"], "Apache-2.0")
        self.assertTrue(by_name["revng"]["upstream"].startswith("https://github.com/"))
        self.assertTrue(by_name["anvill"]["upstream"].startswith("https://github.com/"))

    def test_probe_never_imports_or_calls_subprocess(self):
        """A feasibility probe must not silently become an integration."""
        self.assertFalse(hasattr(probe, "subprocess"))
        with mock.patch.object(probe.shutil, "which", return_value="/fake/revng") as which:
            probe.probe_tool(probe.TOOL_REGISTRY[0], path_env="/fake", env={})
            which.assert_called_once()
        # No run/Popen/call anywhere in the module source.
        source = (ROOT / "tools" / "revng_probe.py").read_text(encoding="utf-8")
        self.assertNotIn("subprocess", source)
        self.assertNotIn("os.system", source)

    # -- PATH detection ---------------------------------------------------
    def test_finds_fake_binaries_on_mocked_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            fake_binaries(directory, "revng", "rellic-decomp", "llvm-objdump")
            report = probe.build_report(path_env=str(directory), env={})
        self.assertEqual(report["tools"]["revng"]["status"], "available")
        self.assertEqual(report["tools"]["rellic"]["status"], "available")
        self.assertEqual(report["tools"]["llvm"]["status"], "available")
        self.assertEqual(report["tools"]["anvill"]["status"], "missing")
        revng_found = report["tools"]["revng"]["found"]
        self.assertEqual(revng_found[0]["name"], "revng")
        self.assertEqual(revng_found[0]["path"], str(directory / "revng"))
        self.assertEqual(revng_found[0]["source"], "path")

    def test_missing_tools_have_actionable_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = probe.build_report(path_env=tmp, env={})
        for name in ("revng", "anvill", "remill", "rellic"):
            entry = report["tools"][name]
            self.assertEqual(entry["status"], "missing", name)
            self.assertTrue(entry["found"] == [] and entry["missing"], name)
            self.assertIn("no matching binary on PATH", entry["reason"])
            self.assertIn("REVNG_BIN" if name == "revng" else "expected one of", entry["reason"])
        self.assertEqual(report["summary"]["overall"], "no-core-toolchain-detected")
        self.assertEqual(report["summary"]["available_core_tools"], [])

    def test_partial_status_and_missing_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_binaries(Path(tmp), "rellic-xref")
            entry = probe.build_report(path_env=tmp, env={})["tools"]["rellic"]
        self.assertEqual(entry["status"], "partial")
        self.assertEqual([f["name"] for f in entry["found"]], ["rellic-xref"])
        self.assertIn("rellic-decomp", entry["missing"])
        self.assertIn("missing rellic-decomp", entry["reason"])

    def test_env_override_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_binaries(Path(tmp), "my-revng")
            env = {"REVNG_BIN": str(Path(tmp) / "my-revng")}
            entry = probe.build_report(path_env=tmp, env=env)["tools"]["revng"]
        self.assertEqual(entry["status"], "available")
        self.assertEqual(entry["found"][0]["source"], "env")

    def test_env_override_absence_is_explained(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = probe.build_report(path_env=tmp, env={"REVNG_BIN": "/no/such/bin"})["tools"]["revng"]
        self.assertEqual(entry["status"], "missing")
        self.assertIn("expected one of: revng", entry["reason"])

    # -- optional JSON tool list ------------------------------------------
    def test_tools_file_strings_are_grouped(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            tools_file = directory / "tools.json"
            tools_file.write_text(json.dumps(["ghidra", "radare2"]), encoding="utf-8")
            fake_binaries(directory, "ghidra")
            extras = probe.load_extra_tools(tools_file)
            self.assertEqual(len(extras), 1)
            self.assertEqual(extras[0]["tool"], "user-supplied")
            self.assertEqual(extras[0]["binaries"], ["ghidra", "radare2"])
            report = probe.build_report(path_env=str(directory), env={}, extra_tools=extras)
        entry = report["tools"]["user-supplied"]
        self.assertEqual(entry["status"], "available")
        self.assertEqual(entry["missing"], ["radare2"])
        self.assertIn("missing radare2", entry["reason"])
        self.assertIn("unspecified (user-supplied)", entry["licence"])

    def test_tools_file_object_specs_override_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            tools_file = directory / "tools.json"
            tools_file.write_text(
                json.dumps(
                    [{
                        "tool": "ghidra",
                        "binaries": ["analyzeHeadless"],
                        "primary": "analyzeHeadless",
                        "env_var": "GHIDRA_BIN",
                        "licence": "Apache-2.0",
                        "upstream": "https://github.com/NationalSecurityAgency/ghidra",
                        "caveat": "user entry",
                    }]
                ),
                encoding="utf-8",
            )
            fake_binaries(directory, "analyzeHeadless")
            extras = probe.load_extra_tools(tools_file)
            report = probe.build_report(
                path_env=str(directory), env={"GHIDRA_BIN": ""}, extra_tools=extras
            )
        entry = report["tools"]["ghidra"]
        self.assertEqual(entry["status"], "available")
        self.assertEqual(entry["licence"], "Apache-2.0")
        self.assertEqual(entry["upstream"], "https://github.com/NationalSecurityAgency/ghidra")

    def test_invalid_tools_file_shapes_raise_probe_input_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            for payload, fragment in (
                ("{not json", "invalid JSON"),
                ('{"tool": "x"}', "must contain a JSON list"),
                ('[42]', "must be a string or an object"),
                ('[{"tool": "x"}]', "missing 'binaries'"),
                ('[{"tool": "x", "binaries": 3}]', "must be a string or a list"),
            ):
                path.write_text(payload, encoding="utf-8")
                with self.subTest(payload=payload):
                    with self.assertRaises(probe.ProbeInputError) as ctx:
                        probe.load_extra_tools(path)
                    self.assertIn(fragment, str(ctx.exception))
            with self.assertRaises(probe.ProbeInputError):
                probe.load_extra_tools(Path(tmp) / "missing.json")

    # -- report shape / CLI -----------------------------------------------
    def test_report_is_machine_readable_with_explicit_reasons(self):
        fixed = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            fake_binaries(Path(tmp), "revng", "llvm-objdump")
            report = probe.build_report(path_env=tmp, env={}, now=fixed)
        text = probe.format_report(report)
        parsed = json.loads(text)
        self.assertEqual(parsed["schema_version"], probe.SCHEMA_VERSION)
        self.assertEqual(parsed["generated_at"], fixed.isoformat())
        self.assertEqual(set(parsed), {"schema_version", "generated_at", "host", "tools", "summary", "limitations"})
        self.assertTrue(parsed["limitations"])
        self.assertEqual(parsed["summary"]["overall"], "partial-toolchain-detected")
        self.assertEqual(parsed["summary"]["available_core_tools"], ["revng"])
        self.assertIn("revng-decompilation", parsed["summary"]["feasible_pipelines"])
        self.assertNotIn("anvill-specification-lifting", parsed["summary"]["feasible_pipelines"])
        for tool in parsed["tools"].values():
            self.assertTrue(tool["reason"].strip())
            self.assertIn(tool["status"], {"available", "partial", "missing"})

    def test_host_can_be_injected_for_determinism(self):
        report = probe.build_report(host={"platform": "test", "machine": "test", "python": "test"}, env={})
        self.assertEqual(report["host"]["platform"], "test")

    def test_main_stdout_and_out_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            out = directory / "report.json"
            self.assertEqual(probe.main(["--path", tmp, "--out", str(out)]), 0)
            self.assertEqual(json.loads(out.read_text())["schema_version"], 1)

    def test_main_bad_tools_file_returns_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("nope", encoding="utf-8")
            self.assertEqual(probe.main(["--tools-file", str(path)]), 2)

    def test_main_compact_is_single_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(sys, "stdout", new_callable=io.StringIO) as buf:
                self.assertEqual(probe.main(["--path", tmp, "--compact"]), 0)
                self.assertEqual(len(buf.getvalue().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
