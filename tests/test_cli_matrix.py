"""Unit tests for the CLI matrix runner (no CLI binary needed)."""
import unittest
from unittest.mock import patch

from tools.cli_matrix import run


class CliMatrixTest(unittest.TestCase):
    def test_run_records_command_and_exit(self):
        with patch('tools.cli_matrix.subprocess.run') as mocked:
            mocked.return_value.returncode = 0
            mocked.return_value.stdout = 'ok'
            rec = run(['fake', 'about'])
        self.assertEqual(rec['exit_code'], 0)
        self.assertEqual(rec['stdout'], 'ok')
        self.assertEqual(rec['command'], ['fake', 'about'])

    def test_timeout_is_recorded_not_guessed(self):
        import subprocess
        with patch('tools.cli_matrix.subprocess.run',
                   side_effect=subprocess.TimeoutExpired('fake', 5, output=b'partial')):
            rec = run(['fake', 'inspect'])
        self.assertEqual(rec['exit_code'], 'TIMEOUT')
        self.assertIn('partial', rec['stdout'])


if __name__ == '__main__':
    unittest.main()
