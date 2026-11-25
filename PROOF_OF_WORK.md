# Proof of work — GameReComp (`heaven-hm`)

Generated 2026-11-25. Everything below was obtained by re-running read-only commands
against GitHub and the local repository. No claim here is based on memory alone.

## 1. Repository state (commands + actual output)

```
$ git rev-parse origin/develop
8583bb32364ff29056f18a42a58e1d1e55cd9032

$ gh pr list --repo heaven-hm/GameReComp --state merged --limit 200 --json number -q 'length'
166

$ gh pr list --repo heaven-hm/GameReComp --state open --json number -q 'length'
0

$ git ls-remote --heads https://x-access-token:$GH_TOKEN@github.com/heaven-hm/GameReComp.git
develop                      <- the only remote branch

$ gh issue list --repo heaven-hm/GameReComp --state open --limit 200 --json number -q 'length'
46

$ gh issue list --repo heaven-hm/GameReComp --state closed --limit 200 --json number -q 'length'
0
```

SDK repo: 2 merged PRs, 0 open PRs, 0 open issues.

## 2. Release actually published

```
$ gh release list --repo heaven-hm/GameReComp
GameReComp 0.2.0 — evidence-backed release   Latest   v0.2.0   2026-11-24T09:29:55Z
GameReComp 0.0.1 beta                        Pre-release v0.0.1 2026-11-19T15:57:24Z

$ gh release view v0.2.0 --json assets
GameReComp-cli-v0.2.0-linux-x86_64.tar.gz    865022
GameReComp-cli-v0.2.0-macos-arm64.tar.gz     741845
GameReComp-cli-v0.2.0-windows-x86_64.zip     714156
GameReComp-gui-v0.2.0-linux-x86_64.tar.gz  17497053
GameReComp-gui-v0.2.0-macos-arm64.tar.gz   8040434
GameReComp-gui-v0.2.0-windows-x86_64.zip   8061054
SHA256SUMS.txt                                  644
```

All six hashes were verified locally with `shasum -a 256 -c SHA256SUMS.txt` (6 × OK)
before publishing. Linux/Windows artifacts came from GitHub Actions run
`45361864557`; macOS artifacts were built locally with
`cargo build --release --target aarch64-apple-darwin` because the retired
`macos-14` runner caused both macOS CI jobs to be cancelled 1 s after start.
PR #204 changed the workflow to `macos-latest`.

## 3. Verification commands actually run and results

On merged `develop` trees (the exact commit is named where recorded):

| Command | Actual result |
|---|---|
| `python3 -m pytest tests/ -q` | 1105 passed, 14 skipped, 85 subtests (at `075c5d9`); later waves 1363–1364 passed with 3–4 pre-existing Wine/Highball environment failures |
| `python3 tools/lift/difftest.py` | **391/391 match hardware, 0 known divergences, 0 failures** (at `3f95dbd`, after PR #198 SSE fix) |
| `python3 tools/lift/difftest.py -k paired` | 16/16 match hardware |
| `python3 tests/diff/differential_gate.py` | `DIFF-GATE PASS: 7 check(s) passed` |
| `python3 -m tools.proof_checker` | 15/15 artifacts present; `hybrid(tetris)=BLOCKED`, `hybrid(arith)=MATCH` |
| `cargo test --all-targets` | 35 passed, 0 failed |
| `bash runtime/win32hle/build_selftests.sh` | up to 90 `ok`, 0 real failures (transient disk/timeout interruptions rerun cleanly) |
| `shasum -a 256 -c SHA256SUMS.txt` | 6/6 OK |
| `python3 -c 'import yaml; yaml.safe_load(...)'` | release workflow YAML valid, 6 matrix targets |

## 4. Recently merged PRs (each reviewed before merge)

| PR | Work |
|---|---|
| #199 | x64 oracles unpack 9th `read_pe()` value; `test_x64_expand.py` green |
| #202 | stale x64 GS test updated to real modelled-TEB helpers (`cpu64_rd_gs64`) |
| #203/#212 | generic differential engine + instruction-form sweep (Agent 3) |
| #204 | release workflow: `macos-14` -> `macos-latest` |
| #205-#215 | 10 wave-10 gate/evidence tools: Frida-free verifier, resource collector, corpus index, Win→mac reporter, Linux runner probe, Mach-O→PE/Linux gates, ELF→PE/macOS gates, OS matrix runner |
| #216-#220 | Rust verification backend, multi-input matrix, backbone hardening, vacuous-PASS fix |
| earlier | loader/DLL forwarding, COM/DirectX, registry, audio/input/network, corpus harness, PE32 coverage, SDK production APIs |

## 5. Evidence files on disk (readable, not fabricated)

```
/Users/haseeb-mir/Downloads/recomp-apps/WINDOWS_APPS_TEST_REPORT.md        54K
/Users/haseeb-mir/Downloads/recomp-apps/END_TO_END_PROOF/AGGREGATE_REPORT.md 2.6K
/Users/haseeb-mir/Downloads/recomp-apps/SIDE_BY_SIDE_EVIDENCE/REPORT.md    3.4K
/Users/haseeb-mir/Downloads/SYSTEM_WIDE_EXE_INVENTORY/REPORT.md            166K
```
And in the repository (verified with `git ls-tree -r --name-only origin/develop`):
`proof/INDEX.json`, `proof/OS_BRIDGE_STATUS.md`, `proof/README.md`,
`proof/binaries/macos/cube-native`, `proof/screenshots/macos/*.bmp`,
`docs/RELEASE_0.2.0.md`, `docs/HYBRID_TEST_PIPELINE.md`, `docs/PE32_COVERAGE.md`.

## 6. Honest limits (part of the proof, not hidden)

- **46 issues remain OPEN.** No issue is closed without full acceptance evidence.
- No original-Windows controlled-input scenario exists here: no game is 100% gameplay-verified.
- Frida attach is BLOCKED host-wide on macOS 27.2.1 (tested Frida 17.24.3 and 17.27.5;
  plain `/bin/sleep` gives `PermissionDeniedError`, Wine gives dyld-probing error).
- No Linux runner available locally; Linux CI configuration is not a Linux test result.
- x64/.NET/Win16 remain DEFERRED by design; the gates refuse them explicitly.
- Build/unit evidence is never presented as gameplay equivalence.
