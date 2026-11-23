"""Unit tests for GameReComp's optional AI source-readability pass."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.autorecomp import readability  # noqa: E402


class ReadabilityHelpersTests(unittest.TestCase):
    def test_parser_accepts_providers_ai_and_function(self):
        args = readability.build_parser().parse_args(
            ["/tmp/proj", "--providers", "ai", "--function", "0x00402020"]
        )
        self.assertEqual(args.project_dir, "/tmp/proj")
        self.assertEqual(args.providers, "ai")
        self.assertEqual(args.function, "0x00402020")

    def test_extracts_c_from_fenced_and_raw_llm_output(self):
        fenced = "Here is cleaner code:\n```c\nvoid sub_402020(void) { /* clean */ }\n```\n"
        self.assertEqual(
            readability._extract_c_from_llm_output(fenced),
            "void sub_402020(void) { /* clean */ }\n",
        )
        raw = "int main(void) { return 0; }"
        self.assertEqual(
            readability._extract_c_from_llm_output(raw),
            "int main(void) { return 0; }\n",
        )

    def test_detects_stdin_llm_commands_vs_wrapper_scripts(self):
        self.assertTrue(readability._is_stdin_llm_cmd(["claude", "-p"]))
        self.assertTrue(readability._is_stdin_llm_cmd(["ollama", "run", "qwen2.5-coder:14b"]))
        self.assertFalse(readability._is_stdin_llm_cmd(["python3", "wrapper.py"]))

    def test_select_units_targets_requested_function(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            gen = proj / "src" / "recomp" / "gen"
            gen.mkdir(parents=True)
            (gen / "recomp_0001.c").write_text("void sub_00401000(void) {}\n")
            u2 = gen / "recomp_0002.c"
            u2.write_text("void sub_00402020(void) {}\n")
            harness = readability.ReadabilityHarness(
                proj, providers="ai", target_function="0x00402020"
            )
            picked = harness._select_units()
            self.assertEqual(picked, [u2.resolve()])

    def test_extracts_exported_dispatched_symbols_only(self):
        code = (
            '#include "recomp_types.h"\n'
            "void sub_00402020(void) {\n"
            "    int tmp = 0; /* sub_00409999 in a comment is not a symbol */\n"
            "    RECOMP_CALL(sub_00401300);\n"
            "}\n"
            "void sub_00401000(void) { }\n"
            "static void local_helper(void) { }\n"
            "const recomp_dispatch_entry_t table[] = {\n"
            "    { 0x00402020u, sub_00402020 },\n"
            "};\n"
        )
        symbols = readability.extract_exported_symbols(code)
        self.assertIn("sub_00402020", symbols)
        self.assertIn("sub_00401300", symbols)
        self.assertIn("sub_00401000", symbols)
        self.assertNotIn("sub_00409999", symbols)
        self.assertNotIn("local_helper", symbols)
        self.assertNotIn("tmp", symbols)
        self.assertNotIn("table", symbols)

    def test_symbol_guard_reports_renamed_dispatched_symbol(self):
        original = "void sub_00402020(void) { int tmp = 1; }\n"
        candidate = "void player_Update(void) { int readableCount = 1; }\n"
        changes = readability.find_symbol_name_changes(original, candidate)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["symbol"], "sub_00402020")
        self.assertEqual(changes[0]["suspected_rename"], "player_Update")
        feedback = readability.format_symbol_guard_feedback("recomp_0001.c", changes)
        self.assertIn("sub_00402020 -> player_Update", feedback)
        self.assertIn("recomp_dispatch.c", feedback)

    def test_symbol_guard_allows_local_renames_and_comments(self):
        original = "void sub_00402020(void) { int tmp = 1; }\n"
        candidate = (
            "void sub_00402020(void) {\n"
            "    /* 0x00402020 counts things; see sub_00401000 for the caller */\n"
            "    int readableCount = 1;\n"
            "}\n"
        )
        self.assertEqual(
            readability.find_symbol_name_changes(original, candidate), [])

    def test_symbol_guard_catches_definition_rename_with_stale_call_ref(self):
        original = (
            "void sub_00402020(void) { }\n"
            "void caller(void) { RECOMP_CALL(sub_00402020); }\n"
        )
        candidate = (
            "void player_Update(void) { }\n"
            "void caller(void) { RECOMP_CALL(sub_00402020); }\n"
        )
        changes = readability.find_symbol_name_changes(original, candidate)
        self.assertEqual([c["symbol"] for c in changes], ["sub_00402020"])
        self.assertEqual(changes[0]["suspected_rename"], "player_Update")

    def test_providers_ai_without_command_fails_closed_without_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            gen = proj / "src" / "recomp" / "gen"
            gen.mkdir(parents=True)
            unit = gen / "recomp_0001.c"
            orig_code = "void sub_00402020(void) {}\n"
            unit.write_text(orig_code)

            reports = proj / "reports"
            reports.mkdir()
            fake_bin = proj / "fake_bin"
            fake_bin.write_text("bin")
            digest = readability.source_digest(proj)
            (proj / "manifest.json").write_text(
                json.dumps({"source_digest_sha256": digest})
            )
            (reports / "verification.json").write_text(
                json.dumps({"status": "partial"})
            )
            (reports / "run.json").write_text(
                json.dumps({"command": [str(fake_bin)], "seconds": 0.1})
            )

            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("REVENG_AI_CMD", None)
                harness = readability.ReadabilityHarness(
                    proj, providers="ai", target_function="0x00402020"
                )
                report = harness.run()

            self.assertEqual(report["outcome"], "blocked")
            self.assertEqual(unit.read_text(), orig_code)
            self.assertTrue((reports / "readability.json").is_file())


    def test_revert_also_rebuilds_baseline_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = readability.ReadabilityHarness(Path(tmp), providers="ai")
            with mock.patch.object(harness, '_revert_unit') as reverted, \
                 mock.patch.object(harness, '_rebuild',
                                   return_value={'status': 'ok', 'log': ''}) as rebuilt:
                harness._revert_and_rebuild(Path(tmp) / 'u.c', Path(tmp) / 'bak.c')
            self.assertEqual(reverted.call_count, 1)
            self.assertEqual(rebuilt.call_count, 1)

    def test_accepted_edit_updates_manifest_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            (proj / "manifest.json").write_text(
                json.dumps({"source_digest_sha256": "old", "other": "kept"}))
            harness = readability.ReadabilityHarness(proj, providers="ai")
            harness._update_manifest_digest("new-digest")
            data = json.loads((proj / "manifest.json").read_text())
            self.assertEqual(data["source_digest_sha256"], "new-digest")
            self.assertEqual(data["other"], "kept")


class SymbolGuardUnitTests(unittest.TestCase):
    """Drive the AI repair loop with a fake stdin provider (no subprocesses)."""

    @staticmethod
    def _make_project(root, original):
        proj = Path(root).resolve()
        gen = proj / "src" / "recomp" / "gen"
        gen.mkdir(parents=True)
        unit = gen / "recomp_0001.c"
        unit.write_text(original)
        work = proj / "readability" / "ai"
        work.mkdir(parents=True)
        return proj, unit, work

    @staticmethod
    def _fake_provider(candidate, calls):
        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return mock.Mock(
                returncode=0,
                stdout="```c\n" + candidate + "\n```\n",
                stderr="",
            )
        return fake_run

    def test_renamed_symbol_is_rejected_without_writing_or_building(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = "void sub_00402020(void) { int tmp = 1; }\n"
            bad = "void player_Update(void) { int readableCount = 1; }\n"
            proj, unit, work = self._make_project(tmp, original)
            harness = readability.ReadabilityHarness(
                proj, providers="ai", max_attempts=2)
            calls = []

            with mock.patch.object(readability.subprocess, "run",
                                   self._fake_provider(bad, calls)), \
                 mock.patch.object(harness, "_rebuild") as rebuilt, \
                 mock.patch.object(harness, "_revert_and_rebuild",
                                   side_effect=lambda u, b: harness._revert_unit(u, b)):
                result = harness._ai_unit(unit, ["claude", "-p"], work)

            self.assertEqual(result, "rejected-after-repair")
            self.assertEqual(unit.read_text(), original)
            rebuilt.assert_not_called()
            self.assertEqual(harness.report["attempts"][0]["step"], "symbol-guard")
            self.assertFalse(harness.report["attempts"][0]["ok"])
            second_feedback = (work / "recomp_0001.c.attempt2.feedback.txt").read_text()
            self.assertIn("sub_00402020 -> player_Update", second_feedback)
            self.assertEqual(harness.report["edits"][0]["last_step"], "symbol-guard")

    def test_safe_local_rename_proceeds_to_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = "void sub_00402020(void) { int tmp = 1; }\n"
            good = "void sub_00402020(void) { /* clearer */ int readableCount = 1; }\n"
            proj, unit, work = self._make_project(tmp, original)
            harness = readability.ReadabilityHarness(proj, providers="ai")
            harness.baseline = {"timed_out": False, "exit_code": 0}
            calls = []
            evidence = {"exit_code": 0, "timed_out": False,
                        "stdout_tail": "", "stderr_tail": ""}

            with mock.patch.object(readability.subprocess, "run",
                                   self._fake_provider(good, calls)), \
                 mock.patch.object(harness, "_rebuild",
                                   return_value={"status": "ok", "log": ""}) as rebuilt, \
                 mock.patch.object(harness, "_rerun", return_value=evidence), \
                 mock.patch.object(harness, "_revert_and_rebuild"):
                result = harness._ai_unit(unit, ["claude", "-p"], work)

            self.assertEqual(result, "accepted")
            self.assertEqual(rebuilt.call_count, 1)
            self.assertEqual(unit.read_text(), good)


if __name__ == "__main__":
    unittest.main()
