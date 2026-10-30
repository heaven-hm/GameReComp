# Game matrix

Every game this product knows about, in one table: the locally supplied corpus this repository measures, and the upstream PCRecomp projects whose status is quoted from their own README. No game binary or asset is stored in this repository.

**Working status** is this pipeline's fail-closed verdict (`docs/VERIFICATION.md`), not an impression: `PASS` requires a recorded scenario; `PARTIAL` means the rebuilt process launched but no independent scenario was run; `FAIL` means it did not get that far. `NOT RUN` is exactly that. Upstream rows are claims, marked `†`, and are never counted as this product's results.

**Port details** are read from each project's own `reports/report.json`: functions recovered, generated C, the rebuilt binary, imports the runtime bound, and the low-level/high-level split. `LOW_LEVEL_FALLBACK` functions are register-transfer C, not reconstructed high-level source.

Updated 2026-10-30T03:54:14Z by `python -m tools.gamesdb --matrix`.

## This repository (measured)

| Name | OS | Arch | Port details | Working status |
| --- | --- | --- | --- | --- |
| Cannonball | Windows | x86-64 (PE32+) | inspect failed: PE32+ is not supported by the PE32 backend yet (machine x86-64); fail-closed instead of guessing | NOT RUN |
| Cave Story | Windows | i386 (PE32) | 406 fns recovered; 2 units / 4.1 MiB C; binary DoConfig 1301 KiB; 88 imports bound, 2 unresolved; low-level 406/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| Cave Story | Windows | i386 (PE32) | 3260 fns recovered; 8 units / 50.7 MiB C; binary Doukutsu 8368 KiB; 137 imports bound, 6 unresolved; low-level 3260/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| Cave Story | Windows | i386 (PE32) | 551 fns recovered; 2 units / 5.4 MiB C; binary OrgView 1517 KiB; 120 imports bound, 2 unresolved; low-level 551/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| GeneRally | Windows | i386 (PE32) | 2577 fns recovered; 46 units / 373.9 MiB C; low-level 2577/ high-level verified 0; compile failed: build failed (see /Users/haseeb-mir/Documents/Code/RevEng-Studio/projects/GeneRally/logs/compile.log | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| GeneRally | Windows | i386 (PE32) | 311 fns recovered; 2 units / 7.0 MiB C; binary TrackEditor 2152 KiB; 88 imports bound, 59 unresolved; low-level 311/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| Gunroar | Windows | i386 (PE32) | 3288 fns recovered; 5 units / 19.6 MiB C; binary gr 0 KiB; 63 imports bound, 73 unresolved; low-level 3288/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| Hero Core | Windows | i386 (PE32) | 12424 fns recovered; 14 units / 59.4 MiB C; binary herocore_gm5 17276 KiB; 353 imports bound, 160 unresolved; low-level 12424/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| Hero Core | Windows | i386 (PE32) | 15788 fns recovered; 18 units / 79.6 MiB C; binary herocore_gm7 22159 KiB; 356 imports bound, 176 unresolved; low-level 15788/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| Ikachan | Windows | i386 (PE32) | 482 fns recovered; 3 units / 9.6 MiB C; binary dxIka 2175 KiB; 97 imports bound; low-level 482/ high-level verified 0 | PARTIAL - launches, no scenario oracle - process launched and exited 1; no oracle supplied |
| Noiz2sa | Windows | i386 (PE32) | 3024 fns recovered; 6 units / 36.3 MiB C; low-level 3024/ high-level verified 0; compile failed: build failed (see /Users/haseeb-mir/Documents/Code/RevEng-Studio/projects/noiz2sa/logs/compile.log) | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| OpenTyrian | Windows | x86-64 (PE32+) | inspect failed: PE32+ is not supported by the PE32 backend yet (machine x86-64); fail-closed instead of guessing | NOT RUN |
| Parsec47 | Windows | i386 (PE32) | 1930 fns recovered; 3 units / 13.2 MiB C; binary p47 4355 KiB; 61 imports bound, 90 unresolved; low-level 1930/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| Pixeltetris | Windows | x86-64 (PE32+) | inspect failed: PE32+ is not supported by the PE32 backend yet (machine x86-64); fail-closed instead of guessing | NOT RUN |
| Snake | Windows | x86-64 (PE32+) | inspect failed: PE32+ is not supported by the PE32 backend yet (machine x86-64); fail-closed instead of guessing | NOT RUN |
| Torus Trooper | Windows | i386 (PE32) | 2342 fns recovered; 4 units / 15.1 MiB C; binary tt 5035 KiB; 61 imports bound, 94 unresolved; low-level 2342/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| Tumiki Fighters | Windows | i386 (PE32) | 1760 fns recovered; 3 units / 12.0 MiB C; binary tf 3830 KiB; 59 imports bound, 86 unresolved; low-level 1760/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| Warning Forever | Windows | i386 (PE32) | 2624 fns recovered; 6 units / 33.6 MiB C; binary wf 7193 KiB; 141 imports bound, 6 unresolved; low-level 2624/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |
| rRootage | Windows | i386 (PE32) | 3199 fns recovered; 7 units / 41.3 MiB C; binary rr 9895 KiB; 18 imports bound, 146 unresolved; low-level 3199/ high-level verified 0 | FAIL - rebuilt process cannot boot - rebuilt process boot-failed (exit 2) |

## This repository (not measured)

| Name | OS | Arch | Port details | Working status |
| --- | --- | --- | --- | --- |
| OpenTyrian | DOS | not a PE | data file shipped with the game, not an executable | NOT APPLICABLE |
| OpenTyrian | DOS | not a PE | data file shipped with the game, not an executable | NOT APPLICABLE |
| OpenTyrian | DOS | not a PE | data file shipped with the game, not an executable | NOT APPLICABLE |
| OpenTyrian | DOS | not a PE | data file shipped with the game, not an executable | NOT APPLICABLE |
| OpenTyrian | DOS | not a PE | data file shipped with the game, not an executable | NOT APPLICABLE |
| OpenTyrian | DOS | not a PE | data file shipped with the game, not an executable | NOT APPLICABLE |
| OpenTyrian | DOS | not a PE | data file shipped with the game, not an executable | NOT APPLICABLE |
| OpenTyrian | DOS | not a PE | data file shipped with the game, not an executable | NOT APPLICABLE |
| OpenTyrian | DOS | not a PE | data file shipped with the game, not an executable | NOT APPLICABLE |
| OpenTyrian | DOS | not a PE | data file shipped with the game, not an executable | NOT APPLICABLE |
| OpenTyrian | DOS | not a PE | data file shipped with the game, not an executable | NOT APPLICABLE |
| OpenTyrian | DOS | not a PE | data file shipped with the game, not an executable | NOT APPLICABLE |

## Upstream PCRecomp projects (claims, not measurements)

Status text is quoted from each project's own README; the architecture and OS columns are what that README states. These rows are context for the corpus above, never evidence that this pipeline reproduces them.

| Name | OS | Arch | Port details | Working status |
| --- | --- | --- | --- | --- |
| [Star Wars: Force Commander](https://github.com/sp00nznet/forcecommander) | Windows | x86 (PE32) | 2000; upstream README claim | In game! briefing room renders in 3D - 39,038 functions, 10.7M lines† |
| [C&C: Red Alert 2 + Yuri's Revenge](https://github.com/sp00nznet/redalert2-recomp) | Windows | x86 (PE32) | 2000; upstream README claim | Runs natively on Linux (win32hle), Bink movies, D3D11 presenter† |
| [C&C: Tiberian Sun + Firestorm](https://github.com/sp00nznet/tiberiansun-recomp) | Windows | x86 (PE32) | 1999; upstream README claim | Runs natively on Linux (win32hle): movies, music, menus, skirmish† |
| [Fury³](https://github.com/sp00nznet/fury3) | Windows | x86 (PE32) | 1995; upstream README claim | Playable! Flies the canyon - 1,945 functions, SDL2+imgui† |
| [Dance eJay 1 + 2](https://github.com/sp00nznet/ejay) | Windows | x86 (Win16/32) | 1997; upstream README claim | Plays and draws! intro decode, 22,050 Hz audio, cursor animation† |
| [Operation Neptune](https://github.com/sp00nznet/operationneptune) | Windows | x86 (PE32) | 1991/1998; upstream README claim | Plays! CRT -> WinMain -> opening -> in the submarine† |
| [SimCity 2000 (CD / Win95)](https://github.com/sp00nznet/simcity2000) | Windows | x86 (PE32) | 1996; upstream README claim | Plays (Windows); win32hle bring-up runs MFC startup into main window† |
| [SkiFree](https://github.com/sp00nznet/skifree) | Windows | x86 (Win16/Win32) | 1991; upstream README claim | Playable rebuild from decompiled C, cross-platform + extras† |
| [Encarta 97 Encyclopedia](https://github.com/sp00nznet/encarta) | Windows | x86 (PE32) | 1996; upstream README claim | Runs! Whole app lifted (7,326 fns); hybrid MFC boundary† |
| [Bolo Adventures III](https://github.com/sp00nznet/bolo) | DOS | x86-16 | 1993; upstream README claim | Boots & runs! 932 functions, 70K lines, EGA/DOS on SDL2† |
| [Catz](https://github.com/sp00nznet/catz) | Windows 3.x/9x | x86-16 (NE) | 1996; upstream README claim | Runs! Win32 window, original frame loop, toys and saving work† |
| [Civilization](https://github.com/sp00nznet/civ) | DOS | x86-16 | 1991; upstream README claim | Runs! 672 functions, interactive boot/menu, 164K lines† |
| [World Empire](https://github.com/sp00nznet/worldempire) | Windows 3.x | x86-16 (VB p-code) | 1994; upstream README claim | Runs! 99/99 segments, 14,826 functions, 226K lines† |
| [Fallout](https://github.com/sp00nznet/fallout1-re) | Windows | x86 (PE32) | 1997; upstream README claim | Fork - native + HTML5 web port, multiplayer† |
| [Fallout 2](https://github.com/sp00nznet/fallout2-re) | Windows | x86 (PE32) | 1998; upstream README claim | Fork - decompilation ~complete (alexbatalov upstream)† |
| [Soldier of Fortune](https://github.com/sp00nznet/sof) | Windows | x86 (PE32) | 2000; upstream README claim | Active - SDL2 port, 8 subsystems, full maps render† |
| [X-Wing Alliance](https://github.com/sp00nznet/xwa) | Windows | x86 (PE32) | 1999; upstream README claim | Active - D3D11 port, concourse UI runs, 2,702 functions† |
| [Black & White](https://github.com/sp00nznet/bw) | Windows | x86 (PE32) | 2001; upstream README claim | Active - all 569 types done, 10 Hz game loop runs† |
| [DinoPark Tycoon](https://github.com/sp00nznet/dinopark) | DOS | x86-16 | 1993; upstream README claim | Boots! ~90K lines; renders .PIC screens + .ACT dinosaurs† |
| [Grand Theft Auto](https://github.com/sp00nznet/gta) | DOS | x86 | 1997; upstream README claim | Builds & runs - 4,094 functions, 444K lines, runtime bringup† |
| [Roller Coaster Construction Set](https://github.com/sp00nznet/coaster) | DOS | x86-16 | 1993; upstream README claim | Boots - 886 functions lifted, runs MSC startup into main()† |
| [X-Wing vs TIE Fighter + Balance of Power](https://github.com/sp00nznet/xvt) | Windows | x86 (PE32) | 1997; upstream README claim | Boots to a window - 1,790 functions; DirectDraw next† |
| [Nocturne](https://github.com/sp00nznet/nocturne) | Windows | x86 (PE32) | 1999; upstream README claim | Phase 7 - 6,027 functions, 0 errors; real window, 42 MB image mapped† |
| [Crimson Skies](https://github.com/sp00nznet/crimsonskies) | Windows | x86 (PE32) | 2000; upstream README claim | Compiles & links - 6,232 functions, 826K lines† |
| [El-Fish](https://github.com/sp00nznet/elfish) | Windows 3.x | x86-16 (NE) | 1993; upstream README claim | Lifted & links - 2,236 functions, 121 segments† |
| [MechWarrior 3](https://github.com/sp00nznet/mechwarrior3-recomp) | Windows | x86 (PE32) | 1999; upstream README claim | Compiles - 2,805 functions, 0 lift errors, 158K lines† |
| [POD Gold](https://github.com/sp00nznet/pod) | Windows | x86 (PE32) | 1997; upstream README claim | Compiles & links - 3,405 functions, 0 lift errors, 422K lines† |
| [Recoil](https://github.com/sp00nznet/recoil-recomp) | Windows | x86 (PE32) | 1999; upstream README claim | Compiles - 3,490 functions, 0 lift errors, 321K lines† |
| [Gunman Chronicles](https://github.com/sp00nznet/gunman) | Windows | x86 (PE32) | 2000; upstream README claim | Phase 2 - 3,990 functions, weapons/entities rebuilt† |
| [Hellbender](https://github.com/sp00nznet/hellbender) | Windows | x86 (PE32) | 1996; upstream README claim | Bring-up - 5,262 functions, 0 errors, 507 import bridges† |
| [Microsoft Bob](https://github.com/sp00nznet/bob) | Windows 95 | x86-16 (NE) | 1995; upstream README claim | Bring-up - modules lift link-clean, InitInstance runs† |
| [Rise of Nations: Rise of Legends](https://github.com/sp00nznet/rol) | Windows | x86 (PE32) | 2006; upstream README claim | Phase 3 - 13.25 MB, 25,513 vtable-only functions, no RTTI† |
| [Heavy Metal: FAKK2](https://github.com/sp00nznet/heavymetal) | Windows | x86 (PE32) | 2000; upstream README claim | Foundation - 57 source files, core systems scaffolded† |
| [Jurassic Park: Trespasser](https://github.com/sp00nznet/trespasser) | Windows | x86 (PE32) | 1998; upstream README claim | P0 - reconnaissance; ships a linker map† |
| [Missile Attack!](https://github.com/sp00nznet/missileattack) | Windows 3.x | x86-16 (NE) | 1992; upstream README claim | P0 fixture: 87 KB NE, one code segment, 21 KB of code† |
| [Monster Truck Madness 1 + 2](https://github.com/sp00nznet/mtm) | Windows | x86 (PE32) | 1996/1998; upstream README claim | P0 - renderer split into named DLLs† |
| [One Must Fall: Battlegrounds](https://github.com/sp00nznet/omfbg) | Windows | x86 (PE32 DLLs) | 2003; upstream README claim | P0 - 10,374 mangled C++ exports; SafeDisc covers 1%† |
| [Terminal Velocity](https://github.com/sp00nznet/tv) | DOS | x86-32 (DOS/4GW LE) | 1995; upstream README claim | P0 - needs the LE/LX front end the toolbox lacks† |
| [The Even More Incredible Machine](https://github.com/sp00nznet/tim) | Windows 3.x | x86-16 (NE) | 1993; upstream README claim | P0 - 27 code segments, imports only KERNEL/USER/GDI† |
| [The Magic School Bus Explores the Human Body](https://github.com/sp00nznet/msbus) | Windows 3.x | x86-16 (NE) | 1994; upstream README claim | P0 - ~60 KB code driving 190 MB of content† |
| [Black & White 2](https://github.com/sp00nznet/bw2) | Windows | x86 (PE32) | 2005; upstream README claim | P0 partial - white.exe (21.7 MB) not extracted† |
| [The Electronic Whole Earth Catalog](https://github.com/sp00nznet/wholeearth) | Classic Mac OS | 68k | 1988; upstream README claim | Format project - no PC binary; classic-HFS lister + macrecomp† |
