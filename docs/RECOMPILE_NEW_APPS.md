# Recompilation of newly downloaded apps/games

Earlier downloads were only header-inspected. They were now passed through the
real `tools.autorecomp` pipeline one at a time with `--out-mode binary-only
--no-run --jobs 1`, with logs in `test-results/recompile-<name>.log`.

| Input | Result | Measured evidence |
|---|---|---|
| PrBoom+ 2.5.1.7um (`prboom-plus.exe`) | **PASS (A0–A3)** | 9,566 functions, 1,695,396 instructions; 25 units / 191,990,875 bytes C, 0 lift errors; native binary 33,237,496 bytes in 203.7 s |
| 7-Zip 26.06 (`7z2606.exe`) | **PASS (A0–A3)** | 272 functions; 2 units / 6,617,727 bytes C, 0 lift errors; native binary 3,318,856 bytes in 13.6 s |
| PuTTY (`putty.exe`) | **BLOCKED (build timeout)** | 8,077 functions, 3,707,667 instructions; 51 units / 427,180,734 bytes C, 0 lift errors; compile exceeded the 900 s budget (exit 124) |

Not yet recompiled: Notepad++ installer, dhewm3 (14.6 MB exe), OpenTTD win32
(15.0 MB exe) — same resource constraint as issue #26. No PASS claim is made
for them; they remain header-inspected only.

`tools/recompile_ledger` records which project directories actually contain a
native binary; a header-only `inspect` result is explicitly `NOT_RECOMPILED`.
A0–A3 means parsed/recovered/generated/compiled — not launched, rendered or
gameplay-verified. No screenshot claim is made for these three recompiles.
