# Native display size / fullscreen evidence (macOS)

The host window used to be created at a fixed 1280×960 regardless of the
game's requested display mode. Fixed in commit `3a99d9b`:

- `RECOMP_WINDOW_SIZE=WxH` creates the window at exactly W×H (fail-closed on
  malformed or out-of-range values).
- `RECOMP_FULLSCREEN=1` starts the window in borderless fullscreen.
- When the guest calls DirectDraw `SetDisplayMode(w,h,bpp)`, the host window is
  resized to w×h (via `hle_screen_set_native_size`), so the game is shown at
  its original size rather than stretched into the default window.
- The runtime logs the actual SDL window size and fullscreen state, so the
  behaviour is verifiable without GUI accessibility permissions.

## Real verification (OUCC Tetris, rebuilt with the fix)

```
$ RECOMP_WINDOW_SIZE=640x480 timeout 5 <native binary> 2>&1 | grep '[screen] window'
[screen] window 640x480 fullscreen=0 (RECOMP_WINDOW_SIZE/RECOMP_FULLSCREEN)

$ RECOMP_FULLSCREEN=1 timeout 5 <native binary> 2>&1 | grep '[screen] window'
[screen] window 1680x1050 fullscreen=1 (RECOMP_WINDOW_SIZE/RECOMP_FULLSCREEN)
```

The display policy is also covered by `api_gap_selftest` (no policy by default,
valid policy parsed, malformed size ignored while fullscreen is kept).

## Limits

- The 640×480 and fullscreen numbers above are the requested window and the
  host desktop's native 1680×1050 respectively; they do not by themselves
  prove the guest framebuffer matches a specific original-Windows video mode
  for every title (that needs per-game oracle evidence).
- F11/F12 still toggle fullscreen/scaling interactively; automated mouse
  testing of those keystrokes is blocked by macOS assistive access (-25211).
