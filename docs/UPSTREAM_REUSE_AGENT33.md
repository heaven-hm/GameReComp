# Agent 33 — Win32 timers and waitable objects

## Coordination
This branch is based on `feature/agent-27-threads` (`7130d76`). The timer object
uses the existing `HLE_*` handle table and Agent 27's `try_take()`/`hle_wait()`
path; no second synchronization manager was introduced.

## Upstream inspection
- PCRecomp `https://github.com/sp00nznet/pcrecomp` (MIT), local HEAD
  `a85ea970f9e0640ab6546d2ca7a01fccb132973d`: existing `kernel32.c` event,
  mutex, semaphore and wait implementations were reused; the new timer type
  follows their handle/closer pattern.
- Wine `https://github.com/wine-mirror/wine` (LGPL): Win32 semantics for
  relative/absolute LARGE_INTEGER deadlines, manual vs auto-reset timers,
  periodic re-arming and `CancelWaitableTimer` were used as a behavioural
  reference only; no Wine code was copied.

## Implementation
Added `HLE_H_TIMER` and a `htimer` object with due time (monotonic
`ticks_ms()`), period, manual-reset flag and cancelled/signaled state.
`CreateWaitableTimerA/W`, `SetWaitableTimer`, `CancelWaitableTimer` and
`WaitForMultipleObjectsEx` are registered in the existing table. Timer
signalling is evaluated inside the shared `try_take()`: manual timers remain
signaled until cancelled, auto-reset timers are consumed by a wait, and
periodic timers re-arm. `SleepEx`/`Wait*Ex` accept the alertable flag but have
no APC queue; they return normal wait results, never `WAIT_IO_COMPLETION`.
`SetWaitableTimer` refuses a non-NULL completion routine with
`ERROR_CALL_NOT_IMPLEMENTED` rather than pretending to deliver an APC.

## Fixture and evidence
`runtime/win32hle/timer33_selftest.c` uses the real shim ABI and covers:
manual one-shot signal + cancel, auto-reset one-shot consumption, periodic
re-arming, absolute FILETIME deadlines, timer and event in one
`WaitForMultipleObjectsEx`, `SleepEx`, invalid-handle refusal and handle
cleanup. Timeouts are 2000 ms with 20–50 ms deadlines to avoid flaky
thresholds. Development of the fixture caught two real test-harness bugs
(host pointer passed as a guest VA, and a still-signaled event masking the
timer index), both corrected.

Commands and outputs on macOS arm64:
```
bash runtime/win32hle/build_selftests.sh   -> 16/16 ok, exit 0
python3 -m pytest tests/ -q                -> 168 passed, 2 skipped
```

## Limitations / unsupported
- APC completion routines and alertable `WAIT_IO_COMPLETION` are not supported
  and are explicitly refused or documented; no fake delivery.
- Timer resolution is host millisecond granularity; no Windows
  high-resolution timer countdown emulation.
- No original-Windows comparison, thread-priority oracle or gameplay claim.
  PASS here means synthetic API/fixture evidence only.
