# Winamp companion-DLL null-target investigation (evidence only)

**Verdict: FAIL — no Winamp frame was produced.** The reported
"NULL vtable/interface pointer" was diagnosed and fixed in the *generated
project's* runtime copy; the run then advanced to a different, deeper blocker
that cannot be fixed in the generated runtime alone.

## Inputs

- Real Windows executable extracted from the Winamp NSIS installer:
  `/Users/haseeb-mir/winamp-payload/winamp.exe`
  (PE32 i386, 735 imports, SHA-256
  `addf561fcdc496c1318ddc3586352aa7f6c1feb684a9e8ffa285409beac5b446`).
- Regenerated standalone project:
  `/Users/haseeb-mir/winamp-companion-proof/winamp-companion-src`
  (`build-fast/winamp`, native macOS/arm64 build, 9,633 lifted functions,
  companions `nsutil.dll` and `tataki.dll` embedded).

## Root cause of `ICALL: null target from 0x10063DCC`

It is **not** a tataki.dll C++ vtable, and tataki.dll was never the crashing
module. Baseline logs (`logs/baseline-strict.log`,
`logs/baseline-permissive.log`):

```
[pe] <embedded image> mapped at 0x10000000   (nsutil.dll)
[pe] <embedded image> mapped at 0x7EFD0000   (tataki.dll)
[pe] unresolved import KERNEL32.dll!DisableThreadLibraryCalls
[pe] companion nsutil.dll: 6 unresolved imports
ICALL: null target from 0x10063DCC (NULL vtable/interface pointer; no dispatch possible)
[win32hle] fatal signal 10 ... in lifted function 0x10063BDB
```

`map_companions()` maps *both* DLLs first, then binds and runs DllMain for each
in order. Only the nsutil line appears because the process died inside
**nsutil.dll's DllMain (entry 0x10063D11)**, before tataki.dll's DllMain was
reached.

Chain of evidence:

- nsutil.dll's KERNEL32 import descriptor has `FirstThunk = 0x65000`; the 5th
  slot is `0x10065010 = KERNEL32.dll!DisableThreadLibraryCalls`.
- The runtime had no shim for that name, so the IAT binder wrote `0` into the
  slot.
- Lifted `sub_10063BDB` calls `sub_10063DCC`, which executes
  `RECOMP_ICALL(MEM32(0x10065010))` (generated
  `src/recomp/gen/recomp_0014.c`, guest address `0x10063DE1`).
- The dispatch saw target `0`, printed the null-target diagnostic, and the
  guest faulted in `sub_10063BDB`.

tataki.dll itself maps correctly at its rebased preferred base `0x7EFD0000`,
exports 332 functions (including `RegionServerI` and `SkinBitmap`), and its
DllMain runs once the nsutil crash is removed. Its earlier absence from the log
was a sequencing artifact, not a mapping/export-binding failure.

## Fix applied to the generated project runtime only

Patches are in `patches/` (unified diffs against the pinned runtime in
`runtime/win32hle/`); the shared repository was **not** modified.

1. `kernel32.c`: `DisableThreadLibraryCalls` shim (returns TRUE, cdecl/stdcall
   one argument). This removes the null IAT slot and the original SIGBUS.
2. Further bring-up shims required to get the guest past startup:
   `GlobalAddAtomW`, Winamp's VC142 `_Mtx*`/`_Cnd*` thread primitives,
   condition-variable calls, `_fsopen`, `?_Syserror_map@std@@YAPBDH@Z`,
   `Path*W`, `CharNextW`/`CharPrevW`, `GetTempFileNameW`, `CopyFileW`,
   `SetDllDirectoryW`, `GetPrivateProfile*W`, `RegisterWindowMessageW`,
   `FindWindowW`, `LoadStringW`, `StrToIntExW`, `CreateSemaphoreW`, etc.
3. `msvcrt.c`: map `APPDATA`/`LOCALAPPDATA`/`USERPROFILE` to the guest's `C:\`
   (its launch/assets directory, no host user name hard-coded). Without this,
   `getenv("APPDATA")` returned 0, Winamp's logger built an empty path and
   threw an uncaught `spdlog_ex`.
4. `user32.c`: bind `MsgWaitForMultipleObjectsEx` and `PostThreadMessageW` to
   the existing real Win32 message-queue implementations, so the UI guest
   thread actually gets machine time (a busy-return stub made Winamp exit
   before the window was ready).

Build result (`logs/final-build.log`): clean rebuild of `build-fast/winamp`.

## Honest run result

`logs/final-permissive.log`:

```
[pe] companion nsutil.dll: 5 unresolved imports
[pe] companion nsutil.dll mapped at 0x10000000 (33 exports)
[pe] companion tataki.dll: 20 unresolved imports
[pe] companion tataki.dll mapped at 0x7EFD0000 (332 exports)
[pe] bound 735 imports (56 delay), 0 unresolved
[host] C:\winamp.exe booted: entry 0x004BB7D9, 0 unresolved imports
...
[module] C:\System\wasabi2.w5s: 28 unresolved imports
[module] LoadLibrary("C:\System\wasabi2.w5s") -> 0x119B0000
ICALL: unresolved VA 0x119B2887 from 0x004A1C13
[win32hle] fatal signal 10 ... in lifted function 0x004311B0
```

Process exit: SIGBUS (`-10`), no window. Strict mode exits `2` with 197
unresolved imports (`logs/final-strict.log`). The CUA desktop capture
(`desktop-no-winamp.jpg`, taken with a `scope=desktop` CUA session) shows only
the GameReComp app and the desktop — **no Winamp frame exists**, so this is not
a PASS claim.

## Remaining blocker (not fixable in the generated runtime alone)

`0x119B2887` is `wasabi2.w5s + 0x2887`, the sole export
`GetWinamp5SystemComponent` of `System\wasabi2.w5s`. Winamp loads that Wasabi
component through `LoadLibrary` at run time. The autorecomp companion
discovery (`tools/autorecomp/pipeline.py::_find_companion_dlls`) only follows
the *static* import graph of `winamp.exe`, so `Plugins/*.dll` and
`System/*.w5s` are mapped as raw bytes with **no lifted functions**. Calls
through their exported/vtable pointers cannot dispatch to generated C.

To go further the pipeline must reconstruct dynamically loaded DLLs as
companions (or supply a host-side implementation of the Wasabi component ABI)
and regenerate the project. That is a generator/shared-repo change, outside
the scope of a generated-runtime-only fix.

## Reproduction

```sh
GEN=/Users/haseeb-mir/winamp-companion-proof/winamp-companion-src
cmake --build "$GEN/build-fast" --parallel 4
RECOMP_PERMISSIVE=1 "$GEN/build-fast/winamp" /Users/haseeb-mir/winamp-payload
```
