# Input-state evidence (headless win32hle fixture)

Scope: `runtime/win32hle/input_state_selftest.c` and this note. No gameplay
claims, no live-game run, no injected DLL into the retail executable. Everything
below is observable state inside the headless win32hle layer under SDL's
`dummy` video driver.

This fixture owns no production code. `screen.c` and `dinput.c` are owned by
other agents on this branch, so where the current code cannot expose a value,
the fixture records the exact blocker and keeps asserting everything that is
testable. A `BLK` line is a missing hook, not a passing emulation of it.

## How to reproduce

```
bash runtime/win32hle/build_selftests.sh
```

The script builds every `*_selftest.c` with the whole layer and runs it
headless (`SDL_VIDEODRIVER=dummy`, `SDL_AUDIODRIVER=dummy`). On this macOS host
it builds 64-bit with the guest's 4 GB based through `guest_mem.c`.

Observed result at this revision (exit 0, all assertions passed):

```
[screen] dummy, renderer software, scaling sharp (F12), fullscreen F11
BLK key: SDL_KEYDOWN set user32's key state but SDL_GetKeyboardState()/DIK_A stayed 0 (SDL_PushEvent does not update SDL's key state; dinput.c has no synthetic-key hook)
BLK key: DirectInput's DIK_A cannot be observed from a synthetic SDL key event
BLK focus: SDL_WINDOWEVENT_FOCUS_{LOST,GAINED} left guest focus on window A (no SDL focus -> user32 set_focus hook)
BLK wheel: SDL_MOUSEWHEEL(120) left DIMOFS_Z=0 in DirectInput GetDeviceState (no wheel hook: screen.c ignores the event, dinput.c hard-codes Z=0)
input_state_selftest: all checks passed (see BLK lines for missing hooks)
```

## What is asserted (passes today)

The fixture creates two guest windows plus a registered guest-class window,
opens the screen layer with a real SDL window under the dummy driver, and
creates acquired DirectInput keyboard/mouse devices through the same COM calls
a game makes (`DirectInputCreateA` -> `CreateDevice` ->
`SetCooperativeLevel`/`Acquire`). It then drives SDL events through
`hle_screen_pump()` and reads back guest state via lifted shim calls:

- **Key events:** `SDL_KEYDOWN`/`SDL_KEYUP` for scancode A set/clear user32's
  key state (`GetKeyState(VK_A)` high bit), and post `WM_KEYDOWN`/`WM_KEYUP`
  with `wParam == VK_A`. `PeekMessageA` reads `MSG.hwnd`, which is asserted to
  be the focused window in each case.
- **Focus handling:** after a guest `SetFocus` switch, the next injected key
  message goes to the *new* focus window (focus is observable through input
  routing). A synthetic SDL left-click at guest (210, 210) over the
  guest-class window becomes `WM_LBUTTONDOWN`; the lifted WndProc calls
  `SetFocus(hwnd)` on dispatch, and both `GetFocus()` and
  `hle_focus_window()` then report the clicked window. The click's release
  clears the guest mouse-button state.
- **Wheel non-interference:** the injected `SDL_MOUSEWHEEL(y=120)` is drained
  by the pump (no wheel event left in SDL's queue) and leaves mouse buttons,
  the DirectInput button bytes, and the keyboard state unchanged.
- **SDL focus events:** `SDL_WINDOWEVENT_FOCUS_LOST` and `_GAINED` are pushed
  and drained without disturbing guest state. The fixture prints a `BLK` line
  when guest focus did not change; if a hook lands and focus does change, it
  restores focus through the guest path and the routing assertions still hold.

## Exact blockers

1. **Wheel delta never reaches DirectInput `DIMOFS_Z`.**
   * `runtime/win32hle/screen.c:807-865` — `hle_screen_pump()`'s event switch
     handles `SDL_QUIT`, key, motion, button, drop and window events. There is
     no `case SDL_MOUSEWHEEL:`. The only wheel reference in that file is
     `menu_event()` at line 585, which just swallows the event while the
     presenter menu is open. A wheel event is therefore drained with no guest
     delta.
   * `runtime/win32hle/dinput.c:273` — `if (want >= 12) MEM32(buf + DIMOFS_Z) = 0;`
     in `dev_GetDeviceState()`. The Z axis is unconditionally zero, and there
     is no host-side wheel accumulator to read.
   * Fix shape (owned elsewhere): accumulate `e.wheel.y` in the screen/present
     input path and have the mouse `GetDeviceState` consume that accumulator
     into `DIMOFS_Z` (with a defined reset policy). The fixture's conditional
     check then asserts `z_after == 120` instead of printing the `BLK` line.

2. **SDL focus events are not mapped to guest focus.**
   * `runtime/win32hle/screen.c:860-863` — the `SDL_WINDOWEVENT` case handles
     only `SDL_WINDOWEVENT_SIZE_CHANGED` and `SDL_WINDOWEVENT_EXPOSED`.
   * `runtime/win32hle/present.c:60-89` — `hle_present_pump()` has no focus
     case either.
   * `runtime/win32hle/user32.c:522-528` — `set_focus()` is file-static;
     `win32hle.h` exposes `hle_focus_window()` (read) but no host focus setter.
     There is no SDL focus -> user32 focus bridge to assert against.
   * Guest focus *is* asserted through the paths that exist: guest `SetFocus`
     and a dispatched `WM_LBUTTONDOWN`.

3. **Synthetic key events are invisible to DirectInput's keyboard state.**
   * `runtime/win32hle/dinput.c:237-246` — `dev_GetDeviceState()` calls
     `SDL_PumpEvents()` then reads `SDL_GetKeyboardState()`.
   * SDL 1.2/2 behaviour: `SDL_PushEvent(SDL_KEYDOWN)` does **not** update
     `SDL_GetKeyboardState()`. This was verified with a standalone SDL2 probe:
     the scancode byte stays 0 after push, poll and `SDL_PumpEvents()`.
     Real OS key events do update it, but a headless fixture cannot synthesize
     them, so `DIK_A` stays 0 while user32's `GetKeyState(VK_A)` is set.
   * Fix shape (owned elsewhere): either feed synthetic key state into the
     DirectInput path (for example reuse `hle_input_key_state`) or have the
     keyboard `GetDeviceState` read the user32 key state. The fixture then
     asserts `DIK_A == 0x80` after keydown and 0 after keyup.

## Boundaries of this evidence

- No retail executable, no Wine, no gameplay input, no in-game result. The
  fixture proves only these headless hle state transitions.
- The `BLK` lines are printed to stdout and do not fail the test. A future
  change that adds a hook must flip the corresponding conditional assertion;
  leaving the `BLK` in place means the hook is still absent.
- `screen.c`, `dinput.c`, `present.c` and `user32.c` were not modified by this
  change.
