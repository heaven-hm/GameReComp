# Architecture

This document describes what the `autorecomp` pipeline actually does, what it
produces, and where the boundaries of the current implementation are. It
describes measured behaviour, not intentions.

## Scope

RevEng-Studio takes a user-supplied Windows PC-game PE executable and its data
files, and produces:

1. a project tree containing generated C source for every recovered function,
2. a native executable built from exactly that tree,
3. reports tying both back to the input by hash.

No game binary or game asset is copied into this repository. The rebuilt
executable needs the user's own copy of the original executable at run time:
`./build/<name> /path/to/original.exe`.

The current backend translates **PE32 (i386) input only**. PE32+ is detected and
rejected fail-closed at the `inspect` stage; it is not silently mis-translated.

## Pipeline stages

The pipeline lives in `tools/autorecomp/pipeline.py` and is driven by
`tools/autorecomp/cli.py`. Each stage records its own status and evidence in
`reports/`; a failure stops dependent stages, but `verify` (when a run exists)
and `report` still execute so the failure is inspectable.

| Stage | Input | Work | Artifact |
|---|---|---|---|
| `inspect` | `PE` path | PE headers, sections, imports, IAT map, hashes; rejects non-PE32 | `reports/inspect.json` |
| `recover` | PE bytes + headers | Seed entry point/exports, scan call/jmp/prologue targets, recover pointer-only and alternate-entry functions, decode to instruction CFGs | `reports/functions.json` |
| `translate` | function set + IAT | Lift each function to register-transfer C, emit split source units, dispatch table and declarations | `src/recomp/gen/recomp_*.c`, `logs/translate.log` |
| `project` | lifted sources | Copy pinned runtime, write `main.c`, `CMakeLists.txt`, `README.md`, `project.json` | project tree |
| `compile` | project tree | `cmake` configure + build | `build/<name>`, `logs/compile.log` |
| `run` | rebuilt binary | Execute headless (`SDL_VIDEODRIVER=dummy`) against the original PE with a timeout; capture stdout/stderr/exit status | `reports/run.json`, `logs/run.*.log` |
| `verify` | run evidence (+ optional oracle JSON) | Classify the outcome; compare process observables if an oracle was supplied | `reports/verification.json` |
| `report` | all evidence | Manifest of generated sources, JSON + HTML report, pipeline score | `manifest.json`, `reports/report.json`, `reports/report.html` |

The score is weighted stage completion (inspect 10, recover 10, translate 10,
project 10, compile 20, run 15, verify 15, report 10). A `partial` verification
earns 8 of its 15; `not-run` and `launch-failed` earn 0. The score measures
pipeline completion for the recorded hash, not game equivalence.

## The source pipeline: evidence → name → generate → certify

The stages above produce the low-level fallback. Beside them, and separate on
purpose, runs a chain that turns a provider's decompilation into typed C and then
**proves** the result against the original's machine code. None of it is part of
the scored pipeline, because none of it is needed for a game to run — it exists to
make the delivered source readable and to say how much of it is verified.

| Step | Tool | Output |
|---|---|---|
| decompile | `tools/autorecomp/revng.py` | rev.ng's C for the whole PE, one function at a time |
| evidence | `tools/autorecomp/rename_evidence.py` | per-identity records: imports called, strings referenced, format strings, constants, global and struct-field accesses with widths, the calling convention from the function's own epilogue |
| name | `rename_evidence.name_all` | three counts — `named_semantic`, `named_structured`, `no_evidence` — with every name mapped back to the records behind it |
| generate | `tools/autorecomp/rename_gen.py` | typed C: guest accesses through `MEMn`, `Model.classify()` at every boundary, memory fused into locals, plus a guest-ABI adapter |
| certify | `tools/lift/difftest.py --project --source` | a verdict per address, from the original's bytes under Unicorn |

Two properties of this chain matter more than its stages.

**The provider is an input, not an authority.** rev.ng emits `undef()` where it
could not determine a value, empty bodies where it could not decompile, and — as
measured — coherent-looking C for bytes that are not code. Every one of those is a
refusal here, not something to reconcile. The frame loop's core, `0x00481BB0`, is
refused with 180 `undef()` values in its body; the low-level fallback carries it.

**The certification is evidence, not proof.** `diff()` compares a named set of
observables and the verdict says which. A generated module is plain C, so it models
its return value and the memory it writes — not the guest's other registers, and not
its flags. `reports/retracted/` holds the case that justifies the caution: two
modules the oracle still certifies that the project refuses, because the oracle
compares **values** and cannot see that a memory operand was a host local's value
rather than a guest address. The retraction was made by a rule; no amount of oracle
running would have caught it.

The verdicts, and what each one claims:

| verdict | claim |
|---|---|
| `agree` | `eax` and the bytes of every absolute guest address the body names match on the entry states driven |
| `agree-return` | the return value matches, for a function the original's bytes show writes no guest memory and calls nothing |
| `agree-memory` | the named memory matches; `eax` is not comparable because the provider recovered the function as returning nothing |
| `agree-frame` | the guest stack frame was compared byte for byte, in addition to the above |
| `nothing-compared` | no observable was compared — never a pass |
| `diverge` | a real difference in the compared state |
| `original-faulted` | the reference machine could not be driven from the seeded state |
| `harness-failed` | the comparison could not be built or run |

