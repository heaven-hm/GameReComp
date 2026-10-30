# Benchmarks

Measured results for the PCRecomp-first pipeline (`./RevEng`). This file is
generated from the run database and per-project stage reports; regenerate it with

```sh
python -m tools.benchmarks
```

Only measured values appear. A game listed as unmeasured was not run, or its
report did not carry the metric; it is never scored from the upstream project's
own claims. Compile seconds come from real compilations: a run that adopted an
existing binary with `--reuse-build` (after checking its manifest source digest)
shows `-` instead of a fake zero and is excluded from the timing statistics.
The full per-input database lives in `table.md`, and the evidence
behind each row is the linked HTML report.

Last measurement: 2026-10-29T22:53:56Z

## How to read the score

`PASS /100` is weighted stage completion: inspect 10, recover 10, translate 10,
project 10, compile 20, run 15, verify 15, report 10. A stage counts only when it
succeeded for the recorded input hash. The verification status is the honest
outcome and can be lower than the score suggests: a rebuilt binary that compiles
but fails to boot or crashes scores 85 and reports `launch-failed`, while one that
launches and exits with no independent oracle scores 93 and reports `partial`.

## Measured inputs

| Game | Executable | SHA-256 | Functions | Instructions | Units | C source (MiB) | Compile (s) | Binary (MiB) | Score | Verification |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Cannonball | cannonball.exe | - | - | - | - | - | - | - | 0 | not-run (verification did not run) |
| Cave Story | DoConfig.exe | 03f3c73a3692 | 406 | 36,488 | 2 | 4.1 | 10 | 1.3 | 85 | launch-failed (rebuilt process boot-failed (exit 2)) |
| Cave Story | Doukutsu.exe | fa18379fe942 | 3,260 | 472,590 | 8 | 50.7 | - | 8.2 | 85 | launch-failed (rebuilt process boot-failed (exit 2)) |
| Cave Story | OrgView.exe | 07bbca123dcb | 551 | 50,090 | 2 | 5.4 | 10 | 1.5 | 85 | launch-failed (rebuilt process boot-failed (exit 2)) |
| GeneRally | GeneRally.exe | 868175b2cc24 | 2,577 | 4,029,924 | 46 | 373.9 | - | 88.0 | 85 | launch-failed (rebuilt process boot-failed (exit 2)) |
| GeneRally | TrackEditor.exe | ced9e54e6578 | 311 | 67,393 | 2 | 7.0 | 16 | 2.1 | 85 | launch-failed (rebuilt process boot-failed (exit 2)) |
| Gunroar | gr.exe | 6ea800ee9185 | 3,288 | 190,302 | 5 | 19.6 | 24 | 6.9 | 85 | launch-failed (rebuilt process boot-failed (exit 2)) |
| Hero Core | herocore gm5.exe | be411b8b64cf | 12,424 | 587,240 | 14 | 59.4 | - | 16.9 | 85 | launch-failed (rebuilt process boot-failed (exit 2)) |
| Hero Core | herocore gm7.exe | 9146fc64eb67 | 15,788 | 787,627 | 18 | 79.6 | - | 21.6 | 85 | launch-failed (rebuilt process boot-failed (exit 2)) |
| Ikachan | dxIka.exe | 22923218ba97 | 482 | 88,424 | 3 | 9.6 | 14 | 2.1 | 93 | partial (process launched and exited 1; no oracle supplied) |
| Noiz2sa | noiz2sa.exe | 389af5a46ee8 | 3,024 | 372,825 | 6 | 36.3 | - | 8.3 | 85 | launch-failed (rebuilt process boot-failed (exit 2)) |
| OpenTyrian | opentyrian.exe | - | - | - | - | - | - | - | 0 | not-run (verification did not run) |
| Parsec47 | p47.exe | 41b2f77d2d7a | 1,930 | 127,248 | 3 | 13.2 | 19 | 4.3 | 85 | launch-failed (rebuilt process boot-failed (exit 2)) |
| Pixeltetris | Pixeltetris.exe | - | - | - | - | - | - | - | 0 | not-run (verification did not run) |
| Snake | snake-windows-x86_64.exe | - | - | - | - | - | - | - | 0 | not-run (verification did not run) |
| Torus Trooper | tt.exe | b3a93b12e110 | 2,342 | 144,504 | 4 | 15.1 | 21 | 4.9 | 85 | launch-failed (rebuilt process boot-failed (exit 2)) |
| Tumiki Fighters | tf.exe | 8e77d496d636 | 1,760 | 114,156 | 3 | 12.0 | 17 | 3.7 | 85 | launch-failed (rebuilt process boot-failed (exit 2)) |
| Warning Forever | wf.exe | 5eb8095ed202 | 2,624 | 308,372 | 6 | 33.6 | 32 | 7.0 | 85 | launch-failed (rebuilt process boot-failed (exit 2)) |
| rRootage | rr.exe | f3d81d702573 | 3,199 | 424,637 | 7 | 41.3 | - | 9.7 | 85 | launch-failed (rebuilt process boot-failed (exit 2)) |

## Aggregate

| Metric | Value |
| --- | ---: |
| Inputs measured | 19 |
| Reached a built binary | 15 |
| Verification `scenario-pass` | 0 |
| Verification `partial` | 1 |
| Verification `launch-failed` | 14 |
| Generated C source | 760.9 MiB |
| Recovered instructions | 7,801,820 |
| Compile time, median | 17 s |
| Compile time, max | 32 s |

## Current ceiling on this host

14 of 19 rebuilt inputs compile to a native binary, then stop in the
PE loader because the runtime does not implement all the imports the executable needs.
The loader prints every unresolved name and exits 2 instead of binding a success stub,
and the verifier records that honestly as `launch-failed`.

Unresolved imports per boot-failed input: 2 to 179.

Ikachan binds every import and launches, then exits before a scenario can be graded;
with no independent original-Windows oracle supplied it is reported as `partial`, not
verified. The remaining gap is runtime coverage (the libraries the loader names), not
translation: every input here already reaches a built binary.

## Method

- Command: `./RevEng <binary> --project-dir projects/<game> --timeout 20`
- Per-function source units are capped at 1,000 functions or 8 MiB of C, whichever
  closes first, so one oversized function cannot defeat the compiler.
- The compile deadline is adaptive: `min(3600, 600 + 5.0 * source MiB)`
  seconds (calibrated on this host at roughly 4.6 s/MiB for a 375 MiB
  overridable with `--build-timeout`, and the whole build runs in its own process
  group so a timeout kills the compiler and not just the wrapper.
- The generated C is register-transfer code (`LOW_LEVEL_FALLBACK`), not recovered
  high-level source; see `docs/SOURCE_PROVENANCE.md` for what the categories mean.

## Reproducing

Set `SMALL_GAMES_PATH` to a directory containing your own legally obtained copies
of the corpus, then rerun the sweep. Input hashes in the table identify exactly
which executable produced each row; game binaries and assets are never stored in
this repository.
