"""Tests for the reusable game-matrix drivers (no game assets required)."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.game_matrix import (
    GIB,
    MIN_FREE_DISK_BYTES,
    check_free_disk,
    discover,
    main as game_matrix_main,
    pe32_x86,
    run_one,
    sha256,
)
from tools.linux_matrix import (
    build_one,
    docker_ready,
    main as linux_matrix_main,
    project_name,
    resources_from_project,
)


CORPUS = os.environ.get('RECOMP_GAME_CORPUS')


class GameMatrixToolTest(unittest.TestCase):
    def test_pe32_detection(self):
        igi = Path('/Users/haseeb-mir/Downloads/IGI/igi.exe')
        if igi.exists():
            self.assertTrue(pe32_x86(igi))
        x64 = Path('/Users/haseeb-mir/Downloads/recomp-apps/games/opentyrian/opentyrian.exe')
        if x64.exists():
            self.assertFalse(pe32_x86(x64))
        self.assertFalse(pe32_x86(Path(__file__)))          # not a PE at all

    def test_discover_filters_x64(self):
        if not CORPUS:
            self.skipTest('RECOMP_GAME_CORPUS not set')
        found = discover(Path(CORPUS))
        self.assertGreater(len(found), 0)
        for p in found:
            self.assertTrue(pe32_x86(p))
        self.assertFalse(any('opentyrian' in str(p) for p in found))
        self.assertFalse(any('cannonball' in str(p) for p in found))

    def test_sha256_stable(self):
        p = Path(__file__)
        self.assertEqual(sha256(p), sha256(p))
        self.assertEqual(len(sha256(p)), 64)

    def test_project_name(self):
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            (project / 'CMakeLists.txt').write_text(
                'cmake_minimum_required(VERSION 3.16)\nproject(dxIka C)\n')
            self.assertEqual(project_name(project), 'dxIka')

    def test_docker_capability_is_reported_not_assumed(self):
        # The value depends on the host; the contract is that it is a bool and
        # that the matrix driver refuses to fake Linux results when it is false.
        self.assertIsInstance(docker_ready(), bool)

    def test_summary_schema_when_present(self):
        summary = Path(__file__).parent.parent / 'test-results/game-matrix/macos/summary.json'
        if not summary.exists():
            self.skipTest('no matrix summary generated yet')
        data = json.loads(summary.read_text())
        self.assertEqual(data['schema'], 1)
        self.assertGreater(data['inputs'], 0)
        for rec in data['results']:
            self.assertIn('sha256', rec)
            self.assertEqual(len(rec['sha256']), 64)
            self.assertIn('stages', rec)
            if rec.get('run_cwd') is not None:
                # A game that needs its own folder must have been launched
                # from exactly the folder holding the PE32 that was hashed.
                self.assertTrue(rec['cwd_ok'], rec['input'])
                self.assertEqual(
                    Path(rec['run_cwd']).resolve(),
                    Path(rec['input']).parent.resolve())


RESOURCES = {
    'generated_source_bytes': 4096,
    'binary_bytes': 2048,
    'project_disk_bytes': 6144,
    'peak_rss_bytes': 123456789,
    'stage_seconds_total': 9.75,
}


def usage(free: int, total: int = 100 * GIB) -> SimpleNamespace:
    """A stand-in for shutil.disk_usage's named tuple."""
    return SimpleNamespace(total=total, used=total - free, free=free)


def write_report(project: Path, resources=None) -> None:
    reports = project / 'reports'
    reports.mkdir(parents=True, exist_ok=True)
    data = {
        'score': 0.5,
        'stages': [{'name': 'translate', 'status': 'ok', 'message': '',
                    'seconds': 2.5, 'data': {'source_bytes': 4096}}],
        'outcome': {'status': 'partial', 'reason': 'no oracle'},
    }
    if resources is not None:
        data['resources'] = resources
    (reports / 'report.json').write_text(json.dumps(data))


