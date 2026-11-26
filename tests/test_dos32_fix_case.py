"""Regression for issue #335: dos32.c::fix_case() path-safety bugs.

The real static C function is extracted from runtime/dos32/dos32.c and compiled
into a tiny POSIX harness. The harness places the path at the very end of an
mmap'd page with a PROT_NONE guard page after it, so *any* byte read or written
past the string's NUL terminates with SIGSEGV. That makes the two original
faults (the unbounded `path + 1` walk on "" and the NULL `strrchr` dereference
on a bare relative name) deterministic failures rather than luck-of-the-heap.

No compiler sanitizer is required; only a working C compiler and POSIX mmap.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DOS32_C = ROOT / 'runtime' / 'dos32' / 'dos32.c'

HARNESS = r'''
#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

/* Default APFS (and many developer laptops) are case-insensitive: stat()
 * succeeds for "MiXeD.TxT" even when the directory holds "mixed.TXT", so the
 * correction branch would never run. Require every component to be spelled
 * exactly as it exists in its parent, i.e. behave like the case-sensitive
 * POSIX host that fix_case was written for. */
static int exact_spelling(const char* path) {
    size_t n = strlen(path);
    char* p = (char*)malloc(n + 1);
    if (!p) return 1;
    memcpy(p, path, n + 1);
    char* comp = p;
    while (*comp == '/') comp++;             /* the root itself is exact */
    int ok = 1;
    while (*comp) {
        char* slash = strchr(comp, '/');
        if (slash) *slash = 0;
        char saved = 0;
        const char* dir;
        if (comp == p) dir = ".";
        else {
            saved = comp[-1];
            comp[-1] = 0;
            dir = p[0] ? p : "/";
        }
        DIR* d = opendir(dir);
        int found = 0;
        if (d) {
            for (struct dirent* e; (e = readdir(d)) != NULL; )
                if (!strcmp(e->d_name, comp)) { found = 1; break; }
            closedir(d);
        }
        if (!found) { ok = 0; break; }
        if (comp != p) comp[-1] = saved;
        if (!slash) break;
        *slash = '/';
        comp = slash + 1;
    }
    free(p);
    return ok;
}

static int harness_stat(const char* path, struct stat* st) {
    if (stat(path, st) != 0) return -1;
    return exact_spelling(path) ? 0 : (errno = ENOENT, -1);
}

#define stat(path, st) harness_stat((path), (st))   /* function-like: struct stat stays intact */
__FIX_CASE__
#undef stat

int main(int argc, char** argv) {
    if (argc != 3) return 2;
    if (!strcmp(argv[1], "null")) { fix_case(NULL); puts("ok"); return 0; }

    size_t n = strlen(argv[2]);
    long ps = sysconf(_SC_PAGESIZE);
    if (ps <= 0) return 3;
    size_t page = (size_t)ps;
    size_t data_pages = (n + page) / page;          /* at least one */
    size_t total = (data_pages + 2) * page;         /* guard | data | guard */
    char* map = (char*)mmap(NULL, total, PROT_NONE,
                            MAP_PRIVATE | MAP_ANON, -1, 0);
    if (map == MAP_FAILED) return 4;
    if (mprotect(map + page, data_pages * page, PROT_READ | PROT_WRITE) != 0)
        return 5;
    /* The NUL is the last byte before the guard page. */
    char* buf = map + (data_pages + 1) * page - (n + 1);
    memcpy(buf, argv[2], n + 1);

    fix_case(buf);

    size_t out = strlen(buf);                       /* stops at the guarded NUL */
    fwrite(buf, 1, out, stdout);
    putchar('\n');
    munmap(map, total);
    return 0;
}
'''


def _extract_fix_case(source):
    """Return the real fix_case definition, brace-matched from the source."""
    marker = 'static void fix_case(char* path) {'
    start = source.index(marker)
    depth = 0
    for i in range(source.index('{', start), len(source)):
        if source[i] == '{':
            depth += 1
        elif source[i] == '}':
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    raise AssertionError('unbalanced braces in fix_case')


def _compiler(tmp):
    """First C compiler in the environment that actually compiles a file."""
    probe = tmp / 'probe.c'
    probe.write_text('int main(void) { return 0; }\n')
    for candidate in ('/usr/bin/cc', 'cc', 'clang', 'gcc'):
        exe = shutil.which(candidate) or candidate
        if not Path(exe).exists():
            continue
        out = tmp / 'probe'
        r = subprocess.run([exe, str(probe), '-o', str(out)],
                           capture_output=True, text=True, check=False)
        if r.returncode == 0:
            return exe
    return None


class Dos32FixCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='dos32-fix-case-')
        cls.root = Path(cls.tmp.name)
        cc = _compiler(cls.root)
        if cc is None:
            raise unittest.SkipTest('no working C compiler')
        src = cls.root / 'harness.c'
        src.write_text(HARNESS.replace('__FIX_CASE__',
                                       _extract_fix_case(DOS32_C.read_text())))
        cls.exe = cls.root / 'harness'
        r = subprocess.run([cc, '-std=c11', '-O1', '-Wall', '-Wextra',
                            str(src), '-o', str(cls.exe)],
                           capture_output=True, text=True, check=False)
        if r.returncode != 0:
            raise unittest.SkipTest(f'harness did not compile: {r.stderr[-500:]}')

        # A DOS/4GW game's on-disk spelling is not the spelling the guest uses.
        (cls.root / 'mixed.TXT').write_text('x')
        (cls.root / 'SubDir').mkdir()
        (cls.root / 'SubDir' / 'File.TXT').write_text('x')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _run(self, mode, path):
        r = subprocess.run([str(self.exe), mode, path], cwd=self.root,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0,
                         f'fix_case({path!r}) faulted:\n{r.stderr[-1000:]}')
        self.assertNotIn('Segmentation fault', r.stderr)
        return r.stdout.rstrip('\n')

    def test_empty_path_is_a_noop(self):
        # The old `p = path + 1` read past the terminator; the guard page
        # catches that on the very first iteration.
        self.assertEqual(self._run('path', ''), '')

    def test_null_path_is_a_noop(self):
        self.assertEqual(self._run('null', 'ignored'), 'ok')

    def test_relative_filename_without_separator(self):
        self.assertEqual(self._run('path', 'MiXeD.TxT'), 'mixed.TXT')
        # The exact issue #335 reproducer: strrchr returns NULL, the old code
        # wrote through it, and stat() of a missing name must be harmless.
        self.assertEqual(self._run('path', 'SAVE.DAT'), 'SAVE.DAT')
        self.assertEqual(self._run('path', './SAVE.DAT'), './SAVE.DAT')

    def test_nested_path(self):
        self.assertEqual(self._run('path', 'SUBDIR/FILE.TXT'),
                         'SubDir/File.TXT')
        self.assertEqual(self._run('path', 'SUBDIR/MISSING.TXT'),
                         'SubDir/MISSING.TXT')
        self.assertEqual(self._run('path', f'{self.root}/SUBDIR/FILE.TXT'),
                         f'{self.root}/SubDir/File.TXT')

    def test_too_long_path(self):
        # ENAMETOOLONG from stat(); no component can be matched or copied.
        long_name = 'A' * 5000 + '.DAT'
        self.assertEqual(self._run('path', long_name), long_name)
        long_path = 'LONGDIR/' * 600 + 'FILE.TXT'
        self.assertEqual(self._run('path', long_path), long_path)

    def test_source_has_no_unbounded_walk_or_unchecked_separator(self):
        text = DOS32_C.read_text()
        fn = _extract_fix_case(text)
        self.assertNotIn('path + 1', fn)
        self.assertIn('if (!path || !*path) return;', fn)
        self.assertIn('char* s = strrchr(path, \'/\');', fn)
        self.assertIn('if (s) *s = 0;', fn)
        self.assertIn(': "."', fn)
        self.assertIn('strlen(e->d_name) == strlen(name)', fn)
        self.assertIn('end <= len', fn)


if __name__ == '__main__':
    unittest.main()
