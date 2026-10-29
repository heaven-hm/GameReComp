"""Gated readability pass: rev.ng first, optional AI polish, re-proved by running.

Readability edits are only allowed on a project whose source has already been
built and executed. Every edit is compilation-checked and re-run against the
baseline process evidence. When an edit fails to build or changes behaviour,
the provider is given the failure as feedback and may retry (a bounded repair
loop); whatever still fails is reverted, so the checked-in source always
corresponds to a run that passed.

The pass has a hard wall-clock budget (``--max-seconds``). Every subprocess is
bounded by the smaller of its own adaptive timeout and the remaining budget,
and an interruption or exception restores every edited file before the report
is written. rev.ng is the primary provider; AI output is never accepted as a
substitute for it, and low-level register C stays ``LOW_LEVEL_FALLBACK``.
"""

import argparse
import difflib
import hashlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

PROVEN_STATUSES = {"partial", "scenario-pass", "verified-playable-scope",
                   "trace-diverged"}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def _tail(path, lines=80):
    try:
        text = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(text[-lines:])


def source_digest(project_dir):
    """The same digest the pipeline records in manifest.json."""
    files = []
    for path in sorted((Path(project_dir) / "src").rglob("*")):
        if path.is_file():
            files.append((str(path.relative_to(project_dir)),
                          _sha256_file(path)))
    return hashlib.sha256(
        "".join(f"{digest}  {name}\n" for name, digest in files).encode()
    ).hexdigest()


class ReadabilityError(Exception):
    pass


class Deadline:
    """Wall-clock budget shared by every step of the pass."""

    def __init__(self, seconds):
        self.seconds = float(seconds)
        self.t0 = time.monotonic()

    def remaining(self):
        return max(0.0, self.seconds - (time.monotonic() - self.t0))

    def expired(self):
        return self.remaining() <= 0.5

    def clamp(self, wanted, floor=1.0):
        return max(floor, min(float(wanted), self.remaining()))


def detect_revng():
    template = os.environ.get("REVNG_DECOMPILE_CMD")
    if template:
        return {"name": "revng", "available": True, "kind": "command-template",
                "command": template, "source": "REVNG_DECOMPILE_CMD"}
    exe = os.environ.get("REVNG_BIN") or shutil.which("revng")
    if not exe:
        return {"name": "revng", "available": False, "kind": "binary",
                "reason": "revng not found: install rev.ng, set REVNG_BIN, "
                          "or provide REVNG_DECOMPILE_CMD"}
    version = None
    try:
        proc = subprocess.run([exe, "--version"], capture_output=True, text=True,
                              timeout=15)
        if proc.returncode == 0:
            version = (proc.stdout or proc.stderr).strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired, IndexError):
        pass
    return {"name": "revng", "available": True, "kind": "binary",
            "command": exe, "version": version, "source": "PATH"}


def detect_ai():
    cmd = os.environ.get("REVENG_AI_CMD")
    if not cmd:
        return {"name": "ai-readability", "available": False,
                "reason": "REVENG_AI_CMD not set; the AI polish pass is "
                          "optional and disabled"}
    return {"name": "ai-readability", "available": True,
            "command": cmd, "source": "REVENG_AI_CMD"}


