# Game database

Every game this project knows about: locally supplied corpus, the legacy engine baseline for the same binaries, and the upstream PCRecomp project table. Filenames and hashes only - no game binaries or assets are stored here.

`PASS /100` is the RevEng pipeline score for the measured input: weighted work, not a verdict. inspect 10, recover 10, translate 10, project 10, compile 20, report 10, run 15 (full when the rebuilt process launched on its own, half when it launched then faulted, none when it never reached its entry), verify 15 (full for a verified scenario, 8 for a launch without an oracle). A stage only counts when it succeeded for the recorded hash, so a binary that never boots scores 70, not 85. Untested rows are 0.

`†` marks a score derived from the upstream project's own README status; it was not measured by this repository and is not a verification claim. Legacy-engine outcomes are context only.

Updated 2026-10-30T03:54:14Z by `python -m tools.gamesdb`. Measured 19; local corpus 15 PE32 + 4 PE32+ executable(s), 12 non-PE data file(s); upstream projects 42.

Best measured result: **Ikachan** (dxIka.exe) at 93/100, partial: process launched and exited 1; no oracle supplied.

## Measured by this repository

| Game | Filename | Arch | OS | PASS /100 | Status | Evidence |
| --- | --- | --- | --- | ---: | --- | --- |
| Ikachan | dxIka.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 1; no oracle supplied | projects/ikachan/reports/report.html (host: Darwin arm64 (host build)) |
| Cave Story | DoConfig.exe | i386 (PE32) | Windows | 85 | launch-failed: rebuilt process boot-failed (exit 2) | projects/cavestory-doconfig/reports/report.html (host: Darwin arm64 (host build)) |
| Cave Story | Doukutsu.exe | i386 (PE32) | Windows | 85 | launch-failed: rebuilt process boot-failed (exit 2) | projects/cavestory-doukutsu/reports/report.html (host: Darwin arm64 (host build)) |
| Cave Story | OrgView.exe | i386 (PE32) | Windows | 85 | launch-failed: rebuilt process boot-failed (exit 2) | projects/cavestory-orgview/reports/report.html (host: Darwin arm64 (host build)) |
| GeneRally | GeneRally.exe | i386 (PE32) | Windows | 85 | launch-failed: rebuilt process boot-failed (exit 2) | projects/generally/reports/report.html (host: Darwin arm64 (host build)) |
| GeneRally | TrackEditor.exe | i386 (PE32) | Windows | 85 | launch-failed: rebuilt process boot-failed (exit 2) | projects/generally-trackeditor/reports/report.html (host: Darwin arm64 (host build)) |
| Gunroar | gr.exe | i386 (PE32) | Windows | 85 | launch-failed: rebuilt process boot-failed (exit 2) | projects/gunroar/reports/report.html (host: Darwin arm64 (host build)) |
| Hero Core | herocore gm5.exe | i386 (PE32) | Windows | 85 | launch-failed: rebuilt process boot-failed (exit 2) | projects/herocore-gm5/reports/report.html (host: Darwin arm64 (host build)) |
| Hero Core | herocore gm7.exe | i386 (PE32) | Windows | 85 | launch-failed: rebuilt process boot-failed (exit 2) | projects/herocore-gm7/reports/report.html (host: Darwin arm64 (host build)) |
| Noiz2sa | noiz2sa.exe | i386 (PE32) | Windows | 85 | launch-failed: rebuilt process boot-failed (exit 2) | projects/noiz2sa/reports/report.html (host: Darwin arm64 (host build)) |
| Parsec47 | p47.exe | i386 (PE32) | Windows | 85 | launch-failed: rebuilt process boot-failed (exit 2) | projects/parsec47/reports/report.html (host: Darwin arm64 (host build)) |
| Torus Trooper | tt.exe | i386 (PE32) | Windows | 85 | launch-failed: rebuilt process boot-failed (exit 2) | projects/torus-trooper/reports/report.html (host: Darwin arm64 (host build)) |
| Tumiki Fighters | tf.exe | i386 (PE32) | Windows | 85 | launch-failed: rebuilt process boot-failed (exit 2) | projects/tumiki-fighters/reports/report.html (host: Darwin arm64 (host build)) |
| Warning Forever | wf.exe | i386 (PE32) | Windows | 85 | launch-failed: rebuilt process boot-failed (exit 2) | projects/warning-forever/reports/report.html (host: Darwin arm64 (host build)) |
| rRootage | rr.exe | i386 (PE32) | Windows | 85 | launch-failed: rebuilt process boot-failed (exit 2) | projects/rrootage/reports/report.html (host: Darwin arm64 (host build)) |
| Cannonball | cannonball.exe | x86-64 (PE32+) | Windows | 0 | not-run: verification did not run | projects/cannonball/reports/report.html |
| OpenTyrian | opentyrian.exe | x86-64 (PE32+) | Windows | 0 | not-run: verification did not run | projects/opentyrian/reports/report.html |
| Pixeltetris | Pixeltetris.exe | x86-64 (PE32+) | Windows | 0 | not-run: verification did not run | projects/pixeltetris/reports/report.html |
| Snake | snake-windows-x86_64.exe | x86-64 (PE32+) | Windows | 0 | not-run: verification did not run | projects/snake/reports/report.html |

