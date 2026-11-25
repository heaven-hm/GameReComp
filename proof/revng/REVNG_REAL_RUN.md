# Real rev.ng container attempt for issue #25 — BLOCKED, no PASS

**Outcome: the pinned rev.ng container did not run. No rev.ng C output, no
rev.ng difftest report, and no acceptance criterion of issue #25 is evidenced.
Issue #25 remains OPEN.** This document records the failed acquisition, the
exact blocker, and the real reports that were produced. Nothing here is a
decompilation result or a provider verdict.

## 1. Pin and host facts

| Fact | Recorded value |
|---|---|
| Pinned image | `revng/revng@sha256:aaafb2bc57cdc4d6f499b99453a48dfd622ef5611f5eb57927e43b80d03063ac` |
| Pin source | `tools/revng_acquire_guide.py` (`PINNED_IMAGE`), merged in PR #277 |
| Host | macOS 27.2.1, `arm64` (emulated `linux/amd64` container required) |
| Docker client / server | `Docker version 29.9.2, build a82e528901` / `29.5.2` |
| Disk before the pull | 8.8 GiB free (`df -k /`: 8,299,540 KiB available) |
| Disk during/after the stalled pull | fell to ~3.2 GiB; the pull was killed |
| Disk when the readiness report was generated | 2.713 GiB free (2,913,185,792 bytes) |
| Readiness verdict | `NO_REVNG_PASS` / `BLOCKED` |

All host facts above were captured by the new read-only runner into
`proof/revng/container_attempt.json` (schema 1). The runner records its own
disk reading rather than relying on a prose claim.

## 2. Exact blocker

The following command was started against the pinned digest:

```sh
docker pull --platform linux/amd64 \
  revng/revng@sha256:aaafb2bc57cdc4d6f499b99453a48dfd622ef5611f5eb57927e43b80d03063ac
```

Observed pull output (tail):

```text
2f2c8c70e70a: Pulling fs layer
5612415fead7: Pulling fs layer
ba082564e660: Pulling fs layer
a7c1865bf551: Pulling fs layer
985a748d8284: Pulling fs layer
2f2c8c70e70a: Download complete
985a748d8284: Download complete
ba082564e660: Download complete
5612415fead7: Download complete
ba082564e660: Pull complete
5612415fead7: Pull complete
a7c1865bf551: Download complete
```

The registry fetch then stalled: two layers reached `Pull complete`, the rest
did not, and the process had only 0.83 s of CPU time in 11 min 47 s. Host free
space fell from 8.8 GiB to about 3.2 GiB during the attempt. The parent session
killed the stalled process (PID 31907) to protect the host disk. **The pull was
not restarted.**

Partial layers are not usable:

```sh
$ docker image ls -a
gamerecomp/frida-probe:local  97f1e848e2e3  1.64GB
gamerecomp/linux-runner:local 4118c8901a0c  1.22GB
ubuntu:24.04                  562111b88caa   261MB

$ docker image inspect revng/revng@sha256:aaaf…63ac
Error response from daemon: No such image: revng/revng@sha256:aaaf…63ac

$ docker system df
Images 3, 2.047GB; Containers 1, 282.1MB; Local Volumes 0; Build Cache 0B
```

There is no revng image ID, no `RepoDigest` for the pin, and no completed
layer chain that a `docker run` could use. `docker info` succeeds, so the
runtime itself is healthy; the blocker is the registry fetch plus the disk it
consumed, not a missing Docker daemon.

The new runner adds a fail-closed guard that would have refused this pull from
the observed starting conditions if the budget had been set from the real
5.8 GiB consumption rather than the guide's compressed-size hint:

- `MIN_FREE_BYTES = PULL_DISK_BUDGET_BYTES = 6 * 1024**3` (6 GiB).
- Current free space (2.713 GiB) is below the floor → `DISK_GUARD_BLOCKED`.
- A pull additionally requires the explicit `--allow-pull` flag →
  `PULL_NOT_AUTHORISED` in this read-only run.
- No pinned image → `PINNED_IMAGE_NOT_USABLE`; no C output →
  `NO_REVNG_C_OUTPUT`.

The runner has no code path that labels a missing, stalled or even successful
container run as a differential PASS.

## 3. Fixture feasibility (read-only, no revng involved)

The planned subject was the checked-in `tests/fixtures/pe32-minimal.exe`
(1,280 bytes, PE32, entry `0x00401000`: `push 0; call [0x402050]; xor eax,eax;
ret 0x10`). A direct read-only probe of the original bytes showed a second,
independent obstacle:

```sh
python3 - <<'PY'
from tools.lift import difftest
p='tests/fixtures/pe32-minimal.exe'
blob,_=difftest.pe_bytes_at(p,0x401000,64)
lo,size=difftest.oracle_window(p)
named=difftest.named_addrs_from_code(blob,0x401000,lo,lo+size)
print('window',hex(lo),hex(size),'named',[hex(x) for x in named])
print(difftest.run_unicorn_function(p,0x401000,named,lambda c:c+1,8))
PY
```

