<div align="center">

<img src="assets/recomp-studio-logo.png" alt="GameReComp logo" width="180">

# GameReComp

**Windows games → editable C source → native Linux/macOS builds**

Recompile legacy Windows PC games using [PCRecomp](https://github.com/sp00nznet/pcrecomp), [rev.ng](https://github.com/revng/revng), and reusable runtime shims.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/target-Linux%20%7C%20macOS-informational)](#quick-start)
[![Input](https://img.shields.io/badge/input-Windows%20PE32-orange)](#what-it-does)
[![Verified games](https://img.shields.io/badge/verified%20playable%20games-0-yellow)](#proof--gameplay)
[![Contributions welcome](https://img.shields.io/badge/contributions-welcome-brightgreen)](https://github.com/heaven-hm/GameReComp/issues)

[**Quick start**](#quick-start) · [**Screenshots & proof**](#proof--gameplay) · [**Game matrix**](docs/GAME_MATRIX.md) · [**Contribute**](#contributing)

</div>

## What it does

```text
Windows game.exe → recover & lift → generate C → compile → run → verify
```

- Generates a rebuildable **C source project** and **native executable** with Windows API shims.
- Uses **rev.ng** for higher-level source recovery; preserves a low-level fallback where needed.
- Records build provenance, execution results, and verification evidence. **Currently supports PE32 (x86) inputs.**

## Proof & gameplay

**Cave Story (Doukutsu.exe)** — the rebuilt game reaches its frame loop and renders. **143/143 imports** bind; the run is classified **partial**, not independently verified playable gameplay.

<table>
<tr>
<td align="center"><img src="docs/screenshots/cavestory-doukutsu-01-intro.png" alt="Cave Story captured intro frame 1" width="260"><br><sub>Intro frame</sub></td>
<td align="center"><img src="docs/screenshots/cavestory-doukutsu-02-intro.png" alt="Cave Story captured intro frame 2" width="260"><br><sub>Animation</sub></td>
<td align="center"><img src="docs/screenshots/cavestory-doukutsu-03-screen.png" alt="Cave Story captured later frame" width="260"><br><sub>Later captured frame</sub></td>
</tr>
</table>

| Evidence | Current result |
| --- | --- |
| Independently **verified playable games** | **0** |
| Cave Story | Rendering/frame loop observed; **partial** |
| Source recovery | **18/955** rev.ng provider functions with scoped differential certifications in the documented experiment |

**Evidence:** [Game matrix](docs/GAME_MATRIX.md) · [Verification methodology](docs/VERIFICATION.md) · [Reports](reports/). Captured frames demonstrate rendering, **not** gameplay equivalence. Upstream PCRecomp game successes are documented separately in the [matrix](docs/GAME_MATRIX.md) and are not counted as GameReComp-verified ports.

## Quick start

**Requirements:** Python 3.10+, Capstone 5, CMake 3.16+, C compiler, pkg-config, SDL2, SDL2_ttf.

```bash
git clone https://github.com/heaven-hm/GameReComp.git
cd GameReComp
python3 -m pip install capstone

./RevEng /path/to/game.exe
./RevEng /path/to/game.exe --interactive
```

Generated files: `projects/<game>/src/` (C), `build/` (binary), `reports/` (evidence). Supply your own legally obtained game executable and assets. [Installation & troubleshooting →](docs/GETTING_STARTED.md)

<details>
<summary><b>More commands</b></summary>

```bash
./RevEng /path/to/game.exe --assets-dir /path/to/game/assets
./RevEng /path/to/game.exe --no-build --no-run
./RevEng /path/to/game.exe --timeout 60 --json
sh verify.sh --fast
```

</details>

## Contributing

Contributions to binary lifting, readable C recovery, runtime shims, and reproducible gameplay tests are welcome. Check [open issues](https://github.com/heaven-hm/GameReComp/issues), run `sh verify.sh --fast`, then open a pull request with test evidence. **Do not commit proprietary game binaries or assets.**

[Issues](https://github.com/heaven-hm/GameReComp/issues) · [Contributors](CONTRIBUTORS.md) · [Architecture](docs/ARCHITECTURE.md)

## License

**[MIT](LICENSE)** · [Third-party notices](NOTICE) · [Dependency licenses](docs/LICENSES.md)
