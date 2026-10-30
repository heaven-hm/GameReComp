# Number audit — README.md and reports/*.md

**Task:** task-7 (adversarial audit of published numbers) · **Branch:** `feature/igi-audit-replay-bisect`,
audited at `71e6b56`; the branch advanced to `6ff7190` during the audit. `tools/lift/difftest.py` is
**identical** in the two commits (checked with `git diff --stat 71e6b56..HEAD -- tools/lift/difftest.py` —
no output), so every oracle result below is from one oracle version; `rename_ast.py`/`rename_gen.py` did
change at `6ff7190`, so the acceptance count in row 12 was **re-measured at `6ff7190`** and is unchanged.
· **Auditor:** reviewer · **Files changed:** this file only.
**Window:** 2026-10-30 22:04 → 23:05 IST. Read-only on source: no `tools/`, `runtime/`, `highlevel/`,
`README.md` or `docs/` file was modified. Every generated module and report fragment went to `/tmp`.

Every claim below is a *published* number (README.md or reports/*.md), the command that should produce it,
what I got, and a verdict: **agrees / disagrees / cannot reproduce**.

## Method, and the two ways this audit could have lied

- **The oracle was run sequentially** (never two at once), as instructed: 1 validation run, 17 runs over
  reconstructed candidate modules, 7 runs over the seven shipped functions. **No run silently failed:
  every invocation wrote its JSON.** The `rc=1` seen on non-`agree` verdicts is the tool's convention
  (`1 address(es): 0 agree, 1 not`), *not* the collision — the collision is `rc=1` **with no JSON**, which
  did not occur.
- **Two of my own batches were invalid and are excluded from every count**: one passed the typed symbol
  instead of the adapter and produced 14 bogus `diverge`; one passed `--symbol sub_0x…` (the harness
  generated `_sub_0x004258B0`) and produced 17 `harness-failed`. Both are recorded here because they are
  the exact "confident, precise, false" shape this audit is for — I caught them by reading one record
  (fields showed eax 1..8 on a body that returns a constant) and one linker error, not by trusting the
  summary line.
- **Verdict vocabulary respected:** `agree` (eax + named memory for a generated module), `agree-return`
  (eax alone, pure-body premise), `nothing-compared` never counted as a pass, `frame.compared` read as
  false in every run below (no `--frame-size` was requested), so these are default-mode numbers,
  comparable to the published ones in *vocabulary* but not to any future frame-mode run.
- To reproduce the 157-era acceptance and the modules behind the sweep I loaded the *commit's own*
  `rename_ast.py` / `rename_gen.py` from `b0c24cd` into `/tmp/era` (never modifying the tree).

## The table

| # | Claim | Published | Command that produces it | What I got | Verdict |
|---|---|---|---|---|---|
| 1 | Doukutsu has **955** provider functions | README:207 | `ls highlevel/doukutsu/revng/0x*.c \| wc -l` | 955 | **agrees** |
| 2 | **157** provider bodies accepted | reports/doukutsu-sweep.md:2 | drive `b0c24cd`'s `rename_ast.rewrite()` over all 955 | **157** (798 refused — exact match) | **agrees** |
| 3 | **157 generated** | sweep.md:2 | — no module artifact exists | cannot rebuild without generating all 157 | **cannot reproduce** |
| 4 | **36 compile** | sweep.md:2 | needs the 157 modules, none are stored | I compiled the 20 acceptance+no-memory candidates: 17 compile; the 36 needs the full set | **cannot reproduce** |
| 5 | **35 modules completed** (1 duplicate address) | sweep.md:2 | no per-module artifact | — | **cannot reproduce** |
| 6 | Buckets: agree 7 · agree-memory 8 · nothing-compared 15 · original-faulted 5 · diverge 0 | sweep.md:4–10 | oracle over the 36 modules | reproduced the 7 (`agree`), the 3 divergences, the 2 retracted `agree-return`; did not re-run the 8 `agree-memory` (their address list is not published) | **partially agrees** |
| 7 | **The 7 full certifications**, named-memory counts 1,2,1,1,2,1,5 | sweep.md:12–20 | `python3 -m tools.lift.difftest --project projects/Doukutsu --pe …/Doukutsu.exe --address <a> --source /tmp/gen7/<a>.c --symbol sub_<a> --cases 8` | **7/7 `agree`**, named counts **1,2,1,1,2,1,5** (exact) | **agrees** |
| 8 | **12 agree-return** | sweep.md:95 | oracle over reconstructed candidates | **13** `agree-return` including the 2 retracted → **11 reliable** vs 12 published | **disagrees (by one)** |
| 9 | **3 divergences**: `0x004882D9` 0x411→0x0, `0x00489227` 0x300→0x320, `0x00489195` 0x20000→0x20010 | sweep.md:96–101 | same oracle form | exactly those **3** `diverge`, with the published values (`project 00000411 / original 00000000`, `00020000 / 00020010`, `00000300 / 00000320`) and instruction counts 10 / 55 / 49 to first `ret` | **agrees** |
| 10 | **19 certified** (7+12), then **17** (7+10) after retraction | sweep.md:104, 180–210; README:210–216 | sum + git history | 7 `agree` + 13 `agree-return` (incl. 2 retracted) = **18 post-retraction** vs 17 | **disagrees (by one)** |
| 11 | Retraction chain: 19 for one commit, then 17; the 2 retracted addresses; "neither was dispatched" | README:218–227; sweep.md:180–210 | `git log` + oracle + `ls highlevel/doukutsu-sweep/` | `5a97039` introduced `agree-return` (19), `fe01690` retracted (17); both addresses return `agree-return` with era modules today and are refused by the current host-VA rule; the shipped dir holds only the seven, and the two are absent | **agrees** |
| 12 | **50** provider bodies "parse and satisfy the module model" | README:209 | drive HEAD's `rename_gen` acceptance over all 955 (default `frames=False`) | **47** accepted (47/955). With the frame model enabled (`frames=True`): **173**. No committed command prints an aggregate. | **disagrees** |
| 13 | **157 accepted → 154 generated → 34 compiling** (pre-struct baseline) | sweep.md:171–176 | needs the module run | not verifiable; bucket sum 7+12+8+2+4 = 33 for "34 modules" (reconciles only if one duplicate, which the report states for the *other* run) | **cannot reproduce** (arithmetic note) |
| 14 | **180 `undef()` values** in the frame-loop body | README:238; sweep.md:139 | `python3 -c "print(open('highlevel/doukutsu/revng/0x00481BB0.c').read().count('undef('))"` | **180** | **agrees** |
| 15 | The body is **8,541 bytes** | README:238 | `wc -c` / extract | per-function file **8,515** bytes; body text **8,467**; aggregate definition **8,469**. No measurement gives 8,541. | **disagrees** |
| 16 | The three provider defects return constants `0x411`, `0x20000`, `0x300` | sweep.md:96–98; README:234 | `cat highlevel/doukutsu/revng/0x004882D9.c …` | `return 1041U;` (**0x411**), `return 0x20000U;`, `return 0x300U` | **agrees** |
| 17 | "the original computes from its inputs" (10/49/55 instructions) | sweep.md:96–101 | capstone to first `ret` on the PE | 0x004882D9: `sub eax,0x3a4 …` (reads eax, **10** insns); 0x00489195: `test bl,…` (**55**); 0x00489227: `test bl,…` (**49**) | **agrees** |
| 18 | ..."with a **`void f(void)` signature** that could not have used them" | README:236 | `cat` provider and generated bodies | provider: `generic32_t function_0x…_Code_x86(void)`; generated module: `uint32_t function_…(void)`. It is the **parameter list** that is void, not the return type. | **disagrees** (as stated) |
| 19 | `0x00481BB0` — **6.6 million dispatched calls in a 12-second run** | README:237 | `reports/doukutsu-call-census.json` | 6,600,457 entries for 0x00481BB0 — but the artifact's own `run` field says **"RECOMP_CALL_CAP=300000, ended by the cap"**, a capped run, not a 12-second one | **disagrees** (provenance) |
| 20 | 12-second counters: **366,408 / 192,261 / 7,473** baseline, **363,938 / 192,490 / 6,858** with the seven dispatched | (Lead's brief) | not published anywhere | searched README, reports, docs, logs, all tracked files and all file types: the strings occur **nowhere**. No artifact names the metric or the build. | **cannot reproduce** |
| 21 | **371,144 import dispatches in 12 s** (192,303 `Blt`, 8,399 `PeekMessageA`, …, 0 failed indirect calls) | README:172, :62 | no artifact | no file in the repository contains 371144 or 192303 | **cannot reproduce** |
| 22 | The seven were certified "on **registers** and named memory" | README:213 | oracle record for a `--source` run | `difftest.py:1774` applies `_eax_only_case` whenever `--source` is used, and `diff()` compares only `('eax',)` (`:1070`). The three divergences in row 9 are eax-only. The sweep report states the correct thing (`agree` = eax + named memory); README says registers. The record's own `compares` field also says "the eight general-purpose registers" — it is wrong for source runs. | **disagrees** |
| 23 | Doukutsu.exe is **1,478,656 bytes**, PE32 i386 | README:169 | `ls -la …/Doukutsu.exe` | 1,478,656 | **agrees** |

### Cross-cutting: the staged table (README:203–210)

README says the stages are "a strict subset of the one before it" and prints **50 → 17**. The 17 was
measured on the **157-era** population (my row 2 reproduces 157 with that commit's own code), while 50 was
computed later under a stricter boundary rule (`16868dc`'s message: "the acceptance figure is 50, not 157 —
the boundary rule refuses 107 bodies"). Those two numbers belong to different rule sets, so the printed
chain is not one measurement: on one tree the acceptance is 47 (or 173 with frames) and the certified set
is 17/18, on another it is 157. The *subset* relation still holds for the certified set — all 18
reconstructed certified modules are accepted by HEAD's default rule (18/18) — but the printed 50 is not
the population any of the certifications were measured against.

## The question nobody asked: are the ten `agree-return` certifications' premises true?

**Short answer: yes for the ten — every function that could be one of them writes no guest memory and
calls nothing — but the rule that says so is not a proof, and I found two functions in the same
population where it is false.**

What I did, with no oracle needed for the verdict itself:

1. **Reconstructed the class** the ten must come from: era-accepted (`157`) ∩ the oracle's "no named
   memory" condition ∩ `has_memory_observable() == False`. That is **20 functions**; 17 of them compile
   (the sweep's 36-module population contains them). Running the oracle over the 17 gives **13
   `agree-return`** (including the two retracted `0x004258B0`/`0x004258C0`), so the reliable set is 11 —
   one more than the published 10. The published ten's membership is not recorded anywhere, so the ten are
   a subset of these 11; every one of the 11 is checked below.
2. **Independent disassembly** (capstone, extent = address → next provider function start, CFG walk from
   entry, *all* mnemonics and addressing modes, memory destinations counted unless they are `[ebp…]`/
   `[esp…]` stack-frame slots) of all 11: **0 of 11 write guest memory and 0 of 11 contain a `call`.**
   The `agree-return` premise is true for every candidate for the ten.
3. **But the premise's test is unsound.** Two functions in the same candidate class have guest-data
   writes the tool's check does not see:

| address | era-accepted | compiles | tool says | the bytes actually do |
|---|---|---|---|---|
| `0x004012D0` | **yes** | **no** (`struct_3410` undeclared) | pure | `0x40131C mov dword ptr [ecx*4 + 0x499b40], edx` — a guest write through an indexed address |
| `0x0041D630` | no | — | pure | `[ecx*8 + 0x4a5500]`, `[eax*8 + 0x4a5504]` guest writes |

`has_memory_observable()` (`tools/lift/difftest.py:1490–1530`) only counts an absolute-displacement write
when **base == 0 and index == 0**, and a register-based write only when the base is a register other than
`{0, ebp, esp}`; its mnemonic whitelist is `mov/and/or/xor/add/sub/inc/dec/not` (absolute) and
`mov/and/or/xor/add/sub` (register base). So `[index*scale + disp]` writes are invisible, as are
`stos*`, x87 stores (`fstp/fistp`), `xchg`, `setcc`, shifts on memory, etc. The two functions above are
that blind spot.

Why this did not produce a false certification in this sweep: `0x004012D0` is era-accepted but its
generated module does **not compile** (it keeps rev.ng's `struct_3410` and `ptr:` type syntax), so it was
never in the 36; `0x0041D630` was never accepted. That is luck, not proof — the same predicate would
certify either one the day the generator can carry its body. This is the retracted-two failure mode one
level up: the *scan that was built to catch host-address MEMn* does not cover computed-address writes in
the original's bytes.

**What would change my verdict:** a published list of the ten addresses (then the check is exact rather
than reconstructed), or one candidate among the compiled set whose bytes write through a computed address
— I found none.

## Every claim I could not verify, and why

| Claim | Why not |
|---|---|
| **157 generated / 36 compile / 35 completed** (sweep.md:2) | No generated-module corpus or per-module log is stored; regenerating all 157 and compiling them is the sweep itself, which is out of this audit's scope |
| **agree-memory 8**, **original-faulted 5**, and the exact **12 agree-return** membership | The per-address verdict file from the sweep was never committed (not in `reports/`, not in any commit, not in the stash); only the aggregate table exists |
| **The ten addresses** | Same: README/report give the count and three named divergences, never the ten |
| **12-second counters 366,408 / 192,261 / 7,473 and 363,938 / 192,490 / 6,858** | Not published in the repository at all; no run log or build label names these metrics |
| **371,144 import dispatches in 12 s** and the per-API breakdown (README:172) | No artifact contains the numbers; the only call census is the capped run of row 19 |
| **6.6 million as a 12-second number** | The artifact it presumably comes from is a 300,000-dispatch capped run; see row 19 |
| **50 acceptance** | No committed driver prints it; the number belongs to a commit two rule-changes back (row 12) |
| **8,541-byte body** | No measurement of the current or any recoverable earlier file yields it (row 15) |
| **Pre-struct baseline 34 modules / buckets summing to 33** | Same missing per-module data (row 13) |

## Bottom line

- The **seven** reproduce exactly, including the named-memory counts. The **three divergences** reproduce
  exactly, with the advertised instruction counts. The **retraction chain** reproduces and the two
  retracted modules really do return `agree-return` under an oracle run — the retraction was necessary.
- The **certified total is off by one in my reconstruction** (18 vs 17; 11 reliable `agree-return` vs 10):
  the ten are not enumerated anywhere, so I cannot say which extra function the sweep did not count — most
  likely one module that did not compile in the sweep's population but does with today's provider output.
- **Two README numbers are wrong as published** (50 acceptance; 8,541-byte body), one is **misattributed**
  (6.6M → 12-second run) and one **overstates the seven's claim** ("registers" when source runs compare
  eax only). The 12-second counters and the 371,144-dispatch figure are not published artifacts at all and
  cannot be audited from this repository.
- The **agree-return premise is true for the ten**, but its test has a real blind spot with two examples in
  the same population; the rule should count computed-address writes before the number is quoted as a
  proof anywhere else.
