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

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
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


class _FakeResource:
    """A drop-in for the optional `resource` module with fixed high-water marks."""

    RUSAGE_SELF = 0
    RUSAGE_CHILDREN = 1

    def __init__(self, self_bytes, children_bytes):
        self.values = {self.RUSAGE_SELF: self_bytes,
                       self.RUSAGE_CHILDREN: children_bytes}

    def getrusage(self, who):
        return SimpleNamespace(ru_maxrss=self.values[who])


class _BrokenResource(_FakeResource):
    def getrusage(self, who):
        raise OSError("resource accounting unavailable")


def _rss_scale():
    """macOS reports ru_maxrss in bytes, Linux in KiB."""
    return 1 if sys.platform == "darwin" else 1024


class PipelineBuildResourcesTest(unittest.TestCase):
    """Issue #26: build resources are measured, and the disk guard is explicit."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_resource_footprint_publishes_per_stage_and_build_duration(self):
        stages = [
            {"name": "translate", "seconds": 2.5, "data": {"source_bytes": 4096}},
            {"name": "compile", "seconds": 7.25, "data": {"seconds": 7.0}},
            {"name": "run", "seconds": 1.0, "data": {}},
        ]
        r = pl.resource_footprint(self.root, stages, None)
        self.assertEqual(r["stage_seconds"],
                         {"translate": 2.5, "compile": 7.25, "run": 1.0})
        self.assertEqual(r["stage_seconds_total"], 10.75)
        self.assertEqual(r["build_seconds"], 7.25)

        skipped = [{"name": "compile", "seconds": 0.0,
                    "data": {"skipped": True}}]
        self.assertEqual(pl.resource_footprint(self.root, skipped, None)
                         ["build_seconds"], 0.0)

    def test_peak_rss_measures_children_and_normalises_units(self):
        fake = _FakeResource(self_bytes=1024, children_bytes=2048)
        with patch.object(pl, "_resource", fake):
            r = pl.resource_footprint(self.root, [], None)
        scale = _rss_scale()
        self.assertEqual(r["peak_rss_bytes_self"], 1024 * scale)
        self.assertEqual(r["peak_rss_bytes_children"], 2048 * scale)
        # The children are the build's real memory cost, and win over self.
        self.assertEqual(r["peak_rss_bytes"], 2048 * scale)

    def test_peak_rss_measurement_failure_is_absent_not_zero(self):
        with patch.object(pl, "_resource", _BrokenResource(0, 0)):
            r = pl.resource_footprint(self.root, [], None)
        for key in ("peak_rss_bytes", "peak_rss_bytes_self",
                    "peak_rss_bytes_children"):
            self.assertIsNone(r[key])
        with patch.object(pl, "_resource", None):
            r = pl.resource_footprint(self.root, [], None)
        self.assertIsNone(r["peak_rss_bytes"])

    def test_stage_records_carry_peak_rss(self):
        pipeline = SimpleNamespace(stages=[], stage_status={}, _emit=lambda **_kw: None)
        with patch.object(pl, "_resource", _FakeResource(1024, 2048)):
            with pl._StageRun(pipeline, "compile") as stage:
                stage.message, stage.data = "ok", {}
        self.assertEqual(len(pipeline.stages), 1)
        self.assertEqual(pipeline.stages[0]["peak_rss_bytes"], 2048 * _rss_scale())

    def test_disk_guard_is_opt_in(self):
        with patch.dict(os.environ, {pl.DISK_GUARD_ENV: ""}):
            pipeline = pl.Pipeline("fake.exe", project_dir=self.root)
            # With the guard off, not even a disk query happens.
            with patch.object(pl.shutil, "disk_usage",
                              side_effect=AssertionError("must not query")):
                self.assertIsNone(pipeline._guard_build_disk())
        self.assertEqual(pl.min_free_disk_bytes(""), 0)
        self.assertEqual(pl.min_free_disk_bytes("not-a-number"), 0)
        guard = pl.check_build_disk_space(self.root, 0)
        self.assertTrue(guard["ok"])
        self.assertIn("disabled", guard["diagnostic"])

    def test_low_disk_produces_diagnostic_and_refuses_before_deleting(self):
        usage = SimpleNamespace(total=10_000, free=100)
        with patch.object(pl.shutil, "disk_usage", return_value=usage):
            guard = pl.check_build_disk_space(self.root, 1000)
        self.assertFalse(guard["ok"])
        self.assertEqual(guard["free_bytes"], 100)
        self.assertIn("DISK GUARD", guard["diagnostic"])
        self.assertIn("truncated", guard["diagnostic"])

        pipeline = pl.Pipeline("fake.exe", project_dir=self.root,
                               min_free_disk_bytes=1000)
        with patch.object(pl.shutil, "disk_usage", return_value=usage):
            with self.assertRaises(pl.StageFailure) as ctx:
                pipeline.stage_compile()
        self.assertIn("DISK GUARD", str(ctx.exception))
        self.assertFalse(ctx.exception.data["disk_guard"]["ok"])
        self.assertEqual(pipeline.disk_guard["free_bytes"], 100)

        # The translate guard runs before the old generated tree is removed.
        sentinel = self.root / "src" / "recomp" / "gen" / "keep.c"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_text("keep me")
        with patch.object(pl.shutil, "disk_usage", return_value=usage):
            with self.assertRaises(pl.StageFailure):
                pipeline.stage_translate()
        self.assertEqual(sentinel.read_text(), "keep me")

    def test_disk_measurement_error_fails_closed(self):
        with patch.object(pl.shutil, "disk_usage",
                          side_effect=OSError("no such volume")):
            guard = pl.check_build_disk_space(self.root, 1000)
        self.assertFalse(guard["ok"])
        self.assertIsNone(guard["free_bytes"])
        self.assertIn("cannot measure", guard["diagnostic"])

        pipeline = pl.Pipeline("fake.exe", project_dir=self.root,
                               min_free_disk_bytes=1000)
        with patch.object(pl.shutil, "disk_usage",
                          side_effect=OSError("no such volume")):
            with self.assertRaises(pl.StageFailure):
                pipeline.stage_compile()
        self.assertFalse(pipeline.disk_guard["ok"])

    def test_report_publishes_resources_and_guard(self):
        pipeline = pl.Pipeline("fake.exe", project_dir=self.root)
        pipeline.stages = [{"name": "compile", "seconds": 3.0, "status": "ok",
                            "message": "built", "data": {}, "peak_rss_bytes": 123}]
        pipeline.disk_guard = pl.check_build_disk_space(self.root, 1000)
        pipeline.stage_report()
        report = json.loads((self.root / "reports" / "report.json").read_text())
        self.assertEqual(report["resources"]["build_seconds"], 3.0)
        self.assertEqual(report["resources"]["stage_seconds"], {"compile": 3.0})
        self.assertEqual(report["disk_guard"]["min_free_bytes"], 1000)
        html = (self.root / "reports" / "report.html").read_text()
        self.assertIn("Measured resources", html)
        self.assertIn("DISK GUARD", html)


if __name__ == "__main__":
    unittest.main()
