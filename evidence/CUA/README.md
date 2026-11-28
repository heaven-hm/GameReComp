# CUA (computer-use) testing

Driver: `CuaDriver.app/Contents/MacOS/cua-driver` v0.17.0, daemon `com.trycua.driver`.

## Verified capability

- `check_permissions`: `accessibility: true`, `screen_recording: true`, attribution
  `com.trycua.driver` (not the shell's TCC identity).
- `start_session` with `capture_scope: desktop` and `capture_scope: window`.
- `get_desktop_state`: native 3360x2100 PNG on a 1680x1050 logical display.
- `list_windows`: resolves app name, pid, window id, bounds and z-order.
- `press_key` with `delivery_mode: foreground` on a window-scope session returns
  `delivery.mode: foreground`, `effect: unverifiable` (driver's own wording).

## Results

- **AssaultCube (native macOS arm64, open-source build)**: CUA located window
  `AssaultCube` (pid 76631, window 4743) and captured a real gameplay frame,
  `ac-setup-cua.png`, 1024x768, 17,689 colours, SHA-256 recorded in
  `cua-testing-evidence.json`. This is CUA-driven visual proof the port renders.
- **IGI level 5 CUA capture**: attempted, but the desktop capture landed on a
  1-colour blank/occluded frame. Recorded as **FAIL** and not used as proof;
  the committed IGI frames were captured by the native binary's own
  `RECOMP_SCREENSHOT` path, not by CUA.

## Input caveat

A CUA `press_key(w)` was delivered, but the following desktop capture showed a
different app frontmost, so no game input-state change is claimed. This is an
honest limitation of the current capture/input loop, not a PASS.
