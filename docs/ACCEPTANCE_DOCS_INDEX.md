# Acceptance evidence index

This index maps the six evidence artifacts named by the master task to the
question each one answers, and -- just as importantly -- to the questions it
does **not** answer. It is a reading aid, not a verdict: a document proving one
narrow fact is not proof of a broader claim.

All six artifacts are owned by different agents and land at different times.
`tests/test_acceptance_docs.py` checks each artifact only once it is committed.
An artifact that has not landed is reported as a **SKIP** with the expected path
as the reason; a missing file never fails the suite. Once a file lands, the test
requires explicit fail-closed / limits language and rejects unqualified
perfect-completion percentages and issue-closure claims.

## Artifacts

| Key | Canonical path | What it proves | What it does NOT prove |
| --- | --- | --- | --- |
| `GAME_MATRIX` | `docs/GAME_MATRIX.md` | A measured status for each known game: which locally supplied PE32 inputs were inspected, rebuilt and launched, with the pipeline's fail-closed verdict (`PASS` / `PARTIAL` / `FAIL` / `NOT RUN`) and the port details read from each `reports/report.json`. | It does not prove original-gameplay equivalence. `PARTIAL` means a process launched with no independent scenario oracle; `NOT RUN` means exactly that. Upstream rows quoted from other projects are claims marked `†` and are never counted as this product's results. It does not cover inputs that were never supplied. |
| `ISSUE_EXECUTION_PLAN` | `docs/ISSUE_EXECUTION_PLAN.md` | The per-issue work plan: which exit criteria are satisfied by checked-in infrastructure and tests, and which remain open. It records ownership and sequencing. | It does not close an issue by itself. Infrastructure or a green unit test is not an independent original-Windows scenario oracle, so the plan cannot establish that any broad exit criterion is fully met. |
| `WIN32_API_COVERAGE` | `docs/WIN32_API_COVERAGE.md` | Which Win32 imports/subsystems are implemented by the checked-in runtime HLE layer, which are detected-but-unsupported, and which are absent from the measured corpus. | Coverage of a name is not coverage of its ABI. Listed shims are not proof of behaviour matching the original DLL under every call pattern; absent imports are not proof that a game never needs them. It does not cover hosts or binaries outside its stated corpus. |
| `PROOF_EVIDENCE` | `docs/PROOF_EVIDENCE.md` | The concrete artifacts backing a narrow, named claim: command lines, digests, logs, screenshots or differential traces, with the exact scope of what was observed. | It does not generalise beyond the recorded scenario. A captured frame, boot log or single differential run is evidence for that one capture only; it is not a guarantee of fidelity for other hardware, drivers, game states or future builds. |
| `ALL_GAMES_MACOS_EVIDENCE` | `docs/ALL_GAMES_MACOS_EVIDENCE.md` | The macOS-host results for the measured game set: which binaries built and launched, which imports bound, and the observed process outcome per entry. | It is macOS-host evidence only. It does not establish Windows-host behaviour, does not turn a launch into a gameplay oracle, and says nothing about binaries excluded from the measured set. A native arm64 launch is not an equivalence claim. |
| `CAPABILITY_BOUNDARIES` | `docs/CAPABILITY_BOUNDARIES.md` | The explicit limits of the product: supported input formats and architectures, the scenarios that can actually be verified, and the conditions under which the pipeline refuses to guess. | It is not a roadmap promise. A stated boundary is a description of current behaviour, not evidence that an unsupported path works, and removing a boundary later requires new evidence rather than a documentation edit. |

## Rules enforced by the test

The gate applies these rules to every landed artifact:

1. **Limits must be stated.** Each doc contains at least one explicit
   fail-closed / limits marker (for example "not run", "partial", "no oracle",
   "does not prove", "limitation", "boundary").
2. **No perfect-completion claims.** Do not write an unqualified `100%`
   percentage. A mention inside a negating or refuting sentence (for example
   "does not claim 100%" or "counterexamples to any 100% claim") is allowed,
   because it is a limit rather than a claim. Negation spanning a line wrap is
   understood.
3. **No issue-closure claims.** A doc must not say that an issue is closed,
   resolved, fixed or done. Residual gaps belong in prose; "fail-closed"
   describes refusal and is not treated as a closure statement.
4. **Missing files skip, not fail.** Until a sibling agent commits an
   artifact, its existence and language checks are skipped with the canonical
   path named as the reason.

The canonical paths above are preferred. As a tolerance measure the test also
accepts the same basename under `docs/evidence/`, `reports/` or the repository
root, with `.md`, `.markdown`, `.txt` or `.json`, so a sibling agent's chosen
location does not break the gate.

## Reading order

Start with `CAPABILITY_BOUNDARIES` to learn what can be claimed at all, then
`GAME_MATRIX` for the measured scope, `WIN32_API_COVERAGE` for implementation
scope, `ISSUE_EXECUTION_PLAN` for what remains open, and
`PROOF_EVIDENCE` / `ALL_GAMES_MACOS_EVIDENCE` for the concrete recorded runs.
Each artifact is evidence for its own narrow statement only; none of them, alone
or together, is a blanket statement of complete issue closure.
