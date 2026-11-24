# Windows x64 corpus evidence

Date: 2026-11-23. One official PE32+/AMD64 game binary was downloaded outside
the repository (no commits) and offered to the pipeline. It was refused.

## Source URL

- Release page: `https://cdn.openttd.org/openttd-releases/15.3/`
- Exact download: `https://cdn.openttd.org/openttd-releases/15.3/openttd-15.3-windows-win64.zip`
- Project: OpenTTD 15.3 (open-source transport simulation game), official CDN.
- Local path (outside repo): `/private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/newgames-x64/openttd-15.3-win64/openttd-15.3-windows-win64/openttd.exe`

## Licence

GNU General Public License version 2.0 (GPL-2.0), per `README.md` and
`COPYING.md` bundled in the official archive: "OpenTTD is licensed under the
GNU General Public License version 2.0." Some 3rd-party components carry their
own licences (see the bundled README).

## Hash

- SHA-256 of `openttd.exe`:
  `360f615cb74cafcedf0486398a396577e8f1470e0f8158f66b7e29557fdb711d`
- SHA-256 of the downloaded ZIP:
  `61c0a6a43d81008c7ff4330fb56351bbf66a980cdad041f1d0e08b51f2eeb34c`
- Size of `openttd.exe`: 17,912,840 bytes.
- `file`: `PE32+ executable (GUI) x86-64, for MS Windows`.
- Header check: COFF `Machine = 0x8664` (AMD64), optional-header
  `Magic = 0x020B` (PE32+); both verified from the binary's bytes.

## Command

```
python3 -m tools.autorecomp /private/var/folders/q0/rj0k1xgn28x14thfz4gnbm5c0000gn/T/opencode/newgames-x64/openttd-15.3-win64/openttd-15.3-windows-win64/openttd.exe --input-os windows --input-arch auto --target-os host --target-arch host --out-mode source-only --no-table --no-progress
```

## Verbatim output

stdout (empty):

```
```

stderr:

```
error: unsupported input architecture: x86_64 (this pipeline reconstructs PE32 x86 guests; a 64-bit build of the same game, if one exists, would be a different binary to reconstruct -- 64-bit guest support is planned, not implemented)
```

Exit code: `4` (`EXIT_UNSUPPORTED`). No project directory, source output, or
report was produced.

## Roadmap decision

Issue #13 (PE32+ / x64 pipeline and native host ABI) remains **DEFERRED** under
the roadmap: no production x64 work, and no x64 implementation was added here.
This download is corpus/verification evidence only; the x64 game is NOT RUN and
NOT TESTED — the pipeline refuses it by design rather than misreading it.
