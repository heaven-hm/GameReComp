# Issue #27 — DMD catch-owner frame fix

## Measured root cause

Target: `gr.exe`, SHA-256
`6ea800ee918547ca1953a24bebdcd07c29cef4ada617d7321a7dc3701e825636`.

Both `RaiseException(0xE0440001)` calls are DMD `OpenEx` failures. The first
catch resumes correctly. For the second, the instrumented runtime observed:

```
catch: record=0x104FEE94 ebp=0x104FEEA0 catch_va=0x0040DC96 tbl=0x0044BAF8 idx=2
  fsrec 0x104FEE94 handler=0x0040DD25 state=2 ebp=0x104FEEA0   <-- inlined OpenEx owner
  fsrec 0x104FEEC4 handler=0x00419D2B state=-1 ebp=0x004FEED0  <-- 0x00419CC0
  fsrec 0x104FEF24 handler=0x0040238A state=1 ebp=0x104FEF30
  fsrec 0x104FFF6C handler=0x0040211D state=0 ebp=0x104FFF78
frames: func=0x00419CC0 entry=0x104FEED4, func=0x00402128 entry=0x104FEF34,
        func=0x00402010 entry=0x104FFF7C
old selection: inner checkpoint 0x00419CC0 (exact=0)
```

`DHandlerTable` at `0x0044BAF8` names `fptr = 0x0040DBC0`, `espoffset = 0x30`,
and catch `0x0040DC96`. The guest reached this table through the tail-call stub
`0x0040DB9C` (`add eax,-0Ch; jmp 0x0040DBC0`). The lifter inlined the callee's
body into `sub_0040DB9C`, which has **no** `RECOMP_EH_ENTER`, so no live frame
has `entry_esp == 0x104FEEA4`. The old innermost-fallback resumed in
`0x00419CC0`; its landing pad returns from that function, skipping the rest of
the caller's work, so `gameManager.init()` never assigns `GameManager+0x74`
(InGameState) and the later ICALLs at `0x0040942C`, `0x00409324` and
`0x00419D38` read a null vtable. No frame is presented.

## What this change fixes

`runtime/win32hle/eh32.c`:

- `dmd_resume_frame()` now also receives the `fptr` from the matched
  `DHandlerTable` and chooses, in order:
  1. the exact checkpoint (`entry_esp == EBP+4`);
  2. a live checkpoint whose `func` is the function named by `fptr` — the
     **catch-owning frame** when an inlined guest record's EBP no longer
     matches its entry stack;
  3. the innermost checkpoint above the record, retained as the fail-visible
     approximation.
- The dispatch reason string names the path: `catch-owning frame` vs
  `inner checkpoint`, so a test can tell them apart without guessing.
- Both non-exact paths still require the catch continuation to be lifted.

`runtime/win32hle/dmd_throw_selftest.c` adds deterministic cases:

| case | layout | expected selection |
| --- | --- | --- |
| 1 | record owned by the live frame | exact checkpoint, inline catch |
| 2 | inlined record, `fptr` names a live owner frame | owner frame, not the nearer inner checkpoint |
| 3 | inlined record, `fptr` has no live frame | inner checkpoint (approximation) |
| 4 | no live checkpoint | explicit named refusal |

All four cases verify the guest EBP/ESP hand-off and the fail-closed reason.
The selftest prints `present NOT_TESTED; no frame claimed`: it never claims a
game frame.

`tests/test_eh27_frame_proof.py` re-derives the verdict from raw selftest
output (no trusted counts, no PASS claim, no frame claim) and checks that a
real captured BMP has more than one colour via `tools.frame_proof.bmp24`.

## Verification

- `dmd_throw_selftest` (built with the whole win32hle layer): 14 `ok` checks,
  0 `FAIL`, exit 0.
- `python3 -m pytest tests/test_eh27_frame_proof.py ...` targeted: 50 passed.
- Full pytest: 2271 passed, 34 skipped, 130 subtests passed (exit 0).
- Rebuilt `gr.exe` from `develop` with this runtime, run with
  `SDL_VIDEODRIVER=cocoa` and `RECOMP_SCREENSHOT`: see "Live result" below.

## Live result

Rebuilt from the task base `develop 0d7c66e` with this runtime:

```
python3 -m tools.autorecomp <gr.exe> --project-dir <temp> \
  --assets-dir <game folder> --input-os windows --input-arch x86 \
  --target-os host --target-arch host --out-mode full
```

Result: inspect/recover/translate/project/compile/run/verify/report all `ok`;
136 imports bound, 0 unresolved; native `gr` built (9,304,144 bytes). The
pipeline's own headless run exited 1 with no oracle. A second, explicit run
with `SDL_VIDEODRIVER=cocoa`, `SDL_AUDIODRIVER=dummy` and
`RECOMP_SCREENSHOT=<temp>/frames` (`MAX=3`, `EVERY=100`) exited 138 with:

```
ICALL: null target from 0x0040942C (NULL vtable/interface pointer; no dispatch possible)
ICALL: null target from 0x00409324 (NULL vtable/interface pointer; no dispatch possible)
ICALL: null target from 0x00419D38 (NULL vtable/interface pointer; no dispatch possible)
[win32hle] fatal signal 10 at 0x7001000003 (guest VA 0x01000003) in lifted function 0x00419D38
```

**Zero BMP frames were written.** No frame proof, no PASS, no closure.

## Exact blocker

The DMD table's `fptr` (`0x0040DBC0`) has **no live checkpoint** in the
tail-call-inlined path: the lifted `sub_0040DB9C` contains the inlined
`0x0040DBC0` body but no `RECOMP_EH_ENTER` landing pad. Selecting any of the
three live outer frames returns from the wrong guest function. A complete fix
therefore also needs the lifter to emit an EH checkpoint for the inlined catch
owner (or to resume at `fptr + retoffset` inside that frame); that change is
outside the four files in scope here. No PASS and no frame are claimed while
this remains true; issue #27 stays **OPEN**.
