#!/usr/bin/env python3
"""Runtime verification: run both built programs with the SAME inputs.

Reconstruction/lifting already happened in the pipeline; this engine does not
repeat it. It reads the recorded source-parity value from the project report and
then executes the original Windows executable (through Wine on non-Windows
hosts) and the rebuilt program with identical arguments, capturing stdout,
stderr and exit codes. Named checks compare those observable outputs; a missing
runtime or no observable output is reported as BLOCKED, never as a pass.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}


def recorded_source_verification(project):
    """Read the source-parity value recorded by the original pipeline run.

    This is deliberately NOT recomputed: the reconstruction/lifting step already
    happened once, and re-running Unicorn here would be duplicated work.
    """
    for name in ('reports/verify.json', 'reports/verification.json', 'reports/report.json'):
        data = load_json(project / name)
        if 'source_percent' in data:
            value = float(data['source_percent'] or 0)
            return {
                'engine': f'recorded in {name} (no re-lifting)',
                'percent': round(value, 1), 'checked': 1, 'passed': 1 if value >= 99 else 0,
                'blocked': False, 'verified': value >= 99.0,
                'detail': f'Source parity recorded at pipeline time: {value:.1f}%',
            }
    return {
        'engine': 'recorded source parity', 'percent': 0.0, 'checked': 0,
        'passed': 0, 'blocked': True, 'verified': False,
        'detail': 'No recorded source-parity value; run the pipeline once, do not re-lift here.',
    }


def host_binary_magic():
    if sys.platform.startswith('linux'):
        return (b'\x7fELF',)
    if sys.platform == 'darwin':
        # 32/64-bit Mach-O, little or big endian, and universal binaries.
        return (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf',
                b'\xfe\xed\xfa\xce', b'\xca\xfe\xba\xbe')
    return ()


def is_host_binary(path):
    magic = host_binary_magic()
    if not magic:
        return True
    try:
        with open(path, 'rb') as f:
            header = f.read(4)
    except OSError:
        return False
    return header in magic


def rebuilt_binary(project, report):
    """Resolve the rebuilt binary without assuming a game name or directory case.

    Prefer a binary the current host can actually execute, so a macOS Mach-O in
    `build/` cannot shadow the Linux ELF in `build-linux/` when verifying Linux.
    """
    candidates = []
    raw = report.get('binary')
    if isinstance(raw, dict):
        raw = raw.get('path')
    if raw:
        candidates.append(Path(raw))
    manifest = load_json(project / 'manifest.json')
    mb = manifest.get('binary')
    if isinstance(mb, dict):
        mb = mb.get('path')
    if mb:
        candidates.append(Path(mb))
    for pat in ('build*/*', 'build*/*/*', '*'):
        candidates.extend(Path(project).glob(pat))
    seen = set()
    for c in candidates:
        try:
            key = str(c.resolve())
            if key in seen:
                continue
            seen.add(key)
            if c.is_file() and os.access(c, os.X_OK) and is_host_binary(c):
                return c.resolve()
        except OSError:
            continue
    return None


def windows_runner():
    env = os.environ.get('RECOMP_WINDOWS_RUNNER')
    if env:
        return [env]
    found = shutil.which('wine')
    if found:
        return [found]
    for candidate in glob.glob('/Applications/Highball.app/Contents/Helpers/WineLoader.app/Contents/MacOS/wine'):
        if Path(candidate).is_file():
            return [candidate]
    return None


def wine_prefix():
    env = os.environ.get('WINEPREFIX')
    if env:
        return env
    candidates = glob.glob(str(Path.home() / 'Library/Application Support/Highball/bottles/*'))
    return candidates[0] if candidates else None


FRIDA_SCRIPT = r'''
function log(ev) { console.log("RECOMP_EVT " + JSON.stringify(ev)); }
function addr(module, name) {
  try {
    if (!module) return Module.getGlobalExportByName(name);
    return Module.getExportByName(module, name);
  } catch (e) { return null; }
}
var names = ["open","read","write","close","exit","_exit",
             "GetCommandLineA","MessageBoxW","CreateFileW","RegOpenKeyExW",
             "LoadLibraryW","GetProcAddress","DrawTextW","ExitProcess"];
function hook(module) {
  names.forEach(function (n) {
    var p = addr(module, n);
    if (p) Interceptor.attach(p, { onEnter: function (a) { log({name:(module?module+"!":"")+n, a0:String(a[0])}); } });
  });
}
hook(null);
// Wine presents Windows DLLs to a Linux Frida host, so do not gate this on
// Process.platform: hook them whenever they are loaded.
['kernel32.dll','user32.dll','advapi32.dll'].forEach(function (m) {
  if (Process.findModuleByName(m)) hook(m);
});
// Frida 17 does not expose Process.argv for spawned targets.
log({name:"start", argv:(typeof Process.argv !== 'undefined' ? Process.argv.join(" ") : "")});
'''


def normalize(line):
    s = line.replace(str(Path.home()), '~')
    # ASLR addresses are not stable output; compare event names and values.
    s = re.sub(r'0x[0-9a-fA-F]{6,}', 'PTR', s)
    parts = s.split()
    return ' '.join(p.rsplit('/', 1)[-1] if '/' in p else p for p in parts)


def run_process_capture(argv, timeout, outdir, env_extra=None):
    """Run one binary and persist its observable output."""
    outdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              env=env, errors='replace')
        code, stdout, stderr = proc.returncode, proc.stdout or '', proc.stderr or ''
    except subprocess.TimeoutExpired as exc:
        code = 124
        stdout = exc.stdout.decode(errors='replace') if isinstance(exc.stdout, bytes) else (exc.stdout or '')
        stderr = exc.stderr.decode(errors='replace') if isinstance(exc.stderr, bytes) else (exc.stderr or '')
        stderr += '\n[timeout]'
    (outdir / 'stdout.txt').write_text(stdout)
    (outdir / 'stderr.txt').write_text(stderr)
    (outdir / 'run.log').write_text(f'exit={code}\n$ ' + ' '.join(map(str, argv)) +
                                    '\n--- stdout ---\n' + stdout + '\n--- stderr ---\n' + stderr)
    return code, stdout, stderr


def _normalized_lines(text):
    return [normalize(line) for line in text.splitlines() if line.strip()]


def runtime_verification(pe, rebuilt, args, timeout, outdir):
    """Run original and rebuilt with identical args; compare observable output."""
    runner = windows_runner()
    if not runner and sys.platform != 'win32':
        return {
            'engine': 'same-input runtime comparison', 'blocked': True, 'percent': 0.0,
            'checked': 0, 'passed': 0, 'verified': False,
            'reason': 'no Windows runtime for the original (set RECOMP_WINDOWS_RUNNER)',
        }
    env = {}
    prefix = wine_prefix()
    if prefix:
        env['WINEPREFIX'] = prefix
    original_argv = ([*runner, str(pe)] if runner else [str(pe)]) + [str(a) for a in args]
    rebuilt_argv = [str(rebuilt)] + [str(a) for a in args]
    ocode, oout, oerr = run_process_capture(original_argv, timeout, outdir / 'original', env)
    rcode, rout, rerr = run_process_capture(rebuilt_argv, timeout, outdir / 'rebuilt', env)

    checks = []
    checks.append(('exit_code', ocode == rcode, f'original={ocode} rebuilt={rcode}'))
    oout_lines, rout_lines = _normalized_lines(oout), _normalized_lines(rout)
    oerr_lines, rerr_lines = _normalized_lines(oerr), _normalized_lines(rerr)
    observable = bool(oout_lines or oerr_lines or rout_lines or rerr_lines)
    if observable:
        checks.append(('stdout', oout_lines == rout_lines,
                       f'{len(oout_lines)} original / {len(rout_lines)} rebuilt lines'))
        checks.append(('stderr', oerr_lines == rerr_lines,
                       f'{len(oerr_lines)} original / {len(rerr_lines)} rebuilt lines'))
    if not observable:
        return {
            'engine': 'same-input runtime comparison', 'blocked': True, 'percent': 0.0,
            'checked': 0, 'passed': 0, 'verified': False, 'original_exit': ocode,
            'rebuilt_exit': rcode,
            'reason': 'neither process produced observable stdout/stderr; no output to compare',
            'detail': f'original exit {ocode}, rebuilt exit {ocode}',
            'logs': [str(outdir / 'original/run.log'), str(outdir / 'rebuilt/run.log')],
        }
    passed = sum(1 for _, ok, _ in checks if ok)
    checked = len(checks)
    failed = [name for name, ok, _ in checks if not ok]
    return {
        'engine': 'same-input runtime comparison (stdout/stderr/exit code)',
        'percent': round(100.0 * passed / checked, 1),
        'checked': checked, 'passed': passed, 'blocked': False,
        'verified': passed == checked,
        'original_exit': ocode, 'rebuilt_exit': rcode,
        'reason': '' if not failed else 'diverged checks: ' + ', '.join(failed),
        'detail': f'{passed}/{checked} checks agree; original exit {ocode}, rebuilt exit {rcode}',
        'checks': [{'name': n, 'ok': ok, 'detail': d} for n, ok, d in checks],
        'logs': [str(outdir / 'original/run.log'), str(outdir / 'rebuilt/run.log')],
    }


def main():
    ap = argparse.ArgumentParser(description='same-input runtime verification of two built binaries')
    ap.add_argument('project', help='autorecomp project dir')
    ap.add_argument('--pe', help='original PE32 i386 executable (default: report input)')
    ap.add_argument('--binary', help='rebuilt executable override (default: discover from metadata)')
    ap.add_argument('--address', action='append', default=[])
    ap.add_argument('--max-functions', type=int, default=12)
    ap.add_argument('--all-functions', action='store_true')
    ap.add_argument('--cases', type=int, default=4)
    ap.add_argument('--timeout', type=int, default=20)
    ap.add_argument('--args', nargs=argparse.REMAINDER, default=[])
    ap.add_argument('--json', help='write report here (default: project/reports/verify.json)')
    a = ap.parse_args()
    project = Path(a.project).resolve()
    report = load_json(project / 'reports' / 'report.json')
    pe = Path(a.pe).resolve() if a.pe else Path(report.get('input') or '')
    if not pe.is_file():
        raise SystemExit(f'original PE not found: {pe}')
    # Reconstruction/lifting already ran in the pipeline. These flags are kept
    # for compatibility with older callers but deliberately do not re-lift.
    if a.address or a.all_functions:
        print('warning: --address/--all-functions are ignored; verification is runtime-only',
              file=sys.stderr)
    src = recorded_source_verification(project)
    rebuilt = Path(a.binary).resolve() if a.binary else rebuilt_binary(project, report)
    run = {'engine': 'Frida', 'blocked': True, 'percent': 0.0,
           'reason': 'rebuilt binary not found (manifest.json binary path or build/<name>)',
           'checked': 0, 'passed': 0, 'verified': False}
    if rebuilt is not None:
        run = runtime_verification(pe, rebuilt, a.args, a.timeout, project / 'reports' / 'runtime')
    out = {
        'schema': 1, 'project': str(project), 'input': str(pe),
        'source_percent': src['percent'], 'source': src,
        'run_percent': run['percent'], 'run': run,
        'verified': run['verified'],
        'honesty': 'Runtime parity only: both built binaries run with the same inputs; '
                   'no lifting is repeated here.',
    }
    dest = Path(a.json) if a.json else project / 'reports' / 'verify.json'
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2) + '\n')
    print(f'source {src["percent"]}%  run {run["percent"]}%  -> {dest}')
    return 0 if out['verified'] else 2


if __name__ == '__main__':
    sys.exit(main())
