# GameReComp

**Recompile legacy Windows PC games into native, editable C projects for Linux and macOS.**

GameReComp automates binary analysis, C generation, compilation, execution, and verification. It builds on [PCRecomp](https://github.com/sp00nznet/pcrecomp) for static recompilation and Windows runtime support, and uses [rev.ng](https://github.com/revng/revng) for higher-level source recovery.

**Status:** Early development. The current pipeline supports **32-bit Windows PE** inputs; broader compatibility and fully readable source remain in progress.

## What it does

```text
Windows game.exe → recover / lift → generate C → native build → run → verify
```

- Produces a self-contained C source project and native executable with runtime shims.
- Attempts to recover readable source; falls back to low-level C when necessary.
- Records build results, source provenance, and the scope of verification. A successful build alone is not a verified game.

## Proof and current results

- **Cave Story (Doukutsu.exe):** Recompiled build reaches its frame loop and renders; 143/143 imports bound. This is a **partial demonstration**, not verified gameplay equivalence.
- **Readable source:** 18 of 955 rev.ng provider functions have recorded scoped differential certifications in the documented Cave Story experiment.
- **Independently verified playable games:** **0** under this repository's current acceptance criteria.

See [game test matrix](docs/GAME_MATRIX.md), [verification details](docs/VERIFICATION.md), and [reports](reports/) for evidence and limitations.

## Quick start

Requires **Python 3**, **CMake 3.16+**, a **C compiler**, **pkg-config**, **SDL2**, and **SDL2_ttf**.

```sh
git clone https://github.com/heaven-hm/GameReComp.git
cd GameReComp

./RevEng /path/to/game.exe
./RevEng /path/to/game.exe --interactive
```

Generated output is under `projects/<game>/`:

- `src/` — generated C and runtime code
- `build/` — native executable
- `reports/` — build and verification evidence

Use your own legally obtained game executable and assets. For setup and troubleshooting, see [Getting started](docs/GETTING_STARTED.md).

## Contributing

Contributions to binary lifting, readable-source recovery, runtime compatibility, game tests, and documentation are welcome.

1. Check [open issues](https://github.com/heaven-hm/GameReComp/issues) and discuss larger changes before implementing them.
2. Make your change with tests. Run `sh verify.sh --fast`.
3. Open a pull request with a concise description, test results, and evidence for any game-compatibility claims.

Do not commit proprietary game executables or assets.

## License

[MIT](LICENSE). See [NOTICE](NOTICE) and [third-party licenses](docs/LICENSES.md) for upstream code and dependency obligations.