class ReadabilityHarness:
    """Gate -> rev.ng -> optional AI repair loop -> rebuild -> rerun -> accept."""

    def __init__(self, project_dir, pe_path=None, max_units=1, timeout=None,
                 providers="all", max_attempts=3, max_seconds=900,
                 provider_timeout=300):
        self.project = Path(project_dir).resolve()
        self.providers = providers
        self.max_units = max(1, int(max_units))
        self.max_attempts = max(1, int(max_attempts))
        self.provider_timeout = max(10.0, float(provider_timeout))
        self.deadline = Deadline(max_seconds)
        self.timeout_override = timeout
        self.log_path = self.project / "logs" / "readability.log"
        self.report = {
            "schema": 1,
            "tool": "reveng-studio readability harness",
            "generated_at": _now(),
            "project": str(self.project),
            "budget_seconds": float(max_seconds),
            "providers": {},
            "gate": {},
            "stages": [],
            "edits": [],
            "attempts": [],
            "outcome": "not-run",
            "reason": "",
        }
        self.baseline = None
        self.revng_ok = False
        self.revng_status = "skipped"
        self._pe_override = Path(pe_path).expanduser().resolve() if pe_path else None
        self._restore_points = []

    # -- logging / bookkeeping ----------------------------------------

    def _log(self, message):
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a") as log:
                log.write(f"{_now()} {message}\n")
        except OSError:
            pass

    def _stage(self, name, status, message, **data):
        record = {"name": name, "status": status, "message": message,
                  "seconds": round(data.pop("seconds", 0.0), 2)}
        record.update(data)
        self.report["stages"].append(record)
        self._log(f"[{name}] {status}: {message}")
        return record

    def _write_report(self):
        out = self.project / "reports"
        out.mkdir(parents=True, exist_ok=True)
        (out / "readability.json").write_text(
            json.dumps(self.report, indent=2) + "\n")
        (out / "readability.html").write_text(self._render_html())
        self._log(f"report written: {out / 'readability.json'}")

    def _render_html(self):
        esc = lambda s: (str(s).replace("&", "&amp;").replace("<", "&lt;")
                         .replace(">", "&gt;"))
        rows = "".join(
            f"<tr><td>{esc(s['name'])}</td><td class='{esc(s['status'])}'>"
            f"{esc(s['status'])}</td><td>{s['seconds']:.1f}s</td>"
            f"<td><pre>{esc(s['message'])}</pre></td></tr>"
            for s in self.report["stages"])
        edits = "".join(
            f"<tr><td>{esc(e['file'])}</td><td>{esc(e['provider'])}</td>"
            f"<td>{esc(e.get('result', '?'))}</td>"
            f"<td>{esc(e.get('patch', ''))}</td></tr>"
            for e in self.report["edits"])
        attempts = "".join(
            f"<tr><td>{esc(a['file'])}</td><td>{a['attempt']}</td>"
            f"<td>{esc(a.get('step', ''))}</td>"
            f"<td>{'ok' if a.get('ok') else 'failed'}</td>"
            f"<td><pre>{esc(a.get('detail', ''))}</pre></td></tr>"
            for a in self.report["attempts"])
        return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>readability report</title>
