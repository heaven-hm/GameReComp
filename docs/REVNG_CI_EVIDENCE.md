# Real rev.ng evidence on GitHub Actions (issue #25)

`revng-evidence.yml` is an **active** job, not a description of one. It runs on
`ubuntu-latest` (native Linux x86-64, so no container or emulation), downloads
the pinned rev.ng distributable, verifies the exact byte count and SHA-256
before extracting it, decompiles a real PE32 fixture with rev.ng, compiles the
result through the repo's canonical guest-ABI adapter, drives the repo's own
difftest and multilifter against the original bytes, and finally evaluates
source quality with `tools/revng_source_quality.py`.

Nothing in this workflow changes issue state. Issue #25 stays OPEN until the
evidence has been reviewed and merged by a human.

## Pins (verified from bytes, not from a moving channel)

| Item | Exact value |
|---|---|
| Distributable commit | `7361e00740573e8990d13651cedd5314e89e5adc` |
| Archive URL | `https://revng-website-downloads.s3.fr-par.scw.cloud/binary-archives/public/linux-x86-64/revng-distributable-public-demo/default/none_7361e00740573e8990d13651cedd5314e89e5adc.tar.xz` |
| Byte count | `564214476` (`stat -c %s`) |
| SHA-256 | `f19060db47165388df24343c6c7abc824742ac9063beeb614d474f3c6b764238` |
| Runner | `ubuntu-latest`, `runs-on: ubuntu-latest`, no matrix/emulation |

Upstream publishes no checksum for this archive. The SHA-256 above was computed
locally from the full 564214476 bytes of the pinned URL on 2026-11-26 and is
written into `$RUNNER_TEMP/revng-evidence/checksums.sha256` by the job, then
checked with `sha256sum -c`. A different byte count or digest is a different
pin: the job stops and the verdict stays `NOT TESTED`.

## What the job actually measures

1. Linux host facts (`uname`, `RUNNER_OS`, `RUNNER_ARCH`, git HEAD) and every
   command are recorded in `environment.txt` and `commands.txt`.
2. `curl -fL --retry 3` downloads the pinned archive. Its rc is measured.
3. `stat -c %s` must return exactly 564214476 and `sha256sum -c checksums.sha256`
   must succeed. Both hashes are copied into `hashes.txt`.
4. `tar -xJf ... --strip-components=1`, `source ./environment`, and
   `revng --version` are run from the extracted distributable; the version
   output is kept.
5. The real PE32 fixture is built deterministically from
   `tests/diff/differential_gate.py:build_gate_pe()` (the gate function at
   `0x00401000`: `mov eax,[0x00404100]; add eax,-2; mov [0x00404104],eax; ret`).
   Its SHA-256 is recorded. The repo's `game-recomp-cli` builds the PCRecomp
   project from that exact PE; a non-verified pipeline rc of 3 is recorded
   verbatim, not promoted.
6. `revng quick artifact emit-c-as-single-file gate.exe` produces PTML; the
   repo's own `tools/autorecomp/revng.py` (`ptml_to_c`, `function_source`)
   turns it into plain C and the function at `0x00401000`.
7. A deterministic adapter maps the provider's function into the repo's
   canonical runtime contract (`MEM32` for guest addresses, `_ABI` stripped,
   `sub_00401000` wrapper with `RETC`). Both candidates get an explicit
   `cc -fsyntax-only` rc file. This is an adapter for *this one function*, not
   a general ABI bridge and not a new dispatch path.
8. `tools/lift/difftest.py --project --pe gate.exe --address 0x401000 --cases 8
   --json ...` is run for the PCRecomp project body and again with
   `--source` for revng. Eight entry states, the original bytes under Unicorn,
   named flags and named guest memory are compared. Divergence is a failure.
9. `tools/lift/multilifter.py --lifter revng=<report> --lifter
   pcrecomp=<report> --json decision.json` performs the real selection. Only
   `decision=PROMOTE` and `chosen_lifter=revng` qualify as revng evidence.
10. `tools/revng_source_quality.py` recomputes every hash and byte count from
    disk, requires both compile rc files to be measured 0, cross-checks the
    difftest reports and the decision, then compares structured-C signals
    measured with `tools/autorecomp/readability.py` and defined in
    `docs/SOURCE_PROVENANCE.md`: named locals/parameters, non-goto control
    flow, recovered types/structs and non-mechanical identifier ratio.

## Verdicts and what they mean

- `SOURCE_IMPROVED` requires *all* of: both candidates compile, both difftest
  reports agree with zero diverged cases, multilifter promotes revng on those
  exact reports, all hashes match, and every recorded readability signal is at
  least as good with at least one strict improvement in naming and identifier
  quality. It is scoped to the one fixture, one address and the exact C hashes
  in the report.
- `NOT PROVEN` is the default and the exit-1 path. Each unmet gate is recorded
  as an exact reason code (`REVNG_COMPILE_NOT_PROVEN`,
  `REVNG_DIFFTEST_DIVERGED_CASES`, `READABILITY_NOT_BETTER`,
  `FIXTURE_HASH_MISMATCH`, ...). A missing C file, a download failure, a hash
  mismatch, a divergence or a missing report can never become a PASS.
- The job summary derives `PASS` from a real Linux host, nine measured rc=0
  stages, a promoted revng decision and a sealed `SOURCE_IMPROVED` report.
  Otherwise it emits `NOT TESTED` and exits non-zero.
- Artifacts are uploaded with `if: always()`: the summary, the source-quality
  JSON, commands, hashes, logs, the PE and the C candidates. A failed run is
  still inspectable.

**Scope limits.** No `HIGH_LEVEL_VERIFIED`, no gameplay equivalence, no
100%, and no general "rev.ng produces better source" claim from one function.
The adapter is not the project's dispatch path; the project's PCRecomp-only
build remains unchanged. The job never calls `gh` and never closes or comments
on an issue.

## Reproduction

```sh
# local structural checks (no network, no rev.ng)
python3 -m pytest tests/test_revng_ci_workflow.py tests/test_revng_source_quality.py -q

# real run from GitHub (needs the workflow on the selected ref)
gh workflow run revng-evidence.yml --repo heaven-hm/GameReComp
gh run list --repo heaven-hm/GameReComp --workflow revng-evidence.yml --limit 1
```

See `proof/revng/SOURCE_QUALITY.md` for the measured run, its exact conclusion
and the run URL.
