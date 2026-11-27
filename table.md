# Game database

Every game this project knows about: locally supplied corpus, the legacy engine baseline for the same binaries, and the upstream PCRecomp project table. Filenames and hashes only - no game binaries or assets are stored here.

`PASS /100` is the GameReComp pipeline score for the measured input: weighted work, not a verdict. inspect 10, recover 10, translate 10, project 10, compile 20, report 10, run 15 (full when the rebuilt process launched on its own, half when it launched then faulted, none when it never reached its entry), verify 15 (full for a verified scenario, 8 for a launch without an oracle). A stage only counts when it succeeded for the recorded hash, so a binary that never boots scores 70, not 85. Untested rows are 0.

`†` marks a score derived from the upstream project's own README status; it was not measured by this repository and is not a verification claim. Legacy-engine outcomes are context only.

Updated 2026-11-27T16:53:39Z by `python -m tools.gamesdb`. Measured 123; local corpus 15 PE32 + 4 PE32+ executable(s), 12 non-PE data file(s); upstream projects 42.

Best measured result: **BusyBox** (busybox-w32.exe) at 93/100, partial: process launched and exited 0; no oracle supplied.

## Measured by this repository

| Game | Filename | Arch | OS | PASS /100 | Status | Evidence |
| --- | --- | --- | --- | ---: | --- | --- |
| BusyBox | busybox-w32.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/busybox_w32/reports/report.html (host: Darwin arm64 (host build)) |
| Cave Story | DoConfig.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 1; no oracle supplied | projects/DoConfig/reports/report.html (host: Darwin arm64 (host build)) |
| Cave Story | Doukutsu.exe | i386 (PE32) | Windows | 93 | partial: process launched and stayed alive until the 10s timeout; no crash observed, no oracle | projects/Doukutsu/reports/report.html (host: Darwin arm64 (host build)) |
| Downloads | idman643build18.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/idman643build18/reports/report.html (host: Darwin arm64 (host build)) |
| GeneRally | TrackEditor.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/TrackEditor/reports/report.html (host: Darwin arm64 (host build)) |
| IGI | igi.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 1; no oracle supplied | projects/igi/reports/report.html (host: Darwin arm64 (host build)) |
| Ikachan | dxIka.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 1; no oracle supplied | projects/dxIka/reports/report.html (host: Darwin arm64 (host build)) |
| Noiz2sa | noiz2sa.exe | i386 (PE32) | Windows | 93 | partial: process launched and stayed alive until the 10s timeout; no crash observed, no oracle | projects/noiz2sa/reports/report.html (host: Darwin arm64 (host build)) |
| PSFTP | psftp-w32.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/psftp_w32/reports/report.html (host: Darwin arm64 (host build)) |
| Pageant | pageant-w32.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 1; no oracle supplied | projects/pageant_w32/reports/report.html (host: Darwin arm64 (host build)) |
| Progress Quest | pq.exe | i386 (PE32) | Windows | 93 | partial: process launched and stayed alive until the 10s timeout; no crash observed, no oracle | projects/pq/reports/report.html (host: Darwin arm64 (host build)) |
| PuTTY | putty-w32.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/putty_w32/reports/report.html (host: Darwin arm64 (host build)) |
| Resource_Convertor | gconv.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/gconv/reports/report.html (host: Darwin arm64 (host build)) |
| Snake | snake.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/snake/reports/report.html (host: Darwin arm64 (host build)) |
| Warning Forever | wf.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/wf/reports/report.html (host: Darwin arm64 (host build)) |
| arith-calc | arith-calc.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/arith_calc/reports/report.html (host: Darwin arm64 (host build)) |
| base64 | base64.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/base64/reports/report.html (host: Darwin arm64 (host build)) |
| blackbox | blackbox.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/blackbox/reports/report.html (host: Darwin arm64 (host build)) |
| bridges | bridges.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/bridges/reports/report.html (host: Darwin arm64 (host build)) |
| cube | cube.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/cube/reports/report.html (host: Darwin arm64 (host build)) |
| dominosa | dominosa.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/dominosa/reports/report.html (host: Darwin arm64 (host build)) |
| dominosasolver | dominosasolver.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 1; no oracle supplied | projects/dominosasolver/reports/report.html (host: Darwin arm64 (host build)) |
| fifteen | fifteen.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/fifteen/reports/report.html (host: Darwin arm64 (host build)) |
| fifteensolver | fifteensolver.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 1; no oracle supplied | projects/fifteensolver/reports/report.html (host: Darwin arm64 (host build)) |
| filling | filling.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/filling/reports/report.html (host: Darwin arm64 (host build)) |
| fillingsolver | fillingsolver.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/fillingsolver/reports/report.html (host: Darwin arm64 (host build)) |
| fixtures | pe32-minimal.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | /Users/haseeb-mir/GameReComp/projects/pe32_min_fixture/reports/report.html (host: Darwin arm64 (host build)) |
| flip | flip.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/flip/reports/report.html (host: Darwin arm64 (host build)) |
| flood | flood.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/flood/reports/report.html (host: Darwin arm64 (host build)) |
| fuzzpuzz | fuzzpuzz.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 1; no oracle supplied | projects/fuzzpuzz/reports/report.html (host: Darwin arm64 (host build)) |
| galaxies | galaxies.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/galaxies/reports/report.html (host: Darwin arm64 (host build)) |
| galaxieseditor | galaxieseditor.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/galaxieseditor/reports/report.html (host: Darwin arm64 (host build)) |
| galaxiespicture | galaxiespicture.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 1; no oracle supplied | projects/galaxiespicture/reports/report.html (host: Darwin arm64 (host build)) |
| galaxiessolver | galaxiessolver.exe | i386 (PE32) | Windows | 93 | partial: process launched and stayed alive until the 10s timeout; no crash observed, no oracle | projects/galaxiessolver/reports/report.html (host: Darwin arm64 (host build)) |
| game2048 | game2048.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/game2048/reports/report.html (host: Darwin arm64 (host build)) |
| grapheditor | grapheditor.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/grapheditor/reports/report.html (host: Darwin arm64 (host build)) |
| guess | guess.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/guess/reports/report.html (host: Darwin arm64 (host build)) |
| guess_game_x86 | guess_game_x86.exe | i386 (PE32) | Windows | 93 | partial: process launched and stayed alive until the 3s timeout; no crash observed, no oracle | projects/guess_game_x86/reports/report.html (host: Darwin arm64 (host build)) |
| hangman_x86 | hangman_x86.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/hangman_x86/reports/report.html (host: Darwin arm64 (host build)) |
| inertia | inertia.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/inertia/reports/report.html (host: Darwin arm64 (host build)) |
| keen | keen.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/keen/reports/report.html (host: Darwin arm64 (host build)) |
| keensolver | keensolver.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 1; no oracle supplied | projects/keensolver/reports/report.html (host: Darwin arm64 (host build)) |
| lightup | lightup.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/lightup/reports/report.html (host: Darwin arm64 (host build)) |
| lightupsolver | lightupsolver.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 1; no oracle supplied | projects/lightupsolver/reports/report.html (host: Darwin arm64 (host build)) |
| loopy | loopy.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/loopy/reports/report.html (host: Darwin arm64 (host build)) |
| loopysolver | loopysolver.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 1; no oracle supplied | projects/loopysolver/reports/report.html (host: Darwin arm64 (host build)) |
| magnets | magnets.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/magnets/reports/report.html (host: Darwin arm64 (host build)) |
| magnetssolver | magnetssolver.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/magnetssolver/reports/report.html (host: Darwin arm64 (host build)) |
| map | map.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/map/reports/report.html (host: Darwin arm64 (host build)) |
| mapsolver | mapsolver.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 1; no oracle supplied | projects/mapsolver/reports/report.html (host: Darwin arm64 (host build)) |
| mineobfusc | mineobfusc.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 1; no oracle supplied | projects/mineobfusc/reports/report.html (host: Darwin arm64 (host build)) |
| mines | mines.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/mines/reports/report.html (host: Darwin arm64 (host build)) |
| mosaic | mosaic.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/mosaic/reports/report.html (host: Darwin arm64 (host build)) |
| netgame | netgame.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 3; no oracle supplied | projects/netgame/reports/report.html (host: Darwin arm64 (host build)) |
| netslide | netslide.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/netslide/reports/report.html (host: Darwin arm64 (host build)) |
| nullgame | nullgame.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/nullgame/reports/report.html (host: Darwin arm64 (host build)) |
| palisade | palisade.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/palisade/reports/report.html (host: Darwin arm64 (host build)) |
| pattern | pattern.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/pattern/reports/report.html (host: Darwin arm64 (host build)) |
| patternpicture | patternpicture.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 1; no oracle supplied | projects/patternpicture/reports/report.html (host: Darwin arm64 (host build)) |
| patternsolver | patternsolver.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 1; no oracle supplied | projects/patternsolver/reports/report.html (host: Darwin arm64 (host build)) |
| pearl | pearl.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/pearl/reports/report.html (host: Darwin arm64 (host build)) |
| pearlbench | pearlbench.exe | i386 (PE32) | Windows | 93 | partial: process launched and stayed alive until the 10s timeout; no crash observed, no oracle | projects/pearlbench/reports/report.html (host: Darwin arm64 (host build)) |
| pearlsolver | pearlsolver.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 1; no oracle supplied | projects/pearlsolver/reports/report.html (host: Darwin arm64 (host build)) |
| pegs | pegs.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/pegs/reports/report.html (host: Darwin arm64 (host build)) |
| prime-gen | prime-gen.exe | i386 (PE32) | Windows | 93 | partial: process launched and stayed alive until the 3s timeout; no crash observed, no oracle | projects/prime_gen/reports/report.html (host: Darwin arm64 (host build)) |
| puzzles | puzzles.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/puzzles/reports/report.html (host: Darwin arm64 (host build)) |
| rRootage | rr.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 2; no oracle supplied | projects/rr/reports/report.html (host: Darwin arm64 (host build)) |
| range | range.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/range/reports/report.html (host: Darwin arm64 (host build)) |
| rect | rect.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/rect/reports/report.html (host: Darwin arm64 (host build)) |
| samegame | samegame.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/samegame/reports/report.html (host: Darwin arm64 (host build)) |
| signpost | signpost.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/signpost/reports/report.html (host: Darwin arm64 (host build)) |
| signpostsolver | signpostsolver.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/signpostsolver/reports/report.html (host: Darwin arm64 (host build)) |
| singles | singles.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/singles/reports/report.html (host: Darwin arm64 (host build)) |
| singlessolver | singlessolver.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 1; no oracle supplied | projects/singlessolver/reports/report.html (host: Darwin arm64 (host build)) |
| sixteen | sixteen.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/sixteen/reports/report.html (host: Darwin arm64 (host build)) |
| slant | slant.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/slant/reports/report.html (host: Darwin arm64 (host build)) |
| slantsolver | slantsolver.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 1; no oracle supplied | projects/slantsolver/reports/report.html (host: Darwin arm64 (host build)) |
| solo | solo.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/solo/reports/report.html (host: Darwin arm64 (host build)) |
| solosolver | solosolver.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 1; no oracle supplied | projects/solosolver/reports/report.html (host: Darwin arm64 (host build)) |
| tents | tents.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/tents/reports/report.html (host: Darwin arm64 (host build)) |
| tentssolver | tentssolver.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 1; no oracle supplied | projects/tentssolver/reports/report.html (host: Darwin arm64 (host build)) |
| tetris | tetris.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/tetris/reports/report.html (host: Darwin arm64 (host build)) |
| tictactoe | tictactoe.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/tictactoe/reports/report.html (host: Darwin arm64 (host build)) |
| towers | towers.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/towers/reports/report.html (host: Darwin arm64 (host build)) |
| towerssolver | towerssolver.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 1; no oracle supplied | projects/towerssolver/reports/report.html (host: Darwin arm64 (host build)) |
| tracks | tracks.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/tracks/reports/report.html (host: Darwin arm64 (host build)) |
| trackssolver | trackssolver.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 1; no oracle supplied | projects/trackssolver/reports/report.html (host: Darwin arm64 (host build)) |
| twiddle | twiddle.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/twiddle/reports/report.html (host: Darwin arm64 (host build)) |
| undead | undead.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/undead/reports/report.html (host: Darwin arm64 (host build)) |
| unequal | unequal.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/unequal/reports/report.html (host: Darwin arm64 (host build)) |
| unequalsolver | unequalsolver.exe | i386 (PE32) | Windows | 93 | partial: process launched and stayed alive until the 10s timeout; no crash observed, no oracle | projects/unequalsolver/reports/report.html (host: Darwin arm64 (host build)) |
| unruly | unruly.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/unruly/reports/report.html (host: Darwin arm64 (host build)) |
| unrulysolver | unrulysolver.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/unrulysolver/reports/report.html (host: Darwin arm64 (host build)) |
| untangle | untangle.exe | i386 (PE32) | Windows | 93 | partial: process launched and exited 0; no oracle supplied | projects/untangle/reports/report.html (host: Darwin arm64 (host build)) |
| Cave Story | OrgView.exe | i386 (PE32) | Windows | 77 | launch-failed: rebuilt process crashed (exit -11) | projects/OrgView/reports/report.html (host: Darwin arm64 (host build)) |
| Gunroar | gr.exe | i386 (PE32) | Windows | 77 | launch-failed: rebuilt process aborted on an unhandled guest C++ exception (exit 3); guest SEH unwinding is not implemented | projects/gr/reports/report.html (host: Darwin arm64 (host build)) |
| Parsec47 | p47.exe | i386 (PE32) | Windows | 77 | launch-failed: rebuilt process aborted on an unhandled guest C++ exception (exit 3); guest SEH unwinding is not implemented | projects/p47/reports/report.html (host: Darwin arm64 (host build)) |
| Torus Trooper | tt.exe | i386 (PE32) | Windows | 77 | launch-failed: rebuilt process aborted on an unhandled guest C++ exception (exit 3); guest SEH unwinding is not implemented | projects/tt/reports/report.html (host: Darwin arm64 (host build)) |
| Tumiki Fighters | tf.exe | i386 (PE32) | Windows | 77 | launch-failed: rebuilt process aborted on an unhandled guest C++ exception (exit 3); guest SEH unwinding is not implemented | projects/tf/reports/report.html (host: Darwin arm64 (host build)) |
| GeneRally | GeneRally.exe | i386 (PE32) | Windows | 70 | launch-failed: rebuilt process boot-failed (exit 2) | projects/GeneRally/reports/report.html (host: Darwin arm64 (host build)) |
| Hero Core | herocore gm5.exe | i386 (PE32) | Windows | 70 | launch-failed: rebuilt process boot-failed (exit 2) | projects/herocore_gm5/reports/report.html (host: Darwin arm64 (host build)) |
| Hero Core | herocore gm7.exe | i386 (PE32) | Windows | 70 | launch-failed: rebuilt process boot-failed (exit 2) | projects/herocore_gm7/reports/report.html (host: Darwin arm64 (host build)) |
| IGI | IGI-Injector.exe | i386 (PE32) | Windows | 70 | launch-failed: rebuilt process boot-failed (exit 2) | projects/IGI_Injector/reports/report.html (host: Darwin arm64 (host build)) |
| TmForeverModLoader | TmForever.exe | i386 (PE32) | Windows | 70 | launch-failed: rebuilt process boot-failed (exit 2) | /Users/haseeb-mir/GameReComp/projects/TmForever/reports/report.html (host: Darwin arm64 (host build)) |
| tetris | Tetris.exe | i386 (PE32) | Windows | 70 | launch-failed: rebuilt process boot-failed (exit 2) | projects/Tetris/reports/report.html (host: Darwin arm64 (host build)) |
| NES_GameGenie | NES_GameGenieEncoder.exe | i386 (PE32) | Windows | 50 | not-run: verification did not run | projects/NES_GameGenieEncoder/reports/report.html |
| bin_win32 | ac_client.exe | i386 (PE32) | Windows | 50 | not-run: verification did not run | /Users/haseeb-mir/GameReComp/projects/AssaultCube/reports/report.html |
| Cannonball | cannonball.exe | x86-64 | Windows | 10 | not-run: verification did not run | projects/cannonball/reports/report.json (host: Darwin arm64) |
| Data | file0001.exe | None | Windows | 10 | not-run: verification did not run | projects/file0001/reports/report.json (host: Darwin arm64) |
| Data | file0002.exe | None | Windows | 10 | not-run: verification did not run | projects/file0002/reports/report.json (host: Darwin arm64) |
| Data | helpme.exe | None | Windows | 10 | not-run: verification did not run | projects/helpme/reports/report.json (host: Darwin arm64) |
| Data | netarena.exe | None | Windows | 10 | not-run: verification did not run | projects/netarena/reports/report.json (host: Darwin arm64) |
| Data | netipx.exe | None | Windows | 10 | not-run: verification did not run | projects/netipx/reports/report.json (host: Darwin arm64) |
| Data | netmodem.exe | None | Windows | 10 | not-run: verification did not run | projects/netmodem/reports/report.json (host: Darwin arm64) |
| Data | netterm.exe | None | Windows | 10 | not-run: verification did not run | projects/netterm/reports/report.json (host: Darwin arm64) |
| Data | order.exe | None | Windows | 10 | not-run: verification did not run | projects/order/reports/report.json (host: Darwin arm64) |
| Data | rtm.exe | None | Windows | 10 | not-run: verification did not run | projects/rtm/reports/report.json (host: Darwin arm64) |
| Data | setup.exe | None | Windows | 10 | not-run: verification did not run | projects/setup/reports/report.json (host: Darwin arm64) |
| Data | shipedit.exe | None | Windows | 10 | not-run: verification did not run | projects/shipedit/reports/report.json (host: Darwin arm64) |
| Data | tyrian.exe | None | Windows | 10 | not-run: verification did not run | projects/tyrian/reports/report.json (host: Darwin arm64) |
| OpenTyrian | opentyrian.exe | x86-64 | Windows | 10 | not-run: verification did not run | projects/opentyrian/reports/report.json (host: Darwin arm64) |
| Pixeltetris | Pixeltetris.exe | x86-64 | Windows | 10 | not-run: verification did not run | projects/Pixeltetris/reports/report.json (host: Darwin arm64) |
| Snake | snake-windows-x86_64.exe | x86-64 | Windows | 10 | not-run: verification did not run | projects/snake_windows_x86_64/reports/report.json (host: Darwin arm64) |

## Local corpus (not yet measured)

All corpus entries have been measured.

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
