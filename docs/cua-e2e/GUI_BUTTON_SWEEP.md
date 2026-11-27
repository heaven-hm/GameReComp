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
| Dark toggle / Themes | Clicked, theme changed to Marked Dark and theme picker opened | `cua-side-dark-toggle.png`, `cua-side-themes.png`, `cua-run-options2.png` (dark) |
| Header **Run Options…** | Opens launch-mode modal with extra arguments, remember toggle and Launch Game Now | `cua-run-options2.png` |
| Header **Report** | Clicked, no GUI crash | `cua-report-button.png` |
| All six tabs (Overview, Functions, Stages & Build, Agents, Frames, Settings) | Each rendered without crash; contact sheet shows all six states | `tabs-contact-sheet-verified.jpg`, `tab3-*.png` |
| Function row → Inspector drawer | Opens `sub_00401000` with provenance, generated C, Source & Provenance / Notes & Hints, Copy C, Open file, Improve C Readability (AI), Star and prev/next controls | `feat-star-function.png` |
| Inspector Notes & Hints / Copy C / Open file / Star / prev / next | All clicked, no crash | `insp-*.png` |
| Functions filters (All, Verified, High-level, Lifted, Unresolved, Starred) and address sort | Clicked, no crash | `feat-*.png` |
| Home **Open path** direct entry | PE32+ x86-64 `cube.exe` selected from an arbitrary path; GUI shows "PE32+ x86-64 accepted — backend result will be recorded" | `anypath-initial.png`, `anypath-x64-result.png` |
| Header **Launch** / **Create Issue** / **Open Output Folder** / **Dismiss** | Present and wired but not exercised in this pass to avoid spawning extra processes or external issue pages | — |

Not clicked deliberately: destructive **Remove / Delete** and external-URL **Report a
problem**. The sweep proves the buttons are wired and that failure detail is visible;
it is not a claim that every game is verified.