<style>
body {{ font: 14px system-ui, sans-serif; margin: 2rem; }}
table {{ border-collapse: collapse; width: 100%; margin: .5rem 0; }}
td, th {{ border: 1px solid #ccc; padding: .35rem .5rem; text-align: left; vertical-align: top; }}
pre {{ margin: 0; white-space: pre-wrap; font-size: 12px; }}
.ok, .accepted {{ color: #146c2e; }} .blocked, .failed, .rejected, .error {{ color: #a11; }}
</style></head><body>
<h1>Readability pass</h1>
<p>Project: <code>{esc(self.project)}</code>.
Outcome: <strong>{esc(self.report['outcome'])}</strong> - {esc(self.report['reason'])}</p>
<h2>Stages</h2>
<table><tr><th>Stage</th><th>Status</th><th>Time</th><th>Detail</th></tr>{rows}</table>
<h2>Repair attempts</h2>
<table><tr><th>File</th><th>Attempt</th><th>Step</th><th>Result</th><th>Detail</th></tr>
{attempts or '<tr><td colspan="5">none</td></tr>'}</table>
<h2>Edits</h2>
<table><tr><th>File</th><th>Provider</th><th>Result</th><th>Patch</th></tr>
{edits or '<tr><td colspan="4">no source edits</td></tr>'}</table>
</body></html>
"""

    # -- gate ----------------------------------------------------------

    def _load_baseline(self):
        manifest = _read_json(self.project / "manifest.json")
        verification = _read_json(self.project / "reports" / "verification.json")
        run = _read_json(self.project / "reports" / "run.json")
        report = _read_json(self.project / "reports" / "report.json", {}) or {}
        if not manifest or not verification or not run:
            raise ReadabilityError(
                "no tested baseline: run `autorecomp <exe>` first so the "
                "project has manifest.json, reports/run.json and "
                "reports/verification.json")
        digest = source_digest(self.project)
        if manifest.get("source_digest_sha256") != digest:
            raise ReadabilityError(
                "source tree changed since the recorded build "
                f"({str(manifest.get('source_digest_sha256'))[:12]} != "
                f"{digest[:12]}); rebuild and re-run the pipeline first")
        status = verification.get("status", "not-run")
        if status not in PROVEN_STATUSES:
            raise ReadabilityError(
                f"baseline verification is '{status}', not a launched-and-"
                "observed run; readability edits are only allowed after the "
                "stage has been tested")
        command = run.get("command") or []
        if not command or not Path(command[0]).exists():
            raise ReadabilityError("baseline binary is missing; rebuild first")
        pe = self._pe_override
        if pe is None and len(command) > 1 and command[1]:
            pe = Path(command[1])
        pe_s = str(pe) if pe else None
        if pe_s and not Path(pe_s).is_file():
            raise ReadabilityError(f"original executable not found: {pe_s}")

        compile_seconds = None
        for st in report.get("stages", []):
            if st.get("name") == "compile" and st.get("status") == "ok":
                compile_seconds = st.get("seconds")
        build_timeout = max(120.0, (float(compile_seconds or 60) * 4) + 60)

        baseline_seconds = float(run.get("seconds") or 0)
        run_timeout = self.timeout_override or float(run.get("timeout_seconds") or 30)
        if not run.get("timed_out"):
            run_timeout = max(run_timeout, baseline_seconds * 3 + 10)
        run_timeout = min(run_timeout, 300.0)
        return {
            "source_digest_sha256": digest,
            "verification_status": status,
            "exit_code": run.get("exit_code"),
            "timed_out": bool(run.get("timed_out", False)),
            "run_seconds": baseline_seconds,
            "command": command,
            "cwd": run.get("cwd"),
            "headless": run.get("headless", True),
            "timeout_seconds": run_timeout,
            "build_timeout": build_timeout,
            "pe": pe_s,
        }

    # -- rev.ng --------------------------------------------------------

    def _revng(self):
        t0 = time.time()
        provider = detect_revng()
        self.report["providers"]["revng"] = provider
        self._log(f"revng provider: {provider}")
        if not provider["available"]:
            self.revng_status = "blocked"
            self._stage("revng", "blocked", provider["reason"],
                        seconds=round(time.time() - t0, 2))
            return False
        if self.deadline.expired():
            self.revng_status = "budget-exhausted"
            self._stage("revng", "failed",
                        "wall-clock budget exhausted before rev.ng could run",
                        seconds=round(time.time() - t0, 2))
            return False
        pe = self.baseline.get("pe")
        if not pe:
            self.revng_status = "blocked"
            self._stage("revng", "blocked",
                        "original executable path is unknown; pass --pe",
                        seconds=round(time.time() - t0, 2))
            return False

        out_dir = self.project / "readability" / "revng"
        try:
            if out_dir.exists():
                shutil.rmtree(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.revng_status = "failed"
            self._stage("revng", "failed", f"cannot create {out_dir}: {exc}",
                        seconds=round(time.time() - t0, 2))
            return False
        log_path = self.project / "logs" / "readability-revng.log"

        if provider["kind"] == "command-template":
            try:
                cmd = [part.format(pe=pe, outdir=str(out_dir))
                       for part in shlex.split(provider["command"])]
            except (ValueError, KeyError) as exc:
                self.revng_status = "failed"
                self._stage("revng", "failed",
                            f"bad REVNG_DECOMPILE_CMD template: {exc}",
                            seconds=round(time.time() - t0, 2))
                return False
        else:
            cmd = [provider["command"], "decompile", pe,
                   "--output-dir", str(out_dir)]

        timeout = self.deadline.clamp(self.provider_timeout * 2, floor=10)
        with open(log_path, "w") as log:
            log.write("$ " + " ".join(cmd) + "\n")
            try:
                rc = subprocess.run(cmd, cwd=str(out_dir), stdout=log,
                                    stderr=subprocess.STDOUT,
                                    timeout=timeout).returncode
            except subprocess.TimeoutExpired:
                log.write(f"\ntimeout after {timeout:.0f}s\n")
                self.revng_status = "timeout"
                self._stage("revng", "failed",
                            f"rev.ng timed out after {timeout:.0f}s; see {log_path}",
                            seconds=round(time.time() - t0, 2), log=str(log_path))
                return False
            except OSError as exc:
                log.write(f"\n{type(exc).__name__}: {exc}\n")
                self.revng_status = "failed"
                self._stage("revng", "failed",
                            f"cannot execute rev.ng: {exc}", seconds=round(
                                time.time() - t0, 2), log=str(log_path))
                return False
        seconds = round(time.time() - t0, 2)
        if rc != 0:
            self.revng_status = "failed"
            self._stage("revng", "failed",
                        f"rev.ng decompile failed (rc={rc}); see {log_path}",
                        seconds=seconds, log=str(log_path))
            return False

        candidates = sorted(str(p.relative_to(self.project))
                            for p in out_dir.rglob("*.c"))
        self.revng_status = "candidates-recorded" if candidates else "no-candidates"
        self._stage(
            "revng", self.revng_status,
            f"rev.ng ran, {len(candidates)} C candidate file(s) recorded; "
            "integration into the dispatch ABI is not implemented, so nothing "
            "is compiled or claimed high-level",
            seconds=seconds, log=str(log_path), candidates=candidates,
            integration="not-implemented")
        return bool(candidates)

    # -- optional AI polish with a bounded repair loop ------------------

    def _ai(self, gen_units):
        t0 = time.time()
        provider = detect_ai()
        self.report["providers"]["ai-readability"] = provider
        self._log(f"ai provider: { {k: v for k, v in provider.items() if k != 'command'} }")
        if not provider["available"]:
            self._stage("ai-readability", "blocked", provider["reason"],
                        seconds=round(time.time() - t0, 2))
            return "blocked"
        if not self.revng_ok:
            self._stage("ai-readability", "blocked",
                        "rev.ng did not produce candidates; the AI polish "
                        "pass is not a substitute for the primary provider",
                        seconds=round(time.time() - t0, 2))
            return "blocked"

        try:
            cmd_template = shlex.split(provider["command"])
        except ValueError as exc:
            self._stage("ai-readability", "failed",
                        f"bad REVENG_AI_CMD: {exc}",
                        seconds=round(time.time() - t0, 2))
            return "failed"
        work = self.project / "readability" / "ai"
        try:
            if work.exists():
                shutil.rmtree(work)
            work.mkdir(parents=True)
        except OSError as exc:
            self._stage("ai-readability", "failed",
                        f"cannot create {work}: {exc}",
                        seconds=round(time.time() - t0, 2))
            return "failed"

        final = None
        for unit in gen_units:
            result = self._ai_unit(unit, cmd_template, work)
            if result in ("accepted", "no-changes"):
                final = result
                if result == "accepted":
                    break
            else:
                self._stage("ai-readability", result,
                            f"{unit.name}: edits reverted after the repair loop",
                            seconds=round(time.time() - t0, 2))
                return result
            if self.deadline.expired():
                self._stage("ai-readability", "budget-exhausted",
                            "wall-clock budget exhausted during the AI pass",
                            seconds=round(time.time() - t0, 2))
                return "budget-exhausted"

        if final == "accepted":
            self.report["source_digest_after"] = source_digest(self.project)
            self._stage("ai-readability", "accepted",
                        "readable-source edit(s) built and reproduced the "
                        "baseline process evidence",
                        seconds=round(time.time() - t0, 2))
            return "accepted"
        self._stage("ai-readability", "no-changes",
                    "provider returned no source changes",
                    seconds=round(time.time() - t0, 2))
        return "no-changes"

    def _ai_unit(self, unit, cmd_template, work):
        rel = unit.relative_to(self.project)
        original_text = unit.read_text(errors="replace")
        backup = self.project / "readability" / "backup" / rel
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(unit, backup)
        self._restore_points.append({"file": str(rel),
                                     "backup": str(backup)})

        feedback = ""
        last_step = "provider"
        last_detail = ""
        for attempt in range(1, self.max_attempts + 1):
            if self.deadline.expired():
                self._revert_unit(unit, backup)
                return "budget-exhausted"
            out = work / f"{unit.name}.attempt{attempt}.c"
            feedback_path = work / f"{unit.name}.attempt{attempt}.feedback.txt"
            feedback_path.write_text(
                f"attempt: {attempt}/{self.max_attempts}\n"
                f"unit: {rel}\n\n"
                "The previous attempt failed the gate below. Keep the file "
                "compiling and behaviour-identical; improve readability only.\n\n"
                f"{feedback or 'none (first attempt)'}\n")
            env = dict(os.environ)
            env["READABILITY_FEEDBACK"] = str(feedback_path)
            env["READABILITY_ATTEMPT"] = str(attempt)
            proc_log = self.project / "logs" / f"readability-ai-{unit.name}-{attempt}.log"
            timeout = self.deadline.clamp(self.provider_timeout, floor=10)
            self._log(f"ai attempt {attempt}/{self.max_attempts} for {rel} "
                      f"(timeout {timeout:.0f}s)")
            try:
                with open(proc_log, "w") as log:
                    proc = subprocess.run(
                        cmd_template + [str(unit), str(out)],
                        stdout=log, stderr=subprocess.STDOUT, env=env,
                        timeout=timeout)
                rc = proc.returncode
            except subprocess.TimeoutExpired:
                self._record_attempt(rel, attempt, "provider", False,
                                     f"timeout after {timeout:.0f}s")
                feedback = f"provider timed out after {timeout:.0f}s"
                last_step, last_detail = "provider", feedback
                continue
            except OSError as exc:
                self._record_attempt(rel, attempt, "provider", False, str(exc))
                self._revert_unit(unit, backup)
                self._log(f"ai provider cannot run: {exc}")
                return "failed"
            if rc != 0:
                detail = f"provider rc={rc}: " + _tail(proc_log, 20)
                self._record_attempt(rel, attempt, "provider", False, detail)
                feedback = detail
                last_step, last_detail = "provider", detail
                continue
            if not out.exists():
                self._record_attempt(rel, attempt, "provider", False,
                                     "provider produced no output file")
                feedback = "provider produced no output file"
                last_step, last_detail = "provider", feedback
                continue
            new_text = out.read_text(errors="replace")
            if new_text == original_text:
                self._record_attempt(rel, attempt, "provider", True,
                                     "no changes returned")
                self._revert_unit(unit, backup)
                return "no-changes"
            unit.write_text(new_text)
            self._record_attempt(rel, attempt, "provider", True,
                                 "candidate applied")

            build = self._rebuild()
            if build["status"] != "ok":
                detail = "build failed: " + _tail(build["log"], 40)
                self._record_attempt(rel, attempt, "build", False, detail)
                self._revert_unit(unit, backup)
                feedback = detail
                last_step, last_detail = "build", detail
                continue

            evidence = self._rerun()
            problems = self._compare(evidence)
            if problems:
                detail = "evidence changed: " + "; ".join(problems) + "\n" + \
                         evidence["stdout_tail"][-1000:] + "\n" + \
                         evidence["stderr_tail"][-1000:]
                self._record_attempt(rel, attempt, "rerun", False, detail)
                self._revert_unit(unit, backup)
                feedback = detail
                last_step, last_detail = "rerun", detail
                continue

            patch = "".join(difflib.unified_diff(
                original_text.splitlines(True), new_text.splitlines(True),
                fromfile=f"a/{rel}", tofile=f"b/{rel}"))
            patch_path = work / (unit.name + ".patch")
            patch_path.write_text(patch)
            self.report["edits"].append({
                "file": str(rel), "provider": "ai-readability",
                "result": "accepted",
                "patch": str(patch_path.relative_to(self.project)),
                "attempts": attempt,
                "original_sha256": _sha256_file(backup),
                "applied_sha256": _sha256_file(unit),
            })
            self._record_attempt(rel, attempt, "rerun", True,
                                 "evidence matched the baseline")
            self._log(f"accepted readability edit for {rel} after {attempt} attempt(s)")
            self._restore_points = [p for p in self._restore_points
                                    if p["file"] != str(rel)]
            return "accepted"

        self._revert_unit(unit, backup)
        self.report["edits"].append({
            "file": str(rel), "provider": "ai-readability",
            "result": f"reverted after {self.max_attempts} attempts",
            "last_step": last_step, "last_detail": last_detail[:2000],
        })
        self._log(f"reverted {rel} after {self.max_attempts} attempts "
                  f"(last failure at {last_step})")
        return "rejected-after-repair"

    def _record_attempt(self, rel, attempt, step, ok, detail):
        self.report["attempts"].append({
            "file": str(rel), "attempt": attempt, "step": step, "ok": ok,
            "detail": detail,
        })

    def _revert_unit(self, unit, backup):
        try:
            shutil.copy2(backup, unit)
        except OSError:
            pass

    def _restore_all(self):
        for point in list(self._restore_points):
            try:
                shutil.copy2(point["backup"], self.project / point["file"])
                self._log(f"restored {point['file']}")
            except OSError as exc:
                self._log(f"could not restore {point['file']}: {exc}")
        self._restore_points = []

    # -- build / rerun / compare ---------------------------------------

    def _rebuild(self):
        log_path = self.project / "logs" / "readability-build.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        cmake = shutil.which("cmake")
        if not cmake:
            log_path.write_text("cmake not found on PATH\n")
            return {"status": "failed", "log": str(log_path)}
        if self.deadline.expired():
            log_path.write_text("wall-clock budget exhausted before build\n")
            return {"status": "failed", "log": str(log_path), "timeout": True}
        build_dir = self.project / "build"
        build_dir.mkdir(exist_ok=True)
        timeout = self.deadline.clamp(self.baseline["build_timeout"], floor=30)
        with open(log_path, "w") as log:
            log.write("$ cmake configure\n")
            try:
                rc = subprocess.run(
                    [cmake, "-S", str(self.project), "-B", str(build_dir),
                     "-DCMAKE_BUILD_TYPE=Release"],
                    stdout=log, stderr=subprocess.STDOUT,
                    timeout=timeout).returncode
                if rc == 0:
                    log.write("\n$ cmake build\n")
                    rc = subprocess.run(
                        [cmake, "--build", str(build_dir), "--parallel",
                         str(os.cpu_count() or 4)],
                        stdout=log, stderr=subprocess.STDOUT,
                        timeout=timeout).returncode
            except subprocess.TimeoutExpired:
                log.write(f"\ntimeout after {timeout:.0f}s\n")
                return {"status": "failed", "log": str(log_path), "timeout": True}
            except OSError as exc:
                log.write(f"\n{type(exc).__name__}: {exc}\n")
                return {"status": "failed", "log": str(log_path)}
        return {"status": "ok" if rc == 0 else "failed",
                "log": str(log_path), "returncode": rc}

    def _rerun(self):
        logs = self.project / "logs"
        logs.mkdir(exist_ok=True)
        stdout_path = logs / "readability-run.stdout.log"
        stderr_path = logs / "readability-run.stderr.log"
        env = dict(os.environ)
        if self.baseline["headless"]:
            env["SDL_VIDEODRIVER"] = "dummy"
            env["SDL_AUDIODRIVER"] = "dummy"
        timeout = self.deadline.clamp(self.baseline["timeout_seconds"], floor=5)
        t0 = time.time()
        timed_out = False
        rc = None
        with open(stdout_path, "w") as out, open(stderr_path, "w") as err:
            try:
                proc = subprocess.Popen(
                    self.baseline["command"], cwd=self.baseline["cwd"], env=env,
                    stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                    start_new_session=(os.name != "nt"))
            except OSError as exc:
                err.write(f"cannot start rebuilt binary: {exc}\n")
                return {"exit_code": None, "timed_out": False,
                        "seconds": 0.0, "stdout_tail": "",
                        "stderr_tail": f"cannot start rebuilt binary: {exc}",
                        "log_stdout": str(stdout_path),
                        "log_stderr": str(stderr_path)}
            try:
                rc = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                if os.name != "nt":
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except OSError:
                        proc.kill()
                else:
                    proc.kill()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
        return {
            "exit_code": rc,
            "timed_out": timed_out,
            "seconds": round(time.time() - t0, 2),
            "timeout_seconds": round(timeout, 1),
            "stdout_tail": _tail(stdout_path, 200),
            "stderr_tail": _tail(stderr_path, 200),
            "log_stdout": str(stdout_path),
            "log_stderr": str(stderr_path),
        }

    def _compare(self, evidence):
        problems = []
        if evidence["timed_out"] != self.baseline["timed_out"]:
            problems.append(
                f"timed_out {self.baseline['timed_out']} -> {evidence['timed_out']}")
        if evidence["exit_code"] != self.baseline["exit_code"]:
            problems.append(
                f"exit code {self.baseline['exit_code']} -> {evidence['exit_code']}")
        combined = evidence["stdout_tail"] + "\n" + evidence["stderr_tail"]
        for marker in ("Symbol not found", "Segmentation fault",
                       "unresolved import", "ERROR lifting"):
            if marker in combined:
                problems.append(f"new fatal marker in output: {marker!r}")
        return problems

    # -- entry point ---------------------------------------------------

    def run(self):
        try:
            self._run_inner()
        except KeyboardInterrupt:
            self._restore_all()
            self.report["outcome"] = "interrupted"
            self.report["reason"] = "interrupted; every edited file was restored"
            self._log("interrupted; restored edited files")
        except Exception as exc:  # noqa: BLE001 - report, never crash the CLI
            self._restore_all()
            tb = traceback.format_exc()
            self.report["outcome"] = "error"
            self.report["reason"] = f"{type(exc).__name__}: {exc}"
            self._stage("harness", "error", f"{type(exc).__name__}: {exc}\n{tb}")
            self._log(f"unhandled error: {tb}")
        finally:
            try:
                self._write_report()
            except OSError as exc:
                print(f"warning: could not write readability report: {exc}",
                      file=sys.stderr)
        return self.report

    def _run_inner(self):
        try:
            self.baseline = self._load_baseline()
        except ReadabilityError as exc:
            self.report["outcome"] = "blocked"
            self.report["reason"] = str(exc)
            self._stage("gate", "blocked", str(exc))
            return
        self.report["gate"] = {k: v for k, v in self.baseline.items()
                               if k != "command"}
        self._stage("gate", "ok",
                    "tested baseline matches the current source",
                    source_digest=self.baseline["source_digest_sha256"],
                    verification=self.baseline["verification_status"])

        if self.providers in ("all", "revng"):
            self.revng_ok = self._revng()
        else:
            self._stage("revng", "skipped",
                        "rev.ng is the primary readability provider; skipping "
                        "it means no readability pass can run")

        ai_status = None
        gen_units = sorted((self.project / "src" / "recomp" / "gen").glob(
            "recomp_*.c"))[:self.max_units]
        if self.providers in ("all", "ai"):
            if not self.revng_ok:
                self._stage("ai-readability", "blocked",
                            "rev.ng did not produce candidates; refusing to use "
                            "AI as a substitute for the primary provider")
            elif not gen_units:
                self._stage("ai-readability", "blocked",
                            "no generated source units found")
            else:
                ai_status = self._ai(gen_units)

        if self.report["edits"] and all(
                e.get("result") == "accepted" for e in self.report["edits"]):
            self.report["outcome"] = "complete"
            self.report["reason"] = ("readability edits were rebuilt and "
                                     "reproduced the baseline process evidence")
        elif ai_status in ("budget-exhausted",):
            self.report["outcome"] = "budget-exhausted"
            self.report["reason"] = ("wall-clock budget ran out; every edited "
                                     "file was restored")
        elif ai_status in ("rejected-after-repair", "rejected-compile-failed",
                           "rejected-evidence-changed", "failed", "timeout"):
            self.report["outcome"] = ai_status
            self.report["reason"] = ("the repair loop could not produce an "
                                     "edit that passed the gate; changes were "
                                     "reverted")
        elif self.revng_status in ("failed", "timeout"):
            self.report["outcome"] = "revng-failed"
            self.report["reason"] = "rev.ng did not complete; no readability " \
                                    "edits were made"
        elif not self.revng_ok:
            self.report["outcome"] = "blocked"
            self.report["reason"] = ("rev.ng unavailable or produced no "
                                     "candidates; no readability edits were made")
        elif ai_status == "no-changes" or ai_status == "blocked":
            self.report["outcome"] = "revng-candidates-recorded"
            self.report["reason"] = ("rev.ng candidates recorded; AI pass "
                                     "applied no readable-source edits")
        elif self.providers == "revng":
            self.report["outcome"] = "revng-candidates-recorded"
            self.report["reason"] = ("rev.ng candidates recorded; integration "
                                     "into the dispatch ABI is not implemented")
        else:
            self.report["outcome"] = "no-edits"
            self.report["reason"] = "no readability edits were applied"
        self._log(f"outcome: {self.report['outcome']} - {self.report['reason']}")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="autorecomp readability",
        description="Gated readability pass over an already built and tested "
                    "autorecomp project: rev.ng first, optional AI polish with "
                    "a repair loop, rebuild and rerun, revert on any evidence "
                    "change, and a hard wall-clock budget.")
    parser.add_argument("project_dir", help="autorecomp project directory")
    parser.add_argument("--pe", help="original .exe if not recorded in run.json")
    parser.add_argument("--providers", choices=("all", "revng", "ai"),
                        default="all")
    parser.add_argument("--max-units", type=int, default=1,
                        help="AI pass: generated source units to rewrite (default 1)")
    parser.add_argument("--max-attempts", type=int, default=3,
                        help="repair attempts per source unit (default 3)")
    parser.add_argument("--max-seconds", type=float, default=900,
                        help="hard wall-clock budget for the whole pass "
                             "(default 900)")
    parser.add_argument("--provider-timeout", type=float, default=300,
                        help="per provider invocation timeout (default 300)")
    parser.add_argument("--timeout", type=float,
                        help="override the recorded run timeout for rebuild checks")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    harness = ReadabilityHarness(
        args.project_dir, pe_path=args.pe, max_units=args.max_units,
        timeout=args.timeout, providers=args.providers,
        max_attempts=args.max_attempts, max_seconds=args.max_seconds,
        provider_timeout=args.provider_timeout)
    report = harness.run()
    print(f"outcome:  {report['outcome']}")
    print(f"reason:   {report['reason']}")
    print(f"log:      {harness.log_path}")
    print(f"report:   {harness.project / 'reports' / 'readability.html'}")
    return 0 if report["outcome"] in (
        "complete", "no-edits", "revng-candidates-recorded") else 2


if __name__ == "__main__":
    sys.exit(main())
