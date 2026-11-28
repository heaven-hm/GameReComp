# Rust builds use Kache

Local and CI Rust builds use [Kache](https://github.com/kunobi-ninja/kache)
with Cargo as the command front end. Benchmark on this host (2026-11-26):

- GameRecomp-SDK: cold 8.25 s, warm 0.88 s, target 72 KB.
- GameReComp: cold 542.57 s, warm 1.25 s.
- Blaze's daemon failed on GameReComp (`USAGE` before listening) and its shared
  store measured 1.8 GB, versus Kache's content-addressed cache.

Full numbers and reproduction commands are in the SDK repo's
`docs/BUILD_TOOL_BENCHMARK.md`. The release workflow installs Kache on
Linux/macOS runners; Windows runners keep plain Cargo.
