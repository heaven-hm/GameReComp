# Agent P2 — IGI "Invalid texture format" evidence

All evidence here comes from the real files: `~/Downloads/IGI/igi.exe`, the real
assets in that folder, a real GameReComp native rebuild, a real Highball Wine
run, and real Unicorn execution of the retail x86 bytes. No synthetic success.

| file | what it is |
| --- | --- |
| `evidence.json` | structured summary; statuses, commands, what is and is not claimed |
| `unicorn-format-check.json` | Unicorn harness output over the retail bytes (20 enumerated formats) |
| `native-baseline.log` | rebuilt native binary BEFORE the fix: `63488 bpp` -> `Invalid texture format` -> `ExitProcess(1)` |
| `native-fixed.log` | same after the fix: `16 bpp`, continues rendering past the check |
| `highball-level5-reference.log` | `highball run --verbose igi.exe window level5` original reference |
| `frida-blocked.json`, `frida-*.txt` | exact Frida BLOCKED reasons (SIP / task_for_pid), no claim of a Frida result |
| `selftests.log` | 53 `ok` checks from the three DirectDraw descriptor selftests |
| `run_desc_selftests.sh` | reproducible command used for that log |

Screenshots: none attached. A `screencapture` attempt on this shared desktop
captured another agent's terminal window, not the game; the Wine game window is
not exposed through macOS accessibility. Pixel-for-pixel frame equality is
therefore **not** claimed.