`reports/certified/` carries every certified address with its generated module and
its verdict JSON, so the published count can be reconstructed rather than believed.

## Function recovery


`discover_functions()` in `tools/lift/translator.py` seeds the PE entry point
and every export, then runs the static scanner in `tools/disasm` plus the
pointer/thunk recovery pass in `tools/lift/recover.py`. The scanner iterates
until no new targets appear; the recovery pass finds functions reachable only
through data pointers and alternate entry points inside known bodies. Every
recovered function keeps its original address; the generated symbol is
`sub_XXXXXXXX`.

## Translation

`tools/lift/lift32.py` emits one C function per original function. The body is
register-transfer C: guest registers are variables, guest memory is addressed
through the based guest mapping (`ADDR(va)`), and control flow is a dispatch
loop over basic blocks. Imports are calls through the IAT map into the runtime
shims.

This is deliberately labelled **`LOW_LEVEL_FALLBACK`** in
`reports/functions.json`. It is compilable, inspectable and addressable, but it
is not reconstructed high-level source, and the pipeline does not claim
otherwise. A function that the lifter cannot translate is emitted as an
`UNRESOLVED` stub that prints and aborts when called; there is no silent
success path.

## Generated project

`tools/autorecomp/projectgen.py` writes a self-contained CMake project:

```
<project>/
  CMakeLists.txt          C11, Release default, pkg-config for SDL2/SDL2_ttf
  README.md               how to build and how to run
  project.json            provenance: input hash, backend, runtime, categories
  src/main.c              calls recomp_host_main()
  src/recomp/gen/         recomp_XXXX.c, recomp_dispatch.c, recomp_funcs.h
  src/runtime/win32hle/   pinned copy of the runtime sources
  src/runtime/recomp32/   recomp_types.h
```

The runtime copy means the delivered tree builds without this repository. The
CMake glob rebuilds its file list on configure, so a re-run that produces fewer
or more units does not leave stale objects; the pipeline additionally deletes
`src/recomp/gen/` before translating.

## Runtime

`runtime/win32hle` answers the guest's imports itself on POSIX/libc + SDL2:

- **Guest memory**: a 32-bit address space mapped on the host (flat on a 32-bit
  host, a single based mapping on 64-bit hosts). `hle_ptr`/`hle_va` are the
  only conversions; pointers outside the mapping stop the run by name instead
  of being truncated.
- **Shim ABI**: every import is a `recomp_func_t`; stdcall arguments are read
  from the guest stack with `A32`/`APTR`/`ASTR` and returns pop the frame with
  `RET`/`RETP`. Native-to-guest calls (`WndProc`, thread starts, callbacks) are
  explicit `hle_call_guest()` calls — no executable trampolines.
- **PE loading**: the original PE is mapped at its preferred base, relocated if
  needed, and its IAT is bound by name or ordinal. Imports the runtime does not
  implement are reported and counted; the rebuilt program exits with code 2
  ("boot-failed") instead of pretending to run.
- **Win32 surface**: kernel32/CRT, user32 windows and controls, GDI DIBs and
  TrueType text, DirectDraw in software, DirectSound on SDL2 audio, winmm,
  winsock, OLE storage, and Bink/Miles decoding when FFmpeg is present.
- **Presentation**: SDL2 window with scaling options; headless runs use the SDL
  dummy drivers.

The runtime is pinned by copy at project-generation time. `project.json`
records every copied file.

## Verification model

`verify` classifies the run as one of:

`not-run`, `launch-failed`, `trace-diverged`, `partial`, `scenario-pass`.

With no oracle, a process that launches and exits (or survives to timeout) is
`partial`; the report lists the oracle gaps (no original-Windows reference, no
frame/input/audio comparison). With an oracle (`--oracle` JSON of expected exit
code and stdout/stderr substrings), a mismatch is `trace-diverged`, a match is
`scenario-pass` scoped to process observables. Crashes and boot failures are
`launch-failed`. The process comparison is never treated as proof of gameplay
equivalence.

## Reproducibility

- `inspect` records SHA-256, MD5 and SHA-1 of the input.
- `compile` records the SHA-256 of the rebuilt binary.
- `manifest.json` hashes every generated source file and the source digest.
- `project.json` records the generator, backend and runtime copies.
- `logs/` keeps translation, compile and run output for each stage.

Given the same input hash, toolchain and dependency versions, the pipeline is
deterministic apart from timestamps and absolute paths.

## Known limitations

- PE32+ input is rejected; there is no x86-64 translation path yet.
- All generated functions are `LOW_LEVEL_FALLBACK`; there is no high-level C
  provider integration yet.
- Verification is process-level unless an external oracle and scenario driver
  supply more; there is no frame or audio comparison.
- The runtime implements what the corpus exercises; unimplemented imports fail
  the boot loudly rather than returning fabricated success.
- macOS hosts on releases older than the Homebrew freetype bottle's build
  target must rebuild freetype from source, or every binary that links
  SDL2_ttf aborts in dyld. This is an environment issue, not a runtime bug; see
  `docs/RUNTIME_COMPAT.md`.
