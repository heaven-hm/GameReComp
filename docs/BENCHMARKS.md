# Benchmarks

Measured results for the PCRecomp-first pipeline (`./autorecomp`). This file is
generated from the run database and per-project stage reports; regenerate it with

```sh
python -m tools.benchmarks
```

Only measured values appear. A game listed as unmeasured was not run, or its
report did not carry the metric; it is never scored from the upstream project's
own claims. The full per-input database lives in `table.md`, and the evidence
behind each row is the linked HTML report.

Last measurement: 2026-10-29T20:40:20Z

## How to read the score

`PASS /100` is weighted stage completion: inspect 10, recover 10, translate 10,
project 10, compile 20, run 15, verify 15, report 10. A stage counts only when it
succeeded for the recorded input hash. The verification status is the honest
outcome and can be lower than the score suggests: a rebuilt binary that compiles
and launches but crashes scores 75 and reports `launch-failed`.

## Measured inputs

| Game | Executable | SHA-256 | Functions | Instructions | Units | C source (MiB) | Compile (s) | Binary (MiB) | Score | Verification |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Cave Story | DoConfig.exe | 03f3c73a3692 | 406 | 36,488 | 2 | 4.1 | 8 | 1.3 | 75 | launch-failed (rebuilt process crashed (exit -6)) |
| Cave Story | Doukutsu.exe | fa18379fe942 | 3,260 | 472,590 | 8 | 50.7 | 22 | 8.2 | 75 | launch-failed (rebuilt process crashed (exit -6)) |
| Cave Story | OrgView.exe | 07bbca123dcb | 551 | 50,090 | 2 | 5.4 | 9 | 1.5 | 75 | launch-failed (rebuilt process crashed (exit -6)) |
| GeneRally | TrackEditor.exe | ced9e54e6578 | 311 | 67,393 | 2 | 7.0 | 14 | 2.1 | 75 | launch-failed (rebuilt process crashed (exit -6)) |
| Gunroar | gr.exe | 6ea800ee9185 | 3,288 | 190,302 | 5 | 19.6 | 28 | 6.9 | 75 | launch-failed (rebuilt process crashed (exit -6)) |
| Hero Core | herocore gm5.exe | be411b8b64cf | 12,424 | 587,240 | 14 | 59.4 | 43 | 16.9 | 75 | launch-failed (rebuilt process crashed (exit -6)) |
| Hero Core | herocore gm7.exe | 9146fc64eb67 | 15,788 | 787,627 | 18 | 79.6 | 38 | 21.6 | 75 | launch-failed (rebuilt process crashed (exit -6)) |
| Ikachan | dxIka.exe | 22923218ba97 | 482 | 88,424 | 2 | 9.6 | 13 | 2.2 | 75 | launch-failed (rebuilt process crashed (exit -6)) |
| Noiz2sa | noiz2sa.exe | 389af5a46ee8 | 3,024 | 372,825 | 6 | 36.3 | 18 | - | 40 | not-run (verification did not run) |
| OpenTyrian | opentyrian.exe | - | - | - | - | - | - | - | 0 | not-run (verification did not run) |
| Parsec47 | p47.exe | 41b2f77d2d7a | 1,930 | 127,248 | 3 | 13.2 | 24 | 4.3 | 75 | launch-failed (rebuilt process crashed (exit -6)) |
| Pixeltetris | Pixeltetris.exe | - | - | - | - | - | - | - | 0 | not-run (verification did not run) |
| Torus Trooper | tt.exe | b3a93b12e110 | 2,342 | 144,504 | 4 | 15.1 | 18 | 4.9 | 75 | launch-failed (rebuilt process crashed (exit -6)) |
| Tumiki Fighters | tf.exe | 8e77d496d636 | 1,760 | 114,156 | 3 | 12.0 | 14 | 3.7 | 75 | launch-failed (rebuilt process crashed (exit -6)) |
| Warning Forever | wf.exe | 5eb8095ed202 | 2,624 | 308,372 | 4 | 33.6 | 32 | 7.1 | 75 | launch-failed (rebuilt process crashed (exit -6)) |
| rRootage | rr.exe | f3d81d702573 | 3,199 | 424,637 | 7 | 41.3 | 40 | 9.7 | 75 | launch-failed (rebuilt process crashed (exit -6)) |

## Aggregate

| Metric | Value |
| --- | ---: |
| Inputs measured | 16 |
| Reached a built binary | 14 |
| Verification `scenario-pass` | 0 |
| Verification `partial` | 0 |
| Verification `launch-failed` | 13 |
| Generated C source | 386.9 MiB |
| Recovered instructions | 3,771,896 |
| Compile time, median | 20 s |
| Compile time, max | 43 s |

## Current ceiling on this host

Every PE32 input that reaches the run stage compiles and launches, then the
rebuilt process aborts during dynamic linking of Homebrew freetype 2.15.0
(`dyld: Symbol not found`), exit code -6. That is a host dependency fault, not a
translation verdict, and it is recorded as `launch-failed` rather than hidden.
Rebuilding freetype from source is the tracked unblock (task: unblock the run
stage). Until then no scenario can pass and the honest measured ceiling is
compile-ok plus launch-failed.

## Method

- Command: `./autorecomp <binary> --project-dir projects/<game> --timeout 20`
- Per-function source units are capped at 1,000 functions or 8 MiB of C, whichever
  closes first, so one oversized function cannot defeat the compiler.
- The compile deadline is adaptive: `min(1800, 600 + 1.0 * source MiB)` seconds,
  overridable with `--build-timeout`, and the whole build runs in its own process
  group so a timeout kills the compiler and not just the wrapper.
- The generated C is register-transfer code (`LOW_LEVEL_FALLBACK`), not recovered
  high-level source; see `docs/SOURCE_PROVENANCE.md` for what the categories mean.

## Reproducing

Set `SMALL_GAMES_PATH` to a directory containing your own legally obtained copies
of the corpus, then rerun the sweep. Input hashes in the table identify exactly
which executable produced each row; game binaries and assets are never stored in
this repository.
