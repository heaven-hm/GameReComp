"""Deterministic tests for the recomp16 DOS path confinement (issue #334).

``dos_path_to_native()`` used to translate a guest INT 21h path by replacing
``\\`` with ``/`` and prepending ``game_dir``. That let a guest name
``..\\..\\etc\\passwd``, a drive prefix or an absolute path and reach any host
file. These tests compile the *real* ``runtime/recomp16/dos_compat.c`` into a
small harness (with the unrelated HAL functions stubbed) and assert that:

  * ordinary DOS paths, including a not-yet-created file, still resolve under
    the game directory;
  * traversal, drive, absolute, unterminated/NUL and symlink escape attempts
    are refused with explicit error codes;
  * INT 21h create/open/delete translate a refusal into a DOS error and never
    touch a file outside ``game_dir``.

No game asset, live injection, network or host-specific path is involved.

Run with: python3 -m pytest tests/test_dos_path_safety.py -q
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "runtime" / "recomp16" / "dos_compat.c"

# Error codes from dos_compat.c (kept in sync with the enum there).
OK = 0
ERR_EMPTY = -1
ERR_NUL = -2
ERR_DRIVE = -3
ERR_ABSOLUTE = -4
ERR_DOTDOT = -5
ERR_ESCAPE = -8

# The harness #includes the translation unit under test so the static helpers
# are callable; the 13 stubs satisfy symbols used by its other handlers.
HARNESS = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#include "recomp/dos_compat.c"

void video_init(VideoState *vs) { (void)vs; }
void video_port_write(VideoState *vs, uint16_t p, uint8_t v) { (void)vs; (void)p; (void)v; }
uint8_t video_port_read(VideoState *vs, uint16_t p) { (void)vs; (void)p; return 0; }
void keyboard_init(KeyboardState *ks) { (void)ks; }
int keyboard_available(const KeyboardState *ks) { (void)ks; return 1; }
uint16_t keyboard_read(KeyboardState *ks) { (void)ks; return 0; }
void mouse_init(MouseState *ms) { (void)ms; }
void timer_init(TimerState *ts) { (void)ts; }
void timer_update(TimerState *ts, uint64_t now) { (void)ts; (void)now; }
void timer_port_write(TimerState *ts, uint16_t p, uint8_t v) { (void)ts; (void)p; (void)v; }
uint8_t timer_port_read(TimerState *ts, uint16_t p) { (void)ts; (void)p; return 0; }
int recomp_mem_write8(CPU *cpu, uint32_t addr, uint8_t v) { (void)cpu; (void)addr; (void)v; return 0; }
int recomp_mem_read8(CPU *cpu, uint32_t addr, uint8_t *out) { (void)cpu; (void)addr; (void)out; return 0; }

static CPU *g_cpu;
static DosState g_ds;

static uint32_t path_addr(void) { return seg_off(0x2000, 0); }

static void put_bytes(const unsigned char *bytes, size_t n)
{
    memcpy(g_cpu->mem + path_addr(), bytes, n);
}

static void path_case(const char *name, const unsigned char *bytes, size_t n)
{
    char out[512];
    int rc;
    memset(out, 0, sizeof out);
    put_bytes(bytes, n);
    /* Deliberately non-NUL after the supplied bytes: an "unterminated" case
     * is not accidentally terminated by leftover memory. */
    g_cpu->mem[path_addr() + n] = 0x41;
    rc = dos_path_to_native(g_cpu, 0x2000, 0, out, sizeof out);
    printf("RESULT|%s|%d|%s\n", name, rc, out);
}

static void int21_case(const char *name, uint8_t ah,
                       const unsigned char *bytes, size_t n)
{
    put_bytes(bytes, n);
    g_cpu->mem[path_addr() + n] = 0;
    g_cpu->ds = 0x2000;
    g_cpu->dx = 0;
    g_cpu->ah = ah;
    g_cpu->al = 0;
    g_cpu->flags &= ~FLAG_CF;
    dos_int21(g_cpu);
    printf("RESULT|%s|cf=%d|ax=%u\n", name,
           (g_cpu->flags & FLAG_CF) ? 1 : 0, (unsigned)g_cpu->ax);
}

int main(int argc, char **argv)
{
    static const unsigned char good[] = "GOOD.TXT";
    static const unsigned char subdir[] = "SUB\\SCORES.MIX";
    static const unsigned char dot[] = ".\\GOOD.TXT";
    static const unsigned char newfile[] = "NEW.DAT";
    static const unsigned char empty[] = "";
    static const unsigned char dotdot[] = "..\\outside\\escape.txt";
    static const unsigned char dotdot_slash[] = "../outside/escape.txt";
    static const unsigned char dotdot_mid[] = "SUB\\..\\GOOD.TXT";
    static const unsigned char dotdot_alone[] = "..";
    static const unsigned char dotdot_deep[] = "..\\..\\..\\etc\\passwd";
    static const unsigned char absolute[] = "/etc/passwd";
    static const unsigned char backslash_root[] = "\\WINDOWS\\WIN.INI";
    static const unsigned char drive[] = "C:\\AUTOEXEC.BAT";
    static const unsigned char drive_lower[] = "z:README.TXT";
    static const unsigned char symlink_dir[] = "LINK\\secret.txt";
    static const unsigned char symlink_file[] = "LINKFILE";
    static const unsigned char embedded_nul[] = {
        'O', 'K', '.', 'T', 'X', 'T', 0, '.', '.', '/', 'S',
        'E', 'C', 'R', 'E', 'T', 0
    };
    unsigned char unterminated[259];
    size_t i;

    (void)argc;
    g_cpu = calloc(1, sizeof *g_cpu);
    g_cpu->mem = calloc(1, MEM_SIZE);
    dos_init(&g_ds, g_cpu, argv[1]);

    path_case("good", good, sizeof good);
    path_case("subdir", subdir, sizeof subdir);
    path_case("dot", dot, sizeof dot);
    path_case("newfile", newfile, sizeof newfile);
    path_case("empty", empty, sizeof empty);
    path_case("dotdot", dotdot, sizeof dotdot);
    path_case("dotdot_slash", dotdot_slash, sizeof dotdot_slash);
    path_case("dotdot_mid", dotdot_mid, sizeof dotdot_mid);
    path_case("dotdot_alone", dotdot_alone, sizeof dotdot_alone);
    path_case("dotdot_deep", dotdot_deep, sizeof dotdot_deep);
    path_case("absolute", absolute, sizeof absolute);
    path_case("backslash_root", backslash_root, sizeof backslash_root);
    path_case("drive", drive, sizeof drive);
    path_case("drive_lower", drive_lower, sizeof drive_lower);
    path_case("symlink_dir", symlink_dir, sizeof symlink_dir);
    path_case("symlink_file", symlink_file, sizeof symlink_file);
    path_case("embedded_nul", embedded_nul, sizeof embedded_nul);
    for (i = 0; i < sizeof unterminated; ++i) unterminated[i] = 'A';
    path_case("unterminated", unterminated, sizeof unterminated);

    int21_case("int21_open_traversal", 0x3D, dotdot, sizeof dotdot);
    int21_case("int21_create_traversal", 0x3C,
               (const unsigned char *)"..\\created_outside.txt",
               sizeof "..\\created_outside.txt");
    int21_case("int21_delete_traversal", 0x41, dotdot, sizeof dotdot);
    int21_case("int21_open_good", 0x3D, good, sizeof good);
    return 0;
}
"""


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory):
    """Build and run the harness against a synthetic game directory."""
    root = tmp_path_factory.mktemp("dos_path_safety")
    game = root / "game"
    sub = game / "SUB"
    outside = root / "outside"
    sub.mkdir(parents=True)
    outside.mkdir()
    (game / "GOOD.TXT").write_text("good", encoding="utf-8")
    (game / "OK.TXT").write_text("ok", encoding="utf-8")
    (sub / "SCORES.MIX").write_text("mix", encoding="utf-8")
    (outside / "escape.txt").write_text("escape", encoding="utf-8")
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    try:
        (game / "LINK").symlink_to(outside, target_is_directory=True)
        (game / "LINKFILE").symlink_to(outside / "secret.txt")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    inc = root / "inc"
    (inc / "recomp").mkdir(parents=True)
    (inc / "hal").mkdir()
    for name in ("dos_compat.c", "dos_compat.h", "cpu.h"):
        shutil.copy(SOURCE.parent / name, inc / "recomp" / name)
    for name in ("video.h", "input.h", "timer.h"):
        shutil.copy(SOURCE.parent / "hal" / name, inc / "hal" / name)
    harness_c = inc / "recomp" / "harness.c"
    harness_c.write_text(HARNESS, encoding="utf-8")
    exe = root / "harness"

    compilers = [shutil.which(c) for c in ("gcc", "clang", "cc")]
    compilers = [c for c in compilers if c]
    if not compilers:
        pytest.skip("no C compiler installed")
    error = ""
    for cc in compilers:
        built = subprocess.run(
            [cc, "-O0", "-I", str(inc), str(harness_c), "-o", str(exe)],
            capture_output=True, text=True,
        )
        if built.returncode == 0:
            break
        error += f"--- {cc}\n{built.stderr}"
    else:
        pytest.skip(f"no compiler could build the DOS path harness: {error}")

    run = subprocess.run([str(exe), str(game)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    results = {}
    for line in run.stdout.splitlines():
        if line.startswith("RESULT|"):
            parts = line.split("|")
            results[parts[1]] = parts[2:]
    return {"results": results, "game": game, "root": root}


def _native(sandbox, *parts: str) -> str:
    """Expected canonical host path for a normal DOS path under the game dir."""
    game = sandbox["root"] / "game"
    return str(Path(os.path.realpath(str(game))).joinpath(*parts))


def _assert_ok(sandbox, name, relpath):
    code, out = sandbox["results"][name]
    assert int(code) == OK, f"{name}: unexpected error {code}"
    assert out == _native(sandbox, *relpath.split("/")), f"{name}: {out!r}"


def _assert_refused(sandbox, name, error):
    code, out = sandbox["results"][name]
    assert int(code) == error, f"{name}: expected {error}, got {code}"
    assert out == "", f"{name}: refused path must not be returned, got {out!r}"


def test_normal_dos_paths_still_work(sandbox):
    _assert_ok(sandbox, "good", "GOOD.TXT")
    _assert_ok(sandbox, "subdir", "SUB/SCORES.MIX")
    _assert_ok(sandbox, "dot", "GOOD.TXT")
    _assert_ok(sandbox, "newfile", "NEW.DAT")  # create: parent canonicalized


def test_traversal_and_root_escapes_are_refused(sandbox):
    for name in ("dotdot", "dotdot_slash", "dotdot_mid",
                 "dotdot_alone", "dotdot_deep"):
        _assert_refused(sandbox, name, ERR_DOTDOT)
    _assert_refused(sandbox, "absolute", ERR_ABSOLUTE)
    _assert_refused(sandbox, "backslash_root", ERR_ABSOLUTE)
    _assert_refused(sandbox, "drive", ERR_DRIVE)
    _assert_refused(sandbox, "drive_lower", ERR_DRIVE)


def test_nul_and_unterminated_paths_are_refused(sandbox):
    _assert_refused(sandbox, "empty", ERR_EMPTY)
    _assert_refused(sandbox, "unterminated", ERR_NUL)
    # An embedded NUL terminates the DOS ASCIIZ string; anything after it is
    # not part of the path and must never be followed out of game_dir.
    _assert_ok(sandbox, "embedded_nul", "OK.TXT")


def test_symlink_escapes_are_refused(sandbox):
    _assert_refused(sandbox, "symlink_dir", ERR_ESCAPE)
    _assert_refused(sandbox, "symlink_file", ERR_ESCAPE)


def test_int21_refuses_escape_and_keeps_normal_open(sandbox):
    results = sandbox["results"]
    for name in ("int21_open_traversal", "int21_create_traversal",
                 "int21_delete_traversal"):
        cf, ax = results[name]
        assert cf == "cf=1", f"{name}: carry flag must signal failure"
        assert ax == "ax=3", f"{name}: DOS error 03h (path not found) expected"

    cf, ax = results["int21_open_good"]
    assert cf == "cf=0", "normal open must succeed"
    assert 5 <= int(ax.split("=")[1]) < 32, "normal open must allocate a handle"

    # The refused create/delete must have left the outside files untouched.
    assert (sandbox["root"] / "outside" / "escape.txt").read_text() == "escape"
    assert not (sandbox["root"] / "created_outside.txt").exists()


def test_source_has_no_unconfined_translation():
    source = SOURCE.read_text(encoding="utf-8")
    assert 'snprintf(out, out_size, "%s/%s", g_dos->game_dir, dos_path);' not in source
    assert "dos_path_canonicalize" in source
    assert "DOS_PATH_ERR_ESCAPE" in source
    # Every path-taking INT 21h service must check the translation result.
    for service in ("0x3C", "0x3D", "0x41"):
        section = source.split(f"case {service}:", 1)[1]
        section = section.split("case 0x", 1)[0]
        assert "dos_path_to_native" in section
        assert "!= DOS_PATH_OK" in section
