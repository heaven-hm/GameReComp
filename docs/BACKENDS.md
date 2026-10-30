# Backends

`autorecomp` is assembled from backends that live in this repository. This file
records which backend does which job, what each one produces, and which
components are present but *not* wired into the production pipeline.

## Production pipeline

| Stage | Backend | Output |
|---|---|---|
| `inspect` | `tools/pe/pe_analyze.py` | headers, sections, imports/exports, IAT map, hashes; rejects non-PE32 |
| `recover` | `tools/disasm` scanner + `tools/lift/recover.py` | function inventory with addresses, CFGs, call edges, thunks |
| `translate` | `tools/lift/lift32.py` (+ `lift32_cpu.py`, `lift32_sse.py`, `eh32.py`) | register-transfer C per function, dispatch table, declarations |
| `project` | `tools/autorecomp/projectgen.py` | CMake project with the pinned `runtime/win32hle` copy |
| `compile` | host CMake/C toolchain | native executable built from exactly the generated tree |
| `run`/`verify` | `pipeline.py` process runner | process observables and a fail-closed status |

The lifter emits one C function per recovered address (`sub_XXXXXXXX`). Guest
registers are locals, guest memory is `ADDR(va)` through the based mapping, and
control flow is a per-function dispatch loop. This is `LOW_LEVEL_FALLBACK` by
definition; `docs/SOURCE_PROVENANCE.md` defines the categories and what it
would take to earn a high-level one.

### Why a PE32-only backend

`autorecomp` translates i386 today. The repository also contains 16-bit and
64-bit tooling (`lift16.py`, `lift64_cpu.py`, `generate64.py`, `disasm64.py`)
used by other workflows; none of it is reachable from the `autorecomp` CLI.
A PE32+ input is detected at `inspect` and rejected with the machine type in
the report rather than being fed to the wrong lifter.

### Runtime

`runtime/win32hle` is the production runtime: it answers imports itself on
libc/POSIX/SDL2, maps the original PE at its preferred base, binds the IAT, and
exits 2 when an import is unimplemented. It is pinned by copy into every
generated project. `runtime/win32hle/README.md` is its own reference.
`docs/RUNTIME_COMPAT.md` covers host requirements.

`runtime/native32` (forwarding imports to the real Win32 API on Windows) is a
separate runtime for a different host story; it is not used by `autorecomp`.

## Source providers (not part of the build path yet)

### rev.ng

`RevEng readability` looks for rev.ng via `REVNG_BIN`/`PATH` or a
`REVNG_DECOMPILE_CMD` template and records candidate `*.c` files under the
project's `readability/revng/`. Candidates are **not compiled into the
project**: there is no ABI bridge that lets a rev.ng function stand in for a
`recomp_func_t` at a recovered address, so the harness reports
`integration: not-implemented` instead of claiming high-level source.

rev.ng is the primary readability provider by design; when it is absent the
harness blocks and does not substitute another provider.

### Optional AI polish

`REVENG_AI_CMD` names a command that rewrites one generated source unit at a
time. Edits are only kept after a rebuild and a rerun reproduce the recorded
baseline evidence; otherwise the unit is restored byte-for-byte. An accepted
edit does not change the function's provenance category — readability and
provenance are tracked separately (see `docs/SOURCE_PROVENANCE.md`).

## Verification backends

- Process runner: records command, cwd, headless mode, timeout, exit code,
  boot line, unresolved imports and stdout/stderr (`reports/run.json`).
- Oracle comparison: optional JSON of expected exit code and stdout/stderr
  substrings (`--oracle`), scoped to process observables.
- Readability harness: re-runs the recorded command and compares exit code,
  timeout flag and fatal markers before accepting an edit.

No backend in this repository runs the original Windows executable or
compares frames/audio; `docs/VERIFICATION.md` lists what a stronger oracle
would require.

## Explicit non-delegation

The rebuilt executable contains the lifted program and this runtime. Nothing
in the build path shells out to Wine, an emulator, or the original executable
to answer guest code. The original PE supplies the guest image, imports and
resources as data, which is why the rebuilt binary requires the user's copy at
run time.
