# PE32 multi-module boot evidence — fail-closed verdict joiner

`tools/multimodule_evidence.py` emits per-module verdicts for a CLI-supplied
PE32 multi-module boot record. It **does not run** a game, native binary,
Frida, Wine or Unicorn. It joins three kinds of supplied evidence and treats
every boot-record value as a claim to be checked, never as a verdict:

| CLI input | What it supplies | How it is checked |
|---|---|---|
| `--boot <record.json>` | EXE + companion modules, bound/unresolved import counts, native binary hash, frame digests, a `source` pointer to a committed proof | schema validation, self-declared-verdict refusal, cross-check against the proof, duplicate/unattested module detection |
| `--proof <issue28-proof.json>` | the committed run proof the boot record cites | bytes re-hashed and compared with `source.sha256`; all module facts re-derived from its JSON, not copied from the boot record |
| `--frame <image> …` | actual captured frames | SHA-256 recomputed from bytes; BMP/PNG decoded to count distinct RGB colours; blank/undecodable frames rejected |
| `--binary <native>` (optional) | native binary bytes | SHA-256 recomputed and compared with the proof's `native_binary_sha256` |

A companion's "real mapping" is a **separate companion import counter in the
committed proof** (e.g. `run.companion_sdl_net_imports_bound = 34`), distinct
from the EXE counter (`run.imports_bound = 166`), with zero unresolved imports.
The boot record names that counter via
`mapping.source_key: "companion_<name>_imports_bound"`; the tool requires the
key to exist in the proof, the mapped count to equal it, the module's own
`imports_bound` to equal it, and the name token (`sdl_net` for `SDL_net.dll`)
to match. No virtual address, span or export count is invented — those were
not recorded by the source evidence and are not claimed here.

## Verdict semantics

* **VERIFIED** — the module is attested by the proof (EXE hash from
  `originals`, or a companion mapping counter), the bound and unresolved
  import counts agree, the native binary hash agrees, and at least one
  non-blank frame was proven from bytes. Scoped to boot/binding/render facts.
* **PARTIAL** — positive but incomplete: no companion mapping, no native
  hash, or no provable frame. Exact reason codes are emitted per module.
* **BLOCKED** — no evidence, a fake/unattested module, a self-declared
  verdict, contradictory counts, mismatched hashes, unresolved imports, or a
  blank/undecodable frame. Tampering in any input blocks every module.

Overall verdict is `VERIFIED` only if every module is `VERIFIED`. Exit status:
0 for `VERIFIED`, 1 for `PARTIAL`/`BLOCKED`, 2 for an output write error.
Output is deterministic (sorted keys and reasons, no timestamps or host
state).

## Real run: Chocolate Doom (issue #28 proof)

Committee evidence source: `proof/issue-28/issue28-proof.json` (SHA-256
`550c129263d1c3b95db107f6005a1d7811c799e5a657f43db075e87a3e1e0187`) and its
three committed BMP frames. The boot record in Appendix A was written to a
temporary file and the tool run from the repository root:

```sh
python3 -m tools.multimodule_evidence \
  --boot /tmp/issue9-boot.json \
  --proof proof/issue-28/issue28-proof.json \
  --frame proof/issue-28/frames/frame-0000.bmp \
  --frame proof/issue-28/frames/frame-0001.bmp \
  --frame proof/issue-28/frames/frame-0002.bmp \
  --label 'issue #9: chocolate-doom.exe + SDL_net.dll multi-module boot' \
  --out /tmp/issue9-verdict.json
```

Result: **exit 0, overall `VERIFIED`, 2/2 modules `VERIFIED`, 3 non-blank
frames, 0 reasons.** The resulting JSON is committed verbatim in Appendix B
(this work item is restricted to three files, so the verdict JSON is embedded
here rather than added as a fourth standalone file). `tests/test_multimodule_evidence.py::test_committed_chocolate_doom_evidence_verified`
reproduces the same join against the committed bytes.

### What this proves and what it does not

Proven: the committed proof attests 166 EXE imports plus a separate 34-import
`SDL_net.dll` companion table, 0 unresolved; the native binary hash and EXE
hash agree across the boot record and proof; the three frame bytes re-hash to
the claimed digests and decode to 3,468 distinct RGB colours each (none
blank).

Not proven: Windows execution, gameplay equivalence, dynamic `LoadLibrary`
lifting of arbitrary DLLs, guest TLS/SEH/C++ exception unwinding, MSVC/Borland
CRT parity or cross-module ABI calls. The native binary and companion DLL
bytes are not committed, so their hashes are cross-checked between the boot
record and proof, not recomputed (pass `--binary` to re-hash when bytes exist).

## Issue #9 remains OPEN — exact blocker

Issue #9's full acceptance is **DLL code lifting, TLS/SEH/C++ EH, CRT and
cross-module ABI with gameplay evidence**. This tool proves only a scoped
multi-module *boot/binding/render* claim for Chocolate Doom. The companion-DLL
null-target evidence merged in PR #306 documents the remaining gap directly:
Winamp's dynamically loaded companions (`nsutil.dll`, `tataki.dll`,
`nde.dll`, `wasabi2.w5s`) still have unresolved CRT/C++/Win32 imports and a
`NULL vtable/interface` indirect call that terminates the guest; there is no
lifted `DllMain` for those modules, no guest SEH unwind and no gameplay oracle.
Therefore #9 is **not closed**. Remaining gates: arbitrary dynamic
`LoadLibrary` DLL lifting, guest SEH/MSVC/Borland/D exception dispatch,
CRT/C++ ABI parity across module boundaries, and end-to-end gameplay evidence
against an original-Windows oracle.

