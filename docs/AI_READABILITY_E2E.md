# Optional AI source-readability pass — end-to-end evidence

The AI key file `/Users/haseeb-mir/Documents/Code/OAuthEnv.txt` was used ONLY
at runtime through `GAMERECOMP_AI_KEY` (an environment variable). No key value
is printed, logged, or committed. The repository contains only a generic
provider shim.

## Provider shim

`tools/ai_openrouter_llm.py` reads the prompt from stdin (the contract in
`tools/autorecomp/readability.py`) and calls an OpenAI-compatible endpoint.
Defaults: OpenRouter, `deepseek/deepseek-chat`, 1024 max tokens. Errors are
reduced to a status code or exception class so credentials or headers are
never echoed. Three unit tests cover missing key, empty prompt and response
extraction.

## Fixture and gates

A tiny real CMake project with one tested function (`sub_00402020`) was given
a valid baseline (`manifest.json`, `reports/run.json`, `reports/verification.json`,
`compile: ok`). The readability harness then:

1. verified the baseline gate,
2. sent the C file to the AI provider through a `llm` wrapper on PATH,
3. compiled the edited source,
4. reran the binary and compared exit code/output with the baseline,
5. accepted only if all gates matched, otherwise reverted.

## Measured result (CLI)

Two consecutive runs with the rebuilt CLI (this worktree) both reported:

```
outcome: complete
reason:  readability edits were rebuilt and reproduced the baseline process evidence
```

The accepted edit renamed `value` → `counter`, used `+=`, and added comments —
readability only, with identical process evidence.

## Defect found and fixed

The first accepted edit left `manifest.json` with the OLD source digest, so the
next run was blocked with `source tree changed since the recorded build`.
`ReadabilityHarness._update_manifest_digest()` now records the new digest on
acceptance; a unit test covers it. After the fix, two consecutive CLI runs
completed and manifest/current digests matched after each (last: `431c3671b5dd`).

## GUI end-to-end

The GUI binary was rebuilt from the same worktree (also fixing a one-line
`ino() as u128` build error in `src/gui/mod.rs`). With the same runtime
environment it was launched on the fixture; CuaDriver found the window and the
GUI-side readability run reported:

```
[ai-readability] accepted: readable-source edit(s) built and reproduced the baseline process evidence
outcome: complete
```

Manifest/current digests matched afterwards (`431c3671b5dd`). Screenshots and
logs are in `test-results/ai-e2e/` (gitignored, reproducible with the commands
documented here).

## Honest limits

- AI is optional and used ONLY for readable C naming/comments/structure.
- One small fixture function was improved; this is not a claim about game C.
- One OpenRouter key had credits; other keys returned 401/402 and were not used.
- No key or secret appears in the repository.
