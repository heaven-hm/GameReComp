"""map_names.py -> name_lift.py name ingestion must be generic and fail closed."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.lift.name_lift import load_known_file, main


class KnownFileTests(unittest.TestCase):
    def test_map_names_json_and_report_shape(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "names.json"
            p.write_text(json.dumps({
                "0x00401000": "Track__Init",
                "0x00401100": {"name": "Track__Tick", "why": "linker map"},
                "00401200": "Track__Finish",
            }))
            self.assertEqual(load_known_file(p), {
                0x401000: "Track__Init",
                0x401100: "Track__Tick",
                0x401200: "Track__Finish",
            })

    def test_malformed_files_fail_closed(self):
        bad = [
            ["not", "an", "object"],
            {"zzz": "Name"},
            {"0x00401000": ""},
            {"0x00401000": 42},
        ]
        for obj in bad:
            with self.subTest(obj=obj), tempfile.TemporaryDirectory() as t:
                p = Path(t) / "names.json"
                p.write_text(json.dumps(obj))
                with self.assertRaises(SystemExit):
                    load_known_file(p)

    def test_cli_merges_file_and_explicit_known(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "names.json"
            p.write_text(json.dumps({"0x00401000": "FromMap", "0x00401100": "Second"}))
            with mock.patch("tools.lift.name_lift.Image", return_value=object()), \
                 mock.patch("tools.lift.name_lift.name_lift", return_value={}) as call:
                main(["fake.exe", "--gen", t, "--known-file", str(p),
                      "--known", "0x00401000=FromHost"])
            known = call.call_args[0][3]
            self.assertEqual(known[0x401000], "FromHost")
            self.assertEqual(known[0x401100], "Second")


if __name__ == "__main__":
    unittest.main()
