"""Unit tests for the E2E scenario matrix (no CLI/GUI binaries needed)."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import e2e_scenarios as es


def _cli_stdout(ok=True, pe32_x86=True):
    return json.dumps({
        'path': 'x.exe', 'ok': ok, 'error': None if ok else 'bad header',
        'magic': 'PE32' if pe32_x86 else 'PE32+', 'machine': 'i386',
        'pe32_x86': pe32_x86, 'image_base': '0x00400000', 'size': 1,
        'input_os': 'windows', 'input_arch': 'x86',
    })


def _fake_runner(cli=(0, None), gui=(0, None), project=0):
    """Build a run() stub whose results depend on which command it receives."""
    def fake(cmd, timeout=30, env=None):
        rec = {'command': list(cmd), 'error': None}
        if 'inspect' in cmd:
            rc, stdout = cli
            rec.update({'exit_code': rc,
                        'stdout': stdout if stdout is not None
                        else _cli_stdout(rc == 0)})
        elif '--selftest-pe' in cmd:
            rc, stdout = gui
            rec.update({'exit_code': rc,
                        'stdout': stdout if stdout is not None
                        else ('PE: PE32 i386 -- supported target'
                              if rc == 0 else
                              'PE: PE32+ amd64 -- NOT a supported target')})
        elif '--selftest' in cmd:
            rec.update({'exit_code': project, 'stdout': 'SELFTEST ok'})
        else:
            raise AssertionError(f'unexpected command: {cmd}')
        return rec
    return fake


class RunTest(unittest.TestCase):
    def test_run_records_exact_command_and_exit_code(self):
        with patch('tools.e2e_scenarios.subprocess.run') as mocked:
            mocked.return_value.returncode = 0
            mocked.return_value.stdout = 'ok'
            rec = es.run(['fake', 'inspect', 'a.exe', '--json'])
        self.assertEqual(rec['exit_code'], 0)
        self.assertEqual(rec['stdout'], 'ok')
        self.assertEqual(rec['command'], ['fake', 'inspect', 'a.exe', '--json'])

    def test_timeout_is_recorded_not_guessed(self):
        with patch('tools.e2e_scenarios.subprocess.run',
                   side_effect=subprocess.TimeoutExpired('fake', 5,
                                                         output=b'partial')):
            rec = es.run(['fake', 'inspect'])
        self.assertEqual(rec['exit_code'], 'TIMEOUT')
        self.assertIn('partial', rec['stdout'])

    def test_spawn_error_is_recorded_not_raised(self):
        with patch('tools.e2e_scenarios.subprocess.run',
                   side_effect=FileNotFoundError(2, 'no such binary')):
            rec = es.run(['missing-bin', 'inspect'])
        self.assertEqual(rec['exit_code'], 'SPAWN_ERROR')
        self.assertIn('FileNotFoundError', rec['error'])


class PlanTest(unittest.TestCase):
    def test_commands_use_only_supplied_paths_not_hardcoded(self):
        plan = es.build_plan(Path('/cli/bin'), Path('/gui/bin'),
                             Path('/repo/root'),
                             [Path('/weird/a.exe'), Path('/weird/b.exe')],
                             [Path('/proj/p1')])
        self.assertEqual([s['id'] for s in plan],
                         ['pe-0001', 'pe-0002', 'project-0001'])
        self.assertEqual(plan[0]['steps'][0]['command'],
                         ['/cli/bin', 'inspect', '/weird/a.exe', '--json',
                          '--repo', '/repo/root'])
        self.assertEqual(plan[0]['steps'][1]['command'],
                         ['/gui/bin', '--selftest-pe', '/weird/a.exe'])
        self.assertEqual(plan[1]['steps'][1]['command'][2], '/weird/b.exe')
        self.assertEqual(plan[2]['steps'][0]['command'],
                         ['/gui/bin', '--selftest', '/proj/p1',
                          '--repo', '/repo/root'])


class ClassificationTest(unittest.TestCase):
    def test_cli_classifies_payload_and_exit_code(self):
        step = {'exit_code': 0, 'stdout': _cli_stdout()}
        self.assertEqual(es.classify_cli(step), es.SUPPORTED)
        step = {'exit_code': 2, 'stdout': _cli_stdout(True, False)}
        self.assertEqual(es.classify_cli(step), es.UNSUPPORTED)
        step = {'exit_code': 1, 'stdout': _cli_stdout(False, False)}
        self.assertEqual(es.classify_cli(step), es.INVALID)

    def test_cli_flags_payload_exit_mismatch(self):
        step = {'exit_code': 2, 'stdout': _cli_stdout()}
        self.assertEqual(es.classify_cli(step), es.INCONSISTENT)

    def test_cli_falls_back_to_exit_code_without_json(self):
        self.assertEqual(es.classify_cli({'exit_code': 2, 'stdout': ''}),
                         es.UNSUPPORTED)
        self.assertEqual(es.classify_cli({'exit_code': 'TIMEOUT', 'stdout': ''}),
                         None)

    def test_gui_text_ordering_and_mismatch(self):
        self.assertEqual(es.gui_text_class('PE: not usable: bad'), es.INVALID)
        self.assertEqual(es.gui_text_class('PE: x -- NOT a supported target'),
                         es.UNSUPPORTED)
        self.assertEqual(es.gui_text_class('PE: x -- supported target'),
                         es.SUPPORTED)
        self.assertEqual(es.classify_gui(
            {'exit_code': 0, 'stdout': 'PE: not usable: bad'}),
            es.INCONSISTENT)
        self.assertEqual(es.classify_gui(
            {'exit_code': 2, 'stdout': 'PE: x -- NOT a supported target'}),
            es.UNSUPPORTED)


class EvaluateTest(unittest.TestCase):
    def _scenario(self):
        return {
            'id': 'pe-0001', 'kind': 'pe', 'input': 'a.exe',
            'input_exists': True, 'sha256': 'a' * 64,
            'steps': [
                {'name': es.CLI_INSPECT, 'command': [], 'exit_code': 0,
                 'stdout': _cli_stdout()},
                {'name': es.GUI_PE, 'command': [], 'exit_code': 0,
                 'stdout': 'PE: PE32 i386 -- supported target'},
            ],
        }

    def test_agreement_is_pass(self):
        result = es.evaluate(self._scenario())
        self.assertEqual(result['verdict'], 'PASS')
        self.assertTrue(result['agreement'])
        self.assertEqual(result['matrix']['classification'], es.SUPPORTED)

    def test_disagreement_is_fail(self):
        scenario = self._scenario()
        scenario['steps'][1]['exit_code'] = 2
        scenario['steps'][1]['stdout'] = 'PE: x -- NOT a supported target'
        result = es.evaluate(scenario)
        self.assertEqual(result['verdict'], 'FAIL')
        self.assertEqual(result['cli_class'], es.SUPPORTED)
        self.assertEqual(result['gui_class'], es.UNSUPPORTED)
        self.assertFalse(result['agreement'])

    def test_runner_error_is_fail(self):
        scenario = self._scenario()
        scenario['steps'][0]['exit_code'] = 'TIMEOUT'
        self.assertEqual(es.evaluate(scenario)['verdict'], 'FAIL')

    def test_missing_input_is_skip_not_pass(self):
        scenario = self._scenario()
        scenario['input_exists'] = False
        self.assertEqual(es.evaluate(scenario)['verdict'], 'SKIP')

    def test_project_selftest_passes_only_on_zero(self):
        scenario = {
            'id': 'project-0001', 'kind': 'project', 'input': '/proj',
            'input_exists': True,
            'steps': [{'name': es.GUI_PROJECT, 'command': [],
                       'exit_code': 0, 'stdout': 'SELFTEST ok'}],
        }
        self.assertEqual(es.evaluate(scenario)['verdict'], 'PASS')
        scenario['steps'][0]['exit_code'] = 1
        self.assertEqual(es.evaluate(scenario)['verdict'], 'FAIL')


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.exe_a = self.root / 'a.exe'
        self.exe_b = self.root / 'b.exe'
        self.exe_a.write_bytes(b'MZ' + b'\0' * 62)
        self.exe_b.write_bytes(b'MZ' + b'\0' * 62)

    def tearDown(self):
        self.tmp.cleanup()

    def _build(self, **kwargs):
        with patch('tools.e2e_scenarios.run',
                   side_effect=_fake_runner(**kwargs)):
            return es.build_report(Path('/cli/bin'), Path('/gui/bin'),
                                   Path('/repo'), [self.exe_a, self.exe_b],
                                   [])

    def test_report_has_one_structure_with_exact_commands(self):
        report = self._build()
        self.assertEqual(report['schema'], 1)
        self.assertEqual(report['summary']['scenarios'], 2)
        self.assertEqual(report['summary']['pass'], 2)
        self.assertEqual(report['verdict'], 'PASS')
        self.assertEqual(len(report['matrix']), 2)
        first = report['scenarios'][0]['steps'][0]
        self.assertEqual(first['command'],
                         ['/cli/bin', 'inspect', str(self.exe_a), '--json',
                          '--repo', '/repo'])
        self.assertIn('--selftest-pe',
                      report['scenarios'][0]['steps'][1]['command'])
        self.assertEqual(len(report['scenarios'][0]['sha256']), 64)

    def test_report_is_deterministic(self):
        self.assertEqual(json.dumps(self._build(), sort_keys=True),
                         json.dumps(self._build(), sort_keys=True))

    def test_report_records_disagreement_and_fails(self):
        report = self._build(gui=(2, None))
        self.assertEqual(report['summary']['cli_gui_disagreements'], 2)
        self.assertEqual(report['summary']['fail'], 2)
        self.assertEqual(report['verdict'], 'FAIL')

    def test_no_inputs_is_fail_not_vacuous_pass(self):
        with patch('tools.e2e_scenarios.run', side_effect=_fake_runner()):
            report = es.build_report(Path('/cli/bin'), Path('/gui/bin'),
                                     Path('/repo'), [], [])
        self.assertEqual(report['summary']['scenarios'], 0)
        self.assertEqual(report['verdict'], 'FAIL')


if __name__ == '__main__':
    unittest.main()