class DiskGuardTest(unittest.TestCase):
    """Issue #26: free disk is measured and enforced, never assumed."""

    def test_measures_scratch_path_with_shutil_disk_usage(self):
        scratch = Path('/scratch/builds')
        with patch('tools.game_matrix.shutil.disk_usage',
                   return_value=usage(MIN_FREE_DISK_BYTES + GIB)) as du:
            guard = check_free_disk(scratch, MIN_FREE_DISK_BYTES)
        du.assert_called_once_with(str(scratch))
        self.assertTrue(guard['ok'])
        self.assertEqual(guard['scratch'], str(scratch))
        self.assertEqual(guard['free_bytes'], MIN_FREE_DISK_BYTES + GIB)
        self.assertEqual(guard['min_free_bytes'], MIN_FREE_DISK_BYTES)
        self.assertIn('free-space check passed', guard['diagnostic'])

    def test_blocks_below_threshold_with_explicit_diagnostic(self):
        free = MIN_FREE_DISK_BYTES - 1
        with patch('tools.game_matrix.shutil.disk_usage', return_value=usage(free)):
            guard = check_free_disk(Path('/scratch'), MIN_FREE_DISK_BYTES)
        self.assertFalse(guard['ok'])
        self.assertEqual(guard['free_bytes'], free)
        self.assertIn('DISK GUARD', guard['diagnostic'])
        self.assertIn('refusing to start a build', guard['diagnostic'])
        self.assertIn('GiB', guard['diagnostic'])

    def test_measurement_error_fails_closed(self):
        with patch('tools.game_matrix.shutil.disk_usage',
                   side_effect=OSError('no such volume')):
            guard = check_free_disk(Path('/missing'))
        self.assertFalse(guard['ok'])
        self.assertIn('no such volume', guard['diagnostic'])
        self.assertIn('refusing to start a build', guard['diagnostic'])


