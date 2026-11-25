# 100-game engine stress test — final report

Engine: `verify-engine/` (PRs #216–#220), via `highball run` only.
Both sides are the SAME original executable: this is an identical-binary
CONTROL validating the engine across 100 real games, NOT game parity.

- Games processed: 100
- Verdicts (after correcting vacuous empty-output passes with the fixed engine): {'blocked': 90, 'pass': 9, 'fail': 1}
- Vacuous cases rechecked with fixed engine: 17

## Honest reading

`pass` in this matrix means both Highball runs of the same PE produced
identical non-empty output and exit code for the supplied input. `blocked`
means timeouts, launcher failures, or no guest output — never a soft pass.
No game is claimed ported, recompiled or playable by this test.

## Notable finding

`fail` at index 47 (`unrulysolver.exe`): the SAME PE with identical input
produced different solver output on two runs (`0 . 0 . 0...` vs
`1 . . . . . 1...`). The engine detected genuine nondeterminism rather
than fabricating a PASS.

## Per-game results

| # | Game | Verdict | Note |
|---|------|---------|------|
| 0 | opentyrian.exe | blocked |  |
| 1 | GeneRally.exe | blocked |  |
| 2 | TrackEditor.exe | blocked |  |
| 3 | tt.exe | blocked |  |
| 4 | rr.exe | blocked |  |
| 5 | herocore gm5.exe | blocked | rechecked with PR #220 fix |
| 6 | herocore gm7.exe | blocked | rechecked with PR #220 fix |
| 7 | cannonball.exe | pass |  |
| 8 | dxIka.exe | blocked | rechecked with PR #220 fix |
| 9 | noiz2sa.exe | blocked |  |
| 10 | wf.exe | blocked |  |
| 11 | tf.exe | blocked |  |
| 12 | Pixeltetris.exe | blocked |  |
| 13 | Doukutsu.exe | blocked | rechecked with PR #220 fix |
| 14 | OrgView.exe | blocked | rechecked with PR #220 fix |
| 15 | DoConfig.exe | blocked |  |
| 16 | gr.exe | blocked |  |
| 17 | p47.exe | blocked |  |
| 18 | snake-windows-x86_64.exe | blocked |  |
| 19 | netmodem.exe | blocked | rechecked with PR #220 fix |
| 20 | rtm.exe | blocked | rechecked with PR #220 fix |
| 21 | file0001.exe | blocked | rechecked with PR #220 fix |
| 22 | setup.exe | blocked | rechecked with PR #220 fix |
| 23 | file0002.exe | blocked | rechecked with PR #220 fix |
| 24 | netterm.exe | blocked | rechecked with PR #220 fix |
| 25 | tyrian.exe | blocked | rechecked with PR #220 fix |
| 26 | helpme.exe | blocked | rechecked with PR #220 fix |
| 27 | order.exe | blocked | rechecked with PR #220 fix |
| 28 | netipx.exe | blocked | rechecked with PR #220 fix |
| 29 | netarena.exe | blocked | rechecked with PR #220 fix |
| 30 | shipedit.exe | blocked | rechecked with PR #220 fix |
| 31 | pearlsolver.exe | pass |  |
| 32 | unequal.exe | blocked |  |
| 33 | pearl.exe | blocked |  |
| 34 | flood.exe | blocked |  |
| 35 | patternpicture.exe | blocked |  |
| 36 | mineobfusc.exe | pass |  |
| 37 | dominosasolver.exe | pass |  |
| 38 | magnets.exe | blocked |  |
| 39 | flip.exe | blocked |  |
| 40 | fuzzpuzz.exe | pass |  |
| 41 | inertia.exe | blocked |  |
| 42 | pq.exe | blocked |  |
| 43 | singles.exe | blocked |  |
| 44 | untangle.exe | blocked |  |
| 45 | keensolver.exe | pass |  |
| 46 | pearlbench.exe | blocked |  |
| 47 | unrulysolver.exe | fail |  |
| 48 | patternsolver.exe | pass |  |
| 49 | towers.exe | blocked |  |
| 50 | singlessolver.exe | pass |  |
| 51 | fifteensolver.exe | pass |  |
| 52 | pegs.exe | blocked |  |
| 53 | galaxies.exe | blocked |  |
| 54 | towerssolver.exe | blocked |  |
| 55 | twiddle.exe | blocked |  |
| 56 | putty-w32.exe | blocked |  |
| 57 | netgame.exe | blocked |  |
| 58 | fillingsolver.exe | blocked |  |
| 59 | busybox-w32.exe | blocked |  |
| 60 | magnetssolver.exe | blocked |  |
| 61 | range.exe | blocked |  |
| 62 | loopy.exe | blocked |  |
| 63 | galaxiespicture.exe | blocked |  |
| 64 | tracks.exe | blocked |  |
| 65 | trackssolver.exe | blocked |  |
| 66 | samegame.exe | blocked |  |
| 67 | lightup.exe | blocked |  |
| 68 | solo.exe | blocked |  |
| 69 | netslide.exe | blocked |  |
| 70 | mines.exe | blocked |  |
| 71 | loopysolver.exe | blocked |  |
| 72 | unequalsolver.exe | blocked |  |
| 73 | dominosa.exe | blocked |  |
| 74 | sixteen.exe | blocked |  |
| 75 | mosaic.exe | blocked |  |
| 76 | lightupsolver.exe | blocked |  |
| 77 | blackbox.exe | blocked |  |
| 78 | unruly.exe | blocked |  |
| 79 | cube.exe | blocked |  |
| 80 | pageant-w32.exe | blocked |  |
| 81 | bridges.exe | blocked |  |
| 82 | galaxiessolver.exe | blocked |  |
| 83 | mapsolver.exe | blocked |  |
| 84 | nullgame.exe | blocked |  |
| 85 | palisade.exe | blocked |  |
| 86 | puzzles.exe | blocked |  |
| 87 | rect.exe | blocked |  |
| 88 | signpostsolver.exe | blocked |  |
| 89 | guess.exe | blocked |  |
| 90 | slantsolver.exe | blocked |  |
| 91 | pattern.exe | blocked |  |
| 92 | grapheditor.exe | blocked |  |
| 93 | map.exe | blocked |  |
| 94 | fifteen.exe | blocked |  |
| 95 | keen.exe | blocked |  |
| 96 | filling.exe | blocked |  |
| 97 | slant.exe | blocked |  |
| 98 | solosolver.exe | blocked |  |
| 99 | undead.exe | blocked |  |
