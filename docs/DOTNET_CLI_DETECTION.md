# .NET / CLI: detection only, no recompiler

Status: **DEFERRED — UNSUPPORTED_NOT_TESTED**. This change adds a fail-closed
*detector* and a compatibility-matrix row. It does not translate IL, does not
compile a managed assembly, and does not claim that any managed program runs.

Authoritative format reference: [ECMA-335, Common Language Infrastructure,
6th edition, June 2012](https://ecma-international.org/publications-and-standards/standards/ecma-335/)
— Partition II (metadata physical layout) and Partition III (CIL instruction
set). The detector reads II.25.3 structures (CLI header and metadata root); it
does not implement Partition III.

## 1. Open-source IL → C / native toolchain survey (2026-11-24)

Licenses were checked against the repositories' own license files and the
GitHub API (`repos/<owner>/<name>` → `license.spdx_id`), not inferred from a
blog post.

| Project | Exact URL | License | What it actually is / why it is not wired in |
| --- | --- | --- | --- |
| IL2C | https://github.com/kekyo/IL2C (license: https://github.com/kekyo/IL2C/blob/master/LICENSE) | **Apache-2.0** ("Under Apache v2" in README) | Translates ECMA-335 CIL to human-readable C99, with its own runtime (`setjmp`/`longjmp`, signals). Closest match to this project's C backend, but upstream describes itself as **experimental**; the [supported-opcodes list](https://github.com/kekyo/IL2C/blob/master/docs/supported-opcodes.md) covers only part of CIL. No evidence it handles the .NET Framework 2.x/4.x assemblies found on this host. Adopting it means importing a second runtime, not just a translator. |
| .NET NativeAOT (in dotnet/runtime) | https://github.com/dotnet/runtime/tree/main/src/coreclr/nativeaot (license: https://github.com/dotnet/runtime/blob/main/LICENSE.TXT) | **MIT** (`dotnet/runtime` SPDX `MIT`) | Ahead-of-time native compilation for current .NET. Produces a runtime-dependent native binary, not standalone C that this project's PE32 backend can lift. Needs the .NET SDK/build graph and a supported target framework; not a drop-in for arbitrary legacy managed PE files. |
| CoreRT | https://github.com/dotnet/corert (license: https://github.com/dotnet/corert/blob/master/LICENSE.TXT) | **MIT** | Experimental .NET Native AOT prototype; GitHub API reports `archived: true`. Historical reference only — no maintained toolchain to build from. |
| Mono AOT | https://github.com/mono/mono (license: https://github.com/mono/mono/blob/main/LICENSE), docs: https://www.mono-project.com/docs/advanced/aot/ | **primarily MIT**; the repo bundles third-party code under BSD, MS-PL, MPL-1.1, GPL (build-time only) and CC-BY-4.0 (docs), so GitHub reports `NOASSERTION` | `mono --aot` emits a `program.exe.so` that is loaded *by the Mono runtime* — a hosted artifact, not a standalone C/C++ translation. Full AOT is limited to x86/AMD64/ARM and still requires the runtime and its class libraries. |
| ILSpy | https://github.com/icsharpcode/ILSpy | MIT | Decompiler (IL → C#), useful as an *inspection* reference; it is not a recompiler. |
| Mono.Cecil | https://github.com/jbevain/cecil | MIT | Managed metadata/IL reader-writer; a reference for a future metadata parser, not a native backend. |
| dnlib | https://github.com/0xd4d/dnlib | MIT | Managed metadata/IL reader-writer; same caveat as Cecil. |
| Unity IL2CPP | https://docs.unity3d.com/Manual/IL2CPP.html | **proprietary** (Unity Engine; no source license to reuse) | IL → C++ → native inside Unity's build pipeline. Not available as a redistributable open-source compiler. |
| .NET Native (Microsoft UWP) | https://learn.microsoft.com/en-us/dotnet/framework/net-native/ | **proprietary** | Windows/UWP-specific closed toolchain; not an option here. |

Conclusion: permissive licenses exist, but every open-source option changes the
product (a second runtime or a .NET SDK build graph). None is integrated. This
is a scope decision, not an unexamined gap.

## 2. Host scan: real managed assemblies

Command (deterministic, no timestamps):

```
python3 -m tools.pe.cli_metadata ~/Downloads/recomp-apps \
    --only-detections --quiet --json reports/dotnet/cli_detection.json
```

Scope: 2,506 files with PE-like suffixes under `~/Downloads/recomp-apps`.
Outcome counts: `557 NOT_CLI`, `1,941 NOT_PE`, `4 REJECTED_MALFORMED`,
`4 UNSUPPORTED_NOT_TESTED`. Full evidence (including rejected files and
per-file SHA-256) is committed at
[`reports/dotnet/cli_detection.json`](../reports/dotnet/cli_detection.json).
Paths in that report are host-local; hashes are the stable identity.

| File (under `~/Downloads/recomp-apps/`) | SHA-256 | PE | COR20 runtime | metadata version | COR20 flags | entry token | streams |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `luhn/luhn.exe` | `61069ebfe79053477e3859075d0a9c800d60d755a795a28ad069efc1d851b304` | PE32 i386 | 2.5 | `v4.0.30319` | ILONLY (0x1) | `0x0600000D` | #~ #Strings #US #GUID #Blob |
| `luhn/luhn.dll` | `e30a028e8db474b8a3b17f7088efb8054b818f08b1e30011a50112480d5be353` | PE32 i386 | 2.5 | `v4.0.30319` | ILONLY (0x1) | `0x06000001` | #~ #Strings #US #GUID #Blob |
| `tictactoe/TicTacToe.exe` | `df4aec7299b1e002ca0e1008b20a889e0cfe5b482a19958399fab16ac6090402` | PE32 i386 | 2.5 | `v4.0.30319` | ILONLY, 32BITREQUIRED, 32BITPREFERRED (0x20003) | `0x06000023` | #~ #Strings #US #GUID #Blob |
| `windows/x86/games/tictactoe-net.exe` | `df4aec7299b1e002ca0e1008b20a889e0cfe5b482a19958399fab16ac6090402` | PE32 i386 | 2.5 | `v4.0.30319` | same as above | `0x06000023` | #~ #Strings #US #GUID #Blob |

The last two are byte-identical (same SHA-256): 3 distinct managed binaries, 4
paths. `COR20.MajorRuntimeVersion/MinorRuntimeVersion = 2.5` is the CLI header
version, while the metadata root string is `v4.0.30319` — both are reported
verbatim rather than collapsed into one "framework version" guess.

Four files under `games/opentyrian/data/` were **rejected**, not classified as
managed: `netipx.exe` (`e92b1f1a57c4`), `netmodem.exe` (`24329b1c3278`),
`order.exe` (`3648ae05c996`) and `rtm.exe` (`10acd85af376`) all have an
`e_lfanew` outside the file. They are data files with an `.exe` suffix.

This matches the legacy ledger in [`docs/MIGRATION.md`](MIGRATION.md):
`TicTacToe.exe` and `luhn.exe` are managed, so there is no machine code to lift.

## 3. Parser output (excerpt)

From `TicTacToe.exe` (full object in the committed report):

```json
{
  "path": ".../tictactoe/TicTacToe.exe",
  "sha256": "df4aec7299b1e002ca0e1008b20a889e0cfe5b482a19958399fab16ac6090402",
  "is_pe": true, "is_managed": true, "pe_type": "PE32", "machine": 332,
  "status": "UNSUPPORTED_NOT_TESTED",
  "reason": "managed CLI assembly detected; no IL recompiler is implemented here, so nothing was built or run",
  "cor20": {
    "cb": 72, "major_runtime_version": 2, "minor_runtime_version": 5,
    "flags": 131075, "flag_names": ["ILONLY", "32BITREQUIRED", "32BITPREFERRED"],
    "entrypoint_token": 100663331,
    "metadata": { "runtime_version": "v4.0.30319", "size": 9228,
      "streams": [ {"name": "#~"}, {"name": "#Strings"}, {"name": "#US"},
                   {"name": "#GUID"}, {"name": "#Blob"} ] }
  },
  "recompile_attempted": false,
  "claim": "detection only; no IL-to-C or IL-to-native translation implemented"
}
```

Every field is bounds-checked against the file and the metadata root; a
damaged header becomes `REJECTED_MALFORMED` with a reason, never a guess and
never a successful verdict.

## 4. Integration and tests

* `tools/pe/cli_metadata.py` — detector and CLI (structured JSON, no timestamps).
* `tools/pe/gen_cli_fixture.py` — deterministic PE32 + COR20 + BSJB fixture with
  malformed variants; the "good" fixture SHA-256 is
  `5d829946730796c343c4c860ced3cdcde26eb67fefa7d33bb03f10ecb4e4cca4`.
* `tools/autorecomp/pipeline.py` — `stage_inspect` now runs the detector before
  lifting. A managed or malformed CLI image stops the pipeline with
  `UNSUPPORTED_NOT_TESTED` and writes `reports/cli_detection.json`; it never
  reaches `stage_recover`/`stage_translate` and cannot produce a fake build.
* `docs/TARGETS.md` — compatibility matrix row for managed CLI images.
* `tests/test_cli_metadata.py` — 10 tests covering the fixture parse,
  deterministic JSON, all six malformed variants, native/non-PE handling,
  random corruption, I/O errors, the pipeline gate and the real host
  assemblies (skipped when the corpus is absent).

```
python3 -m unittest tests.test_cli_metadata -v     # 10 tests, all passing here
```

## 5. Honest blockers (why .NET stays DEFERRED)

1. **No IL decoder in this repository.** `lift32` decodes x86; it would read the
   PE32 loader stub of a managed EXE and produce C from a few instructions that
   are not the program. This is exactly the false success the gate prevents.
2. **No `--` tables decoder.** The parser verifies stream presence/bounds but
   does not resolve `#~` tables (MethodDef, TypeDef, MemberRef, ...) — required
   before any IL body or token can be located.
3. **No CLR semantics.** Exceptions, GC, generics, reflection, virtual dispatch
   and the .NET class libraries are not implemented by `runtime/win32hle`.
   NativeAOT/Mono AOT solve this by shipping a CLR/runtime; IL2C ships its own
   runtime. Importing one is a separate, much larger decision with its own
   license and maintenance surface.
4. **No verification oracle for managed code.** Even a successful translation
   would need a behavioural comparison against a CLR host, which this repo does
   not have (and no .NET runtime is assumed on the build host).
5. **Host evidence is limited.** Only four managed paths were found, all
   CLR-header 2.5 / metadata v4.0.30319; no conclusion is drawn about other
   framework versions, mixed-mode assemblies or ReadyToRun images.

"UNSUPPORTED_NOT_TESTED" means the format is recognised and deliberately not
implemented. It is not a PASS, not a FAIL, and not a recompile.
