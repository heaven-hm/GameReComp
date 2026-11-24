#!/usr/bin/env python3
"""Optional AI readability provider for GameReComp (OpenAI-compatible API).

Reads the full prompt from stdin and writes the model's response to stdout,
matching the `REVENG_AI_CMD` stdin-LLM contract in
`tools/autorecomp/readability.py`. The API key is read ONLY from the
`GAMERECOMP_AI_KEY` environment variable. It is never printed, logged, or
stored in the repository. This tool does not edit files and does not claim
that an AI edit is correct: the readability harness still compiles, reruns
and reverts on any gate failure.

Hardening in this shim:
  * endpoint and model are configurable through the environment;
  * transient HTTP 429/5xx responses get a bounded number of immediate
    retries (no sleeping, so callers keep their own timeout budget);
  * every diagnostic is sanitized: fixed messages, status codes and exception
    class names only, never response bodies, request headers, URLs or keys.

See `docs/AI_PROVIDER_CONFIG.md` for the full configuration reference.

Environment:
  GAMERECOMP_AI_KEY          required API key (never echoed)
  GAMERECOMP_AI_ENDPOINT     chat-completions endpoint (alias of _BASE)
  GAMERECOMP_AI_BASE         chat-completions endpoint (default OpenRouter)
  GAMERECOMP_AI_MODEL        model name (default deepseek/deepseek-chat)
  GAMERECOMP_AI_MAX_ATTEMPTS total attempts for 429/5xx (default 3, max 5)
  GAMERECOMP_AI_TIMEOUT      per-attempt network timeout in seconds (default 180)
  GAMERECOMP_AI_MAX_TOKENS   response token cap (default 1024)
  GAMERECOMP_CA_FILE         CA bundle (default /etc/ssl/cert.pem when present)
"""
from __future__ import annotations

import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_ENDPOINT = 'https://openrouter.ai/api/v1/chat/completions'
DEFAULT_MODEL = 'deepseek/deepseek-chat'
DEFAULT_MAX_ATTEMPTS = 3
MAX_ATTEMPTS = 5
DEFAULT_TIMEOUT = 180.0
DEFAULT_MAX_TOKENS = 1024
CA_FILE_CHOICES = ('/etc/ssl/cert.pem',)
# 429 (rate limited) plus the whole 5xx server-error class are transient.
RETRYABLE_STATUSES = frozenset({429, *range(500, 600)})
REDACTED = '<redacted>'
MAX_SANITIZED_LEN = 300

_BEARER_RE = re.compile(r'(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+')
# Consume the whole header value (including "Bearer <token>"), not just the
# first word, so a token can never survive between the name and the newline.
_AUTH_HEADER_RE = re.compile(
    r'(?i)\b(authorization|x-api-key|api[-_]?key)\s*:\s*[^\r\n,;]+')
_QUERY_SECRET_RE = re.compile(
    r'(?i)((?:^|[?&])(?:api[-_]?key|apikey|key|token|access[-_]?token|'
    r'authorization)\s*=\s*)([^&#\s]*)')


class ConfigError(Exception):
    """Invalid configuration; ``safe_message`` is safe to print."""

    def __init__(self, safe_message: str) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message


class ProviderError(Exception):
    """Provider failure; ``safe_message`` is safe to print."""

    def __init__(self, safe_message: str) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message


def sanitize_error(message: object, *, secrets: tuple[str, ...] = ()) -> str:
    """Return a single-line copy of *message* with credentials removed.

    This is a defence-in-depth helper: callers still prefer fixed messages,
    numeric status codes and exception class names over exception text. It
    redacts the known key, Authorization/Bearer header values and secret-like
    query parameters, collapses whitespace (no log/header injection) and
    truncates the result.
    """
    text = str(message)
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    text = _BEARER_RE.sub('Bearer ' + REDACTED, text)
    text = _AUTH_HEADER_RE.sub(
        lambda m: m.group(0).split(':', 1)[0] + ': ' + REDACTED, text)
    text = _QUERY_SECRET_RE.sub(
        lambda m: m.group(1) + REDACTED, text)
    text = ' '.join(text.split())
    if len(text) > MAX_SANITIZED_LEN:
        text = text[:MAX_SANITIZED_LEN] + '...'
    return text


def is_retryable_status(code: int) -> bool:
    """True for transient HTTP statuses that warrant an immediate retry."""
    return code in RETRYABLE_STATUSES


def _env(name: str) -> str:
    return (os.environ.get(name, '') or '').strip()


