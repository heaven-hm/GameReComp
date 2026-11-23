"""Tests for the secret-free AI readability provider shim."""
import io
import unittest
from unittest.mock import patch


class AiProviderTest(unittest.TestCase):
    def test_missing_key_fails_closed(self):
        from tools.ai_openrouter_llm import main
        with patch.dict('os.environ', {'GAMERECOMP_AI_KEY': ''}, clear=False), \
             patch('sys.stdin', io.StringIO('prompt')), \
             patch('sys.stderr', io.StringIO()):
            self.assertEqual(main(), 2)

    def test_empty_prompt_fails_closed(self):
        from tools.ai_openrouter_llm import main
        with patch.dict('os.environ', {'GAMERECOMP_AI_KEY': 'dummy'}, clear=False), \
             patch('sys.stdin', io.StringIO('')), \
             patch('sys.stderr', io.StringIO()):
            self.assertEqual(main(), 2)

    def test_response_text_is_written(self):
        from tools.ai_openrouter_llm import main
        fake = {'choices': [{'message': {'content': 'void f(void) {}'}}]}
        with patch.dict('os.environ', {'GAMERECOMP_AI_KEY': 'dummy'}, clear=False), \
             patch('sys.stdin', io.StringIO('prompt')), \
             patch('urllib.request.urlopen') as op, \
             patch('sys.stdout', io.StringIO()) as out:
            op.return_value.__enter__.return_value.read.return_value = b'{}'
            with patch('json.load', return_value=fake):
                self.assertEqual(main(), 0)
            self.assertEqual(out.getvalue(), 'void f(void) {}')


if __name__ == '__main__':
    unittest.main()
