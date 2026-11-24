"""Linux staging paths handed to Docker must be absolute."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import linux_matrix  # noqa: E402


class LinuxPathTests(unittest.TestCase):
    def test_relative_out_and_project_are_resolved(self):
        seen = {}

        def fake_build_one(project, out, image, jobs, run_timeout,
                           build_timeout, min_free_bytes, scratch):
            seen["project"] = project
            seen["out"] = out
            seen["scratch"] = scratch
            return {"name": project.name, "build_rc": 0, "run_rc": 0,
                    "seconds": 0.0}

        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            project = root / "proj"
            project.mkdir()
            with mock.patch.object(linux_matrix, "docker_ready", return_value=True), \
                 mock.patch.object(linux_matrix, "check_free_disk",
                                   return_value={"ok": True}), \
                 mock.patch.object(linux_matrix, "build_one", side_effect=fake_build_one):
                rc = linux_matrix.main(["--project", "proj", "--out", "results",
                                        "--min-free-gb", "0.001"])
            self.assertEqual(rc, 0)
            self.assertTrue(seen["out"].is_absolute())
            self.assertTrue(seen["project"].is_absolute())
            self.assertTrue(seen["scratch"].is_absolute())


if __name__ == "__main__":
    unittest.main()