```text
window 0x400000 0x41000 named ['0x402050']
OracleFault: the ORIGINAL's bytes at 0x00401000 faulted on case 0:
Invalid memory fetch (UC_ERR_FETCH_UNMAPPED)
```

The fixture's IAT entry at `0x402050` holds the unresolved ILT value `0x2020`,
so the reference machine faults before any candidate could be compared. That
fixture would produce `original-faulted`, never a PASS, even with a working
revng. A future real run needs a PE whose original bytes execute cleanly in the
oracle (the differential gate's derived gate PE is one such subject).

## 4. Real reports produced and fed to the existing tools

| File | What produced it | Real result |
|---|---|---|
| `proof/revng/container_attempt.json` | `python3 tools/revng_container_runner.py --out …` | `NO_REVNG_PASS`, `BLOCKED`; four reason codes above |
| `proof/revng/diff_gate.json` | `python3 tests/diff/differential_gate.py --json …` | `ok: true`, 7/7 checks, decisions `clean_body=PROMOTE`, `flag_bug=FAILED`, `memory_bug=FAILED` |
| `proof/revng/multilifter_blocked.json` | `python3 tools/lift/multilifter.py --quiet --lifter revng=proof/revng/revng_difftest.json --json …` | `BLOCKED`, `chosen_lifter=null`, revng row `UNVERIFIED` |
| `proof/revng/issue25_blocker_evidence.json` | sealed with `canonical_digest` from `tools/issue25_evidence.py` | `schema_version=1`, criterion `revng_selection`, seal `1ec8e355ab699d1463c863a2d25f826842c60711b384d9a1901ae81d1cfbf203`; a blocker record, **not** a pass |
| `proof/revng/issue25_audit.json`, `issue25_audit.md` | `python3 tools/issue25_evidence.py …` | `overall=OPEN`, `PASS=0`, `FAIL=9` |

Reproduction of the audit (the `revng_difftest.json` path is intentionally
absent; no report was invented to make the selector produce a PASS):

```sh
python3 tools/lift/multilifter.py --quiet \
  --lifter revng=proof/revng/revng_difftest.json \
  --json proof/revng/multilifter_blocked.json

python3 tools/issue25_evidence.py \
  --selection blocked=proof/revng/multilifter_blocked.json \
  --diff-gate gate=proof/revng/diff_gate.json \
  --evidence proof/revng/issue25_blocker_evidence.json \
  --json proof/revng/issue25_audit.json \
  --out proof/revng/issue25_audit.md
```

The differential gate is genuine evidence that the comparator is not vacuous,
but it is not provider evidence: its checks are named
`memory-break-detected-at-named-address`, `flag-break-detected-when-flags-compared`
and so on, not the `pointer-break-detected`, `stack-purge-break-detected` or
`callback-break-detected` checks the issue #25 auditor requires, and it names no
revng candidate. It was included as a real input, not relabelled as a revng
result.

## 5. Issue #25 criteria — exact status

| # | Criterion | Genuinely evidenced? | Reason |
|---|---|---|---|
| 1 | rev.ng passes → selects rev.ng | **No** | No container run, no revng C, no revng difftest report. Auditor: `SELECTION_NOT_CITED`. |
| 2 | rev.ng diverges → PCRecomp passes → fallback | **No** | No revng failure report and no PCRecomp report for the same address. |
| 3 | Both fail/unverifiable → UNRESOLVED, no verified label | **No** | The `BLOCKED` selector decision is real, but no two-lifter both-fail report pair exists. |
| 4 | Wrong guest pointer rejected | **No** | No `pointer-break-detected` gate report. |
| 5 | Wrong stack purge rejected | **No** | No `stack-purge-break-detected` gate report. |
| 6 | Wrong callback rejected | **No** | No `callback-break-detected` gate report. |
| 7 | Deterministic repeated runs | **No** | Nothing was run twice; no pair of byte-identical reports. |
| 8 | Opt-in, default pipeline unchanged | **No** | No baseline/default-run artifact pair was produced. |
| 9 | Source quality vs verified behavior reported separately | **No** | No gate-backed scoped behavior verdict for a real candidate. |

Sealed records exist only for criterion 1, and that record is a **blocker
record** whose missing `selection` is detected by the auditor. No PASS, seal or
criterion was fabricated. The audit's fail-closed `OPEN` verdict is the real
result of this work.

## 6. Limits and next step

- No revng binary was executed; no revng C was emitted; no provider version was
  recorded from a real run. The version strings in section 1 are Docker host
  facts only.
- No `difftest.py --project --json` report naming revng exists in this proof
  set, so nothing was promoted and no source was replaced.
- `CLOSABLE` from `tools/issue25_evidence.py` would be permission to review, not
  a state change; this audit returned `OPEN`. The issue state was not edited and
  no GitHub auto-closing keyword was emitted.
- Next attempt requires: host free space back above the 6 GiB guard, a registry
  fetch that completes, `docker image inspect` returning the pinned digest,
  real `revng --version` and `project artifact --help` output, a real PE whose
  original bytes run in the oracle, then real per-lifter difftest reports and
  repeated-run digests before any criterion may be claimed.
