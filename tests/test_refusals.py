"""Structured refusals must be typed, evidence-led and fail closed.

The fixtures below use the strings the pipeline and runtime actually emit:
``/* ERROR lifting sub_XXXXXXXX: ... */`` in generated C, ``[pe] unresolved
import ...`` in runtime logs, and the ``unresolved_imports`` field of
``reports/run.json``. A report that invents a cause, or accepts a project with
an unread source, is the failure these tests pin down.
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.autorecomp import refusals  # noqa: E402


def write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class LiftParserTest(unittest.TestCase):
    def test_parses_marker_with_address_message_and_line(self):
        text = ("void ok(void) {}\n"
                "/* ERROR lifting sub_00401000: unsupported opcode 0x0F */\n"
                "void sub_00401000(void) { abort(); }\n")
        found = refusals.parse_lift_errors(text, "src/recomp/gen/recomp_0000.c")
        self.assertEqual(len(found), 1)
        hit = found[0]
        self.assertIs(hit.cause, refusals.RefusalCause.LIFT_ERROR)
        self.assertEqual(hit.key, "0x00401000")
        self.assertEqual(hit.detail["address"], "0x00401000")
        self.assertEqual(hit.detail["message"], "unsupported opcode 0x0F")
        self.assertEqual(hit.line, 2)
        self.assertEqual(hit.source, "src/recomp/gen/recomp_0000.c")

    def test_unrelated_text_and_empty_input_produce_nothing(self):
        self.assertEqual(refusals.parse_lift_errors("void ok(void) {}\n", "x"), [])
        self.assertEqual(refusals.parse_lift_errors(None, "x"), [])
        starred = refusals.parse_lift_errors(
            "/* ERROR lifting sub_00401000: bad *ptr read */\n", "x")
        self.assertEqual(starred[0].detail["message"], "bad *ptr read")
        text = ("/* ERROR lifting sub_00401000: first */\n"
                "/* ERROR lifting custom_symbol: second */\n")
        keys = [r.key for r in refusals.parse_lift_errors(text, "x")]
        self.assertEqual(keys, ["0x00401000", "custom_symbol"])
        self.assertIsNone(refusals.parse_lift_errors(text, "x")[1].detail["address"])


class ImportParserTest(unittest.TestCase):
    STRICT_STDERR = """\
