# CUA GUI button sweep — real clicks, no simulated PASS

Driver: `cua-driver 0.17.0`, session `gui-e2e`, target `target/debug/game-recomp-gui`
built from this source tree, project `pe32_min_fixture` (tested PE32 i386 fixture).
Every click used `delivery_mode: "foreground"` with a fresh window snapshot; the
screenshots below are the driver's own captures.

| Button / control | Result | Evidence |
| --- | --- | --- |
| Sidebar project `pe32_minimal` | Opens project overview | `cua-project2.png` |
| Tabs Overview / Functions / Stages & Build / Agents / Frames / Settings | All switch, no crash | `cua-tab-*.png` |
| Header **Run pipeline** | Completed: 93/100, post-launch banner appeared | `cua-run-pipeline.png` |
| Banner **Verify (Unicorn + Frida)** | Job ran; report `reports/verify.json` written with 0% source (1 blocked) and 0% run (Frida attach denied) | `cua-verify-button.png` |
| Agents tab **Rev.ng Source Pass** | First run failed rc=4 (Colima daemon stopped). Wrapper now restarts Colima; second CUA click succeeded: `outcome: revng-candidates-recorded`, real PTML in `readability/revng/decompiled.c` | `cua-revng-click.png`, `cua-revng-success.png` |
| Sidebar Agents & models | Route opens, presets listed | `cua-side-agents-models.png` |
| Sidebar Compilers & tools | Route opens | `cua-side-compilers-tools.png` |
| Sidebar Settings | Route opens | `cua-side-global-settings.png` |
| Sidebar About | Route opens | `cua-side-about.png` |
| Dark toggle / Themes | Clicked, screenshots captured | `cua-side-dark-toggle.png`, `cua-side-themes.png` |

Not clicked deliberately: destructive **Remove / Delete** and external-URL **Report a
problem**. The sweep proves the buttons are wired and that failure detail is visible;
it is not a claim that every game is verified.
