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


if __name__ == "__main__":
    unittest.main()