[pe] tf.exe mapped at 0x00400000, span 0x3D000
[pe] unresolved import bulletml.dll!_BulletMLRunner_new_parser
[pe] unresolved import KERNEL32.DLL!SuspendThread
[pe] bound 59 imports, 86 unresolved
[host] tf.exe booted: entry 0x004187D0, 86 unresolved imports
"""

    def test_reads_only_real_loader_lines_and_keeps_aggregates_separate(self):
        found, counted = refusals.parse_unresolved_imports_text(
            self.STRICT_STDERR, "logs/run.stderr.log")
        self.assertEqual([r.key for r in found],
                         ["bulletml.dll!_BulletMLRunner_new_parser",
                          "KERNEL32.DLL!SuspendThread"])
        self.assertEqual(found[0].line, 2)
        self.assertEqual(found[0].detail["evidence"],
                         "[pe] unresolved import bulletml.dll!_BulletMLRunner_new_parser")
        self.assertEqual([(a["kind"], a["count"]) for a in counted],
                         [("iat-bind", 86), ("boot", 86)])

    def test_clean_and_empty_logs_are_not_evidence(self):
        clean = ("[pe] bound 147 imports, 0 unresolved\n"
                 "[host] wf.exe booted: entry 0x00433569, 0 unresolved imports\n"
                 "[win32hle] ExitProcess(1)\n")
        self.assertEqual(refusals.parse_unresolved_imports_text(clean, "x"), ([], []))
        self.assertEqual(refusals.parse_unresolved_imports_text(None, "x"), ([], []))
        self.assertEqual(refusals.parse_unresolved_imports_text("", "x"), ([], []))

    def test_json_report_parser_walks_nested_keys(self):
        data = {"run": {"unresolved_imports": ["a!f", {"import": "b!g"}],
                        "unresolved_import_count": 7},
                "other": {"iat_unresolved": 2}}
        found, counted = refusals.parse_unresolved_imports_report(
            data, "reports/run.json")
        self.assertEqual([r.key for r in found], ["a!f", "b!g"])
        self.assertEqual(found[0].detail["report_path"], "run.unresolved_imports")
        self.assertEqual([(a["kind"], a["count"]) for a in counted],
                         [("unresolved_import_count", 7), ("iat_unresolved", 2)])


class NativeBinaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "proj"
        self.project.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_records_checked_paths_and_clears_when_a_binary_exists(self):
        write(self.project / "manifest.json", json.dumps(
            {"game": "wf.exe", "binary": {"path": str(self.project / "build" / "wf")}}))
        found, info = refusals.parse_missing_native(self.project)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].cause, refusals.RefusalCause.MISSING_NATIVE_BINARY)
        self.assertEqual(found[0].key, "native-binary")
        self.assertIn(str(self.project / "build" / "wf"), info["checked"])
        self.assertEqual(info["existing"], [])

        write(self.project / "build" / "wf", "ELF")
        found, info = refusals.parse_missing_native(self.project)
        self.assertEqual(found, [])
        self.assertEqual(info["existing"], [str(self.project / "build" / "wf")])

    def test_conventional_exe_path_without_manifest(self):
        write(self.project / "project.json", json.dumps({"name": "game"}))
        self.assertEqual(len(refusals.parse_missing_native(self.project)[0]), 1)
        write(self.project / "build" / "game.exe", "MZ")
        self.assertEqual(refusals.parse_missing_native(self.project)[0], [])

    def test_relative_manifest_path_is_resolved_against_the_project(self):
        write(self.project / "manifest.json", json.dumps(
            {"game": "game.exe", "binary": {"path": "build/game"}}))
        self.assertEqual(len(refusals.parse_missing_native(self.project)[0]), 1)
        write(self.project / "build" / "game", "ELF")
        self.assertEqual(refusals.parse_missing_native(self.project)[0], [])


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "proj"
        self.project.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def make_clean_project(self):
        write(self.project / "src" / "recomp" / "gen" / "recomp_0000.c",
              "void sub_00401000(void) { return; }\n")
        write(self.project / "logs" / "run.stderr.log",
              "[pe] bound 10 imports, 0 unresolved\n"
              "[host] game.exe booted: entry 0x00401000, 0 unresolved imports\n")
        write(self.project / "reports" / "run.json", json.dumps(
            {"status": "exited", "unresolved_imports": [], "unresolved_import_count": 0}))
        binary = self.project / "build" / "game"
        write(binary, "ELF")
        write(self.project / "manifest.json", json.dumps(
            {"game": "game.exe", "binary": {"path": str(binary)}}))
        return self.project

    def test_all_three_causes_are_named_counted_and_anchored(self):
        write(self.project / "src" / "recomp" / "gen" / "recomp_0000.c",
              "/* ERROR lifting sub_00401000: bad opcode */\n")
        write(self.project / "src" / "recomp" / "gen" / "recomp_0001.c",
              "/* ERROR lifting sub_00402000: bad register */\n")
        write(self.project / "reports" / "functions.json", json.dumps({
            "functions": [
                {"address": "0x00401000", "name": "sub_00401000", "category": "UNRESOLVED"},
                {"address": "0x00402000", "name": "sub_00402000", "category": "UNRESOLVED"},
                {"address": "0x00403000", "name": "sub_00403000",
                 "category": "LOW_LEVEL_FALLBACK"},
            ], "lift_errors": 2}))
        write(self.project / "logs" / "run.stderr.log",
              "[pe] unresolved import a!f\n"
              "[pe] unresolved import b!g\n"
              "[pe] bound 10 imports, 2 unresolved\n")
        write(self.project / "reports" / "run.json", json.dumps(
            {"unresolved_imports": ["a!f"], "unresolved_import_count": 2}))

        report = refusals.collect_refusals(self.project)
        self.assertEqual(report["schema"], "gamerecomp.refusals.v1")
        self.assertTrue(report["fail_closed"])
        self.assertTrue(report["refused"])
        self.assertEqual(report["verdict"], "REFUSED")
        self.assertEqual(report["counts"], {"LIFT_ERROR": 2,
                                            "UNRESOLVED_IMPORT": 2,
                                            "MISSING_NATIVE_BINARY": 1})
        self.assertEqual(report["total"], 5)
        self.assertEqual(report["reported_unresolved"], 2)
        self.assertEqual(report["error"], None)

        lifts = report["causes"]["LIFT_ERROR"]
        self.assertEqual(sorted(i["address"] for i in lifts["items"]),
                         ["0x00401000", "0x00402000"])
        self.assertEqual(lifts["occurrences"], 4)  # 2 C markers + 2 report rows
        self.assertIn("src/recomp/gen/recomp_0000.c", lifts["sources"])
        self.assertIn("reports/functions.json", lifts["sources"])
        from_c = next(i for i in lifts["items"]
                      if i["source"] == "src/recomp/gen/recomp_0000.c")
        self.assertEqual(from_c["line"], 1)

        imports = report["causes"]["UNRESOLVED_IMPORT"]
        self.assertEqual(sorted(i["key"] for i in imports["items"]), ["a!f", "b!g"])
        a = next(i for i in imports["items"] if i["key"] == "a!f")
        self.assertEqual(a["occurrences"], 2)  # log + run.json
        self.assertIn("logs/run.stderr.log", imports["sources"])

        native = report["causes"]["MISSING_NATIVE_BINARY"]
        self.assertEqual(native["count"], 1)
        self.assertTrue(native["items"][0]["checked"])

        kinds = {s["kind"] for s in report["sources_scanned"]}
        self.assertEqual(kinds, {"generated-c", "functions-report", "log", "report"})

    def test_clean_project_is_accepted(self):
        report = refusals.collect_refusals(self.make_clean_project())
        self.assertEqual(report["verdict"], "ACCEPTED")
        self.assertFalse(report["refused"])
        self.assertEqual(report["total"], 0)
        self.assertEqual(report["reported_unresolved"], 0)
        self.assertEqual(set(report["counts"].values()), {0})
        self.assertEqual(report["error"], None)
        self.assertTrue(report["native_binary"]["existing"])

    def test_aggregate_only_count_refuses_without_inventing_names(self):
        write(self.project / "logs" / "run.stderr.log",
              "[pe] bound 59 imports, 86 unresolved\n")
        report = refusals.collect_refusals(self.project)
        entry = report["causes"]["UNRESOLVED_IMPORT"]
        self.assertEqual(entry["count"], 86)
        self.assertEqual(entry["items"][0]["import"], None)
        self.assertEqual(entry["items"][0]["source"], "aggregate")
        self.assertTrue(report["refused"])

    def test_missing_project_directory_fails_closed(self):
        missing = self.project / "not-created"
        report = refusals.collect_refusals(missing)
        self.assertTrue(report["refused"])
        self.assertEqual(report["verdict"], "REFUSED")
        self.assertIn("does not exist", report["error"])
        self.assertEqual(report["total"], 0)

    def test_unreadable_source_fails_closed(self):
        self.make_clean_project()
        original = Path.read_text

        def deny(self, *args, **kwargs):
            if self.name == "run.stderr.log":
                raise OSError("permission denied")
            return original(self, *args, **kwargs)

        with patch.object(Path, "read_text", deny):
            report = refusals.collect_refusals(self.project)
        self.assertIn("unreadable log", report["error"])
        self.assertTrue(report["refused"])
        self.assertEqual(report["verdict"], "REFUSED")


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "proj"
        self.project.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_exit_codes_and_json_output(self):
        binary = self.project / "build" / "game"
        write(binary, "ELF")
        write(self.project / "manifest.json", json.dumps(
            {"game": "game.exe", "binary": {"path": str(binary)}}))
        out = self.project / "refusals.json"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = refusals.main([str(self.project), "--json", str(out), "--quiet"])
        self.assertEqual(code, refusals.EXIT_ACCEPTED)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(json.loads(out.read_text())["verdict"], "ACCEPTED")

        write(self.project / "src" / "recomp" / "gen" / "recomp_0000.c",
              "/* ERROR lifting sub_00401000: bad */\n")
        with contextlib.redirect_stdout(io.StringIO()):
            code = refusals.main([str(self.project), "--json", str(out)])
        self.assertEqual(code, refusals.EXIT_REFUSED)
        self.assertEqual(json.loads(out.read_text())["counts"]["LIFT_ERROR"], 1)


if __name__ == "__main__":
    unittest.main()
