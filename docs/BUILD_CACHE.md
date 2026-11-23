# Content-addressed object cache for generated C

`tools/autorecomp/build_cache.py` is a **standalone, opt-in** cache for the
objects produced when compiling generated C sources (`cc -c unit.c -o unit.o`).
It is not wired into the pipeline: it imports no pipeline module and nothing in
`pipeline.py` or the CMake files references it. A caller adopts it explicitly,
one compile at a time. Nothing is cached unless the caller opts in.

The design follows the artifact-cache methodology used in the Stela port work
(pinned Stela audit commit `140ffaf716e2fa8a2f13d55880ca0fc4521df6fe`):
derive identity from content, publish atomically, validate on read, fail closed.
Stela's engine is GPL-2.0-or-later; this module is original code and copies none
of it. See `docs/STELA_BENCHMARK_METHOD.md` for rule zero on speed claims.

## What is keyed

The object key (`object_key()`) is a SHA-256 over length-prefixed parts:

| Component | Function | Contents |
| --- | --- | --- |
| Schema | `CACHE_SCHEMA` | `gamerecomp-build-cache-v1`; bumps invalidate all older entries |
| Source | `source_digest()` | SHA-256 of the `.c` file **bytes**, not its path or mtime |
| Compiler | `compiler_identity()` | resolved path, `<compiler> --version` output, and the compiler file's own bytes when it is a regular file under 64 MiB |
| Flags | `flags_digest()` | SHA-256 of the flag list, **in order**, length-prefixed per flag |
| Extra (optional) | `extra_identity` | caller-supplied discriminator for hidden inputs; empty by default |

The three documented components are always bound. A changed source byte, a
different compiler, or a reordered flag produces a different key and therefore
a different cache entry. `-c`, `-o` and the source path are *not* identity
inputs: `compile_cached()` constructs that part of the command itself.

### Why no stale object can be reused

- The key is content-derived, so a source edit cannot accidentally hit the old
  object, and an object never carries a source path or timestamp identity.
- The compiler file bytes are bound in addition to `--version`, so a wrapper
  whose version text is unchanged but whose behaviour changed cannot collide.
- The schema prefix prevents an older entry from being read by a newer rule set.
- Each entry stores a JSON manifest with the key, object SHA-256 and byte
  count. `get_bytes()` re-hashes the object on every read and returns `None`
  unless the manifest schema, key, digest **and** length all match. Empty
  objects are rejected. A corrupt or partial entry is a miss, never a hit.
- Publication writes the object first and the manifest second, each to a
  unique temporary file that is flushed, `fsync`ed and `os.replace`d into
  place. The manifest is the commit marker: a crash between the two writes
  leaves an object with no manifest, which reads as a miss. Concurrent writers
  cannot clobber a valid entry (`put_bytes()` returns `False`); if they race
  before either commits, validation still rejects any mismatched pair.
- On a hit, `get_object()` installs the object at the destination through the
  same temporary-file + replace path, so a reader never sees a partial object.
  A failed compile leaves neither a cache entry nor a changed destination.

## Enabling and using it

Opt in with `RECOMP_BUILD_CACHE=1`. The store lives at
`$RECOMP_BUILD_CACHE_DIR` when set, otherwise `$TMPDIR/gamerecomp-build-cache`.
Any other value of `RECOMP_BUILD_CACHE` (or none) disables the cache: no key is
computed, no entry is read or written, and compilation proceeds normally.

```python
from tools.autorecomp.build_cache import compile_cached

result = compile_cached(
    source=project / "src/recomp/gen/recomp_0000.c",
    compiler=clang,
    flags=["--target=wasm32-unknown-unknown", "-nostdinc", "-O2", "-w"],
    output=build / "obj/recomp_0000.o",
)
# result: {hit, key, cached, compiled, returncode, stderr, output}
```

`generated_sources(project_dir)` is an optional helper that lists a generated
project's `src/recomp/gen/recomp_*.c` files plus `src/highlevel/*.c` in sorted
order. `BuildObjectCache(key)` exposes the entry directly (`get_bytes`,
`put_bytes`, `get_object`, `capture_object`, `clear`) for callers that manage
their own compiles.

Command line (flags use the `--flag=VALUE` form so the leading dash is not
parsed as an option):

```
python3 -m tools.autorecomp.build_cache key --source unit.c --compiler cc \
    --flag=-O2 --flag=-w [--verbose]
python3 -m tools.autorecomp.build_cache dir
python3 -m tools.autorecomp.build_cache clear --key <64-hex>
python3 -m tools.autorecomp.build_cache sources /path/to/project
```

## Verification

```
python3 -m pytest tests/test_build_cache.py -q
```

The suite is light: it uses temporary files and an injected fake compiler
runner, so it needs no game assets and no real toolchain. It covers key
sensitivity to each component, content addressing independent of path,
checksum corruption, atomic publish and temporary-file hygiene, hit/miss and
re-key behaviour, failed compiles, the disabled path, and the CLI.

## Honest limits

- **Identity is a heuristic for the compiler.** `--version` plus the compiler
  file bytes is strong in practice, but it cannot see state outside that file
  (a toolchain file read from elsewhere, an environment variable that changes
  code generation). Pass such inputs through `extra_identity`, or leave the
  cache off. Flags that read files (for example `-include header.h`) must list
  the file's content-affecting identity themselves; the cache hashes the flag
  string, not the file it names.
- **Path-dependent debug info.** The key intentionally ignores the source
  path. With `-g`, some compilers embed that path in the object; two identical
  sources at different paths would then share a key. For such builds pass a
  path discriminator via `extra_identity` (or normalise the path with a flag
  such as `-fdebug-prefix-map`, which is itself part of the key). The pipeline
  build this targets compiles generated units without `-g`.
- **Compile determinism is assumed for a key.** Two writers that race on the
  same new key may produce different bytes with a non-deterministic compiler;
  the manifest digest validation turns that into a miss on the next read
  rather than silently serving a wrong object. Deterministic builds avoid the
  wasted work.
- **No eviction or cross-host transport.** Entries accumulate under the cache
  directory until `clear()` or an external cleanup removes them; there is no
  size cap, garbage collection or remote cache. Cache roots are local paths.
- **Filesystem atomicity.** `os.replace` is atomic on POSIX for same-volume
  renames and on modern Windows/NTFS; network filesystems may differ. `fsync`
  is best-effort on directories that refuse it.
- **No benchmark is claimed here.** This change is not integrated into the
  pipeline, so it has no measured wall-time effect. Any speed claim needs a
  paired before/after run of `tools/stela_bench` with the same input and
  settings, as required by `docs/STELA_BENCHMARK_METHOD.md`.
