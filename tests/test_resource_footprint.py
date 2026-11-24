"""Issue #26: every autorecomp report must carry measured resource footprints."""
import tempfile
import unittest
from pathlib import Path

from tools.autorecomp.pipeline import resource_footprint


class ResourceFootprintTest(unittest.TestCase):
    def test_measures_generated_binary_disk_seconds(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            generated = root / 'src' / 'gen'
            generated.mkdir(parents=True)
            (generated / 'recomp_a.c').write_bytes(b'x' * 4096)
            binary = root / 'build' / 'game'
            binary.parent.mkdir()
            binary.write_bytes(b'y' * 2048)

            stages = [
                {'name': 'translate', 'seconds': 2.5,
                 'data': {'source_bytes': 4096}},
                {'name': 'compile', 'seconds': 7.25, 'data': {}},
            ]
            r = resource_footprint(root, stages, binary)
            self.assertEqual(r['generated_source_bytes'], 4096)
            self.assertEqual(r['binary_bytes'], 2048)
            self.assertGreaterEqual(r['project_disk_bytes'], 6144)
            self.assertEqual(r['stage_seconds_total'], 9.75)
            self.assertIn('peak_rss_bytes', r)

    def test_missing_binary_and_no_stages_is_zero_not_a_guess(self):
        with tempfile.TemporaryDirectory() as d:
            r = resource_footprint(Path(d), [], None)
            self.assertEqual(r['generated_source_bytes'], 0)
            self.assertEqual(r['binary_bytes'], 0)
            self.assertEqual(r['stage_seconds_total'], 0.0)


if __name__ == '__main__':
    unittest.main()