def _bounded_int(name: str, default: int, *, maximum: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f'{name} must be an integer') from None
    if value < 1:
        raise ConfigError(f'{name} must be at least 1')
    return min(value, maximum)


def load_config() -> dict:
    """Read and validate provider settings from the environment.

    Only the names of bad variables are reported, never their values: an
    endpoint or key may legitimately be secret-bearing.
    """
    endpoint = _env('GAMERECOMP_AI_ENDPOINT') or _env('GAMERECOMP_AI_BASE')
    endpoint = endpoint or DEFAULT_ENDPOINT
    parts = urllib.parse.urlsplit(endpoint)
    if parts.scheme not in ('http', 'https') or not parts.netloc:
        raise ConfigError(
            'GAMERECOMP_AI_ENDPOINT/GAMERECOMP_AI_BASE must be an http(s) URL')

    ca_file = _env('GAMERECOMP_CA_FILE')
    if not ca_file:
        ca_file = next((p for p in CA_FILE_CHOICES if os.path.isfile(p)), '')
    return {
        'endpoint': endpoint,
        'model': _env('GAMERECOMP_AI_MODEL') or DEFAULT_MODEL,
        'attempts': _bounded_int(
            'GAMERECOMP_AI_MAX_ATTEMPTS', DEFAULT_MAX_ATTEMPTS,
            maximum=MAX_ATTEMPTS),
        'timeout': float(_bounded_int('GAMERECOMP_AI_TIMEOUT',
                                      int(DEFAULT_TIMEOUT), maximum=3600)),
        'max_tokens': _bounded_int(
            'GAMERECOMP_AI_MAX_TOKENS', DEFAULT_MAX_TOKENS, maximum=32768),
        'ca_file': ca_file,
    }


def request_json(req: urllib.request.Request, ctx: ssl.SSLContext,
                 config: dict, key: str) -> dict:
    """POST *req* and return decoded JSON, retrying only transient statuses.

    Retries are immediate and strictly bounded by ``config['attempts']``.
    No exception text, header or URL is ever placed in a ProviderError.
    """
    attempts = config['attempts']
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(
                    req, timeout=config['timeout'], context=ctx) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            code = exc.code
            if attempt < attempts and is_retryable_status(code):
                continue
            if is_retryable_status(code):
                raise ProviderError(
                    f'AI provider unavailable after {attempt} attempts '
                    f'(HTTP {code})') from None
            raise ProviderError(f'AI provider HTTP {code}') from None
        except ProviderError:
            raise
        except Exception as exc:
            # Class name only: str(exc) can contain the URL, headers or key.
            raise ProviderError('AI provider error: ' + sanitize_error(
                type(exc).__name__, secrets=(key,))) from None
    raise ProviderError('AI provider request failed')


def main() -> int:
    key = (os.environ.get('GAMERECOMP_AI_KEY', '') or '').strip()
    if not key:
        print('GAMERECOMP_AI_KEY is not set', file=sys.stderr)
        return 2
    prompt = sys.stdin.read()
    if not prompt.strip():
        print('empty prompt on stdin', file=sys.stderr)
        return 2

    try:
        config = load_config()
        body = json.dumps({
            'model': config['model'],
            'messages': [{'role': 'user', 'content': prompt}],
            # Keep the request small: a readability edit to one small unit needs
            # far fewer tokens, and a large max_tokens can exceed a key's credit.
            'max_tokens': config['max_tokens'],
            'temperature': 0,
        }).encode('utf-8')
        ctx = ssl.create_default_context()
        ca = config['ca_file']
        if ca and os.path.isfile(ca):
            ctx = ssl.create_default_context(cafile=ca)
        req = urllib.request.Request(config['endpoint'], data=body, method='POST',
                                     headers={
            'Authorization': 'Bearer ' + key,
            'Content-Type': 'application/json',
        })
        data = request_json(req, ctx, config, key)
    except ConfigError as exc:
        print('AI provider configuration error: ' + exc.safe_message,
              file=sys.stderr)
        return 2
    except ProviderError as exc:
        print(exc.safe_message, file=sys.stderr)
        return 1
    except Exception as exc:
        # Last-resort guard: never let raw exception text reach the log.
        print('AI provider error: ' + sanitize_error(
            type(exc).__name__, secrets=(key,)), file=sys.stderr)
        return 1

    try:
        text = data['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError):
        print('AI provider returned no message content', file=sys.stderr)
        return 1
    if not isinstance(text, str):
        print('AI provider returned no message content', file=sys.stderr)
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
