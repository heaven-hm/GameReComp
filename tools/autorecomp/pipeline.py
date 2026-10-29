"""End-to-end pipeline: PE -> recovered functions -> C -> project -> binary -> run -> verify.

Stages are deliberately small and fail closed. Every stage records its own
status and evidence; a failure stops dependent stages, but the report stage
still runs so the failure is inspectable.
"""

import contextlib
import hashlib
import io
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

_TOOLS = Path(__file__).resolve().parents[1]
for _sub in ("pe", "disasm", "lift"):
    _p = str(_TOOLS / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pe_analyze import analyze_pe, build_iat_map  # noqa: E402
from translator import (MAX_UNIT_BYTES, discover_functions,  # noqa: E402
                        generate_recomp_files)

from . import projectgen  # noqa: E402

STAGES = ["inspect", "recover", "translate", "project", "compile",
          "run", "verify", "report"]

STAGE_WEIGHTS = {
    "inspect": 10, "recover": 10, "translate": 10, "project": 10,
    "compile": 20, "run": 15, "verify": 15, "report": 10,
}

# A stalled compile used to hold the pipeline open indefinitely: GeneRally's
# 402 MB translation (a single 273 MB unit, see translator.MAX_UNIT_BYTES) ran
# for 30+ minutes with no sign of finishing. The deadline scales with generated
# source size, can be overridden with --build-timeout, and 0 disables it.
BUILD_TIMEOUT_BASE = 600.0
BUILD_TIMEOUT_PER_MB = 1.0
BUILD_TIMEOUT_CAP = 1800.0


def default_build_timeout(source_bytes):
    return min(BUILD_TIMEOUT_CAP,
               BUILD_TIMEOUT_BASE + BUILD_TIMEOUT_PER_MB * (source_bytes / (1 << 20)))

CATEGORIES = ("HIGH_LEVEL_VERIFIED", "HIGH_LEVEL_UNVERIFIED",
              "LOW_LEVEL_FALLBACK", "UNRESOLVED")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tail(path, lines=80):
    try:
        text = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(text[-lines:])


class StageFailure(Exception):
    def __init__(self, message, data=None):
        super().__init__(message)
        self.data = data or {}


class Pipeline:
    def __init__(self, pe_path, project_dir=None, assets_dir=None,
                 timeout=30.0, headless=True, oracle=None, jobs=None,
                 split=1000, build=True, run_binary=True, on_event=None,
                 split_bytes=MAX_UNIT_BYTES, build_timeout=None):
        self.pe_path = Path(pe_path).expanduser().resolve()
        self.project_dir = Path(project_dir or projectgen.default_project_dir(self.pe_path)).resolve()
        self.assets_dir = Path(assets_dir).expanduser().resolve() if assets_dir else self.pe_path.parent
        self.timeout = timeout
        self.headless = headless
        self.oracle_path = Path(oracle).expanduser().resolve() if oracle else None
        self.jobs = jobs
        self.split = split
        self.split_bytes = split_bytes
        self.build_timeout = build_timeout
        self.do_build = build
        self.do_run = run_binary
        self.on_event = on_event

        self.info = None
        self.pe_data = None
        self.iat_map = {}
        self.functions = {}
        self.lifted = []
        self.unresolved_functions = 0
        self.name = None
        self.binary = None
        self.stages = []
        self.stage_status = {}
        self.run_result = None
        self.verification = None
        self.manifest = None

    # ------------------------------------------------------------------
    # events / stage bookkeeping

    def _emit(self, **evt):
        if self.on_event:
            try:
                self.on_event(evt)
            except Exception:
                pass

    def _stage(self, name):
        return _StageRun(self, name)

    # ------------------------------------------------------------------
    # stages

    def stage_inspect(self):
        if not self.pe_path.is_file():
            raise StageFailure(f"input executable not found: {self.pe_path}",
                               {"path": str(self.pe_path)})
        info = analyze_pe(str(self.pe_path), do_hashes=True)
        if info.pe_type != "PE32":
            raise StageFailure(
                f"{info.pe_type} is not supported by the PE32 backend yet "
                f"(machine {info.machine}); fail-closed instead of guessing",
                {"pe_type": info.pe_type, "machine": info.machine})
        self.info = info
        self.pe_data = self.pe_path.read_bytes()
        self.iat_map = build_iat_map(info)
        self.name = projectgen.executable_name(self.pe_path)

        data = {
            "path": str(self.pe_path),
            "size": info.filesize,
            "sha256": _sha256_file(self.pe_path),
            "md5": info.md5,
            "sha1": info.sha1,
            "machine": info.machine,
            "pe_type": info.pe_type,
            "subsystem": info.subsystem,
            "image_base": f"0x{info.image_base:08X}",
            "entry_point": f"0x{info.image_base + info.entry_point_rva:08X}",
            "linker_version": info.linker_version,
            "code_range": [f"0x{info.code_start:08X}", f"0x{info.code_end:08X}"],
            "import_dlls": list(info.import_dlls),
            "import_count": len(info.imports),
            "iat_entries": len(self.iat_map),
        }
        self._write_json("reports/inspect.json", data)
        return f"{info.machine} {info.pe_type}, {len(info.imports)} imports, entry {data['entry_point']}", data

    def stage_recover(self):
        functions = discover_functions(self.pe_data, self.info, self.iat_map)
        self.functions = functions
        ordered = sorted(functions.values(), key=lambda f: f.address)
        entries = []
        for i, func in enumerate(ordered):
            entries.append({
                "address": f"0x{func.address:08X}",
                "name": func.name,
                "size": func.size,
                "blocks": len(func.blocks),
                "instructions": func.num_instructions,
                "is_thunk": bool(func.is_thunk),
                "calls": len(func.calls_to),
                "source_unit": f"src/recomp/gen/recomp_{i // self.split:04d}.c",
                "category": "LOW_LEVEL_FALLBACK",
            })
        data = {
            "count": len(entries),
            "thunks": sum(1 for e in entries if e["is_thunk"]),
            "instructions": sum(e["instructions"] for e in entries),
            "functions": entries,
        }
        self._write_json("reports/functions.json", data)
        return f"{len(entries)} functions, {data['instructions']} instructions", {"count": len(entries), "instructions": data["instructions"]}

    def stage_translate(self):
        gen_dir = self.project_dir / "src" / "recomp" / "gen"
        # Re-translation must not leave stale units behind: the dispatch table
        # and recomp_funcs.h are regenerated together, but an old recomp_0007.c
        # from a previous, larger run would still be compiled by the GLOB.
        if gen_dir.exists():
            shutil.rmtree(gen_dir)
        gen_dir.mkdir(parents=True, exist_ok=True)

        log_path = self.project_dir / "logs" / "translate.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        buf = io.StringIO()
        t0 = time.time()
        with contextlib.redirect_stdout(buf):
            lifted = generate_recomp_files(
                self.functions, self.iat_map, str(gen_dir),
                split_size=self.split, max_unit_bytes=self.split_bytes)
        log_path.write_text(buf.getvalue())
        self.lifted = lifted

        # stage_recover guesses the unit from the function count; by-size
        # chunking makes that guess wrong, so use the generator's own manifest.
        unit_ranges = []
        units_manifest = gen_dir / "recomp_units.json"
        if units_manifest.exists():
            for unit in json.loads(units_manifest.read_text()).get("units", []):
                unit_ranges.append((int(unit["first_address"], 16),
                                    int(unit["last_address"], 16),
                                    f"src/recomp/gen/{unit['file']}"))

        errors = 0
        failed_names = set()
        for path in sorted(gen_dir.glob("recomp_*.c")):
            text = path.read_text(errors="replace")
            errors += text.count("/* ERROR lifting")
            failed_names.update(re.findall(r"/\* ERROR lifting (\w+):", text))
        self.unresolved_functions = errors

        by_addr = {f"0x{a:08X}": name for a, name in lifted}
        func_path = self.project_dir / "reports" / "functions.json"
        if func_path.exists():
            data = json.loads(func_path.read_text())
            for entry in data["functions"]:
                if entry["address"] in by_addr:
                    entry["name"] = by_addr[entry["address"]]
                if entry["name"] in failed_names:
                    entry["category"] = "UNRESOLVED"
                if unit_ranges:
                    try:
                        addr = int(entry["address"], 16)
                    except (KeyError, ValueError):
                        continue
                    for first, last, rel in unit_ranges:
                        if first <= addr <= last:
                            entry["source_unit"] = rel
                            break
            data["lift_errors"] = errors
            func_path.write_text(json.dumps(data, indent=2) + "\n")

        units = sorted(gen_dir.glob("*.c"))
        total_bytes = sum(p.stat().st_size for p in units)
        data = {
            "source_units": len(units),
            "source_bytes": total_bytes,
            "lift_errors": errors,
            "seconds": round(time.time() - t0, 1),
        }
        if errors:
            data["warning"] = (f"{errors} function(s) failed to lift; they are "
                               f"emitted as UNRESOLVED stubs that abort when called")
        return f"{len(units)} source files, {total_bytes} bytes, {errors} lift errors", data

    def stage_project(self):
        pe_info = {
            "path": str(self.pe_path),
            "sha256": _sha256_file(self.pe_path),
            "machine": self.info.machine,
            "pe_type": self.info.pe_type,
            "image_base": f"0x{self.info.image_base:08X}",
            "entry_point": f"0x{self.info.image_base + self.info.entry_point_rva:08X}",
        }
        unresolved = self.unresolved_functions
        total = len(self.functions)
        categories = {
            "HIGH_LEVEL_VERIFIED": 0,
            "HIGH_LEVEL_UNVERIFIED": 0,
            "LOW_LEVEL_FALLBACK": max(0, total - unresolved),
            "UNRESOLVED": unresolved,
        }
        record = projectgen.write_project(
            self.project_dir, self.name, self.pe_path.name, pe_info,
            extra={"functions": total, "source_categories": categories})
        data = {
            "project": str(self.project_dir),
            "name": self.name,
            "source_categories": categories,
            "runtime_files": len(record["runtime_files"]),
        }
        return f"project at {self.project_dir}", data

    def stage_compile(self):
        if not self.do_build:
            return "build skipped (--no-build)", {"skipped": True}
        build_dir = self.project_dir / "build"
        build_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.project_dir / "logs" / "compile.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        cmake = shutil.which("cmake")
        if not cmake:
            raise StageFailure("cmake not found on PATH")
        configure = [cmake, "-S", str(self.project_dir), "-B", str(build_dir),
                     "-DCMAKE_BUILD_TYPE=Release"]
        build = [cmake, "--build", str(build_dir), "--parallel",
                 str(self.jobs or os.cpu_count() or 4)]

        source_bytes = sum(p.stat().st_size
                           for p in (self.project_dir / "src").rglob("*")
                           if p.is_file())
        adaptive = self.build_timeout is None
        timeout = (default_build_timeout(source_bytes)
                   if adaptive else float(self.build_timeout))
        deadline = (time.monotonic() + timeout) if timeout > 0 else None
        deadline_data = {"log": str(log_path), "timed_out": True,
                         "timeout_seconds": timeout, "adaptive": adaptive,
                         "source_bytes": source_bytes}

        t0 = time.time()
        with open(log_path, "w") as log:
            log.write("$ " + " ".join(configure) + "\n")
            rc, timed_out = self._run_build_command(configure, log, deadline)
            if timed_out:
                raise StageFailure(
                    f"cmake configure exceeded the {timeout:g}s build deadline "
                    f"(see {log_path})", deadline_data)
            if rc == 0:
                log.write("\n$ " + " ".join(build) + "\n")
                rc, timed_out = self._run_build_command(build, log, deadline)
            if timed_out:
                raise StageFailure(
                    f"build exceeded the {timeout:g}s deadline (see {log_path}); "
                    f"the deadline scales with generated source size and can be "
                    f"raised or disabled with --build-timeout", deadline_data)
        seconds = round(time.time() - t0, 1)
        binary = build_dir / self.name
        if os.name == "nt":
            binary = binary.with_suffix(".exe")
        if rc != 0:
            raise StageFailure(
                f"build failed (see {log_path})\n{_tail(log_path)}",
                {"log": str(log_path), "returncode": rc, "seconds": seconds})
        if not binary.exists():
            raise StageFailure(f"build reported success but {binary} is missing",
                               {"log": str(log_path)})
        self.binary = binary
        return f"{binary} ({binary.stat().st_size} bytes, {seconds}s)", {
            "binary": str(binary),
            "sha256": _sha256_file(binary),
            "seconds": seconds,
            "source_bytes": source_bytes,
            "build_timeout_seconds": timeout or None,
            "build_timeout_adaptive": adaptive,
        }

    def _run_build_command(self, cmd, log, deadline):
        """Run a build command under one shared deadline.

        subprocess.run(timeout=...) kills only the direct child; cmake leaves
        compiler processes behind. The build gets its own session so the whole
        group can be killed when the deadline expires.
        """
        timeout = None if deadline is None else max(1.0, deadline - time.monotonic())
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=(os.name != "nt"))
        try:
            return proc.wait(timeout=timeout), False
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except OSError:
                    proc.kill()
            else:
                proc.kill()
            proc.wait()
            return None, True

    def stage_run(self):
        if not self.binary:
            return "run skipped (no binary)", {"skipped": True}
        if not self.do_run:
            return "run skipped (--no-run)", {"skipped": True}

        log_dir = self.project_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = log_dir / "run.stdout.log"
        stderr_path = log_dir / "run.stderr.log"
        env = dict(os.environ)
        if self.headless:
            env["SDL_VIDEODRIVER"] = "dummy"
            env["SDL_AUDIODRIVER"] = "dummy"
        cmd = [str(self.binary), str(self.pe_path)]
        t0 = time.time()
        status, rc, timed_out = "exited", None, False
        with open(stdout_path, "w") as out, open(stderr_path, "w") as err:
            proc = subprocess.Popen(
                cmd, cwd=str(self.assets_dir), env=env,
                stdout=out, stderr=err,
                stdin=subprocess.DEVNULL, start_new_session=(os.name != "nt"))
            try:
                rc = proc.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                status = "timeout"
                if os.name != "nt":
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except OSError:
                        proc.kill()
                else:
                    proc.kill()
                proc.wait()
        seconds = round(time.time() - t0, 1)
        stdout = _tail(stdout_path, 200)
        stderr = _tail(stderr_path, 200)
        output = "\n".join(part for part in (stdout, stderr) if part)
        if not timed_out:
            if rc is not None and rc < 0:
                status = "crashed"
            elif rc == 2 and "unresolved imports" in output:
                status = "boot-failed"
            else:
                status = "exited"

        unresolved = sorted(set(re.findall(r"\[pe\] unresolved import (\S+)", output)))
        boot_m = re.search(r"\[host\] .* booted: entry 0x([0-9A-Fa-f]+), (\d+) unresolved imports", output)
        bound_m = re.search(r"\[pe\] bound (\d+) imports, (\d+) unresolved", output)
        result = {
            "command": cmd,
            "cwd": str(self.assets_dir),
            "headless": self.headless,
            "timeout_seconds": self.timeout,
            "timed_out": timed_out,
            "status": status,
            "exit_code": rc,
            "seconds": seconds,
            "booted": bool(boot_m),
            "entry_point": f"0x{int(boot_m.group(1), 16):08X}" if boot_m else None,
            "unresolved_import_count": int(boot_m.group(2)) if boot_m else None,
            "bound_imports": int(bound_m.group(1)) if bound_m else None,
            "unresolved_imports": unresolved,
            "log_stdout": str(stdout_path),
            "log_stderr": str(stderr_path),
            "stdout_tail": stdout,
            "stderr_tail": stderr,
            "output_tail": output,
        }
        self.run_result = result
        self._write_json("reports/run.json", result)

        if status == "boot-failed":
            raise StageFailure(
                f"boot failed: {result['unresolved_import_count']} unresolved imports"
                f" (see {stdout_path} and {stderr_path})", result)
        return f"{status}, exit={rc}, {seconds}s, {len(unresolved)} unresolved imports", result

    def stage_verify(self):
        checks = []
        gaps = []
        run = self.run_result
        if run is None:
            status = "not-run"
            reason = "the rebuilt binary was not run"
        elif run["status"] in ("crashed", "boot-failed"):
            status = "launch-failed"
            reason = (f"rebuilt process {run['status']}"
                      + (f" (exit {run['exit_code']})" if run["exit_code"] is not None else ""))
        elif self.oracle_path:
            oracle = json.loads(self.oracle_path.read_text())
            mismatches = []
            if "exit_code" in oracle:
                ok = run["exit_code"] == oracle["exit_code"]
                checks.append({"check": "exit_code", "expected": oracle["exit_code"],
                               "actual": run["exit_code"], "ok": ok})
                if not ok:
                    mismatches.append("exit_code")
            for needle in oracle.get("stdout_contains", []):
                ok = needle in run.get("stdout_tail", "")
                checks.append({"check": "stdout_contains", "expected": needle, "ok": ok})
                if not ok:
                    mismatches.append(f"stdout missing {needle!r}")
            for needle in oracle.get("stderr_contains", []):
                ok = needle in run.get("stderr_tail", "")
                checks.append({"check": "stderr_contains", "expected": needle, "ok": ok})
                if not ok:
                    mismatches.append(f"stderr missing {needle!r}")
            if mismatches:
                status = "trace-diverged"
                reason = "; ".join(mismatches)
            else:
                status = "scenario-pass"
                reason = "process-level oracle matched"
                gaps.append("oracle compares process observables only, not gameplay state")
        else:
            gaps.append("no independent original-Windows oracle supplied")
            if run["timed_out"]:
                status = "partial"
                reason = ("process launched and stayed alive until the "
                          f"{self.timeout:g}s timeout; no crash observed, no oracle")
            elif run["status"] == "exited" and run["exit_code"] == 0:
                status = "partial"
                reason = "process launched and exited 0; no oracle supplied"
            else:
                status = "partial"
                reason = (f"process launched and exited {run['exit_code']}; "
                          "no oracle supplied")
        gaps.append("no frame, input or audio comparison")
        gaps.append("low-level register C is not high-level reconstructed source")

        self.verification = {
            "schema": 1,
            "generated_at": _now(),
            "status": status,
            "reason": reason,
            "scope": "process" if status in ("scenario-pass", "trace-diverged") else "launch",
            "game": self.pe_path.name,
            "executable_sha256": _sha256_file(self.pe_path),
            "target": {"os": sys.platform, "arch": platform.machine(),
                       "label": f"{platform.system()} {platform.machine()} (host build)"},
            "checks": checks,
            "oracle_gaps": gaps,
            "run": run,
        }
        self._write_json("reports/verification.json", self.verification)
        return f"{status}: {reason}", {"status": status, "reason": reason}

    def stage_report(self):
        self._write_manifest()
        report = {
            "schema": 1,
            "tool": "reveng-studio autorecomp",
            "version": "0.1.0",
            "generated_at": _now(),
            "input": str(self.pe_path),
            "project": str(self.project_dir),
            "stages": self.stages,
            "outcome": self.verification or {"status": "not-run", "reason": "verification did not run"},
            "score": self.score(),
            "manifest": "manifest.json",
        }
        self._write_json("reports/report.json", report)
        html_path = self.project_dir / "reports" / "report.html"
        html_path.write_text(self._render_html(report))
        return f"reports in {self.project_dir / 'reports'}", {"score": report["score"], "html": str(html_path)}

    # ------------------------------------------------------------------
    # helpers

    def _write_json(self, rel, data):
        path = self.project_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")

    def _write_manifest(self):
        # A failure before the project stage (e.g. an unsupported PE) still
        # has to leave a report behind, so the directory may not exist yet.
        self.project_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for base in ("src",):
            for path in sorted((self.project_dir / base).rglob("*")):
                if path.is_file():
                    files.append({
                        "path": str(path.relative_to(self.project_dir)),
                        "sha256": _sha256_file(path),
                        "size": path.stat().st_size,
                    })
        source_digest = hashlib.sha256(
            "".join(f"{f['sha256']}  {f['path']}\n" for f in files).encode()).hexdigest()
        manifest = {
            "schema": 1,
            "game": self.pe_path.name,
            "input_sha256": _sha256_file(self.pe_path) if self.pe_path.exists() else None,
            "source_digest_sha256": source_digest,
            "source_files": files,
            "binary": None,
        }
        if self.binary and self.binary.exists():
            manifest["binary"] = {
                "path": str(self.binary),
                "sha256": _sha256_file(self.binary),
                "size": self.binary.stat().st_size,
                "built_from_source_digest": source_digest,
                "build_command": f"cmake -S {self.project_dir} -B {self.binary.parent} "
                                 f"&& cmake --build {self.binary.parent}",
            }
        self.manifest = manifest
        (self.project_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n")

    def score(self):
        total = 0
        for st in self.stages:
            if st["status"] != "ok":
                continue
            if st["name"] == "verify" and self.verification:
                vs = self.verification["status"]
                if vs in ("scenario-pass", "verified-playable-scope"):
                    total += STAGE_WEIGHTS["verify"]
                elif vs == "partial":
                    total += 8
            elif st["name"] in ("compile", "run") and st.get("data", {}).get("skipped"):
                continue
            else:
                total += STAGE_WEIGHTS[st["name"]]
        return total

    def _render_html(self, report):
        esc = lambda s: (str(s).replace("&", "&amp;").replace("<", "&lt;")
                         .replace(">", "&gt;"))
        rows = []
        for st in report["stages"]:
            rows.append(
                f"<tr><td>{esc(st['name'])}</td><td class='{esc(st['status'])}'>"
                f"{esc(st['status'])}</td><td>{st['seconds']:.1f}s</td>"
                f"<td><pre>{esc(st.get('message',''))}</pre></td></tr>")
        v = self.verification or {}
        run = self.run_result or {}
        cats = (json.loads((self.project_dir / "project.json").read_text())
                .get("source_categories", {}) if (self.project_dir / "project.json").exists() else {})
        cat_rows = "".join(
            f"<tr><td>{esc(k)}</td><td>{v}</td></tr>" for k, v in cats.items())
        checks = "".join(
            f"<tr><td>{esc(c.get('check'))}</td><td>{esc(c.get('expected',''))}</td>"
            f"<td>{esc(c.get('actual',''))}</td><td>{'PASS' if c.get('ok') else 'FAIL'}</td></tr>"
            for c in v.get("checks", []))
        return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>autorecomp report - {esc(self.pe_path.name)}</title>
<style>
body {{ font: 14px system-ui, sans-serif; margin: 2rem; color: #1b1b1b; }}
h1 {{ font-size: 1.4rem; }} h2 {{ font-size: 1.1rem; margin-top: 1.6rem; }}
table {{ border-collapse: collapse; width: 100%; margin: .5rem 0; }}
td, th {{ border: 1px solid #ccc; padding: .35rem .5rem; text-align: left; vertical-align: top; }}
pre {{ margin: 0; white-space: pre-wrap; font-size: 12px; }}
.ok {{ color: #146c2e; }} .failed {{ color: #a11; }} .skipped {{ color: #666; }}
</style></head><body>
<h1>autorecomp report: {esc(self.pe_path.name)}</h1>
<p>Generated {esc(report['generated_at'])}. Project: <code>{esc(self.project_dir)}</code>.
Score: <strong>{report['score']}/100</strong>.
Verification: <strong>{esc(v.get('status','not-run'))}</strong> - {esc(v.get('reason',''))}</p>
<h2>Stages</h2>
<table><tr><th>Stage</th><th>Status</th><th>Time</th><th>Detail</th></tr>{''.join(rows)}</table>
<h2>Source provenance</h2>
<table><tr><th>Category</th><th>Functions</th></tr>{cat_rows}</table>
<p>Source digest: <code>{esc((self.manifest or {}).get('source_digest_sha256',''))}</code></p>
<h2>Oracle checks</h2>
<table><tr><th>Check</th><th>Expected</th><th>Actual</th><th>Result</th></tr>{checks or '<tr><td colspan="4">none supplied</td></tr>'}</table>
<h2>Oracle gaps</h2><ul>{''.join(f'<li>{esc(g)}</li>' for g in v.get('oracle_gaps', []))}</ul>
<h2>Run output (tail)</h2><pre>{esc(run.get('output_tail',''))}</pre>
</body></html>
"""


class _StageRun:
    def __init__(self, pipeline, name):
        self.p = pipeline
        self.name = name

    def __enter__(self):
        self.t0 = time.time()
        self.p._emit(event="stage-start", stage=self.name)
        return self

    def __exit__(self, exc_type, exc, tb):
        seconds = time.time() - self.t0
        record = {"name": self.name, "seconds": round(seconds, 2)}
        if exc_type is None:
            record.update(status="ok", message=self.message, data=self.data)
        elif issubclass(exc_type, StageFailure):
            record.update(status="failed", message=str(exc), data=exc.data)
        else:
            record.update(status="failed",
                          message=f"{exc_type.__name__}: {exc}\n{traceback.format_exc()}")
        self.p.stages.append(record)
        self.p.stage_status[self.name] = record["status"]
        self.p._emit(event="stage-end", **record)
        return exc_type is not None and not issubclass(exc_type, KeyboardInterrupt)


def _run_stage(pipeline, name):
    stage = _StageRun(pipeline, name)
    fn = getattr(pipeline, f"stage_{name}")
    with stage:
        message, data = fn()
        stage.message, stage.data = message, data


def run_pipeline(pipeline):
    """Run all stages in order, stopping at the first non-report failure.

    Verification still runs when the run stage failed but left evidence:
    a launch-failed verdict is a verification result, and skipping it
    would leave the project without any reports/verification.json.
    """
    stop = False
    for name in STAGES:
        if stop and name != "report" and not (
                name == "verify" and pipeline.run_result is not None):
            pipeline.stages.append({
                "name": name, "status": "skipped", "seconds": 0.0,
                "message": "skipped after earlier failure", "data": {}})
            pipeline.stage_status[name] = "skipped"
            pipeline._emit(event="stage-end", **pipeline.stages[-1])
            continue
        _run_stage(pipeline, name)
        if pipeline.stage_status[name] == "failed" and name != "report":
            stop = True
    return pipeline
