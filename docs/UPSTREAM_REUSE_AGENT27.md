# Agent 27 — Win32 threading and synchronization

## Upstream inspection
- PCRecomp `https://github.com/sp00nznet/pcrecomp`, local checkout
  `/Users/haseeb-mir/Documents/Code/pcrecomp`, HEAD
  `a85ea970f9e0640ab6546d2ca7a01fccb132973d`, MIT (Copyright (c) 2026 sp00nz).
  Inspected `runtime/win32hle/kernel32.c`, `kernel32_crt.c`, `host_lite.c`.
  The existing `hle_thread_create`, handle table, `hle_wait`, and TIB/TLS model
  were reused unchanged; only the defective semantics were corrected.
- Wine `https://github.com/wine-mirror/wine` (LGPL): `dlls/ntdll/sync.c` was
  checked for semaphore creation semantics only. No Wine source was copied;
  the licence does not permit transplanting its implementation here.

## Fixes (all in existing files)
1. `WaitForMultipleObjects(..., bWaitAll=TRUE, ...)` previously called
   `try_take()` while scanning, so an unsuccessful wait consumed a signaled
   auto-reset event. `try_take()` now has a non-consuming peek mode; wait-all
   peeks every handle first and only consumes after all are ready.
2. `ReleaseSemaphore` clamped the count to the maximum and returned success.
   It now fails with `ERROR_TOO_MANY_POSTS` when the release would exceed the
   limit, validates negative counts, and `CreateSemaphoreA` rejects
   `initial > max` / `max <= 0`.
3. `WaitForMultipleObjects` with `n == 0 || n > MAXIMUM_WAIT_OBJECTS (64)`
   returns `WAIT_FAILED` with `ERROR_INVALID_PARAMETER` instead of silently
   truncating to 64.
4. `SetThreadPriority`/`GetThreadPriority` returned canned values. The
   requested priority is now stored on the thread object and read back; invalid
   handles and out-of-range priorities (`-7..9`) fail like the Windows API.
   Host scheduling priority is deliberately not changed: guest threads are
   cooperatively serialised under the machine lock.
5. `TlsFree` now marks the index free and clears the current thread's slot;
   `TlsGetValue`/`TlsSetValue` reject indices that were never allocated or were
   freed with `ERROR_INVALID_PARAMETER`.

## Fixture and verification
New `runtime/win32hle/threadsync_selftest.c` builds guest API calls through the
existing shim ABI and covers: auto-reset event consume/timeout, semaphore
overflow and release, wait-all atomicity, invalid wait count, four concurrent
guest threads incrementing a shared counter under a real mutex (800 total),
thread exit code 42, TLS isolation between the main thread and workers,
priority round-trip and invalid-handle rejection.

Commands and results on macOS arm64:
```
bash runtime/win32hle/build_selftests.sh     -> 15/15 ok, exit 0
python3 -m pytest tests/ -q                  -> 168 passed, 2 skipped
5x threadsync_selftest repeat run            -> all passes, no deadlock
```
Before the fixes the fixture failed on `semaphore overflow refused`,
wait-all consumption, and priority round-trip.

## Limitations / unsupported
- `SuspendThread`/`ResumeThread` only suspend before the first guest entry; a
  thread already inside lifted code cannot be suspended mid-function.
- `GetThreadContext`/`SetThreadContext` remain deliberately fail-closed
  (`ERROR_CALL_NOT_IMPLEMENTED`) because no suspended guest register file exists.
- TLS clearing after `TlsFree` is immediate only in the calling host thread;
  other host threads retain their slot value until they write again, but any
  subsequent get/set through the freed index is rejected globally.
- AddressSanitizer/UBSan could not be used on this macOS host: the runtime
  aborts in the sanitizer's own allocator initialisation when SDL2 threads start
  (`sanitizer_malloc_mac.inc CHECK failed`). The unsanitised stress repeats
  above are the evidence; this is UNTESTABLE here, not a pass.
- No original-Windows comparison and no gameplay evidence; PASS here means
  deterministic synthetic guest-ABI fixtures only.
