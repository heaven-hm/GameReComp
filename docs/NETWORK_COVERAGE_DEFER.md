# Issue #12 — network API coverage: fail-closed DEFER/UNSUPPORTED status

Issue #12 ("Legacy multiplayer networking: Winsock 2, DirectPlay/IPX, LAN
discovery and deterministic network verification") explicitly accepts:

> Fail-closed unsupported API status with import+subsystem attribution in
> diagnostics. No promise that legacy multiplayer works from socket API test
> alone.

This document records that limited deliverable. It does **not** claim that
legacy multiplayer, DirectPlay LAN sessions, IPX servers, or restored online
matchmaking work.

## What was added

`tools/network_api_audit.py` is a CLI that reads **only** CLI-supplied import
records and selftest results. It does not scan imports itself, register shims,
open sockets, run a game, or contact any service. It emits one verdict per
observed API family with exact `dll` / `symbol` / `ordinal` / GUID / `subsystem`
attribution from the supplied records, plus a top-level verdict.

| Verdict | Emitted only when |
| --- | --- |
| `SUPPORTED` | A selftest has `scenario: loopback` or `scenario: multi_process`, `status: pass`, `real: true`, a literal loopback address, a valid port, a bound endpoint, a connected endpoint, `bytes_exchanged > 0`, and a SHA-256 that matches the on-disk trace. Multi-process additionally requires at least two **distinct** process identities with client and server roles. |
| `DEFERRED` | Import/registration demand exists, but no qualifying scenario was supplied, or a qualifying scenario is incomplete (missing hash or trace). Reasons include `REGISTRATION_ONLY`, `IMPORT_MENTIONS_ONLY`, `NO_REAL_SCENARIO`, `NO_HASH`, `NO_TRACE`. |
| `UNSUPPORTED` | A purported proof is rejected (`FAKE_LOOPBACK`, `SYNTHETIC_SCENARIO`, `NON_LOOPBACK_ADDRESS`, `NON_DISTINCT_PROCESSES`, `HASH_MISMATCH`, `TAMPER`, `EXPECTED_*_HASH_MISMATCH`), or the API is an out-of-scope external service (`EXTERNAL_SERVICE_UNAVAILABLE`). |

Fail-closed invariants, proven by `tests/test_network_api_audit.py`:

- Registration and import mentions **never** produce `SUPPORTED`; the worst
  possible outcome for a registry-only record is `DEFERRED`.
- A "loopback" selftest that is synthetic, uses a non-loopback address,
  exchanges zero bytes, or reuses one process identity is rejected as
  `UNSUPPORTED`, not downgraded to a softer status.
- Any hash mismatch (trace binary modified after capture, or an input JSON
  that differs from `--expected-hashes`) sets `tamper_detected` and forces
  `UNSUPPORTED` for every observed API. No partial credit is given when one
  scenario in the set is tampered with.
- A scenario linked to one API cannot support a different API; DirectPlay
  stays `DEFERRED` even when a Winsock loopback trace passes.

## Current coverage status (not a support claim)

These statuses describe diagnostics, not a shipped multiplayer implementation:

| API family | Subsystem | Status basis |
| --- | --- | --- |
| `winsock1_compat` | `winsock1` | `wsock32.c` Winsock 1.1 with `WSAAsyncSelect` and IPX-over-UDP framing already exists; imports still need a real game scenario for `SUPPORTED`. |
| `winsock2_overlapped` | `winsock2` | Overlapped `WSARecv`/`WSASend` and `WSAEventSelect` are listed in issue #12 P1 but are not implemented here; no `ws2_32.dll` game trace supplied. |
| `directplay` | `directplay` | No DirectPlay COM frontend; `dplayx.dll`/`dpnet.dll` names in `module.c` are system-module registration, not demand or implementation. |
| `ipx_spx` | `ipx_spx` | Partial UDP mapping only; no owned game LAN fixture or packet capture. |
| `lan_discovery` | `lan_discovery` | No broadcast/LAN discovery fixture with verified hashes. |
| `external_service` | `external_service` | Proprietary matchmaking/auth is `EXTERNAL_SERVICE_UNAVAILABLE`; no server emulation or credential handling is attempted. |

## Reproduce

```sh
# Fail-closed diagnostic (no SUPPORTED from registration alone):
python3 tools/network_api_audit.py \
  --imports /path/to/import-records.json \
  --selftests /path/to/selftest-results.json \
  --json-out /path/to/network-audit.json

# Pin the supplied evidence files themselves:
python3 tools/network_api_audit.py \
  --imports /path/to/import-records.json \
  --selftests /path/to/selftest-results.json \
  --expected-hashes /path/to/expected-hashes.json \
  --json-out /path/to/network-audit.json

python3 -m pytest tests/test_network_api_audit.py -q
```

Exit codes: `0` for a completed `SUPPORTED`/`DEFERRED` audit, `1` for a
fail-closed `UNSUPPORTED` audit (including any tamper), `2` for malformed
input. The `--expected-hashes` file is an optional JSON object such as
`{"imports": "<sha256>", "selftests": "<sha256>"}`.

## Exact blocker for closing issue #12

This change satisfies only the issue's **fail-closed diagnostics** bullet. It
does not satisfy the issue's own acceptance work:

- **P1**: Winsock 2 overlapped I/O, event selection, cancellation and Windows
  error mapping are not implemented (no `ws2_32.c` frontend sharing the
  existing socket core).
- **P2**: DirectPlay `IDirectPlay2/3/4` interfaces, lobby/session enumeration,
  and deterministic IPX/SPX LAN discovery are not implemented.
- **P3**: No DirectPlay LAN two-player session was run against an owned game
  fixture, and no original-Windows-vs-native message/checksum comparison under
  the same tick/input schedule exists.
- Ledger criterion **network-game-oracle** ("Deterministic multiplayer
  verification with oracle") is unmet, and **lan-replay** ("LAN replay test
  demonstrated") is unmet.

Therefore issue #12 remains **OPEN**. A loopback selftest with a passing hash
proves only that a diagnostic can recognise loopback evidence; it is not a
legacy multiplayer game, a LAN replay, or a gameplay oracle. Reopen completion
only when a licensed Windows game fixture supplies a two-process/multi-host
scenario whose captured messages and checksums are compared against the
original executable under a deterministic input schedule.
