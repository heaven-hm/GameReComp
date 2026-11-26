# Compatibility inventory and regression gate

Issue #14 owns this directory: one machine-readable source of truth for API
coverage, synthetic fixtures, CI gating and status aggregation. It does **not**
own D3D/input/audio/networking backends or the independent original-game
gameplay oracle (issues #1/#3). See [issue #14](https://github.com/heaven-hm/GameReComp/issues/14).

## Files

- `coverage.schema.json` — canonical JSON Schema (draft 2020-12). Every field
  below is defined there; validate before consuming the data.
- `baseline.json` — audited expectations: API family, interface/version, IID,
  PE format, guest DLL/import/method slot, expected implemented status, expected
  fixture result, ABI signature, host dependencies, known limitations, source
  provenance, owner issue, fixture IDs and allowed unresolved imports.
- `coverage.json` — generated inventory, keyed by **input SHA-256**. For an
  absent input the key is SHA-256 of `missing:` plus its path, so absence is
  still addressed by a stable digest. Never edit by hand.
- `api_matrix.md`, `games.md` — concise generated reports. `games.md` is a
  read-only view of `reports/games/results.json`, not a second source of truth.

## Run

```sh
python3 -m tools.compatibility                    # regenerate reports + evidence
python3 -m tools.compatibility --check            # 0 PASS, 1 FAIL, 2 INCOMPLETE
python3 -m tools.compatibility --branch main --repo /path/to/checkout
```

`verify.sh` runs the gate in both local and CI workflows. CI validation lives
in `docs/ci/github-workflows-ci.yml`; it uploads `docs/compatibility` and
`reports/compatibility` as artifacts.

## Honest statuses

- `implemented_status`: `not-present` < `declared` < `linked` < `unit-tested` <
  `integration-tested` < `original-verified`. Static registration is only
  `declared`; a shim existing never proves a functional API. No row here is
  `original-verified` — that status requires the evidence in `docs/VERIFICATION.md`.
- `fixture_result`: `PASS`, `PARTIAL`, `UNSUPPORTED`, `MISSING_INPUT`, `NOT_RUN`,
  `HOST_NOT_TESTED`, `SKIP`, `FAIL`. **A missing input is `MISSING_INPUT` /
  `NOT_RUN`, never `PASS`.** A host that was not exercised is `HOST_NOT_TESTED`,
  never `PASS`; SDL dummy video is not an OpenGL context.
- Gate verdict: `FAIL` on schema errors, deleted tests, new unresolved critical
  imports, status mismatches, or any attempt to mark a missing/unsupported row
  as PASS. `INCOMPLETE` when required inputs are absent. Only an explicit PASS
  exits 0 under `--check`.

The gate fails a branch that is behind `develop` in registered HLE coverage
(for example an older tip without `win32hle_wininet`); that is a regression
relative to the baseline, reported with `MISSING_INPUT`, not silently passed.

## Synthetic fixtures

`tests/fixtures/make_compat_fixture.py` builds deterministic PE32, NE and PE32+
images from a DLL/import label. Supported and unsupported families use the same
generator, so an absent handler cannot be reported as success. The canonical
supported PE32 case still comes from the existing
`tests/fixtures/make_pe32_fixture.py`; `fx-missing-input-probe` intentionally
has no bytes and must always report `MISSING_INPUT`/`NOT_RUN`.

## Corrected documentation claim

The 2026-10-30 audit claimed `docs/RUNTIME_COMPAT.md` said DirectInput was
unimplemented. Re-audited on `develop`: `runtime/win32hle/host.c` registers
`win32hle_dinput`, `runtime/win32hle/dinput.c` installs the COM vtables, and
`docs/RUNTIME_COMPAT.md` already describes keyboard/mouse as implemented with
named gaps. The inventory records DirectInput 3/5/7 as `declared`/`PARTIAL`
with those limitations; it does not repeat the false "absent" claim.
