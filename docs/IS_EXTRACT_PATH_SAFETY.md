# Issue #344 — InstallShield extractor path confinement

## Problem

`tools/assets/isextract.py` built each member's destination directly from the
untrusted `data1.hdr` directory/name strings, in both `extract_all()` (the
`extract()` path in the issue report) and `extract_cab_only()`:

```python
rel_path = os.path.join(fd.directory.replace('\\', os.sep), fd.name)
out_path = os.path.join(output_dir, rel_path)
os.makedirs(os.path.dirname(out_path), exist_ok=True)
```

There was no containment check. A crafted archive could use `..` components
(`../../evil`, `..\..\evil`), an absolute name (`/tmp/evil` — note that
`os.path.join(output_dir, "/abs")` silently discards `output_dir`), a Windows
drive prefix (`C:\...`), or a symlink already inside the extraction tree to
create directories and overwrite arbitrary files writable by the user.

## Fix

A shared, fail-closed helper now resolves every member destination **before**
any `os.makedirs()` or `open(..., "wb")`:

- Lexical checks on the raw, untrusted strings: empty name, NUL byte, leading
  `/` or `\` (absolute/UNC), drive-letter prefix (`^[A-Za-z]:`), and **any**
  `..` component. InstallShield backslashes are normalized to `/` so both
  spellings of traversal are caught on every host OS.
- `base = os.path.realpath(output_dir)` and
  `target = os.path.realpath(os.path.join(base, *safe_components))` canonicalize
  both paths, resolving symlinks in intermediate directories and at the leaf.
- The destination is accepted only when it is strictly below the root:
  `target != base` and `os.path.commonpath([base, target]) == base`. A
  `commonpath` error (different Windows drives) is treated as a refusal.
- Every refusal raises `ValueError("unsafe archive member: <reason>")` with an
  exact diagnostic (e.g. `parent directory component ('..') in path:
  '../../evil'`, `absolute path: '/tmp/evil'`, `drive letter in path: 'C:'`,
  `destination escapes extraction root: '...'`). Neither extraction method
  catches it, so the unsafe member is never written and the failure is loud.

Both `extract_all()` and `extract_cab_only()` call the same helper, so loose
files and compressed files are guarded identically.

## Verification

`tests/test_isextract_safety.py` loads the real module and asserts:

- ordinary members (including backslash-separated subdirectories) still
  resolve under the extraction root;
- empty names, NUL bytes, drive prefixes, absolute paths and all `..`
  spellings are refused with the exact diagnostic string;
- symlinked directories and symlinked leaves are refused after canonicalization;
- calling either extraction method with `../../evil`, an absolute path,
  `..\..\evil` or a symlink escape raises `ValueError` and leaves files outside
  the root untouched, while a legitimate member is still written inside it;
- the source guards both methods (a regression check against reintroducing the
  raw `fd.directory.replace(...)` join).

Run:

```sh
python3 -m pytest tests/test_isextract_safety.py -q
```

The fix is static/tooling validation only; it makes no claim about live-game
behavior and requires no game assets.
