# GameReComp 0.0.1 beta release

This is the reproducible release and verification checklist for the first
beta. GitHub Actions builds native CLI binaries for Linux x86_64, macOS arm64,
and Windows x86_64 and attaches them to the `v0.0.1` GitHub release.

## Build the CLI locally

```bash
cargo build --release
./target/release/gamerecomp --version
./GameReComp --version
```

`GameReComp` builds `target/release/gamerecomp` when needed and forwards all
arguments to it.

## Run one executable

```bash
./GameReComp /path/to/game.exe \
  --input-os windows --input-arch x86 \
  --target-os macos --target-arch arm64 \
  --timeout 10 --no-progress --clean-objects --verify-standalone
```

The pipeline runs `inspect`, `recover`, `translate`, `project`, `compile`,
`run`, `verify`, and `report` in that order. Exit code `0` means an
oracle-backed success, `2` means a pipeline failure, and `3` means the rebuilt
process ran but no gameplay oracle proved it.

## Run the measured Win32 corpus

The supplied corpus uses symlinked game directories, so use a NUL-safe loop:

```bash
find -L /Users/haseeb-mir/Downloads/recomp-apps/games/win32 \
  -type f -iname '*.exe' -print0 |
while IFS= read -r -d '' exe; do
  ./GameReComp "$exe" \
    --input-os windows --input-arch x86 \
    --target-os macos --target-arch arm64 \
    --timeout 10 --no-progress --clean-objects --verify-standalone
done
```

For a flat directory, the equivalent command is:

```bash
./GameReComp batch /path/to/flat-win32-directory \
  --timeout 10 --clean-objects --verify-standalone
```

## Inspect results and update Markdown

```bash
./GameReComp tasks
python3 -m tools.gamesdb --matrix --record projects/<project-name>
```

The generated list is `docs/GAME_MATRIX.md`; `table.md`,
`reports/games/results.json`, and the README measured table are updated from
the same evidence. Do not commit `projects/<game>/src`, `build`, or original
game files.

## Verify the repository

```bash
sh verify.sh --fast
cargo test --release
cargo build --release
git diff --check
```

After recording reports, delete only the generated project directories for the
measured inputs. Keep the Markdown evidence and aggregate JSON.

## GitHub release assets

Tagging `v0.0.1` starts `.github/workflows/release.yml`. It builds and uploads:

- `GameReComp-v0.0.1-linux-x86_64.tar.gz`
- `GameReComp-v0.0.1-macos-arm64.tar.gz`
- `GameReComp-v0.0.1-windows-x86_64.zip`

The release source branch remains `develop`; the tag is the immutable beta
source point.
