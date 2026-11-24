"""Unit tests for the reproducible Stela benchmark and comparison tools.

These are offline unit tests: ``subprocess`` is mocked and the pipeline report
is synthetic JSON. They lock down measurement extraction (per-stage seconds and
byte counts), TIMEOUT handling, percentage math, and the same-input guard.
No real recompilation, game input, or network access is used.
"""
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tools.stela_bench import bench
from tools.stela_compare import main as compare_main
from tools.stela_compare import pct

_REPORT_STAGES = [
    {'name': 'decode', 'seconds': 1.25, 'data': {}},
    {'name': 'translate', 'seconds': 2.5,
     'data': {'source_bytes': 4096, 'source_units': 7}},
    {'name': 'compile', 'seconds': 3.75, 'data': {'seconds': 3.75}},
]


def write_report(project: Path, stages):
    reports = project / 'reports'
    reports.mkdir(parents=True, exist_ok=True)
    (reports / 'report.json').write_text(json.dumps({'stages': stages}))


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


class BenchExtractionTest(unittest.TestCase):
    def test_extracts_stages_bytes_units_binary_size_and_command(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pe = root / 'game.exe'
            pe.write_bytes(b'MZ' + b'\x00' * 30)
            project = root / 'proj'
            project.mkdir()
            write_report(project, _REPORT_STAGES)
            binary = project / 'build' / project.name
            binary.parent.mkdir()
            binary.write_bytes(b'x' * 2048)

            done = subprocess.CompletedProcess(args=[], returncode=0, stdout='ok\n')
            with patch('tools.stela_bench.subprocess.run', return_value=done) as run, \
                    patch('tools.stela_bench.time.time', side_effect=[100.0, 100.5]):
                rec = bench(pe, project, 'source', 2, 60)

            self.assertEqual(rec['rc'], 0)
            self.assertEqual(rec['wall_seconds'], 0.5)
            self.assertEqual(rec['mode'], 'source')
            self.assertEqual(rec['jobs'], 2)
            self.assertEqual(rec['stages_seconds'],
                             {'decode': 1.25, 'translate': 2.5, 'compile': 3.75})
            self.assertEqual(rec['generated_source_bytes'], 4096)
            self.assertEqual(rec['generated_units'], 7)
            self.assertEqual(rec['binary_bytes'], 2048)
            self.assertEqual(rec['compile_seconds'], 3.75)
            self.assertEqual(rec['output_tail'], 'ok\n')
            self.assertEqual(rec['input'], str(pe))
            self.assertEqual(rec['input_sha256'],
                             hashlib.sha256(pe.read_bytes()).hexdigest())

            cmd = run.call_args.args[0]
            self.assertEqual(cmd[cmd.index('--out-mode') + 1], 'source-only')
            self.assertEqual(cmd[cmd.index('--jobs') + 1], '2')
            self.assertEqual(cmd[cmd.index('--target-arch') + 1], 'host')
            self.assertIn('--no-run', cmd)

    def test_binary_mode_uses_binary_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pe = root / 'game.exe'
            pe.write_bytes(b'MZ')
            project = root / 'proj'
            project.mkdir()
            done = subprocess.CompletedProcess(args=[], returncode=0, stdout='')
            with patch('tools.stela_bench.subprocess.run', return_value=done) as run, \
                    patch('tools.stela_bench.time.time', side_effect=[0.0, 1.0]):
                rec = bench(pe, project, 'binary', 1, 30)
            cmd = run.call_args.args[0]
            self.assertEqual(cmd[cmd.index('--out-mode') + 1], 'binary-only')
            self.assertEqual(rec['mode'], 'binary')

    def test_missing_report_yields_zero_metrics_not_guesses(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pe = root / 'game.exe'
            pe.write_bytes(b'MZ')
            project = root / 'proj'
            project.mkdir()
            done = subprocess.CompletedProcess(args=[], returncode=1, stdout='failed\n')
            with patch('tools.stela_bench.subprocess.run', return_value=done), \
                    patch('tools.stela_bench.time.time', side_effect=[0.0, 0.0]):
                rec = bench(pe, project, 'source', 1, 30)
            self.assertEqual(rec['rc'], 1)
            self.assertEqual(rec['stages_seconds'], {})
            self.assertEqual(rec['generated_source_bytes'], 0)
            self.assertEqual(rec['generated_units'], 0)
            self.assertEqual(rec['binary_bytes'], 0)
            self.assertIsNone(rec['compile_seconds'])


class BenchTimeoutTest(unittest.TestCase):
    def test_timeout_is_recorded_with_bytes_output(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pe = root / 'game.exe'
            pe.write_bytes(b'MZ')
            project = root / 'proj'
            project.mkdir()
            write_report(project, _REPORT_STAGES)
            with patch('tools.stela_bench.subprocess.run',
                       side_effect=subprocess.TimeoutExpired(
                           'fake', 5, output=b'partial-output')):
                rec = bench(pe, project, 'source', 1, 5)
            self.assertEqual(rec['rc'], 'TIMEOUT')
            self.assertIn('partial-output', rec['output_tail'])
            self.assertEqual(rec['stages_seconds'],
                             {'decode': 1.25, 'translate': 2.5, 'compile': 3.75})

    def test_timeout_with_str_output_is_kept_as_is(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pe = root / 'game.exe'
            pe.write_bytes(b'MZ')
            project = root / 'proj'
            project.mkdir()
            with patch('tools.stela_bench.subprocess.run',
                       side_effect=subprocess.TimeoutExpired(
                           'fake', 5, output='text-partial')):
                rec = bench(pe, project, 'source', 1, 5)
            self.assertEqual(rec['rc'], 'TIMEOUT')
            self.assertEqual(rec['output_tail'], 'text-partial')


class PercentageTest(unittest.TestCase):
    def test_percent_from_measured_values(self):
        self.assertEqual(pct(10.0, 15.0), 50.0)
        self.assertEqual(pct(100.0, 90.0), -10.0)
        self.assertEqual(pct(8.0, 8.0), 0.0)

    def test_zero_baseline_returns_zero_not_division_error(self):
        self.assertEqual(pct(0, 5), 0.0)
        self.assertEqual(pct(0.0, 0.0), 0.0)


class CompareTest(unittest.TestCase):
    def _compare(self, before, after, out=None):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            bp = write_json(root / 'before.json', before)
            ap = write_json(root / 'after.json', after)
            argv = ['--before', str(bp), '--after', str(ap)]
            if out is not None:
                argv += ['--out', str(root / out)]
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = compare_main(argv)
            written = None
            if out is not None and (root / out).is_file():
                written = json.loads((root / out).read_text())
            return rc, json.loads(buf.getvalue()), written

    def test_rows_percentages_and_same_input(self):
        before = {
            'input_sha256': 'abc123',
            'wall_seconds': 10.0,
            'generated_source_bytes': 4096,
            'binary_bytes': 2048,
            'compile_seconds': 4.0,
            'stages_seconds': {'translate': 2.0, 'decode': 1.0},
        }
        after = {
            'input_sha256': 'abc123',
            'wall_seconds': 8.0,
            'generated_source_bytes': 8192,
            'binary_bytes': 2048,
            'compile_seconds': 2.0,
            'stages_seconds': {'translate': 1.0, 'decode': 1.5},
        }
        rc, result, _ = self._compare(before, after)
        self.assertEqual(rc, 0)
        self.assertTrue(result['same_input'])
        rows = {r['metric']: r for r in result['rows']}
        self.assertEqual(rows['wall_seconds']['delta_percent'], -20.0)
        self.assertEqual(rows['generated_source_bytes']['delta_percent'], 100.0)
        self.assertEqual(rows['binary_bytes']['delta_percent'], 0.0)
        self.assertEqual(rows['compile_seconds']['delta_percent'], -50.0)
        self.assertEqual(rows['stage:translate']['before'], 2.0)
        self.assertEqual(rows['stage:translate']['after'], 1.0)
        self.assertEqual(rows['stage:translate']['delta_percent'], -50.0)
        self.assertEqual(rows['stage:decode']['delta_percent'], 50.0)

    def test_same_input_false_when_hashes_differ(self):
        rc, result, _ = self._compare(
            {'input_sha256': 'aaa', 'wall_seconds': 10.0},
            {'input_sha256': 'bbb', 'wall_seconds': 9.0},
        )
        self.assertEqual(rc, 0)
        self.assertFalse(result['same_input'])

    def test_out_file_matches_stdout_and_skips_non_numeric(self):
        before = {'input_sha256': 'x', 'wall_seconds': 'n/a', 'binary_bytes': 10}
        after = {'input_sha256': 'x', 'wall_seconds': 8, 'binary_bytes': 12,
                 'extra': 99}
        rc, result, written = self._compare(before, after, out='cmp.json')
        self.assertEqual(rc, 0)
        self.assertEqual(written, result)
        metrics = [r['metric'] for r in result['rows']]
        self.assertNotIn('wall_seconds', metrics)
        self.assertIn('binary_bytes', metrics)
        binary_row = next(r for r in result['rows']
                          if r['metric'] == 'binary_bytes')
        self.assertEqual(binary_row['delta_percent'], 20.0)


if __name__ == '__main__':
    unittest.main()
