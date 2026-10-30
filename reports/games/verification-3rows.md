# Independent verification of three published rows

Verified commit: `85ef6a36c2e5bcfa2e2527a2b885ee37cfb3e307`
Runtime frozen from that commit (`host.c` sha256 `b9790ecc9c281cab5514b690bc896c709c0c3bde40052008cc09e11d61371f67`),
copied into each project, project rebuilt, then launched permissively
(`RECOMP_PERMISSIVE=1`) and strictly, with `RECOMP_TRACE=1` for the profile.
Raw numbers: `reports/games/verification-3rows.json`.

| row | published | re-measured | agrees |
|-----|-----------|-------------|--------|
| Doukutsu | `timeout`, 12.0 s, score 93, partial | `timeout`, still running at 15 s, strict 143 bound / 0 unresolved | status yes, interpretation no |
| OrgView | `exited`, exit 0, 0.4 s, 122 bound / 0 unresolved | `exited`, exit 0, strict 122 bound / 0 unresolved | yes, exactly |
| gunroar (`gr`) | `crashed`, exit -10, 1.0 s, 136 bound / 0 unresolved | `crashed` SIGBUS, exit -10, strict 136 bound / 0 unresolved | yes, exactly |

## Doukutsu: it loops, but once per frame it calls a NULL interface

The published row's own log (`projects/Doukutsu/logs/run.stderr.log`) is
7,017 lines, of which **7,005 are ICALL failures** and 0 are import
dispatches (`import_trace_enabled` is false, `import_calls_total` 0,
`import_profile` empty — the sweep did not trace this run).

```
[pe] bound 143 imports, 0 unresolved
[host] ... booted: entry 0x00481D27, 0 unresolved imports
[ddraw] SetDisplayMode(640x480x16)
[ddraw] CreateSurface 640x480 16 bpp caps 0xA00 primary -> 0x100058A0
[dsound] dummy, 44100 Hz
[guest] DeviceGUID = 6f1d2b61
ICALL: unresolved VA 0x00000080 from 0x004120F0     x7004
```

One 15 s traced run of the frozen-commit rebuild:

| | count |
|---|---|
| import dispatches | 372,183 |
| `ICALL: unresolved VA 0x00000080` | 8,528 |
| `IDirectDrawSurface::Blt` | 195,535 |
| `GetLastError` / `TlsGetValue` / `SetLastError` | 35,871 / 35,863 / 35,863 |
| `IDirectDrawSurface::IsLost` | 22,307 |
| `GetTickCount` / `PeekMessageA` / `IDirectInputDevice::GetDeviceState` | 8,556 / 8,529 / 8,528 |

`GetTickCount`, `PeekMessageA`, `GetDeviceState` and the ICALL all occur
~8,528 times: the frame loop is real (about 569 frames/s, 23 blits per
frame, message pump and input polling present), and **once per frame the
guest calls through a NULL interface pointer at offset 0x80**. Reproduced
identically with a real display driver (5,364 of 5,376 lines ICALL, same
two call sites), so it is not a headless artifact.

So the row is not a spin and not a healthy run: it is a running game whose
input path calls a NULL interface once per frame. `run_status: timeout`
is correct; the profile quoted alongside it was not in the published
artifact.

## Eight sweep rows are invalid for a different reason

The sweep ran while `tools/autorecomp/pipeline.py:250` referenced an
undefined `_ROOT`, so these rows are `score 30 / not-run` because the
translate stage raised, not because of runtime coverage:

`herocore gm7`, `ikachan`, `noiz2sa`, `parsec47`, `rrootage`,
`torus_trooper`, `tumiki_fighters`, `warning_forever`.

Fixed in `5a6d14b`; these eight rows need re-measuring before publication.
