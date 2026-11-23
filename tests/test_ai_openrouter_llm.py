"""Tests for the hardened, secret-free AI readability provider shim.

Covers: env-driven endpoint/model config, bounded 429/5xx retry with no
sleeping, and sanitized diagnostics that never echo keys, headers or bodies.
"""
import io
import json
import unittest
import urllib.error
from contextlib import contextmanager
from unittest.mock import patch

from tools import ai_openrouter_llm as provider

SECRET = 'sk-test-SUPERSECRET-1234'
BASE_ENV = {
    'GAMERECOMP_AI_KEY': SECRET,
    'GAMERECOMP_AI_ENDPOINT': '',
    'GAMERECOMP_AI_BASE': '',
    'GAMERECOMP_AI_MODEL': '',
    'GAMERECOMP_AI_MAX_ATTEMPTS': '',
    'GAMERECOMP_AI_TIMEOUT': '',
    'GAMERECOMP_AI_MAX_TOKENS': '',
    'GAMERECOMP_CA_FILE': '',
}


@contextmanager
def ai_env(**overrides):
    env = dict(BASE_ENV)
    env.update(overrides)
    with patch.dict('os.environ', env, clear=False):
        yield


def ok_response(content='void f(void) {}'):
    payload = {'choices': [{'message': {'content': content}}]}
    return io.BytesIO(json.dumps(payload).encode('utf-8'))


def http_error(code, msg='server said so'):
    """An HTTPError whose headers and body deliberately carry secret text."""
    return urllib.error.HTTPError(
        'https://provider.invalid/chat?key=' + SECRET, code, msg,
        {'Authorization': 'Bearer ' + SECRET},
        io.BytesIO(('body with ' + SECRET).encode('utf-8')))


