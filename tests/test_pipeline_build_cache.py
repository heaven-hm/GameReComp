"""Measured build-cache hookup for the wasm32 compile path (issue #26 scale).

The object cache in tools/autorecomp/build_cache.py used to be standalone: the
pipeline never called it, so a repeated wasm build recompiled every generated
unit. These tests pin the hookup's two contracts:

  * with RECOMP_BUILD_CACHE off, compile commands are byte-for-byte what they
    were before the hookup (no behaviour change, no cache directory);
  * with it on, a second identical build issues zero compiler invocations, and
    a changed header re-keys every object so a stale object is never reused.

Everything here is light: temporary files and an injected fake compiler. No
game assets and no real toolchain are needed, and no wall-time speed claim is
made -- the measurement is the number of compiler invocations.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.autorecomp import build_cache as bc
from tools.autorecomp import pipeline as pl


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def make_run(calls, fail_on=()):
    """Fake cc: records each command and writes an object derived from bytes."""
    def run(cmd, **_kwargs):
        calls.append([str(c) for c in cmd])
        src = Path(cmd[cmd.index("-c") + 1])
        obj = Path(cmd[cmd.index("-o") + 1])
        if src.name in fail_on:
            return FakeProc(returncode=1, stderr="error: deliberate failure\n")
        obj.parent.mkdir(parents=True, exist_ok=True)
        obj.write_bytes(b"OBJ<" + src.read_bytes() + b">")
        return FakeProc()
    return run


class PipelineBuildCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src_dir = self.root / "src"
        self.src_dir.mkdir()
        self.obj_dir = self.root / "obj"
        self.cache_root = self.root / "cache"
        self.sources = []
        for i in range(3):
            src = self.src_dir / f"recomp_{i:04d}.c"
            src.write_text(f"void sub_{i}(void) {{ /* {i} */ }}\n")
            self.sources.append(src)
        self.objs = [self.obj_dir / f"{s.stem}.o" for s in self.sources]
        self.cflags = ["fake-cc", "-O2", "-w"]

    def _env(self, enabled, cache=True):
        values = {"RECOMP_BUILD_CACHE": "1" if enabled else "0"}
        if cache:
            values["RECOMP_BUILD_CACHE_DIR"] = str(self.cache_root)
        return patch.dict(os.environ, values)

    def _build(self, run, enabled, cflags=None):
        with self._env(enabled):
            return pl.compile_units_with_cache(
                self.sources, self.objs, cflags or self.cflags,
                jobs=1, run=run)

    def test_disabled_path_preserves_original_compile_commands(self):
        calls = []
        errors, stats = self._build(make_run(calls), enabled=False)
        self.assertEqual(errors, [])
        self.assertEqual(stats["enabled"], False)
        self.assertEqual(stats["units"], 3)
        self.assertEqual(stats["compiled"], 3)
        self.assertEqual(stats["hits"], 0)
        self.assertIsNone(stats["include_identity"])  # no header hashing when off
        # Exactly the old shape: [cflags, -c, src, -o, obj].
        self.assertEqual(len(calls), 3)
        for src, obj, cmd in zip(self.sources, self.objs, calls):
            self.assertEqual(cmd, ["fake-cc", "-O2", "-w", "-c", str(src), "-o", str(obj)])
        self.assertFalse(self.cache_root.exists())

    def test_enabled_second_build_makes_zero_compiler_calls(self):
        calls = []
        run = make_run(calls)
        errors, first = self._build(run, enabled=True)
        self.assertEqual(errors, [])
        self.assertEqual((first["compiled"], first["hits"]), (3, 0))
        self.assertEqual(len(calls), 3)
        self.assertTrue(self.cache_root.is_dir())
        before = [p.read_bytes() for p in self.objs]

        errors, second = self._build(run, enabled=True)
        self.assertEqual(errors, [])
        self.assertEqual((second["compiled"], second["hits"]), (0, 3))
        self.assertEqual(len(calls), 3)  # not one more compiler invocation
        self.assertEqual([p.read_bytes() for p in self.objs], before)
        self.assertEqual(first["include_identity"], second["include_identity"])
        self.assertEqual(len(first["include_identity"]), 64)

    def test_include_identity_binds_header_bytes_not_paths(self):
        inc = self.root / "include"
        inc.mkdir()
        header = inc / "recomp_types.h"
        header.write_text("typedef unsigned int uint32_t;\n")
        cflags = ["fake-cc", "-I", str(inc), "-O2"]
        digest = pl.wasm_include_identity(cflags)

        # Same bytes at a different location produce the same identity.
        inc2 = self.root / "include_copy"
        inc2.mkdir()
        (inc2 / "recomp_types.h").write_bytes(header.read_bytes())
        self.assertEqual(pl.wasm_include_identity(
            ["fake-cc", "-I", str(inc2), "-O2"]), digest)

        # Editing a header changes the identity even though the unit bytes and
        # the flag strings are identical.
        header.write_text("typedef unsigned long uint32_t;\n")
        self.assertNotEqual(pl.wasm_include_identity(cflags), digest)

        # Adding another visible header changes it too.
        (inc / "recomp_funcs.h").write_text("void sub_0(void);\n")
        self.assertNotEqual(pl.wasm_include_identity(cflags), digest)

    def test_changed_header_rekeys_objects(self):
        inc = self.root / "include"
        inc.mkdir()
        header = inc / "recomp_types.h"
        header.write_text("version 1\n")
        cflags = ["fake-cc", "-I", str(inc), "-O2"]
        calls = []
        run = make_run(calls)

        errors, first = self._build(run, enabled=True, cflags=cflags)
        self.assertEqual(errors, [])
        self.assertEqual((first["compiled"], first["hits"]), (3, 0))

        header.write_text("version 2\n")
        errors, second = self._build(run, enabled=True, cflags=cflags)
        self.assertEqual(errors, [])
        self.assertEqual((second["compiled"], second["hits"]), (3, 0))
        self.assertEqual(len(calls), 6)
        self.assertNotEqual(first["include_identity"], second["include_identity"])

    def test_failed_compile_is_reported_and_never_cached(self):
        calls = []
        run = make_run(calls, fail_on={"recomp_0001.c"})
        with self._env(True):
            errors, stats = pl.compile_units_with_cache(
                self.sources, self.objs, self.cflags, jobs=1, run=run)
        self.assertEqual(len(errors), 1)
        self.assertIn("deliberate failure", errors[0])
        self.assertEqual(stats["hits"], 0)
        self.assertFalse(self.objs[1].exists())
        self.assertEqual(len(list(self.cache_root.rglob("manifest.json"))), 2)


if __name__ == "__main__":
    unittest.main()
