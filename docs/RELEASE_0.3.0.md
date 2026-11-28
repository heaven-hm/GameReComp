# GameReComp 0.3.0

Release tag: `v0.3.0`. Base: `develop` after 88 reviewed PRs since `v0.2.0`.

## Assets

The release workflow builds CLI and GUI binaries for Linux x86_64, macOS arm64
and Windows x86_64, plus `SHA256SUMS.txt`. Verify with:

```sh
shasum -a 256 -c SHA256SUMS.txt
```

## Evidence highlights

- Real Windows-oracle process proofs in `proof/windows-oracle/` and `proof/e2e-133/`.
- Chocolate Doom: 166 EXE + 34 SDL_net imports bound, 0 unresolved, 3 non-blank
  256x200 frames (3,468 colours) in `proof/issue-28/`.
- Green Linux-host CI run `45677290863` (`ubuntu-latest`, native ELF artifact).
- Real rev.ng 1af3940 run on GitHub Linux, honest `NOT PROVEN` (revng 8/8
  diverged, PCRecomp promoted) in `proof/revng/`.
- Fail-closed gate tooling with deterministic tests across `tools/` and `proof/`.

## Limits

No original-Windows gameplay oracle for proprietary games, no non-silent real
audio capture, no legal 10,000-game corpus, and no PE32+ game execution.
PARTIAL/BLOCKED verdicts are preserved rather than promoted to PASS.

Final verification: `python3 -m pytest tests/ -q` with one known environment
test deselected; 2414 passed, 33 skipped, 130 subtests.
