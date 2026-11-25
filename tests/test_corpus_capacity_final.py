"""Honest corpus-capacity accounting: dedupe, shortfall and tamper evidence."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import corpus_capacity_final as ccf  # noqa: E402


def sha(tag: str) -> str:
    return hashlib.sha256(tag.encode("utf-8")).hexdigest()


def write_json(path: Path, rows: list[dict]) -> Path:
    path.write_text(json.dumps({"schema": 1, "rows": rows}, indent=2) + "\n")
    return path


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


class CorpusCapacityFinalTests(unittest.TestCase):
    def run_tool(self, root: Path, *, inventory: Path | None = None,
                 static: Path | None = None, results: Path | None = None,
                 target: int = 10000, name: str = "audit.json") -> dict:
        out = root / name
        argv = ["--out", str(out), "--target", str(target)]
        if inventory is not None:
            argv += ["--inventory", str(inventory)]
        if static is not None:
            argv += ["--static", str(static)]
        if results is not None:
            argv += ["--results", str(results)]
        self.assertEqual(ccf.main(argv), 0)
        return json.loads(out.read_text())

    def test_checked_in_reports_exact_counts_and_determinism(self):
        paths = {
            "inventory": ROOT / "reports" / "local-games-inventory.json",
            "static": ROOT / "reports" / "pe32-1000-static.json",
            "results": ROOT / "reports" / "b1_results_snapshot.jsonl",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self.run_tool(root, **paths)
            second = self.run_tool(root, **paths, name="audit-again.json")
            self.assertEqual(
                (root / "audit.json").read_bytes(),
                (root / "audit-again.json").read_bytes(),
                "audit must be byte-for-byte deterministic",
            )

        inv, static, results = first["sources"]
        self.assertEqual((inv["rows"], inv["in_scope_rows"]), (262, 165))
        self.assertEqual((inv["unique_dedup_keys"], inv["unique_sha256"]), (214, 213))
        self.assertEqual(inv["rows_missing_sha256"], 10)
        self.assertEqual(inv["status_counts"], {"UNSPECIFIED": 262})
        self.assertEqual((static["rows"], static["unique_dedup_keys"]), (1000, 550))
        self.assertEqual(static["kind_counts"], {"dll": 77, "exe": 923})
        self.assertEqual(static["status_counts"], {"STATIC_OK": 1000})
        self.assertEqual(static["synthetic_fixture_rows"], 1)
        self.assertEqual((results["rows"], results["unique_dedup_keys"]), (126, 126))
        self.assertEqual(
            results["status_counts"], {"BLOCKED": 57, "BUILD-ONLY": 69})
        self.assertEqual(results["pass_rows"], 0)

        # Every source digest is the exact SHA-256 of the supplied bytes.
        for source in first["sources"]:
            actual = hashlib.sha256(
                (ROOT / source["path"]).read_bytes()).hexdigest()
            self.assertEqual(source["sha256"], actual, source["path"])

        dedup = first["dedup"]
        self.assertEqual(dedup["total_rows_supplied"], 1388)
        self.assertEqual(dedup["union_dedup_keys"], 715)
        self.assertEqual(dedup["union_actual_sha256"], 714)
        self.assertEqual(dedup["duplicate_rows_eliminated"], 673)
        self.assertEqual(dedup["rows_with_missing_sha256"], 10)
        self.assertEqual(dedup["synthetic_fixture_rows_excluded"], 1)
        self.assertEqual(dedup["dll_labeled_sha256_excluded"], 73)
        self.assertEqual(dedup["executable_game_candidate_sha256"], 640)

        verification = first["verification"]
        self.assertEqual(verification["pass_rows"], 0)
        self.assertEqual(verification["verified_pass_distinct_sha256"], 0)
        self.assertTrue(verification["no_fake_pass"])

        capacity = first["capacity"]
        self.assertEqual(capacity["status"], "CAPACITY_LIMIT")
        self.assertEqual(capacity["upper_bound_distinct_real_windows_games"], 640)
        self.assertEqual(capacity["shortfall_to_upper_bound"], 9360)
        self.assertEqual(capacity["verified_distinct_real_windows_games"], 0)
        self.assertEqual(capacity["shortfall_to_verified_target"], 10000)
        self.assertEqual(capacity["blocker"], "LEGAL_CORPUS_ABSENT")
        self.assertEqual(first["issue_130"], "OPEN")

        committed = json.loads(
            (ROOT / "proof" / "corpus-capacity" / "audit.json").read_text())
        self.assertEqual(committed, first, "committed audit.json is stale")

    def test_dedupe_across_sources_fixtures_dlls_and_missing_hashes(self):
        a, b, c, d, e = (sha(t) for t in ("a", "b", "c", "d", "e"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inv = write_json(root / "inv.json", [
                {"path": "/games/a.exe", "sha256": a, "in_scope": True},
                {"path": "/games/a-copy.exe", "sha256": a.upper(), "in_scope": True},
                {"path": "/games/b.exe", "sha256": b, "in_scope": True},
                {"path": "/games/bad.exe", "error": "PEFormatError"},
            ])
            static = write_json(root / "static.json", [
                {"path": "/games/a.exe", "sha256": a, "kind": "exe",
                 "status": "STATIC_OK"},
                {"path": "/games/c.exe", "sha256": c, "kind": "exe",
                 "status": "STATIC_OK"},
                {"path": "/games/d.dll", "sha256": d, "kind": "dll",
                 "status": "STATIC_OK"},
                {"path": "/games/d-again.dll", "sha256": d, "kind": "dll",
                 "status": "STATIC_OK"},
                {"path": "/repo/tests/fixtures/pe32-minimal.exe", "sha256": e,
                 "kind": "exe", "status": "STATIC_OK"},
            ])
            results = write_jsonl(root / "results.jsonl", [
                {"path": "/games/b.exe", "sha256": b, "status": "BUILD-ONLY",
                 "is_game": True},
            ])
            doc = self.run_tool(root, inventory=inv, static=static,
                                results=results, target=4)

        self.assertEqual(doc["dedup"]["total_rows_supplied"], 10)
        self.assertEqual(doc["dedup"]["union_dedup_keys"], 6)
        self.assertEqual(doc["dedup"]["union_actual_sha256"], 5)
        self.assertEqual(doc["dedup"]["duplicate_rows_eliminated"], 4)
        self.assertEqual(doc["dedup"]["rows_with_missing_sha256"], 1)
        self.assertEqual(doc["dedup"]["synthetic_fixture_rows_excluded"], 1)
        self.assertEqual(doc["dedup"]["dll_labeled_sha256_excluded"], 1)
        self.assertEqual(doc["dedup"]["executable_game_candidate_sha256"], 3)
        self.assertEqual(doc["capacity"]["shortfall_to_upper_bound"], 1)
        self.assertEqual(doc["sources"][0]["in_scope_rows"], 3)
        self.assertEqual(doc["sources"][0]["unique_dedup_keys"], 3)

    def test_shortfall_is_exact_and_tracks_target(self):
        x, y = sha("x"), sha("y")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            static = write_json(root / "static.json", [
                {"path": "/games/x.exe", "sha256": x, "kind": "exe",
                 "status": "STATIC_OK"},
                {"path": "/games/y.exe", "sha256": y, "kind": "exe",
                 "status": "STATIC_OK"},
            ])
            for target, expected_shortfall in ((3, 1), (2, 0), (10000, 9998)):
                doc = self.run_tool(root, static=static, target=target,
                                    name=f"audit-{target}.json")
                cap = doc["capacity"]
                self.assertEqual(cap["upper_bound_distinct_real_windows_games"], 2)
                self.assertEqual(cap["shortfall_to_upper_bound"], expected_shortfall)
                self.assertEqual(cap["verified_distinct_real_windows_games"], 0)
                self.assertEqual(cap["shortfall_to_verified_target"], target)
                self.assertEqual(cap["status"], "CAPACITY_LIMIT")
                self.assertEqual(cap["blocker"], "LEGAL_CORPUS_ABSENT")

    def test_bare_pass_is_never_counted_as_verified(self):
        good, bare, bad = sha("good"), sha("bare"), sha("bad")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = write_jsonl(root / "results.jsonl", [
                {"path": "/games/good.exe", "sha256": good, "status": "PASS",
                 "verified": True, "evidence_sha256": sha("evidence"),
                 "oracle": "windows-oracle"},
                {"path": "/games/bare.exe", "sha256": bare, "status": "PASS"},
                {"path": "/games/bad.exe", "sha256": bad, "status": "PASS",
                 "verified": True, "evidence_sha256": "not-a-digest",
                 "oracle": "none"},
            ])
            doc = self.run_tool(root, results=results)

        verification = doc["verification"]
        self.assertEqual(verification["pass_rows"], 3)
        self.assertEqual(verification["refused_pass_rows"], 2)
        self.assertEqual(verification["verified_pass_distinct_sha256"], 1)
        self.assertEqual(doc["sources"][0]["verified_pass_keys"], [good])
        self.assertEqual(doc["capacity"]["verified_distinct_real_windows_games"], 1)

    def test_tamper_changes_digests_counts_and_statuses(self):
        a, b, c = sha("a"), sha("b"), sha("c")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            static = write_json(root / "static.json", [
                {"path": "/games/a.exe", "sha256": a, "kind": "exe",
                 "status": "STATIC_OK"},
            ])
            results = write_jsonl(root / "results.jsonl", [
                {"path": "/games/b.exe", "sha256": b, "status": "BUILD-ONLY"},
            ])
            before = self.run_tool(root, static=static, results=results,
                                   name="before.json")

            # Tamper 1: promote a build-only row to a bare PASS.
            write_jsonl(results, [
                {"path": "/games/b.exe", "sha256": b, "status": "PASS"},
            ])
            after_pass = self.run_tool(root, static=static, results=results,
                                       name="after-pass.json")
            self.assertNotEqual(before["inputs_digest"], after_pass["inputs_digest"])
            self.assertNotEqual(before["sources"][1]["sha256"],
                                after_pass["sources"][1]["sha256"])
            self.assertEqual(before["verification"]["pass_rows"], 0)
            self.assertEqual(after_pass["verification"]["pass_rows"], 1)
            self.assertEqual(after_pass["verification"]["refused_pass_rows"], 1)
            self.assertEqual(after_pass["capacity"][
                "verified_distinct_real_windows_games"], 0)

            # Tamper 2: inject a brand new binary into the static sweep.
            write_json(root / "static.json", [
                {"path": "/games/a.exe", "sha256": a, "kind": "exe",
                 "status": "STATIC_OK"},
                {"path": "/games/c.exe", "sha256": c, "kind": "exe",
                 "status": "STATIC_OK"},
            ])
            after_row = self.run_tool(root, static=static, results=results,
                                      name="after-row.json")
            self.assertNotEqual(after_pass["inputs_digest"], after_row["inputs_digest"])
            self.assertEqual(before["dedup"]["union_dedup_keys"], 2)
            self.assertEqual(after_row["dedup"]["union_dedup_keys"], 3)
            self.assertEqual(after_row["dedup"]["executable_game_candidate_sha256"], 3)
            self.assertEqual(after_row["capacity"]["shortfall_to_upper_bound"], 9997)

    def test_only_cli_supplied_paths_are_read(self):
        a = sha("a")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            static = write_json(root / "static.json", [
                {"path": "/games/a.exe", "sha256": a, "status": "STATIC_OK"}])
            original = Path.read_bytes
            read_paths: list[Path] = []

            def tracked(self, *args, **kwargs):
                read_paths.append(self)
                return original(self, *args, **kwargs)

            with mock.patch.object(Path, "read_bytes", tracked):
                self.run_tool(root, static=static)
            self.assertEqual(set(read_paths), {static})

    def test_missing_sources_are_rejected(self):
        with self.assertRaises(SystemExit):
            ccf.main([])


if __name__ == "__main__":
    unittest.main()
