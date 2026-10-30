#!/bin/sh
# verify.sh -- the gate. Everything this project claims to check, in one command.
#
# CI and a developer run the same script, because a check that exists only in CI is
# a check nobody runs before pushing, and a check that exists only locally is one
# that quietly rots. `docs/ci/github-workflows-ci.yml` calls this where the runner
# has the tools; the rest is documented in its header.
#
#   sh verify.sh            # everything that can run on this machine
#   sh verify.sh --fast     # skip the parts that need a game corpus
#
# Exit 0 only when every check passed. A skipped check is reported as skipped with
# its reason and does NOT fail the run -- a machine without Qt is not a broken
# project -- but a check that ran and failed always does.
set -u
cd "$(dirname "$0")" || exit 1

FAST=0
[ "${1:-}" = "--fast" ] && FAST=1

fail=0
ran=0
skipped=0

say()  { printf '%s\n' "$*"; }
head1() { printf '\n=== %s\n' "$*"; }
ok()   { ran=$((ran+1)); printf '  PASS  %-42s %s\n' "$1" "${2:-}"; }
bad()  { ran=$((ran+1)); fail=$((fail+1)); printf '  FAIL  %-42s %s\n' "$1" "${2:-}"; }
skip() { skipped=$((skipped+1)); printf '  SKIP  %-42s %s\n' "$1" "${2:-}"; }

# ---------------------------------------------------------------- python
head1 "python"
if python3 -m pytest tests/ -q > /tmp/verify-pytest.log 2>&1; then
    ok "unit tests" "$(tail -1 /tmp/verify-pytest.log)"
else
    bad "unit tests" "$(tail -3 /tmp/verify-pytest.log | tr '\n' ' ')"
fi

if python3 tests/fixtures/make_pe32_fixture.py --check > /tmp/verify-fixture.log 2>&1; then
    ok "synthetic PE32 fixture reproducible"
else
    bad "synthetic PE32 fixture reproducible" "$(tail -2 /tmp/verify-fixture.log | tr '\n' ' ')"
fi

# --------------------------------------------------------------- runtime
head1 "runtime"
if command -v pkg-config > /dev/null 2>&1 && pkg-config --exists sdl2; then
    if sh runtime/win32hle/build_selftests.sh > /tmp/verify-selftests.log 2>&1; then
        ok "win32hle selftests" "$(grep -c '^ok' /tmp/verify-selftests.log) ok"
    else
        bad "win32hle selftests" "$(grep -E '^(FAIL|BUILD FAIL)' /tmp/verify-selftests.log | head -2 | tr '\n' ' ')"
    fi
else
    skip "win32hle selftests" "SDL2 not found via pkg-config"
fi

# ------------------------------------------------------------------- gui
head1 "gui"
if [ -x gui/build/reveng-studio ]; then
    if QT_QPA_PLATFORM=offscreen gui/build/reveng-studio --selftest projects/cavestory-doukutsu --repo "$PWD" \
         > /tmp/verify-gui.log 2>&1; then
        ok "gui reads a real project headlessly" "$(grep '^functions:' /tmp/verify-gui.log)"
    else
        bad "gui reads a real project headlessly" "$(head -2 /tmp/verify-gui.log | tr '\n' ' ')"
    fi
    if QT_QPA_PLATFORM=offscreen gui/build/reveng-studio --selftest-pe tests/fixtures/pe32-minimal.exe \
         > /tmp/verify-gui-pe.log 2>&1; then
        ok "gui accepts a supported executable"
    else
        bad "gui accepts a supported executable" "$(head -2 /tmp/verify-gui-pe.log | tr '\n' ' ')"
    fi
else
    skip "gui checks" "gui/build/reveng-studio not built (cmake -S gui -B gui/build)"
fi

# ------------------------------------------------------------ regression
head1 "regression"
if python3 -m tools.autorecomp.regress --tier fast > /tmp/verify-regress.log 2>&1; then
    ok "regression, fast tier" "$(grep -c 'PASS' /tmp/verify-regress.log) check(s)"
else
    bad "regression, fast tier" "$(grep -E 'FAIL|REGRESSED' /tmp/verify-regress.log | head -3 | tr '\n' ' ')"
fi

if [ "$FAST" = 0 ]; then
    if [ -n "${SMALL_GAMES_PATH:-}" ]; then
        if python3 -m tools.autorecomp.regress --tier games > /tmp/verify-games.log 2>&1; then
            ok "regression, game tier" "$(grep -c 'PASS' /tmp/verify-games.log) game(s)"
        else
            bad "regression, game tier" "$(grep -E 'FAIL|REGRESSED' /tmp/verify-games.log | head -3 | tr '\n' ' ')"
        fi
    else
        skip "regression, game tier" "SMALL_GAMES_PATH not set"
    fi

    # The declared certified set must reconstruct from the repository alone.
    head1 "evidence"
    if python3 - <<'PY' > /tmp/verify-certified.log 2>&1
import json, sys
from pathlib import Path
d = Path("reports/certified")
idx = json.loads((d / "index.json").read_text())
missing = [r["address"] for r in idx["rows"]
           if not (d / r["module"]).is_file() or not (d / r["verdict_json"]).is_file()]
counts = idx["counts"]
print(f"counts {counts}")
if missing:
    print("missing:", missing)
    sys.exit(1)
if counts["total"] != counts["agree"] + counts["agree_return"]:
    print("counts do not add up")
    sys.exit(1)
PY
    then
        ok "every certified verdict reconstructs" "$(head -1 /tmp/verify-certified.log)"
    else
        bad "every certified verdict reconstructs" "$(tail -2 /tmp/verify-certified.log | tr '\n' ' ')"
    fi
fi

# ------------------------------------------------------------------ done
printf '\n%s\n' "----------------------------------------"
if [ "$fail" -eq 0 ]; then
    say "VERIFIED: $ran check(s) passed, $skipped skipped"
    exit 0
fi
say "FAILED: $fail of $ran check(s) failed, $skipped skipped"
exit 1