class AiProviderTest(unittest.TestCase):
    def run_main(self, *, prompt='prompt', side_effect=None, return_value=None):
        """Run main() with networking mocked; assert it never sleeps."""
        stderr, stdout = io.StringIO(), io.StringIO()
        with ai_env(), \
             patch('sys.stdin', io.StringIO(prompt)), \
             patch('sys.stdout', stdout), \
             patch('sys.stderr', stderr), \
             patch('time.sleep', side_effect=AssertionError('retry slept')), \
             patch('urllib.request.urlopen', side_effect=side_effect,
                   return_value=return_value) as op:
            code = provider.main()
        return code, stdout.getvalue(), stderr.getvalue(), op

    # --- fail-closed basics -------------------------------------------------
    def test_missing_key_fails_closed(self):
        stderr = io.StringIO()
        with ai_env(GAMERECOMP_AI_KEY=''), \
             patch('sys.stdin', io.StringIO('prompt')), \
             patch('sys.stderr', stderr):
            self.assertEqual(provider.main(), 2)
        self.assertIn('GAMERECOMP_AI_KEY', stderr.getvalue())

    def test_empty_prompt_fails_closed(self):
        code, _, stderr, op = self.run_main(prompt='   \n')
        self.assertEqual(code, 2)
        self.assertIn('empty prompt', stderr)
        op.assert_not_called()

    def test_response_text_is_written(self):
        code, out, stderr, op = self.run_main(return_value=ok_response())
        self.assertEqual(code, 0)
        self.assertEqual(out, 'void f(void) {}')
        self.assertEqual(stderr, '')
        self.assertEqual(op.call_count, 1)
        self.assertNotIn(SECRET, out + stderr)

    def test_missing_message_content_fails_closed(self):
        code, _, stderr, _ = self.run_main(
            return_value=io.BytesIO(b'{"choices": []}'))
        self.assertEqual(code, 1)
        self.assertIn('no message content', stderr)

    # --- bounded retry on transient statuses -------------------------------
    def test_retries_429_then_succeeds(self):
        code, out, stderr, op = self.run_main(
            side_effect=[http_error(429), ok_response('after retry')])
        self.assertEqual(code, 0)
        self.assertEqual(out, 'after retry')
        self.assertEqual(op.call_count, 2)
        self.assertEqual(stderr, '')

    def test_retries_5xx_then_succeeds(self):
        code, out, _, op = self.run_main(
            side_effect=[http_error(500), http_error(503),
                         ok_response('recovered')])
        self.assertEqual(code, 0)
        self.assertEqual(out, 'recovered')
        self.assertEqual(op.call_count, 3)

    def test_retry_budget_exhausted_sanitizes_error(self):
        code, out, stderr, op = self.run_main(
            side_effect=[http_error(429) for _ in range(3)])
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertEqual(op.call_count, 3)
        self.assertIn('after 3 attempts', stderr)
        self.assertIn('HTTP 429', stderr)
        for leak in (SECRET, 'Bearer', 'server said so', 'body with'):
            self.assertNotIn(leak, stderr)

    def test_attempts_are_bounded_even_if_config_is_large(self):
        stderr = io.StringIO()
        with ai_env(GAMERECOMP_AI_MAX_ATTEMPTS='9999'), \
             patch('sys.stdin', io.StringIO('prompt')), \
             patch('sys.stderr', stderr), \
             patch('urllib.request.urlopen',
                   side_effect=[http_error(503)] * provider.MAX_ATTEMPTS) as op:
            code = provider.main()
        self.assertEqual(code, 1)
        self.assertEqual(op.call_count, provider.MAX_ATTEMPTS)
        self.assertIn(f'after {provider.MAX_ATTEMPTS} attempts',
                      stderr.getvalue())

    def test_non_retryable_http_error_does_not_retry(self):
        code, _, stderr, op = self.run_main(side_effect=[http_error(401)])
        self.assertEqual(code, 1)
        self.assertEqual(op.call_count, 1)
        self.assertEqual(stderr.strip(), 'AI provider HTTP 401')
        self.assertNotIn(SECRET, stderr)

    def test_transient_status_helper(self):
        for code in (429, 500, 502, 503, 599):
            self.assertTrue(provider.is_retryable_status(code))
        for code in (200, 400, 401, 402, 403, 404, 418, 600):
            self.assertFalse(provider.is_retryable_status(code))

    # --- sanitization ------------------------------------------------------
    def test_sanitize_error_redacts_key_headers_and_tokens(self):
        raw = (f'Authorization: Bearer {SECRET}\n'
               f'X-Api-Key: other-secret-999\n'
               f'GET https://h.invalid/chat?api_key={SECRET}&model=x\n'
               f'Bearer another-token-777 ' + SECRET)
        clean = provider.sanitize_error(raw, secrets=(SECRET,))
        for leak in (SECRET, 'other-secret-999', 'another-token-777'):
            self.assertNotIn(leak, clean)
        self.assertIn(provider.REDACTED, clean)
        self.assertIn('api_key=' + provider.REDACTED, clean)
        self.assertIn('Authorization: ' + provider.REDACTED, clean)
        self.assertNotIn('\n', clean)  # no header/log injection
        self.assertLessEqual(len(clean), provider.MAX_SANITIZED_LEN + 3)

    def test_sanitize_error_handles_plain_header_value(self):
        clean = provider.sanitize_error(
            'Authorization: Basic dXNlcjpwYXNz', secrets=())
        self.assertNotIn('dXNlcjpwYXNz', clean)
        self.assertIn('Authorization: ' + provider.REDACTED, clean)

    def test_generic_exception_is_reduced_to_class_name(self):
        side_effect = RuntimeError(
            f'url https://h.invalid/?key={SECRET} hdrs Authorization: Bearer {SECRET}')
        code, _, stderr, _ = self.run_main(side_effect=[side_effect])
        self.assertEqual(code, 1)
        self.assertEqual(stderr.strip(), 'AI provider error: RuntimeError')
        self.assertNotIn(SECRET, stderr)
        self.assertNotIn('https://', stderr)

    # --- environment-driven endpoint / model / limits ----------------------
    def test_endpoint_and_model_are_configurable(self):
        endpoint = 'https://local.invalid/v1/chat/completions'
        with ai_env(GAMERECOMP_AI_ENDPOINT=endpoint,
                    GAMERECOMP_AI_MODEL='local/test-model'), \
             patch('sys.stdin', io.StringIO('prompt')), \
             patch('sys.stdout', io.StringIO()), \
             patch('urllib.request.urlopen',
                   return_value=ok_response()) as op:
            self.assertEqual(provider.main(), 0)
        req = op.call_args.args[0]
        self.assertEqual(req.full_url, endpoint)
        self.assertEqual(req.get_header('Authorization'), 'Bearer ' + SECRET)
        sent = json.loads(req.data)
        self.assertEqual(sent['model'], 'local/test-model')
        self.assertEqual(sent['max_tokens'], provider.DEFAULT_MAX_TOKENS)

    def test_base_alias_and_defaults(self):
        cfg = provider.load_config
        with ai_env(GAMERECOMP_AI_BASE='https://base.invalid/chat'):
            self.assertEqual(cfg()['endpoint'], 'https://base.invalid/chat')
            self.assertEqual(cfg()['model'], provider.DEFAULT_MODEL)
        with ai_env():
            self.assertEqual(cfg()['endpoint'], provider.DEFAULT_ENDPOINT)
            self.assertEqual(cfg()['model'], provider.DEFAULT_MODEL)
            self.assertEqual(cfg()['attempts'], provider.DEFAULT_MAX_ATTEMPTS)

    def test_invalid_endpoint_fails_closed_without_echoing_value(self):
        poison = 'ftp://bad.invalid/?key=sk-NEVER-ECHO-ME'
        with ai_env(GAMERECOMP_AI_ENDPOINT=poison), \
             patch('sys.stdin', io.StringIO('prompt')), \
             patch('sys.stderr', io.StringIO()) as stderr, \
             patch('urllib.request.urlopen') as op:
            self.assertEqual(provider.main(), 2)
        self.assertIn('configuration error', stderr.getvalue())
        self.assertIn('GAMERECOMP_AI_ENDPOINT', stderr.getvalue())
        self.assertNotIn('NEVER-ECHO-ME', stderr.getvalue())
        op.assert_not_called()

    def test_invalid_numeric_config_fails_closed_without_echoing_value(self):
        with ai_env(GAMERECOMP_AI_MAX_ATTEMPTS='not-a-number'), \
             patch('sys.stdin', io.StringIO('prompt')), \
             patch('sys.stderr', io.StringIO()) as stderr, \
             patch('urllib.request.urlopen') as op:
            self.assertEqual(provider.main(), 2)
        self.assertIn('GAMERECOMP_AI_MAX_ATTEMPTS', stderr.getvalue())
        self.assertNotIn('not-a-number', stderr.getvalue())
        op.assert_not_called()


if __name__ == '__main__':
    unittest.main()
