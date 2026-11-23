"""Light tests for the opt-in content-addressed object cache."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.autorecomp import build_cache as bc


class FakeProc:
    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class BuildCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cache_root = self.root / 'cache'
        self.env = patch.dict(os.environ, {
            'RECOMP_BUILD_CACHE': '1',
            'RECOMP_BUILD_CACHE_DIR': str(self.cache_root),
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def key(self, source=b'int x;\n', compiler='fake-cc', flags=('-O2', '-w'), **kw):
        return bc.object_key(source, compiler, flags, version_output='v1', **kw)

    def test_enabled_is_explicit_opt_in(self):
        with patch.dict(os.environ, {'RECOMP_BUILD_CACHE': '0'}):
            self.assertFalse(bc.enabled())
        with patch.dict(os.environ, {'RECOMP_BUILD_CACHE': '1'}):
            self.assertTrue(bc.enabled())
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(bc.enabled())

    def test_source_digest_tracks_bytes_not_path(self):
        a = self.root / 'a.c'
        b = self.root / 'b.c'
        a.write_bytes(b'same')
        b.write_bytes(b'same')
        self.assertEqual(bc.source_digest(a), bc.source_digest(b))
        self.assertEqual(bc.source_digest(b'same'), bc.source_digest(a))
        b.write_bytes(b'other')
        self.assertNotEqual(bc.source_digest(a), bc.source_digest(b))

    def test_flags_digest_is_ordered_and_unambiguous(self):
        self.assertNotEqual(bc.flags_digest(['-O2', '-w']), bc.flags_digest(['-w', '-O2']))
        self.assertNotEqual(bc.flags_digest(['ab', 'c']), bc.flags_digest(['a', 'bc']))
        self.assertEqual(bc.flags_digest(['-O2']), bc.flags_digest(['-O2']))
        self.assertNotEqual(bc.flags_digest(['-O2']), bc.flags_digest(['-O1']))

    def test_object_key_binds_all_three_components(self):
        base = self.key()
        self.assertEqual(base, self.key())
        self.assertNotEqual(base, self.key(source=b'int y;\n'))
        self.assertNotEqual(base, self.key(compiler='other-cc'))
        self.assertNotEqual(base, self.key(flags=('-O1', '-w')))
        with patch.dict(os.environ, {}, clear=True):
            # same inputs, different env: key is a pure function of its inputs
            self.assertEqual(base, self.key())
        # -g path identity is opt-in via extra_identity
        self.assertNotEqual(base, self.key(extra_identity='/some/path'))

    def test_compiler_identity_binds_version_and_file_bytes(self):
        cc1 = self.root / 'cc1'
        cc2 = self.root / 'cc2'
        cc1.write_text('#!/bin/sh\necho v1\n')
        cc2.write_text('#!/bin/sh\necho v2\n')
        self.assertNotEqual(bc.compiler_identity(cc1), bc.compiler_identity(cc2))
        self.assertEqual(bc.compiler_identity(cc1), bc.compiler_identity(cc1))
        # explicit version text changes identity even for the same file
        self.assertNotEqual(
            bc.compiler_identity(cc1, version_output='v1'),
            bc.compiler_identity(cc1, version_output='v9'))
        cc2.write_text('#!/bin/sh\n# changed body echo v2\n')
        self.assertNotEqual(bc.compiler_identity(cc2, 'v2'),
                            bc.compiler_identity(cc1, 'v2'))

    def test_cache_roundtrip_corruption_and_empty_object(self):
        c = bc.BuildObjectCache(self.key())
        self.assertTrue(c.put_bytes(b'object-bytes'))
        self.assertEqual(c.get_bytes(), b'object-bytes')
        self.assertEqual(c.manifest_path.read_text(),
                         json.dumps({
                             'schema': bc.CACHE_SCHEMA,
                             'key': c.key,
                             'object_sha256': bc._sha256(b'object-bytes'),
                             'object_bytes': len(b'object-bytes'),
                         }, sort_keys=True) + '\n')
        # a valid entry is not overwritten by a second writer
        self.assertFalse(c.put_bytes(b'different'))
        self.assertEqual(c.get_bytes(), b'object-bytes')

        c.object_path.write_bytes(b'tampered')
        self.assertIsNone(c.get_bytes())
        c.put_bytes(b'object-bytes')  # first republish after corruption
        self.assertEqual(c.get_bytes(), b'object-bytes')
        manifest = json.loads(c.manifest_path.read_text())
        manifest['key'] = '0' * 64
        c.manifest_path.write_text(json.dumps(manifest))
        self.assertIsNone(c.get_bytes())

        empty = bc.BuildObjectCache(self.key(source=b'empty'))
        self.assertFalse(empty.put_bytes(b''))
        self.assertIsNone(empty.get_bytes())

    def test_atomic_publish_ignores_stray_temporary_files(self):
        key = self.key(source=b'atomic')
        c = bc.BuildObjectCache(key)
        self.assertTrue(c.put_bytes(b'obj'))
        mode = c.object_path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)
        self.assertFalse(any('.tmp-' in p.name for p in c.root.iterdir()))
        (c.root / 'object.o.tmp-stale').write_bytes(b'partial')
        self.assertEqual(c.get_bytes(), b'obj')
        dst = self.root / 'out' / 'unit.o'
        self.assertTrue(c.get_object(dst))
        self.assertEqual(dst.read_bytes(), b'obj')
        self.assertEqual(c.clear(), 3)  # object, manifest, stale tmp
        self.assertIsNone(c.get_bytes())

    def test_invalid_key_is_refused(self):
        with self.assertRaises(bc.CacheIneligible):
            bc.BuildObjectCache('not-a-digest')

    def test_compile_cached_hit_miss_and_rekeying(self):
        src = self.root / 'recomp_0000.c'
        src.write_bytes(b'int a;\n')
        out = self.root / 'out' / 'recomp_0000.o'
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            idx = cmd.index('-o')
            source = Path(cmd[cmd.index('-c') + 1]).read_bytes()
            obj = b'OBJ<' + source[:8] + b'|' + ''.join(cmd[1:idx]).encode() + b'>'
            Path(cmd[idx + 1]).write_bytes(obj)
            return FakeProc()

        res1 = bc.compile_cached(src, 'fake-cc', ['-O2'], out,
                                 version_output='v1', run=fake_run)
        self.assertTrue(res1['compiled'])
        self.assertFalse(res1['hit'])
        self.assertEqual(out.read_bytes()[:4], b'OBJ<')
        self.assertEqual(len(calls), 1)

        res2 = bc.compile_cached(src, 'fake-cc', ['-O2'], out,
                                 version_output='v1', run=fake_run)
        self.assertTrue(res2['hit'])
        self.assertFalse(res2['compiled'])
        self.assertEqual(res2['key'], res1['key'])
        self.assertEqual(len(calls), 1)  # compiler not run again

        src.write_bytes(b'int changed;\n')
        res3 = bc.compile_cached(src, 'fake-cc', ['-O2'], out,
                                 version_output='v1', run=fake_run)
        self.assertNotEqual(res3['key'], res1['key'])
        self.assertTrue(res3['compiled'])
        self.assertEqual(len(calls), 2)

        res4 = bc.compile_cached(src, 'fake-cc', ['-O1'], out,
                                 version_output='v1', run=fake_run)
        self.assertTrue(res4['compiled'])
        self.assertEqual(len(calls), 3)

        res5 = bc.compile_cached(src, 'fake-cc', ['-O1'], out,
                                 version_output='v2', run=fake_run)
        self.assertTrue(res5['compiled'])
        self.assertEqual(len(calls), 4)

    def test_failed_compile_is_never_cached_or_installed(self):
        src = self.root / 'bad.c'
        src.write_bytes(b'broken')
        out = self.root / 'bad.o'
        out.write_bytes(b'sentinel')

        def fail(cmd, **kwargs):
            Path(cmd[cmd.index('-o') + 1]).write_bytes(b'partial')
            return FakeProc(returncode=1, stderr='error: bad source\n')

        res = bc.compile_cached(src, 'fake-cc', ['-O2'], out,
                                version_output='v1', run=fail)
        self.assertEqual(res['returncode'], 1)
        self.assertIn('bad source', res['stderr'])
        self.assertFalse(res['compiled'])
        self.assertEqual(out.read_bytes(), b'sentinel')
        key = bc.object_key(src, 'fake-cc', ['-O2'], version_output='v1')
        self.assertIsNone(bc.BuildObjectCache(key).get_bytes())
        self.assertFalse(any('.tmp-' in p.name for p in self.root.iterdir()))

    def test_disabled_mode_compiles_without_touching_cache(self):
        src = self.root / 'off.c'
        src.write_bytes(b'int off;\n')
        out = self.root / 'off.o'
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            Path(cmd[cmd.index('-o') + 1]).write_bytes(b'obj')
            return FakeProc()

        with patch.dict(os.environ, {'RECOMP_BUILD_CACHE': '0'}):
            res = bc.compile_cached(src, 'fake-cc', ['-O2'], out, run=fake_run)
        self.assertTrue(res['compiled'])
        self.assertIsNone(res['key'])
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.cache_root.exists())

    def test_generated_sources_and_cli(self):
        proj = self.root / 'proj'
        gen = proj / 'src' / 'recomp' / 'gen'
        hl = proj / 'src' / 'highlevel'
        gen.mkdir(parents=True)
        hl.mkdir(parents=True)
        for name in ('recomp_0001.c', 'recomp_0000.c', 'recomp_units.json'):
            (gen / name).write_text('')
        (hl / 'typed.c').write_text('')
        names = [p.name for p in bc.generated_sources(proj)]
        self.assertEqual(names, ['recomp_0000.c', 'recomp_0001.c', 'typed.c'])
        self.assertEqual([p.name for p in bc.generated_sources(proj, include_highlevel=False)],
                         ['recomp_0000.c', 'recomp_0001.c'])

        src = gen / 'recomp_0000.c'
        src.write_bytes(b'int a;\n')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            bc.main(['key', '--source', str(src), '--compiler', 'fake-cc',
                     '--flag=-O2', '--verbose'])
        info = json.loads(buf.getvalue())
        self.assertEqual(info['key'], bc.object_key(src, 'fake-cc', ['-O2']))
        self.assertEqual(set(info), {'source_sha256', 'compiler_identity',
                                     'flags_sha256', 'key'})

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            bc.main(['dir'])
        self.assertEqual(buf.getvalue().strip(), str(bc.cache_dir()))
        self.assertEqual(Path(buf.getvalue().strip()).resolve(), self.cache_root.resolve())


if __name__ == '__main__':
    unittest.main()
