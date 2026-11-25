# x86 / x64 architecture support — feature/linux-to-windows

Based on `develop` (`f9982ef`). Status here is measured; unsupported means BLOCKED.

| Architecture | Status | Measured evidence |
|---|---|---|
| **x86 (PE32 i386)** | x86 output: i686-w64-mingw32-gcc PE32 (SHA 70588132870a…) built from Linux-generated C, Highball exit 0 with stdout b6674fd6… |
| **x64 (PE32+ x86-64)** | x64 output: x86_64-w64-mingw32-gcc PE32+ (SHA ee7461e1d873…) built from same source, Highball exit 0, identical stdout (LINUX_PROOF/linux-to-windows-64.json). |

## Honest limit

Architecture support is not a claim of gameplay equivalence. All PASS results
are process-level console scenarios (identical stdout and exit code against the
original Windows executable under Highball) unless explicitly labelled frames.