## Local corpus (not yet measured)

| Game | Filename | Arch | OS | PASS /100 | Status | Evidence |
| --- | --- | --- | --- | ---: | --- | --- |
| Cannonball | cannonball.exe | x86-64 (PE32+) | Windows | 0 | not yet run; PE32+ backend not implemented (pipeline fails closed) | sha256 6a631fecc563 |
| OpenTyrian | opentyrian.exe | x86-64 (PE32+) | Windows | 0 | not yet run; PE32+ backend not implemented (pipeline fails closed) | sha256 8eb615e58e38 |
| Pixeltetris | Pixeltetris.exe | x86-64 (PE32+) | Windows | 0 | not yet run; PE32+ backend not implemented (pipeline fails closed) | sha256 3eabbe4a4de9 |
| Snake | snake-windows-x86_64.exe | x86-64 (PE32+) | Windows | 0 | not yet run; PE32+ backend not implemented (pipeline fails closed) | sha256 207d311be674 |

### Corpus files that are not PE executables

| Game | Filename | Arch | OS | PASS /100 | Status | Evidence |
| --- | --- | --- | --- | ---: | --- | --- |
| OpenTyrian | file0001.exe | not a PE | DOS | 0 | not a PE executable: data file shipped with the game | sha256 0f51a58732e4 |
| OpenTyrian | file0002.exe | not a PE | DOS | 0 | not a PE executable: data file shipped with the game | sha256 4941c19d2b36 |
| OpenTyrian | helpme.exe | not a PE | DOS | 0 | not a PE executable: data file shipped with the game | sha256 b4da7bab01b0 |
| OpenTyrian | netarena.exe | not a PE | DOS | 0 | not a PE executable: data file shipped with the game | sha256 2af257308e67 |
| OpenTyrian | netipx.exe | not a PE | DOS | 0 | not a PE executable: data file shipped with the game | sha256 e92b1f1a57c4 |
| OpenTyrian | netmodem.exe | not a PE | DOS | 0 | not a PE executable: data file shipped with the game | sha256 24329b1c3278 |
| OpenTyrian | netterm.exe | not a PE | DOS | 0 | not a PE executable: data file shipped with the game | sha256 42926dcfdefd |
| OpenTyrian | order.exe | not a PE | DOS | 0 | not a PE executable: data file shipped with the game | sha256 3648ae05c996 |
| OpenTyrian | rtm.exe | not a PE | DOS | 0 | not a PE executable: data file shipped with the game | sha256 10acd85af376 |
| OpenTyrian | setup.exe | not a PE | DOS | 0 | not a PE executable: data file shipped with the game | sha256 010fff01150e |
| OpenTyrian | shipedit.exe | not a PE | DOS | 0 | not a PE executable: data file shipped with the game | sha256 9be5f168ee0e |
| OpenTyrian | tyrian.exe | not a PE | DOS | 0 | not a PE executable: data file shipped with the game | sha256 a7e3075d5b18 |

## Upstream PCRecomp projects (claims only)

Statuses below are quoted from the PCRecomp README's project table. The row score is a rubric mapping of that claim (plays/playable 80-90, runs 70-75, boots 50-55, compiles 40, bring-up/active 25-60, P0/recon 5-20), not a measurement made here and not an independent verification.

