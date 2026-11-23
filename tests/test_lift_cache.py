"""Tests for the fingerprinted recover/translate artifact cache."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.autorecomp import lift_cache


class LiftCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        (self.repo / 'tools' / 'lift').mkdir(parents=True)
        (self.repo / 'tools' / 'autorecomp').mkdir(parents=True)
        (self.repo / 'tools' / 'pe').mkdir(parents=True)
        (self.repo / 'tools' / 'disasm').mkdir(parents=True)
        for p in ('tools/lift/translator.py', 'tools/autorecomp/pipeline.py',
                  'tools/autorecomp/lift_cache.py', 'tools/autorecomp/highlevel.py',
                  'tools/pe/pe_analyze.py', 'tools/disasm/disasm32.py'):
            (self.repo / p).write_text('# fixture\n')
        self.pe = self.root / 'game.exe'
        self.pe.write_bytes(b'MZ' + b'payload')

    def tearDown(self):
        self.tmp.cleanup()

    def fingerprint(self, **kw):
        args = dict(pe_path=self.pe, companions=[], split=1000,
                    split_bytes=8 * 1024 * 1024, highlevel_dir=None,
                    repo_root=self.repo)
        args.update(kw)
        return lift_cache.fingerprint(**args)

    def test_fingerprint_tracks_input_tools_and_options(self):
        base = self.fingerprint()
        self.assertEqual(base, self.fingerprint())
        self.pe.write_bytes(b'MZ' + b'changed')
        self.assertNotEqual(base, self.fingerprint())
        self.pe.write_bytes(b'MZ' + b'payload')
        (self.repo / 'tools/lift/translator.py').write_text('# changed\n')
        self.assertNotEqual(base, self.fingerprint())
        (self.repo / 'tools/lift/translator.py').write_text('# fixture\n')
        self.assertNotEqual(base, self.fingerprint(split=500))

    def test_cache_roundtrip_and_corruption(self):
        with patch.dict(os.environ, {
                'RECOMP_LIFT_CACHE_DIR': str(self.root / 'cache')}, clear=False):
            c = lift_cache.LiftCache('test-key')
            c.put('recover', {'fun': 1})
            self.assertEqual(c.get('recover'), {'fun': 1})
            c._entry('recover').write_bytes(b'garbage')
            self.assertIsNone(c.get('recover'))

    def test_gen_dir_copy_roundtrip(self):
        with patch.dict(os.environ, {
                'RECOMP_LIFT_CACHE_DIR': str(self.root / 'cache')}, clear=False):
            c = lift_cache.LiftCache('gen-key')
            src = self.root / 'gen'
            src.mkdir()
            (src / 'recomp_0000.c').write_text('void sub_1(void) {}\n')
            (src / 'recomp_units.json').write_text('{"units":[]}')
            c.capture_gen_dir(src)
            dst = self.root / 'gen2'
            dst.mkdir()
            (dst / 'stale.c').write_text('stale')
            self.assertTrue(c.copy_gen_dir(dst))
            self.assertEqual((dst / 'recomp_0000.c').read_text(),
                             'void sub_1(void) {}\n')
            self.assertFalse((dst / 'stale.c').exists())

    def test_cache_is_opt_in(self):
        with patch.dict(os.environ, {'RECOMP_LIFT_CACHE': '0'}, clear=False):
            self.assertFalse(lift_cache.enabled())
        with patch.dict(os.environ, {'RECOMP_LIFT_CACHE': '1'}, clear=False):
            self.assertTrue(lift_cache.enabled())


if __name__ == '__main__':
    unittest.main()
