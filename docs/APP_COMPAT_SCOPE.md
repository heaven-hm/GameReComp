# Windows application compatibility scope — measured, not 100%

`tools/app_compat_probe` classifies a binary from its own headers and emits a
fail-closed verdict. It deliberately never says "works". Its highest verdict is
`SUPPORTED_CANDIDATE`, defined as "PE32 x86 native, structurally eligible for
the pipeline" — import binding, lift correctness, graphics, audio and gameplay
still need per-app evidence.

## Classes actually observed

| Class | Verdict | Reason (verbatim from the probe) |
|---|---|---|
| PE32 / x86 native (`dxIka.exe`, `igi.exe`) | `SUPPORTED_CANDIDATE` | "structurally eligible only: import binding, lift accuracy, graphics/audio and gameplay still require per-app evidence" |
| PE32+ / x64 (`opentyrian.exe`) | `UNSUPPORTED` | "x64 is explicitly refused by the pipeline (issue #13 DEFERRED); no x64 loader/lifter/ABI" |
| PE32 / .NET (CLR data directory present) | `UNSUPPORTED` | "managed CLR assembly: no native Win32 import table to reconstruct" |
| Win16 / NE | `UNSUPPORTED` | "Win16/NE is roadmap-DEFERRED (issue #8), not supported" |
| MZ without PE, scripts, MSI archives | `UNSUPPORTED` | "not a DOS/PE executable (no MZ magic)" or no PE header |

Real output is in `test-results/app-compat-probe.json`; unit tests cover all
five classes with synthetic headers.

## Why "100% of all Windows apps" cannot be proven

1. The pipeline accepts exactly one binary class; four common classes are
   refused by design and covered by regression tests.
2. Even in the accepted class, real games currently have unbound imports,
   blank frames, exceptions or SIGBUS — see `docs/ALL_GAMES_MACOS_EVIDENCE.md`
   and `docs/PROOF_EVIDENCE.md`.
3. There is no Linux runner and no original-Windows oracle here.
4. Proprietary applications (IDM, Winamp, HyperCam2) cannot legally be
   downloaded or redistributed for testing; substitutes must be official
   open-licence binaries.

## What would be required for a broader claim

A loader/lifter/host ABI for each additional class, a legal corpus per class,
per-app A0–A10 evidence on both target OSes, and an original-Windows frame and
memory oracle. Until then the honest statement is: **one structurally eligible
class, partial measured coverage, multiple explicit refusals**.