class GameMatrixResourcesTest(unittest.TestCase):
    def test_run_one_records_measured_resources(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            exe = root / 'game.exe'
            exe.write_bytes(b'MZfake-exe')
            project = root / 'out' / 'projects' / 'game'
            write_report(project, RESOURCES)
            with patch('tools.game_matrix.shutil.disk_usage',
                       return_value=usage(10 * GIB)), \
                 patch('tools.game_matrix.subprocess.run',
                       return_value=SimpleNamespace(stdout='ok', returncode=0)):
                rec = run_one(exe, root / 'out', 1, 60, 8, False, True, True)
            self.assertTrue(rec['build_started'])
            self.assertEqual(rec['resources'], RESOURCES)
            self.assertEqual(rec['score'], 0.5)
            self.assertTrue((root / 'out' / 'reports' / 'game' / 'report.json').exists())

    def test_missing_resources_is_none_not_a_guess(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            exe = root / 'game.exe'
            exe.write_bytes(b'MZfake-exe')
            write_report(root / 'out' / 'projects' / 'game', resources=None)
            with patch('tools.game_matrix.shutil.disk_usage',
                       return_value=usage(10 * GIB)), \
                 patch('tools.game_matrix.subprocess.run',
                       return_value=SimpleNamespace(stdout='ok', returncode=0)):
                rec = run_one(exe, root / 'out', 1, 60, 8, False, True, True)
            self.assertIn('resources', rec)
            self.assertIsNone(rec['resources'])

    def test_run_one_refuses_build_when_disk_is_low(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            out = root / 'out'
            out.mkdir()
            exe = root / 'game.exe'
            exe.write_bytes(b'MZfake-exe')
            with patch('tools.game_matrix.shutil.disk_usage', return_value=usage(GIB)), \
                 patch('tools.game_matrix.subprocess.run') as run, \
                 contextlib.redirect_stderr(io.StringIO()) as err:
                rec = run_one(exe, out, 1, 60, 8, False, True, True)
            run.assert_not_called()
            self.assertEqual(rec['skipped'], 'disk_guard')
            self.assertFalse(rec['build_started'])
            self.assertFalse(rec['disk_guard']['ok'])
            self.assertIn('DISK GUARD', err.getvalue())
            self.assertIn('DISK GUARD', (out / 'game.log').read_text())
            self.assertFalse((out / 'projects' / 'game').exists())
            self.assertFalse((out / 'frames' / 'game').exists())

    def test_main_preflight_refuses_before_any_build(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            out = root / 'out'
            exe = root / 'game.exe'
            exe.write_bytes(b'MZfake-exe')
            with patch('tools.game_matrix.shutil.disk_usage', return_value=usage(GIB)), \
                 patch('tools.game_matrix.subprocess.run') as run, \
                 contextlib.redirect_stderr(io.StringIO()) as err:
                rc = game_matrix_main(['--exe', str(exe), '--out', str(out),
                                       '--parallel', '1'])
            self.assertEqual(rc, 3)
            run.assert_not_called()
            summary = json.loads((out / 'summary.json').read_text())
            self.assertFalse(summary['disk_guard']['ok'])
            self.assertEqual(summary['inputs'], 0)
            self.assertEqual(summary['requested_inputs'], 1)
            self.assertIn('DISK GUARD', err.getvalue())

    def test_main_reports_midrun_refusal_without_partial_build(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            out = root / 'out'
            exe = root / 'game.exe'
            exe.write_bytes(b'MZfake-exe')
            # First call is the preflight (enough disk), second is the per-build
            # check after another concurrent build consumed the free space.
            with patch('tools.game_matrix.shutil.disk_usage',
                       side_effect=[usage(10 * GIB), usage(GIB)]), \
                 patch('tools.game_matrix.subprocess.run') as run, \
                 contextlib.redirect_stderr(io.StringIO()):
                rc = game_matrix_main(['--exe', str(exe), '--out', str(out),
                                       '--parallel', '1'])
            self.assertEqual(rc, 4)
            run.assert_not_called()
            summary = json.loads((out / 'summary.json').read_text())
            self.assertEqual(summary['disk_guard']['blocked_inputs'], [str(exe)])
            self.assertEqual(summary['results'][0]['skipped'], 'disk_guard')


class LinuxMatrixResourcesTest(unittest.TestCase):
    def _project(self, root: Path, resources=None) -> Path:
        project = root / 'proj'
        project.mkdir(parents=True, exist_ok=True)
        (project / 'CMakeLists.txt').write_text('project(dxIka C)\n')
        if resources is not None:
            write_report(project, resources)
        return project

    def test_resources_from_project_missing_report_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(resources_from_project(Path(d)))

    def test_build_one_records_measured_resources(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            project = self._project(root, RESOURCES)
            with patch('tools.game_matrix.shutil.disk_usage',
                       return_value=usage(10 * GIB)), \
                 patch('tools.linux_matrix.subprocess.run',
                       return_value=SimpleNamespace(stdout='ok', returncode=0)) as run:
                rec = build_one(project, root / 'out', 'alpine:3.20', 1, 8, 60)
            self.assertEqual(rec['resources'], RESOURCES)
            self.assertEqual(rec['build_rc'], 0)
            self.assertEqual(run.call_count, 2)  # container build + run

    def test_build_one_refuses_before_staging(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            project = self._project(root)
            out = root / 'out'
            out.mkdir()
            with patch('tools.game_matrix.shutil.disk_usage', return_value=usage(GIB)), \
                 patch('tools.linux_matrix.subprocess.run') as run, \
                 contextlib.redirect_stderr(io.StringIO()) as err:
                rec = build_one(project, out, 'alpine:3.20', 1, 8, 60)
            run.assert_not_called()
            self.assertEqual(rec['skipped'], 'disk_guard')
            self.assertFalse(rec['build_started'])
            self.assertEqual(rec['build_rc'], 'NOT_RUN')
            self.assertEqual(rec['run_rc'], 'NOT_RUN')
            self.assertFalse((out / 'staging' / 'dxIka').exists())
            self.assertIn('DISK GUARD', err.getvalue())

    def test_main_preflight_refuses_before_any_build(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            project = self._project(root)
            out = root / 'out'
            with patch('tools.linux_matrix.docker_ready', return_value=True), \
                 patch('tools.game_matrix.shutil.disk_usage', return_value=usage(GIB)), \
                 patch('tools.linux_matrix.subprocess.run') as run, \
                 contextlib.redirect_stderr(io.StringIO()) as err:
                rc = linux_matrix_main(['--project', str(project), '--out', str(out)])
            self.assertEqual(rc, 3)
            run.assert_not_called()
            summary = json.loads((out / 'linux-summary.json').read_text())
            self.assertFalse(summary['disk_guard']['ok'])
            self.assertEqual(summary['results'], [])
            self.assertIn('DISK GUARD', err.getvalue())


if __name__ == '__main__':
    unittest.main()