## Appendix A — boot record used for the committed run

```json
{
  "schema_version": 1,
  "label": "issue #9: chocolate-doom.exe + SDL_net.dll multi-module boot",
  "source": {
    "path": "proof/issue-28/issue28-proof.json",
    "sha256": "550c129263d1c3b95db107f6005a1d7811c799e5a657f43db075e87a3e1e0187"
  },
  "native_binary": {
    "sha256": "c4c000a39369c3964cac0ab11bbba86e8fcd1d2d1980f2aa2f5db14f9640a411"
  },
  "modules": [
    {
      "name": "chocolate-doom.exe",
      "role": "exe",
      "sha256": "d1b9dabc567d0a79480a4b319850b69ea76e69a3b4be578586ae045b96af33cc",
      "imports_bound": 166,
      "unresolved_imports": 0
    },
    {
      "name": "SDL_net.dll",
      "role": "companion",
      "companion_of": "chocolate-doom.exe",
      "imports_bound": 34,
      "unresolved_imports": 0,
      "mapping": {
        "source_key": "companion_sdl_net_imports_bound",
        "imports_bound": 34
      }
    }
  ],
  "frames": [
    {
      "path": "proof/issue-28/frames/frame-0000.bmp",
      "sha256": "9b547da7426542ce27f39dc2c57bccf9942e309628d50b2666f4464e9656513b"
    },
    {
      "path": "proof/issue-28/frames/frame-0001.bmp",
      "sha256": "9b547da7426542ce27f39dc2c57bccf9942e309628d50b2666f4464e9656513b"
    },
    {
      "path": "proof/issue-28/frames/frame-0002.bmp",
      "sha256": "9b547da7426542ce27f39dc2c57bccf9942e309628d50b2666f4464e9656513b"
    }
  ]
}
```

## Appendix B — committed verdict JSON (real evidence)

```json
{
  "evidence": {
    "frames": {
      "attested": 3,
      "bytes_supplied": 3,
      "claimed": 3,
      "nonblank": 3,
      "verified": [
        {
          "blank": false,
          "distinct_colors": 3468,
          "format": "bmp24",
          "height": 200,
          "path": "proof/issue-28/frames/frame-0000.bmp",
          "sha256": "9b547da7426542ce27f39dc2c57bccf9942e309628d50b2666f4464e9656513b",
          "width": 256
        },
        {
          "blank": false,
          "distinct_colors": 3468,
          "format": "bmp24",
          "height": 200,
          "path": "proof/issue-28/frames/frame-0001.bmp",
          "sha256": "9b547da7426542ce27f39dc2c57bccf9942e309628d50b2666f4464e9656513b",
          "width": 256
        },
        {
          "blank": false,
          "distinct_colors": 3468,
          "format": "bmp24",
          "height": 200,
          "path": "proof/issue-28/frames/frame-0002.bmp",
          "sha256": "9b547da7426542ce27f39dc2c57bccf9942e309628d50b2666f4464e9656513b",
          "width": 256
        }
      ]
    },
    "native_binary": {
      "actual_sha256": null,
      "attested": true,
      "bytes_supplied": false,
      "sha256": "c4c000a39369c3964cac0ab11bbba86e8fcd1d2d1980f2aa2f5db14f9640a411"
    },
    "source": {
      "hash_verified": true,
      "path": "proof/issue-28/issue28-proof.json",
      "sha256": "550c129263d1c3b95db107f6005a1d7811c799e5a657f43db075e87a3e1e0187"
    }
  },
  "issue": 9,
  "label": "issue #9: chocolate-doom.exe + SDL_net.dll multi-module boot",
  "limits": [
    "VERIFIED is scoped to boot/binding/render facts: the committed proof attests the EXE hash, the separate companion import counter, the native binary hash and the frame digests, and this tool re-hashes and decodes the actual frame bytes. It is not a Windows-oracle or gameplay claim.",
    "No game, native, Frida, Wine or Unicorn is executed here. Native binary and companion DLL bytes are not committed with this evidence; their hashes are cross-checked between the boot record and the proof, not recomputed from bytes. --binary re-hashes the native binary when supplied.",
    "No value in any input is promoted by being claimed. A self-declared verdict, a module absent from the proof, a contradictory count or a hash that disagrees with supplied bytes is always BLOCKED.",
    "A blank, undecodable, unattested or tampered frame is never render evidence; distinct colours are measured from decoded pixels only."
  ],
  "modules": [
    {
      "facts": {
        "hash_attested": true,
        "imports_bound": 166,
        "mapping_attested": false,
        "native_hash_attested": true,
        "nonblank_frames": 3,
        "unresolved_imports": 0
      },
      "index": 0,
      "name": "chocolate-doom.exe",
      "reasons": [],
      "role": "exe",
      "verdict": "VERIFIED"
    },
    {
      "facts": {
        "hash_attested": false,
        "imports_bound": 34,
        "mapping_attested": true,
        "native_hash_attested": true,
        "nonblank_frames": 3,
        "unresolved_imports": 0
      },
      "index": 1,
      "name": "SDL_net.dll",
      "reasons": [],
      "role": "companion",
      "verdict": "VERIFIED"
    }
  ],
  "reasons": [],
  "schema_version": 1,
  "summary": {
    "blocked": 0,
    "modules": 2,
    "nonblank_frames": 3,
    "partial": 0,
    "verified": 2
  },
  "tool": "tools/multimodule_evidence.py",
  "verdict": "VERIFIED"
}
```
