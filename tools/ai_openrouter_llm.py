#!/usr/bin/env python3
"""Optional AI readability provider for GameReComp (OpenAI-compatible API).

Reads the full prompt from stdin and writes the model's response to stdout,
matching the `REVENG_AI_CMD` stdin-LLM contract in
`tools/autorecomp/readability.py`. The API key is read ONLY from the
`GAMERECOMP_AI_KEY` environment variable. It is never printed, logged, or
stored in the repository. This tool does not edit files and does not claim
that an AI edit is correct: the readability harness still compiles, reruns
and reverts on any gate failure.

Environment:
  GAMERECOMP_AI_KEY    required API key
  GAMERECOMP_AI_BASE   chat-completions endpoint (default OpenRouter)
  GAMERECOMP_AI_MODEL  model name (default deepseek/deepseek-chat)
  GAMERECOMP_CA_FILE   CA bundle (default /etc/ssl/cert.pem when present)
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request


def main() -> int:
    key = os.environ.get('GAMERECOMP_AI_KEY', '')
    if not key:
        print('GAMERECOMP_AI_KEY is not set', file=sys.stderr)
        return 2
    prompt = sys.stdin.read()
    if not prompt.strip():
        print('empty prompt on stdin', file=sys.stderr)
        return 2
    base = os.environ.get('GAMERECOMP_AI_BASE',
                          'https://openrouter.ai/api/v1/chat/completions')
    model = os.environ.get('GAMERECOMP_AI_MODEL', 'deepseek/deepseek-chat')
    body = json.dumps({
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        # Keep the request small: a readability edit to one small unit needs
        # far fewer tokens, and a large max_tokens can exceed a key's credit.
        'max_tokens': int(os.environ.get('GAMERECOMP_AI_MAX_TOKENS', '1024')),
        'temperature': 0,
    }).encode('utf-8')
    ctx = ssl.create_default_context()
    ca = os.environ.get('GAMERECOMP_CA_FILE', '/etc/ssl/cert.pem')
    if ca and os.path.isfile(ca):
        ctx = ssl.create_default_context(cafile=ca)
    req = urllib.request.Request(base, data=body, method='POST', headers={
        'Authorization': 'Bearer ' + key,
        'Content-Type': 'application/json',
    })
    try:
        with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        # Do not echo the request or headers: they carry the key.
        print(f'AI provider HTTP {exc.code}', file=sys.stderr)
        return 1
    except Exception as exc:
        print('AI provider error: ' + type(exc).__name__, file=sys.stderr)
        return 1
    try:
        text = data['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError):
        print('AI provider returned no message content', file=sys.stderr)
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
