"""Linux runner: absolute staging paths, image provisioning and ELF evidence."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import linux_matrix  # noqa: E402


PROBE = {
    'image': 'gamerecomp/linux-runner:local', 'status': 'OK',
    'CMAKE': 'cmake version 3.28.3', 'CC': 'cc (Ubuntu 13.2.0) 13.2.0',
    'PKG': '0.29.2', 'SDL': '2.30.0 2.20.0', 'UNAME': 'Linux aarch64',
    'toolchain_ok': True,
}


class LinuxPathTests(unittest.TestCase):
    def test_relative_out_and_project_are_resolved(self):
        seen = {}

        def fake_build_one(project, out, image, jobs, run_timeout,
                           build_timeout, min_free_bytes, scratch,
                           provision, probe, original):
            seen["project"] = project
            seen["out"] = out
            seen["scratch"] = scratch
            seen["provision"] = provision
            seen["original"] = original
            return {"name": project.name, "build_rc": 0, "run_rc": 0,
                    "seconds": 0.0}

        with tempfile.TemporaryDirectory() as t:
            with mock.patch.object(linux_matrix, "docker_ready", return_value=True), \
                 mock.patch.object(linux_matrix, "image_available", return_value=True), \
                 mock.patch.object(linux_matrix, "probe_image", return_value=PROBE), \
                 mock.patch.object(linux_matrix, "verify_bind_mount",
                                   return_value=(True, '')), \
                 mock.patch.object(linux_matrix, "check_free_disk",
                                   return_value={"ok": True}), \
                 mock.patch.object(linux_matrix, "build_one", side_effect=fake_build_one):
                rc = linux_matrix.main(["--project", "proj", "--out", "results",
                                        "--min-free-gb", "0.001"])
            self.assertEqual(rc, 0)
            self.assertTrue(seen["out"].is_absolute())
            self.assertTrue(seen["project"].is_absolute())
            self.assertTrue(seen["scratch"].is_absolute())
            self.assertEqual(seen["provision"], "prebuilt")
            self.assertIsNone(seen["original"])

    def test_unshared_bind_mount_fails_closed(self):
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / 'results'
            reason = 'Docker sees no files at results; use a shared host path'
            with mock.patch.object(linux_matrix, "docker_ready", return_value=True), \
                 mock.patch.object(linux_matrix, "image_available", return_value=True), \
                 mock.patch.object(linux_matrix, "probe_image", return_value=PROBE), \
                 mock.patch.object(linux_matrix, "check_free_disk",
                                   return_value={"ok": True}), \
                 mock.patch.object(linux_matrix, "verify_bind_mount",
                                   return_value=(False, reason)):
                rc = linux_matrix.main(["--project", "proj", "--out", str(out)])
            self.assertEqual(rc, 3)
            summary = __import__('json').loads((out / 'linux-summary.json').read_text())
            self.assertFalse(summary['bind_mount']['ok'])
            self.assertEqual(summary['bind_mount']['reason'], reason)
            self.assertEqual(summary['results'], [])

    def test_missing_image_fails_closed(self):
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / 'results'
            with mock.patch.object(linux_matrix, "docker_ready", return_value=True), \
                 mock.patch.object(linux_matrix, "image_available", return_value=False):
                rc = linux_matrix.main(["--project", "proj", "--out", str(out)])
            self.assertEqual(rc, 3)
            summary = __import__('json').loads((out / 'linux-summary.json').read_text())
            self.assertEqual(summary['results'], [])
            self.assertIn('image not present', summary['image_probe']['reason'])


class LinuxProvisionTests(unittest.TestCase):
    def test_probe_parses_toolchain_and_flags(self):
        output = (
            'CMAKE=cmake version 3.28.3\nCC=cc (Ubuntu 13.2.0) 13.2.0\n'
            'PKG=0.29.2\nSDL=2.30.0 2.20.0\nAPK=\nAPT=/usr/bin/apt-get\n'
            'UNAME=Linux aarch64\n'
        )
        proc = subprocess.CompletedProcess([], 0, stdout=output)
        with mock.patch.object(linux_matrix.subprocess, 'run', return_value=proc):
            probe = linux_matrix.probe_image('image:tag')
        self.assertEqual(probe['status'], 'OK')
        self.assertTrue(probe['toolchain_ok'])
        self.assertEqual(probe['CC'], 'cc (Ubuntu 13.2.0) 13.2.0')
        self.assertEqual(probe['UNAME'], 'Linux aarch64')

    def test_bind_mount_probe_detects_empty_mount(self):
        with tempfile.TemporaryDirectory() as t:
            proc = subprocess.CompletedProcess([], 1, stdout='cat: no such file\n')
            with mock.patch.object(linux_matrix.subprocess, 'run', return_value=proc):
                ok, reason = linux_matrix.verify_bind_mount(Path(t), 'image:tag')
            self.assertFalse(ok)
            self.assertIn('sees no files', reason)
            self.assertEqual(list(Path(t).iterdir()), [])  # marker cleaned up

    def test_auto_uses_prebuilt_when_toolchain_present(self):
        mode, error = linux_matrix.provision_plan(PROBE, 'auto')
        self.assertEqual((mode, error), ('prebuilt', None))
        self.assertEqual(linux_matrix.setup_prefix(mode), '')

    def test_prebuilt_without_toolchain_is_blocked(self):
        mode, error = linux_matrix.provision_plan({'toolchain_ok': False}, 'prebuilt')
        self.assertEqual(mode, 'prebuilt')
        self.assertIn('lacks a complete toolchain', error)

    def test_apk_and_apt_install_over_network(self):
        for mode in ('apk', 'apt'):
            self.assertIsNone(linux_matrix.provision_plan({}, mode)[1])
            self.assertTrue(linux_matrix.setup_prefix(mode))
        self.assertIn('apk add', linux_matrix.setup_prefix('apk'))
        self.assertIn('apt-get update', linux_matrix.setup_prefix('apt'))

    def test_auto_without_toolchain_or_package_manager_is_blocked(self):
        mode, error = linux_matrix.provision_plan({'toolchain_ok': False}, 'auto')
        self.assertIsNotNone(error)
        self.assertIn('no apk/apt', error)


class LinuxBuildCommandTests(unittest.TestCase):
    def test_prebuilt_build_is_network_free_and_records_elf(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            project = root / 'proj'
            (project / 'src').mkdir(parents=True)
            (project / 'CMakeLists.txt').write_text('project(arith_calc C)\n')
            out = root / 'out'
            proc = subprocess.CompletedProcess([], 0, stdout='build ok\n')
            commands = []

            def fake_run(cmd, **kwargs):
                commands.append(cmd)
                return proc

            with mock.patch.object(linux_matrix, 'check_free_disk',
                                   return_value={'ok': True}), \
                 mock.patch.object(linux_matrix.subprocess, 'run',
                                   side_effect=fake_run), \
                 mock.patch.object(linux_matrix, 'elf_evidence',
                                   return_value={'status': 'OK', 'sha256': 'a' * 64}):
                rec = linux_matrix.build_one(
                    project, out, 'gamerecomp/linux-runner:local', 4, 8, 60,
                    min_free_bytes=1, scratch=root, provision='prebuilt', probe=PROBE)
            self.assertEqual(rec['build_rc'], 0)
            for cmd in commands:
                mount = cmd[cmd.index('-v') + 1]
                self.assertTrue(Path(mount.split(':')[0]).is_absolute(), cmd)
                self.assertIn('arith_calc', mount)
            self.assertIn('--network', rec['build_cmd'])
            self.assertIn('--network', rec['run_cmd'])
            self.assertNotIn('apk', rec['build_cmd'][-1])
            self.assertNotIn('apt-get', rec['build_cmd'][-1])
            self.assertEqual(rec['elf']['status'], 'OK')

    def test_original_pe_is_mounted_for_the_run(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            project = root / 'proj'
            (project / 'src').mkdir(parents=True)
            (project / 'CMakeLists.txt').write_text('project(arith_calc C)\n')
            original = root / 'arith-calc.exe'
            original.write_bytes(b'MZ')
            proc = subprocess.CompletedProcess([], 0, stdout='ok\n')
            with mock.patch.object(linux_matrix, 'check_free_disk',
                                   return_value={'ok': True}), \
                 mock.patch.object(linux_matrix.subprocess, 'run',
                                   return_value=proc), \
                 mock.patch.object(linux_matrix, 'elf_evidence',
                                   return_value={'status': 'OK'}):
                rec = linux_matrix.build_one(
                    project, root / 'out', 'image:tag', 1, 8, 60,
                    min_free_bytes=1, scratch=root, provision='prebuilt',
                    probe=PROBE, original=original)
            self.assertIn(f'{original}:/original:ro', rec['run_cmd'])
            self.assertNotIn('/original', ' '.join(rec['build_cmd']))
            self.assertTrue(rec['run_cmd'][-1].endswith('./build/arith_calc /original'))

    def test_incbin_absolute_paths_are_rewritten(self):
        with tempfile.TemporaryDirectory() as t:
            staging = Path(t) / 'staging' / 'proj'
            gen = staging / 'src' / 'recomp' / 'gen'
            gen.mkdir(parents=True)
            (gen / 'recomp_image.bin').write_bytes(b'PE')
            asm = gen / 'recomp_image.S'
            origin = '/origin/build-machine/recomp_image.bin'
            asm.write_text(f'.globl image\nimage:\n    .incbin "{origin}"\n')
            changed = linux_matrix.rewrite_incbin_paths(staging)
            self.assertEqual(len(changed), 1)
            self.assertIn('/work/src/recomp/gen/recomp_image.bin', asm.read_text())
            self.assertNotIn(origin, asm.read_text())
            asm.write_text(f'.incbin "{origin}"\n')
            (gen / 'recomp_image.bin').unlink()
            with self.assertRaises(FileNotFoundError):
                linux_matrix.rewrite_incbin_paths(staging)

    def test_elf_parser_reads_header(self):
        with tempfile.TemporaryDirectory() as t:
            binary = Path(t) / 'binary'
            header = bytearray(20)
            header[:4] = b'\x7fELF'
            header[4] = 2  # ELF64
            header[5] = 1  # LSB
            header[18:20] = (183).to_bytes(2, 'little')  # aarch64
            binary.write_bytes(bytes(header))
            info = linux_matrix.elf_evidence(binary)
            self.assertEqual(info['status'], 'OK')
            self.assertEqual(info['class'], 'ELF64')
            self.assertEqual(info['machine_name'], 'aarch64')
            self.assertEqual(len(info['sha256']), 64)
            bad = Path(t) / 'not-elf'
            bad.write_bytes(b'MZ\x90\x00')
            self.assertEqual(linux_matrix.elf_evidence(bad)['status'], 'NOT_ELF')


if __name__ == "__main__":
    unittest.main()
