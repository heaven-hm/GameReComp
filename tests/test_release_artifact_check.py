"""Tests for the release artifact verifier (SHA256SUMS re-hashing)."""
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tools.release_artifact_check import (
    build_report,
    main,
    parse_sha256sums,
    render_json,
    render_markdown,
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ReleaseArtifactCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        self.sums = self.root / "SHA256SUMS"

    def tearDown(self):
        self.tmp.cleanup()

    def add_artifact(self, rel: str, data: bytes) -> str:
        path = self.artifacts / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return _digest(data)

    def write_sums(self, lines):
        self.sums.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def report(self):
        return build_report(self.sums, self.artifacts)

    def statuses(self, report):
        return {item["path"]: item["status"] for item in report["artifacts"]}

    def test_all_ok_is_complete(self):
        first = self.add_artifact("game.dll", b"dll bytes")
        second = self.add_artifact("README.txt", b"readme")
        self.write_sums([f"{first}  game.dll", f"{second}  README.txt"])

        report = self.report()
        self.assertTrue(report["complete"])
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(self.statuses(report), {
            "README.txt": "OK", "game.dll": "OK"})
        self.assertEqual(report["summary"]["failures"], 0)
        self.assertEqual(report["summary"]["ok"], 2)
        self.assertIn("PASS (release complete)", render_markdown(report))

    def test_missing_artifact_is_detected(self):
        present = self.add_artifact("present.bin", b"here")
        self.write_sums([
            f"{present}  present.bin",
            f"{_digest(b'gone')}  omitted.bin",
        ])

        report = self.report()
        entry = next(item for item in report["artifacts"]
                     if item["path"] == "omitted.bin")
        self.assertEqual(entry["status"], "MISSING")
        self.assertIsNone(entry["actual_sha256"])
        self.assertEqual(entry["expected_sha256"], _digest(b"gone"))
        self.assertFalse(report["complete"])
        self.assertEqual(report["summary"]["missing"], 1)

    def test_hash_mismatch_is_detected(self):
        expected = self.add_artifact("payload.bin", b"original")
        path = self.artifacts / "payload.bin"
        path.write_bytes(b"tampered")
        self.write_sums([f"{expected}  payload.bin"])

        report = self.report()
        entry = report["artifacts"][0]
        self.assertEqual(entry["status"], "HASH_MISMATCH")
        self.assertEqual(entry["actual_sha256"], _digest(b"tampered"))
        self.assertNotEqual(entry["actual_sha256"], entry["expected_sha256"])
        self.assertFalse(report["complete"])
        self.assertEqual(report["summary"]["hash_mismatch"], 1)

    def test_extra_unlisted_file_is_detected(self):
        digest = self.add_artifact("listed.bin", b"listed")
        self.add_artifact("unlisted.bin", b"sneaky")
        self.write_sums([f"{digest}  listed.bin"])

        report = self.report()
        self.assertEqual(self.statuses(report), {
            "listed.bin": "OK", "unlisted.bin": "EXTRA"})
        extra = next(item for item in report["artifacts"]
                     if item["path"] == "unlisted.bin")
        self.assertEqual(extra["actual_sha256"], _digest(b"sneaky"))
        self.assertIsNone(extra["expected_sha256"])
        self.assertFalse(report["complete"])
        self.assertEqual(report["summary"]["extra"], 1)

    def test_all_four_statuses_and_deterministic_renders(self):
        ok = self.add_artifact("ok.bin", b"ok")
        bad = self.add_artifact("bad.bin", b"before")
        (self.artifacts / "bad.bin").write_bytes(b"after")
        self.add_artifact("extra.bin", b"extra")
        self.write_sums([
            f"{ok}  ok.bin",
            f"{bad}  bad.bin",
            f"{_digest(b'absent')}  missing.bin",
        ])

        report = self.report()
        self.assertEqual(self.statuses(report), {
            "bad.bin": "HASH_MISMATCH",
            "extra.bin": "EXTRA",
            "missing.bin": "MISSING",
            "ok.bin": "OK",
        })
        self.assertEqual(report["summary"]["failures"], 3)
        self.assertFalse(report["complete"])
        self.assertIn("FAIL (release incomplete)", render_markdown(report))

        first_json = render_json(report)
        first_md = render_markdown(report)
        # Re-hash from scratch: output must not depend on run order or time.
        again = self.report()
        self.assertEqual(render_json(again), first_json)
        self.assertEqual(render_markdown(again), first_md)
        self.assertEqual(json.loads(first_json), report)
        paths = [item["path"] for item in json.loads(first_json)["artifacts"]]
        self.assertEqual(paths, sorted(paths))

    def test_cli_writes_json_and_markdown_and_returns_exit_codes(self):
        digest = self.add_artifact("a.bin", b"payload")
        self.write_sums([f"{digest}  a.bin"])
        json_path = self.root / "out" / "report.json"
        md_path = self.root / "out" / "report.md"

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main([
                "--sums", str(self.sums),
                "--artifact-dir", str(self.artifacts),
                "--json", str(json_path),
                "--markdown", str(md_path),
            ])
        self.assertEqual(code, 0)
        report = self.report()
        self.assertEqual(json_path.read_text(encoding="utf-8"),
                         render_json(report))
        self.assertEqual(md_path.read_text(encoding="utf-8"),
                         render_markdown(report))
        self.assertIn("Release complete: True", out.getvalue())

        self.add_artifact("unexpected.bin", b"nope")
        with contextlib.redirect_stdout(io.StringIO()):
            code = main([
                "--sums", str(self.sums),
                "--artifact-dir", str(self.artifacts),
                "--json", str(json_path),
            ])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(json_path.read_text())["complete"])

    def test_cli_prints_markdown_when_no_output_paths(self):
        digest = self.add_artifact("a.bin", b"payload")
        self.write_sums([f"{digest}  a.bin"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--sums", str(self.sums),
                         "--artifact-dir", str(self.artifacts)])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), render_markdown(self.report()))

    def test_missing_inputs_do_not_claim_completion(self):
        report = build_report(self.root / "does-not-exist", self.artifacts)
        self.assertFalse(report["complete"])
        self.assertGreaterEqual(report["summary"]["errors"], 1)
        self.assertNotIn("PASS (release complete)", render_markdown(report))

        digest = self.add_artifact("a.bin", b"payload")
        self.write_sums([f"{digest}  a.bin"])
        report = build_report(self.sums, self.root / "no-such-dir")
        self.assertFalse(report["complete"])
        self.assertGreaterEqual(report["summary"]["errors"], 1)

    def test_supplied_manifest_inside_artifact_dir_is_not_extra(self):
        digest = self.add_artifact("a.bin", b"payload")
        manifest = self.artifacts / "SHA256SUMS"
        manifest.write_text(f"{digest}  a.bin\n", encoding="utf-8")

        report = build_report(manifest, self.artifacts)
        self.assertTrue(report["complete"])
        self.assertEqual(self.statuses(report), {"a.bin": "OK"})

    def test_listed_checksum_file_is_verified_like_any_artifact(self):
        digest = self.add_artifact("a.bin", b"payload")
        checksum = self.add_artifact("SHA256SUMS.txt", b"checksum data")
        self.write_sums([
            f"{digest}  a.bin",
            f"{checksum}  SHA256SUMS.txt",
        ])

        report = self.report()
        self.assertEqual(self.statuses(report)["SHA256SUMS.txt"], "OK")
        self.assertTrue(report["complete"])

    def test_nested_paths_spaces_comments_and_binary_marker(self):
        first = self.add_artifact("nested/dir/first.bin", b"one")
        second = self.add_artifact("name with spaces.bin", b"two")
        self.write_sums([
            "# generated manifest",
            "",
            f"{first.upper()}  nested/dir/first.bin",
            f"{second} *name with spaces.bin",
        ])

        report = self.report()
        self.assertTrue(report["complete"], report["errors"])
        self.assertTrue(all(status == "OK"
                            for status in self.statuses(report).values()))

    def test_gnu_escaped_and_star_prefixed_names(self):
        backslash_name = "back\\slash.txt"
        star_name = "*star.bin"
        first = self.add_artifact(backslash_name, b"one")
        second = self.add_artifact(star_name, b"two")
        self.write_sums([
            f"\\{first}  back\\\\slash.txt",
            f"{second}  {star_name}",
        ])

        report = self.report()
        self.assertEqual(self.statuses(report), {
            backslash_name: "OK", star_name: "OK"})
        self.assertTrue(report["complete"])

    def test_malformed_and_unsafe_manifest_lines_fail_closed(self):
        self.add_artifact("safe.bin", b"safe")
        self.write_sums([
            "deadbeef  short.bin",
            f"{_digest(b'safe')}",
            f"{_digest(b'x')}  ../escape.bin",
            f"{_digest(b'x')}  /absolute.bin",
            f"{_digest(b'x')}  safe.bin",
        ])

        entries, errors = parse_sha256sums(self.sums.read_text())
        self.assertEqual([entry["path"] for entry in entries], ["safe.bin"])
        self.assertEqual(len(errors), 4)
        report = self.report()
        self.assertFalse(report["complete"])
        self.assertEqual(report["summary"]["errors"], 4)

    def test_conflicting_duplicate_entries_are_rejected(self):
        digest = self.add_artifact("a.bin", b"payload")
        self.write_sums([f"{digest}  a.bin", f"{_digest(b'other')}  a.bin"])
        report = self.report()
        self.assertFalse(report["complete"])
        self.assertTrue(any("conflicting" in error for error in report["errors"]))

    def test_empty_manifest_does_not_pass_vacuously(self):
        self.sums.write_text("", encoding="utf-8")
        report = self.report()
        self.assertFalse(report["complete"])
        self.assertEqual(report["summary"]["listed"], 0)
        self.assertTrue(any("no artifacts" in error for error in report["errors"]))

    def test_listed_directory_is_missing_not_ok(self):
        (self.artifacts / "folder.bin").mkdir()
        self.write_sums([f"{_digest(b'x')}  folder.bin"])
        report = self.report()
        self.assertEqual(self.statuses(report), {"folder.bin": "MISSING"})
        self.assertFalse(report["complete"])

    def test_markdown_escapes_pipe_in_path(self):
        digest = self.add_artifact("a|b.bin", b"payload")
        self.write_sums([f"{digest}  a|b.bin"])
        markdown = render_markdown(self.report())
        self.assertIn("`a\\|b.bin`", markdown)


if __name__ == "__main__":
    unittest.main()
