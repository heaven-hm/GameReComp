# General verification engine (Unicorn + Frida)

This is the verification path used by the GUI's **Verify (Unicorn + Frida)** button and
by `python3 -m tools.autorecomp.verify <project>`. It is deliberately **not** game
specific: no IGI/TrackMania addresses, filenames or keys are hard-coded in the engine.
Every input comes from the project's own `reports/` metadata or the command line.

## Two independent percentages

| KPI | Engine | What is measured |
| --- | --- | --- |
| **Source code verification** | Unicorn via `tools/lift/difftest.oracle` | The original PE32 i386 bytes and the project's generated C body are driven with the same seeded registers/stack/memory. GPRs, flags and guest memory are compared per function. One named check per function per entry state. |
| **Run verification** | Frida 17 | The original executable (through a Windows runtime such as Wine) and the rebuilt binary are launched with **the same arguments**. A small script hooks named OS/API calls and logs normalized events; the streams are compared element by element. |

A percentage is `passed / checked`. A check that cannot run (missing generated body,
original fault, synthetic-state limit, missing Frida runtime, attach denied) is counted
as **blocked**, never as a pass. `verified` is true only when *both* sides are 100% and
at least one check actually ran. Reports are written to `reports/verify.json`, with the
underlying per-address rows and the Frida logs under `reports/frida/`.

## CLI

```sh
python3 -m tools.autorecomp.verify /path/to/project \
  --max-functions 4 --cases 2 --timeout 20 \
  --args window level7
```

`--address 0x...` can override function selection; `--all-functions` removes the cap.
`--binary /path/to/rebuilt` selects the rebuilt executable explicitly, which is needed
when several architecture-specific builds live in one project. Same-input arguments
after `--args` are used for both binaries. The GUI worker honours
`RECOMP_PYTHON` if the `.app` has a restricted PATH. Do not claim a game PASS from this
command alone: a game verdict still needs frame/input evidence.

## GUI

The project overview shows **SOURCE CODE VERIFICATION** and **RUN VERIFICATION** as
separate percentages, with the reason under each. The **Verification detail** panel
prints every blocked/diverged check and the last lines of the running job's log, so a
failure to recompile or run is never reduced to a toast.

Real GUI capture: `docs/verification-engine/gui-separate-percentages.jpg` (built from
this source tree, IGI project open). Both percentages are shown at 0.0% because the
Unicorn sweep had not finished and Frida attach is blocked on this host — the GUI
shows the measured value, not a desired one.

## Honest status on this machine

* Frida 17 is installed (`frida --version`), and the script uses the current API
  (`Module.getExportByName` / `Module.getGlobalExportByName`; Frida 17 resumes spawned
  targets by default, so `--no-pause` was removed).
* **Frida cannot attach to any spawned process here.** `frida -f /bin/ls ...` fails with
  `unable to access process with pid ... from the current user account`, and `sudo -n`
  requires a password. Therefore run parity is reported as **0% / BLOCKED**, not as a
  pass and not as a fabricated percentage.
* The Unicorn side runs locally. The completed IGI sweep measured **75.0% source parity**:
  `0x004A537A`, `0x004C1780` and `0x004D3710` agree-return; `0x0044D224` is blocked
  because the ORIGINAL's own bytes fault on the synthetic entry state
  (`UC_ERR_READ_UNMAPPED`), not because the generated C diverged. Raw rows:
  `projects/igi/reports/verify.json`.
* The macOS Frida run-parity attempt captured no events (exit 124 on both sides after
  the attach failure); it is recorded as 0% / BLOCKED, not a pass. A second attempt
  runs inside a Linux VM with Wine32 so the runtime oracle can attach.

## IGI runtime evidence (separate from the percentages)

* `window level5` and `window level7` now boot (the host no longer mis-parses the data
  folder when extra arguments follow it). `docs/igi-levels/level7-window/` contains 40
  real captured frames from `IGI ... window level7`; frame 39 shows the level-7 intro
  cutscene rendering (helicopter, snow, trees). `docs/igi-levels/level5-window` / the
  earlier level-5 capture show the in-game view.
* This is evidence that the level argument and renderer work. It is **not** a 100%
  gameplay verdict: no same-input original-Windows oracle frames were captured, and
  Frida run parity is blocked in this environment.

## Not yet verified

* Linux: no running Docker daemon and no Linux/x86 sysroot with SDL2 here, so no Linux
  binary or Linux frames were produced. No Linux claim is made.
* TrackMania and AssaultCube: source executables exist under `Downloads`, but no
  same-input original-vs-port frame evidence was collected in this session. They stay
  unverified rather than being silently marked PASS.
