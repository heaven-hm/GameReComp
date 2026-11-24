# GBX header parser provenance

## Upstream source
- Repository: `https://github.com/BigBang1112/gbx-net`
- Commit inspected: `8c839df5c5d22c61eee36272ed3997445de6a88c`
- Licence: MIT for `Src/GBX.NET` (GBX.NET 2 README, "License" section). The
  LZO-compressed body path requires GBX.NET.LZO (GPLv3) and was **not** copied.
- Exact files read:
  - `Src/GBX.NET/Components/GbxHeaderBasic.cs` — magic/version/format/compression/mode layout.
  - `Src/GBX.NET/Serialization/GbxHeaderReader.cs` — class id, user-data length
    limit (16 MiB), number-of-nodes validation, version gate for user data.
- Destination: `tools/gbx/parser.py` (new), adapted to Python; no C# code copied
  verbatim. The MIT attribution is retained in the module docstring.

## Scope
Only the **uncompressed header** is parsed: magic, version, format, compression
flags, mode, class id, user-data length (skipped with bounds checks), node count
and body offset. Compressed body, reference table, node chunks, textures, models
and archives are **not** implemented and are not claimed.

## Real fixtures used for verification
TrackMania Nations Forever installation under
`/Users/haseeb-mir/Library/TrackManiaMacOS/games/tmnf/.../TmNationsForever`
was supplied via `RECOMP_GBX_FIXTURES`; the first five `*.Gbx` files were parsed
successfully in `tests/test_gbx_parser.py`. No game assets are committed to git;
the test skips with a reason when the environment variable is absent.

## Other upstream projects inspected in this assignment
- PCRecomp `a85ea970…` (MIT): existing filesystem/resource code was audited; no
  parallel asset subsystem was added.
- ModTMNF / ReHitman: no code copied; version-specific offsets are not reused
  across different game builds.
- Assimp / KTX / FFmpeg: no new dependency was introduced; the existing FFmpeg
  bridge in `runtime/win32hle` remains the host decoder path.
