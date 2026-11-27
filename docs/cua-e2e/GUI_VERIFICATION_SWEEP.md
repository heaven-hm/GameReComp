# GUI-driven verification sweep (Unicorn + Frida)

Every percentage below was produced by clicking the project's **Verify** button in the
GUI (`WorkerKind::Verify` → `tools.autorecomp.verify`), then reading the resulting
`reports/verify.json` and the GUI KPI. Same-input run parity is recorded by the Frida
side; on this macOS host Frida attach is denied by the OS for every spawned process,
so run parity is honestly 0% / BLOCKED everywhere.

| Project (GUI click) | Source % (Unicorn) | Run % (Frida) | Evidence |
| --- | --- | --- | --- |
| `pe32_minimal` | 0.0 (1 check blocked) | 0.0 (attach denied) | `allverify-fixture-result.png` |
| `winamp` | **100.0** | 0.0 (attach denied) | `allverify-winamp-100.png` |
| `Project_IGI` | 0.0 (2 checks blocked, non-code-section VAs) | 0.0 (attach denied) | `allverify-projectigi-0.png` |
| `IGI` | **75.0** (3 agree, 1 original-side fault) | 0.0 (attach denied) | `allverify-igi-75.png` |
| `TmForever` | 0.0 (2 checks blocked) | 0.0 (attach denied) | `allverify-tm-final.png` |
| `ac_client` (AssaultCube) | pending rebuild (compile deadline raised) | — | sidebar `ac_client` in `allverify-home.png` |

A blocked check is not a pass. The GUI shows the measured number and the exact reason
in the Verification detail panel. The Frida engine can produce a real run percentage
only where it can attach; a Linux/Wine retry was attempted separately.