| Game | Filename | Arch | OS | PASS /100 | Status | Evidence |
| --- | --- | --- | --- | ---: | --- | --- |
| [Star Wars: Force Commander](https://github.com/sp00nznet/forcecommander) | not recorded | x86 (PE32) | Windows | 90† | In game! briefing room renders in 3D - 39,038 functions, 10.7M lines | upstream README (2000) |
| [C&C: Red Alert 2 + Yuri's Revenge](https://github.com/sp00nznet/redalert2-recomp) | not recorded | x86 (PE32) | Windows | 85† | Runs natively on Linux (win32hle), Bink movies, D3D11 presenter | upstream README (2000) |
| [C&C: Tiberian Sun + Firestorm](https://github.com/sp00nznet/tiberiansun-recomp) | not recorded | x86 (PE32) | Windows | 85† | Runs natively on Linux (win32hle): movies, music, menus, skirmish | upstream README (1999) |
| [Fury³](https://github.com/sp00nznet/fury3) | not recorded | x86 (PE32) | Windows | 85† | Playable! Flies the canyon - 1,945 functions, SDL2+imgui | upstream README (1995) |
| [Dance eJay 1 + 2](https://github.com/sp00nznet/ejay) | not recorded | x86 (Win16/32) | Windows | 80† | Plays and draws! intro decode, 22,050 Hz audio, cursor animation | upstream README (1997) |
| [Operation Neptune](https://github.com/sp00nznet/operationneptune) | not recorded | x86 (PE32) | Windows | 80† | Plays! CRT -> WinMain -> opening -> in the submarine | upstream README (1991/1998) |
| [SimCity 2000 (CD / Win95)](https://github.com/sp00nznet/simcity2000) | not recorded | x86 (PE32) | Windows | 80† | Plays (Windows); win32hle bring-up runs MFC startup into main window | upstream README (1996) |
| [SkiFree](https://github.com/sp00nznet/skifree) | ski32.exe | x86 (Win16/Win32) | Windows | 80† | Playable rebuild from decompiled C, cross-platform + extras | upstream README (1991) |
| [Encarta 97 Encyclopedia](https://github.com/sp00nznet/encarta) | not recorded | x86 (PE32) | Windows | 75† | Runs! Whole app lifted (7,326 fns); hybrid MFC boundary | upstream README (1996) |
| [Bolo Adventures III](https://github.com/sp00nznet/bolo) | not recorded | x86-16 | DOS | 70† | Boots & runs! 932 functions, 70K lines, EGA/DOS on SDL2 | upstream README (1993) |
| [Catz](https://github.com/sp00nznet/catz) | not recorded | x86-16 (NE) | Windows 3.x/9x | 70† | Runs! Win32 window, original frame loop, toys and saving work | upstream README (1996) |
| [Civilization](https://github.com/sp00nznet/civ) | not recorded | x86-16 | DOS | 70† | Runs! 672 functions, interactive boot/menu, 164K lines | upstream README (1991) |
| [World Empire](https://github.com/sp00nznet/worldempire) | not recorded | x86-16 (VB p-code) | Windows 3.x | 70† | Runs! 99/99 segments, 14,826 functions, 226K lines | upstream README (1994) |
| [Fallout](https://github.com/sp00nznet/fallout1-re) | not recorded | x86 (PE32) | Windows | 65† | Fork - native + HTML5 web port, multiplayer | upstream README (1997) |
| [Fallout 2](https://github.com/sp00nznet/fallout2-re) | not recorded | x86 (PE32) | Windows | 60† | Fork - decompilation ~complete (alexbatalov upstream) | upstream README (1998) |
| [Soldier of Fortune](https://github.com/sp00nznet/sof) | not recorded | x86 (PE32) | Windows | 60† | Active - SDL2 port, 8 subsystems, full maps render | upstream README (2000) |
| [X-Wing Alliance](https://github.com/sp00nznet/xwa) | not recorded | x86 (PE32) | Windows | 60† | Active - D3D11 port, concourse UI runs, 2,702 functions | upstream README (1999) |
| [Black & White](https://github.com/sp00nznet/bw) | not recorded | x86 (PE32) | Windows | 55† | Active - all 569 types done, 10 Hz game loop runs | upstream README (2001) |
| [DinoPark Tycoon](https://github.com/sp00nznet/dinopark) | not recorded | x86-16 | DOS | 55† | Boots! ~90K lines; renders .PIC screens + .ACT dinosaurs | upstream README (1993) |
| [Grand Theft Auto](https://github.com/sp00nznet/gta) | not recorded | x86 | DOS | 55† | Builds & runs - 4,094 functions, 444K lines, runtime bringup | upstream README (1997) |
| [Roller Coaster Construction Set](https://github.com/sp00nznet/coaster) | not recorded | x86-16 | DOS | 50† | Boots - 886 functions lifted, runs MSC startup into main() | upstream README (1993) |
| [X-Wing vs TIE Fighter + Balance of Power](https://github.com/sp00nznet/xvt) | not recorded | x86 (PE32) | Windows | 50† | Boots to a window - 1,790 functions; DirectDraw next | upstream README (1997) |
| [Nocturne](https://github.com/sp00nznet/nocturne) | not recorded | x86 (PE32) | Windows | 45† | Phase 7 - 6,027 functions, 0 errors; real window, 42 MB image mapped | upstream README (1999) |
| [Crimson Skies](https://github.com/sp00nznet/crimsonskies) | not recorded | x86 (PE32) | Windows | 40† | Compiles & links - 6,232 functions, 826K lines | upstream README (2000) |
| [El-Fish](https://github.com/sp00nznet/elfish) | not recorded | x86-16 (NE) | Windows 3.x | 40† | Lifted & links - 2,236 functions, 121 segments | upstream README (1993) |
| [MechWarrior 3](https://github.com/sp00nznet/mechwarrior3-recomp) | not recorded | x86 (PE32) | Windows | 40† | Compiles - 2,805 functions, 0 lift errors, 158K lines | upstream README (1999) |
| [POD Gold](https://github.com/sp00nznet/pod) | not recorded | x86 (PE32) | Windows | 40† | Compiles & links - 3,405 functions, 0 lift errors, 422K lines | upstream README (1997) |
| [Recoil](https://github.com/sp00nznet/recoil-recomp) | not recorded | x86 (PE32) | Windows | 40† | Compiles - 3,490 functions, 0 lift errors, 321K lines | upstream README (1999) |
| [Gunman Chronicles](https://github.com/sp00nznet/gunman) | not recorded | x86 (PE32) | Windows | 30† | Phase 2 - 3,990 functions, weapons/entities rebuilt | upstream README (2000) |
| [Hellbender](https://github.com/sp00nznet/hellbender) | not recorded | x86 (PE32) | Windows | 25† | Bring-up - 5,262 functions, 0 errors, 507 import bridges | upstream README (1996) |
| [Microsoft Bob](https://github.com/sp00nznet/bob) | not recorded | x86-16 (NE) | Windows 95 | 25† | Bring-up - modules lift link-clean, InitInstance runs | upstream README (1995) |
| [Rise of Nations: Rise of Legends](https://github.com/sp00nznet/rol) | not recorded | x86 (PE32) | Windows | 20† | Phase 3 - 13.25 MB, 25,513 vtable-only functions, no RTTI | upstream README (2006) |
| [Heavy Metal: FAKK2](https://github.com/sp00nznet/heavymetal) | not recorded | x86 (PE32) | Windows | 15† | Foundation - 57 source files, core systems scaffolded | upstream README (2000) |
| [Jurassic Park: Trespasser](https://github.com/sp00nznet/trespasser) | not recorded | x86 (PE32) | Windows | 10† | P0 - reconnaissance; ships a linker map | upstream README (1998) |
| [Missile Attack!](https://github.com/sp00nznet/missileattack) | not recorded | x86-16 (NE) | Windows 3.x | 10† | P0 fixture: 87 KB NE, one code segment, 21 KB of code | upstream README (1992) |
| [Monster Truck Madness 1 + 2](https://github.com/sp00nznet/mtm) | not recorded | x86 (PE32) | Windows | 10† | P0 - renderer split into named DLLs | upstream README (1996/1998) |
| [One Must Fall: Battlegrounds](https://github.com/sp00nznet/omfbg) | not recorded | x86 (PE32 DLLs) | Windows | 10† | P0 - 10,374 mangled C++ exports; SafeDisc covers 1% | upstream README (2003) |
| [Terminal Velocity](https://github.com/sp00nznet/tv) | not recorded | x86-32 (DOS/4GW LE) | DOS | 10† | P0 - needs the LE/LX front end the toolbox lacks | upstream README (1995) |
| [The Even More Incredible Machine](https://github.com/sp00nznet/tim) | not recorded | x86-16 (NE) | Windows 3.x | 10† | P0 - 27 code segments, imports only KERNEL/USER/GDI | upstream README (1993) |
| [The Magic School Bus Explores the Human Body](https://github.com/sp00nznet/msbus) | not recorded | x86-16 (NE) | Windows 3.x | 10† | P0 - ~60 KB code driving 190 MB of content | upstream README (1994) |
| [Black & White 2](https://github.com/sp00nznet/bw2) | white.exe | x86 (PE32) | Windows | 5† | P0 partial - white.exe (21.7 MB) not extracted | upstream README (2005) |
| [The Electronic Whole Earth Catalog](https://github.com/sp00nznet/wholeearth) | not recorded | 68k | Classic Mac OS | 0† | Format project - no PC binary; classic-HFS lister + macrecomp | upstream README (1988) |
