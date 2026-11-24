# AI provider configuration (`tools/ai_openrouter_llm.py`)

The optional readability pass (`tools/autorecomp/readability.py`) calls a
stdin-LLM command named by `REVENG_AI_CMD`. The shipped shim
`tools/ai_openrouter_llm.py` implements that contract: it reads a prompt from
stdin, POSTs it to an OpenAI-compatible chat-completions endpoint, and writes
the reply text to stdout. It never edits files and never claims an AI edit is
correct — the harness still compiles, reruns and reverts on any gate failure.

## Environment variables

All settings come from the environment. No file in the repository holds a key
or a user-specific path.

| Variable | Purpose | Default |
| --- | --- | --- |
| `GAMERECOMP_AI_KEY` | **Required.** API key sent as `Authorization: Bearer …`. | none (exit 2 if unset/empty) |
| `GAMERECOMP_AI_ENDPOINT` | Chat-completions URL. Preferred name. | `GAMERECOMP_AI_BASE`, then OpenRouter default |
| `GAMERECOMP_AI_BASE` | Legacy alias for the endpoint URL. | `https://openrouter.ai/api/v1/chat/completions` |
| `GAMERECOMP_AI_MODEL` | Model identifier in the request body. | `deepseek/deepseek-chat` |
| `GAMERECOMP_AI_MAX_ATTEMPTS` | Total attempts for a transient HTTP error. Clamped to `1..5`. | `3` |
| `GAMERECOMP_AI_TIMEOUT` | Per-attempt network timeout, seconds. Clamped to `1..3600`. | `180` |
| `GAMERECOMP_AI_MAX_TOKENS` | Response token cap. Clamped to `1..32768`. | `1024` |
| `GAMERECOMP_CA_FILE` | CA bundle path. | `/etc/ssl/cert.pem` when present, else library default |

`GAMERECOMP_AI_ENDPOINT` wins over `GAMERECOMP_AI_BASE` when both are set.
The endpoint must parse as an `http` or `https` URL with a host; an invalid
value fails closed (exit 2) without echoing the value. Numeric variables must
be integers; invalid values also fail closed with the *variable name* only.

Example (values supplied by the caller at runtime, never committed):

```sh
export GAMERECOMP_AI_KEY='<key supplied at runtime>'
export GAMERECOMP_AI_ENDPOINT='https://openrouter.ai/api/v1/chat/completions'
export GAMERECOMP_AI_MODEL='deepseek/deepseek-chat'
export GAMERECOMP_AI_MAX_ATTEMPTS=3
export REVENG_AI_CMD='python3 tools/ai_openrouter_llm.py'
```

## Retry policy

* Only HTTP **429** (rate limited) and **5xx** (500–599, server errors) are
  treated as transient and retried. All other statuses (for example 400, 401,
  402, 403, 404) fail after a single attempt.
* Retries are **bounded**: at most `GAMERECOMP_AI_MAX_ATTEMPTS` requests, hard
  capped at 5 regardless of a larger configured value.
* Retries are **immediate**: the shim never calls `sleep`. The caller's own
  timeout budget is respected via the per-attempt timeout, and the bounded
  loop keeps worst-case work predictable.
* Network exceptions and malformed responses are not retried; they fail
  closed on the first occurrence.

## Error handling and secret hygiene

* Exit codes: `0` success; `1` provider/response failure; `2` configuration or
  input failure (missing key, empty prompt, bad env value).
* Diagnostics contain only fixed messages, numeric HTTP status codes and
  exception **class names** — never exception text, response bodies, request
  headers, URLs or key material.
* `sanitize_error()` is a defence-in-depth helper used before any dynamic text
  reaches stderr. It removes the known key, `Bearer`/`Authorization` and
  common API-key header values, secret-like query parameters (`key=`,
  `api_key=`, `token=`, …), collapses whitespace (blocking log/header
  injection) and truncates long output.
* The key is read only from `GAMERECOMP_AI_KEY` and is never printed, logged
  or written to the repository.

## Tests

```sh
python3 -m pytest tests/test_ai_openrouter_llm.py -q
```

The tests cover fail-closed basics, env-driven endpoint/model selection, a
429 retry then success, multiple 5xx retries then success, retry-budget
exhaustion, the hard cap, non-retryable statuses, an assertion that no sleep
occurs, and sanitization of keys/headers/tokens in both the helper and the
printed diagnostics.

## Limits

* This is a thin command-line shim, not a provider SDK: no connection pooling,
  backoff jitter, streaming, or response-schema validation beyond extracting
  `choices[0].message.content`.
* No live API call is made by the tests; networking is mocked.
* The shim does not verify that a returned edit is correct. Compilation,
  behavioural replay and baseline restoration remain the harness's job.
