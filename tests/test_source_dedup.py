"""Issue #26: standalone duplicate-C-body reporter (synthetic C only).

Every fixture is invented C written by the test, never a real generated tree.
The tests prove that byte-identical bodies are counted exactly, that a one-byte
edit is never reported as a saving, that a prior report detects tampering, and
that ambiguous/unreadable inputs fail closed rather than producing a guessed
report. No build is run and no build improvement is asserted.

Run with: python3 -m pytest tests/test_source_dedup.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.autorecomp import source_dedup as sd


BODY = "\n    return 7;\n"


def write_c(tmp_path: Path, name: str, body: str = BODY,
            fname: str | None = None, prelude: str = "") -> Path:
    """Write one synthetic generated C file and return its path."""
    fname = fname or "f_" + name.replace(".", "_")
    path = tmp_path / name
    path.write_bytes((prelude + f"void {fname}(void) {{{body}}}\n").encode("utf-8"))
    return path


def facts_of(summary: dict) -> dict:
    return summary["facts"]


class TestDedupCounting:
    """Duplicate bodies are grouped and their redundant bytes measured exactly."""

    def test_two_identical_bodies_in_one_file(self, tmp_path):
        path = write_c(tmp_path, "one.c", fname="first")
        with path.open("ab") as fh:
            fh.write(f"void second(void) {{{BODY}}}\n".encode())
        summary = sd.analyse([path])
        facts = facts_of(summary)
        assert facts["function_count"] == 2
        assert facts["duplicate_group_count"] == 1
        group = facts["duplicate_groups"][0]
        assert group["copies"] == 2
        assert group["redundant_copies"] == 1
        assert group["body_bytes"] == len(BODY.encode())
        assert group["redundant_bytes"] == len(BODY.encode())
        assert facts["exact_duplicate_byte_savings"] == len(BODY.encode())
        assert facts["input_bytes_total"] == path.stat().st_size

    def test_identical_bodies_across_files(self, tmp_path):
        a = write_c(tmp_path, "a.c", fname="alpha")
        b = write_c(tmp_path, "b.c", fname="beta")
        facts = facts_of(sd.analyse([a, b]))
        assert facts["duplicate_group_count"] == 1
        group = facts["duplicate_groups"][0]
        assert group["copies"] == 2
        assert [m["path"] for m in group["members"]] == sorted([str(a), str(b)])
        assert facts["exact_duplicate_byte_savings"] == len(BODY.encode())
        assert facts["input_bytes_total"] == a.stat().st_size + b.stat().st_size

    def test_three_copies_count_two_redundant(self, tmp_path):
        paths = [write_c(tmp_path, f"f{i}.c", fname=f"fn{i}") for i in range(3)]
        facts = facts_of(sd.analyse(paths))
        group = facts["duplicate_groups"][0]
        assert group["copies"] == 3
        assert group["redundant_copies"] == 2
        assert group["redundant_bytes"] == 2 * len(BODY.encode())
        assert facts["exact_duplicate_byte_savings"] == 2 * len(BODY.encode())

    def test_body_offsets_are_exact_byte_slices(self, tmp_path):
        path = write_c(tmp_path, "off.c", fname="offsetted")
        fn = facts_of(sd.analyse([path]))["inputs"][0]["functions"][0]
        raw = path.read_bytes()
        assert raw[fn["body_start"]:fn["body_end"]] == BODY.encode()
        assert fn["body_bytes"] == len(BODY.encode())
        assert fn["sha256"] == sd.hashlib.sha256(BODY.encode()).hexdigest()

    def test_nested_braces_strings_and_comments_do_not_end_body_early(self, tmp_path):
        body = ('\n    int i = 0;\n    if (i) { i++; }\n'
                '    const char *s = "}"; /* } */\n    return i;\n')
        path = write_c(tmp_path, "nested.c", body=body, fname="nested")
        facts = facts_of(sd.analyse([path]))
        assert facts["function_count"] == 1
        fn = facts["inputs"][0]["functions"][0]
        assert fn["body_bytes"] == len(body.encode())
        assert path.read_bytes()[fn["body_start"]:fn["body_end"]] == body.encode()

    def test_struct_initializer_and_directive_braces_are_not_functions(self, tmp_path):
        prelude = ('#line 1 "gen.c"\n'
                   'static const char names[] = { "a", "b" };\n'
                   'struct S { int x; };\n'
                   '#define WRAP(x) do { (x); } while (0)\n')
        path = write_c(tmp_path, "prelude.c", fname="real", prelude=prelude)
        facts = facts_of(sd.analyse([path]))
        assert facts["function_count"] == 1
        assert facts["inputs"][0]["functions"][0]["name"] == "real"
        assert facts["exact_duplicate_byte_savings"] == 0


class TestNoFalseSavings:
    """Near-misses and non-duplicates must never be reported as savings."""

    def test_one_byte_difference_is_not_a_duplicate(self, tmp_path):
        a = write_c(tmp_path, "a.c", fname="fa")
        b = write_c(tmp_path, "b.c", body=BODY.replace("7", "8"), fname="fb")
        facts = facts_of(sd.analyse([a, b]))
        assert facts["function_count"] == 2
        assert facts["duplicate_group_count"] == 0
        assert facts["exact_duplicate_byte_savings"] == 0

    def test_comment_difference_is_not_a_duplicate(self, tmp_path):
        a = write_c(tmp_path, "a.c", fname="fa")
        b = write_c(tmp_path, "b.c", body=BODY + "/* x */\n", fname="fb")
        facts = facts_of(sd.analyse([a, b]))
        assert facts["duplicate_group_count"] == 0
        assert facts["exact_duplicate_byte_savings"] == 0

    def test_same_signature_different_body_is_not_a_duplicate(self, tmp_path):
        a = write_c(tmp_path, "a.c", fname="same")
        b = write_c(tmp_path, "b.c", body="\n    return 99;\n", fname="same")
        facts = facts_of(sd.analyse([a, b]))
        assert facts["duplicate_group_count"] == 0
        assert facts["exact_duplicate_byte_savings"] == 0

    def test_different_signatures_same_body_is_grouped_but_flagged(self, tmp_path):
        a = write_c(tmp_path, "a.c", fname="first")
        b = write_c(tmp_path, "b.c", fname="second")
        facts = facts_of(sd.analyse([a, b]))
        group = facts["duplicate_groups"][0]
        assert group["copies"] == 2
        assert group["signatures_identical"] is False
        assert group["redundant_bytes"] == len(BODY.encode())

    def test_empty_bodies_group_with_zero_savings(self, tmp_path):
        a = write_c(tmp_path, "a.c", body="", fname="ea")
        b = write_c(tmp_path, "b.c", body="", fname="eb")
        facts = facts_of(sd.analyse([a, b]))
        assert facts["duplicate_group_count"] == 1
        assert facts["duplicate_groups"][0]["body_bytes"] == 0
        assert facts["exact_duplicate_byte_savings"] == 0

    def test_no_functions_yields_zero_savings(self, tmp_path):
        path = tmp_path / "empty.c"
        path.write_bytes(b"/* no functions here */\n")
        facts = facts_of(sd.analyse([path]))
        assert facts["function_count"] == 0
        assert facts["duplicate_group_count"] == 0
        assert facts["exact_duplicate_byte_savings"] == 0

    def test_savings_never_exceed_measured_input_bytes(self, tmp_path):
        paths = [write_c(tmp_path, f"g{i}.c", fname=g) for i, g in enumerate("abcd")]
        facts = facts_of(sd.analyse(paths))
        assert 0 <= facts["exact_duplicate_byte_savings"] <= facts["input_bytes_total"]

    def test_duplicate_input_path_is_refused(self, tmp_path):
        path = write_c(tmp_path, "a.c")
        with pytest.raises(sd.AmbiguousParse, match="duplicate input path"):
            sd.analyse([path, path])

    def test_no_claims_of_measured_build_gain(self, tmp_path):
        summary = sd.analyse([write_c(tmp_path, "a.c")])
        assert summary["build_improvement_measured"] is False
        assert summary["code_rewritten"] is False
        md = sd.render_markdown(summary)
        assert "Build improvement measured: **NO**" in md
        assert "Code rewritten: **NO**" in md


class TestTamperDetection:
    """A prior report and a changed input disagree explicitly."""

    def _report(self, paths, out: Path) -> dict:
        rc = sd.main([*map(str, paths), "--out-json", str(out), "--quiet"])
        assert rc == 0
        return json.loads(out.read_text())

    def test_verify_clean_report_is_ok_and_output_is_deterministic(self, tmp_path):
        paths = [write_c(tmp_path, "a.c", fname="fa"),
                 write_c(tmp_path, "b.c", fname="fb")]
        first = self._report(paths, tmp_path / "first.json")
        second = self._report(paths, tmp_path / "second.json")
        assert sd.dumps_json(first) == sd.dumps_json(second)
        assert (tmp_path / "first.json").read_bytes() == (tmp_path / "second.json").read_bytes()
        assert first["verdict"] == "OK"
        assert first["findings"] == []
        assert sd.main([*map(str, paths), "--verify", str(tmp_path / "first.json"),
                        "--quiet"]) == 0

    def test_tampered_body_is_detected(self, tmp_path):
        a = write_c(tmp_path, "a.c", fname="fa")
        b = write_c(tmp_path, "b.c", fname="fb")
        prior = tmp_path / "prior.json"
        self._report([a, b], prior)
        b.write_bytes(b.read_bytes().replace(b"return 7", b"return 8"))
        out = tmp_path / "after.json"
        rc = sd.main([str(a), str(b), "--verify", str(prior),
                      "--out-json", str(out), "--quiet"])
        assert rc == 1
        after = json.loads(out.read_text())
        assert after["verdict"] == "TAMPERED"
        assert any("exact_duplicate_byte_savings" in f or "duplicate_groups" in f
                   for f in after["findings"])
        assert after["facts"]["exact_duplicate_byte_savings"] == 0

    def test_appended_comment_outside_body_is_detected(self, tmp_path):
        a = write_c(tmp_path, "a.c")
        prior = tmp_path / "prior.json"
        self._report([a], prior)
        with a.open("ab") as fh:
            fh.write(b"\n/* appended after the report */\n")
        out = tmp_path / "after.json"
        rc = sd.main([str(a), "--verify", str(prior),
                      "--out-json", str(out), "--quiet"])
        assert rc == 1
        after = json.loads(out.read_text())
        assert after["verdict"] == "TAMPERED"
        assert any("facts.inputs" in f for f in after["findings"])

    def test_malformed_prior_report_fails_closed(self, tmp_path):
        a = write_c(tmp_path, "a.c")
        prior = tmp_path / "prior.json"
        prior.write_text("{}\n")
        rc = sd.main([str(a), "--verify", str(prior), "--quiet"])
        assert rc == 1

    def test_unreadable_prior_report_is_an_error(self, tmp_path):
        a = write_c(tmp_path, "a.c")
        rc = sd.main([str(a), "--verify", str(tmp_path / "missing.json"), "--quiet"])
        assert rc == 2


class TestFailClosedParses:
    """Ambiguous or unreadable inputs must never yield a guessed report."""

    @pytest.mark.parametrize("source, match", [
        ('void f(void) { const char *s = "oops; }\n', "literal"),
        ("void f(void) { /* never closed\n", "block comment"),
        ("void f(void) { return; } }\n", "unmatched"),
        ("void f(void) { return; \n", "unbalanced braces"),
        ("#define OPEN {\nvoid f(void) { return; }\n", "directive"),
        ("int x) { return; }\n", "cannot classify"),
        ("void f(void { return; }\n", "cannot classify"),
    ])
    def test_ambiguous_c_fails_closed(self, tmp_path, source, match):
        path = tmp_path / "bad.c"
        path.write_text(source)
        out = tmp_path / "must-not-exist.json"
        rc = sd.main([str(path), "--out-json", str(out), "--quiet"])
        assert rc == 2
        assert not out.exists()

    def test_missing_file_fails_closed(self, tmp_path):
        rc = sd.main([str(tmp_path / "missing.c"), "--quiet"])
        assert rc == 2

    def test_directory_input_fails_closed(self, tmp_path):
        rc = sd.main([str(tmp_path), "--quiet"])
        assert rc == 2

    def test_no_files_is_a_cli_error(self):
        with pytest.raises(SystemExit) as exc:
            sd.main([])
        assert exc.value.code == 2


class TestCLIContract:
    def test_positional_and_file_flag_are_combined(self, tmp_path):
        a = write_c(tmp_path, "a.c", fname="fa")
        b = write_c(tmp_path, "b.c", fname="fb")
        summary = sd.analyse([a, b])
        rc = sd.main([str(b), "--file", str(a), "--quiet",
                      "--out-json", str(tmp_path / "r.json")])
        assert rc == 0
        assert json.loads((tmp_path / "r.json").read_text())["facts"] == summary["facts"]

    def test_report_has_no_timestamps_or_absolute_machine_state(self, tmp_path):
        a = write_c(tmp_path, "a.c")
        summary = sd.analyse([a])
        blob = sd.dumps_json(summary)
        assert '"generated_at"' not in blob
        assert set(summary) == {"schema", "generator", "verdict", "facts",
                                "build_improvement_measured", "code_rewritten",
                                "limitations", "findings"}
