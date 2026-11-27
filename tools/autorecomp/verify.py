#!/usr/bin/env python3
"""General verification engine: source parity (Unicorn) + run parity (Frida).

No game-specific addresses or paths: every input comes from the project report
or the command line. Source parity uses tools.lift.difftest.oracle, which runs
the ORIGINAL x86 bytes and the project's generated C side by side in Unicorn
and compares the guest state. Run parity uses Frida to record observable calls
and output from the original executable (through a Windows runtime when the
host is not Windows) and the rebuilt binary with the SAME arguments, then
compares the normalized event streams. Both percentages are counts of named
checks; a missing runtime or zero events is reported as BLOCKED, never a pass.
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


def function_addresses(project, explicit, limit, allow_all=False):
    out = [int(a, 0) for a in explicit]
    if out:
        return out[:limit] if not allow_all else out
    data = load_json(project / 'reports' / 'functions.json')
    funcs = data.get('functions') or []
    # Prefer small, non-thunk functions: a synthetic entry state can legitimately
    # fault a complex original, and a blocked check is not evidence of parity.
    funcs = [f for f in funcs if not f.get('is_thunk')]
    funcs.sort(key=lambda f: (int(f.get('calls') or 0), int(f.get('instructions') or 0)))
    for f in funcs:
        va = f.get('address')
        if va:
            out.append(int(va, 0) if isinstance(va, str) else int(va))
    if not out:
        for mod in sorted((project / 'src/highlevel').glob('*.json')):
            for fn in load_json(mod).get('functions', []):
                va = fn.get('address') or fn.get('va')
                if va:
                    out.append(int(va, 0) if isinstance(va, str) else int(va))
    if not out:
        manifest = load_json(project / 'manifest.json')
        va = manifest.get('entry_point') or manifest.get('entry')
        if va:
            out.append(int(va, 0) if isinstance(va, str) else int(va))
    out = list(dict.fromkeys(out))
    return out if allow_all else out[:limit]


def source_verification(project, pe, addrs, cases):
    from tools.lift.difftest import oracle
    rows = []
    for va in addrs:
        try:
            row = oracle(str(project), str(pe), [va], cases=cases,
                         progress=lambda *_: None)[0]
        except (Exception, SystemExit) as exc:
            # difftest uses SystemExit for "original VA is not in a code section".
            # That is a blocked check, not a reason to abandon the whole sweep.
            row = {'address': f'0x{va:08X}', 'verdict': 'engine-error', 'detail': str(exc)}
        rows.append(row)
    passed = sum(1 for r in rows if str(r.get('verdict', '')).startswith('agree'))
    failed = sum(1 for r in rows if r.get('verdict') == 'diverge')
    blocked = len(rows) - passed - failed
    return {
        'engine': 'Unicorn / tools.lift.difftest.oracle',
        'checked': len(rows), 'passed': passed, 'failed': failed, 'blocked': blocked,
        'percent': round(100.0 * passed / len(rows), 1) if rows else 0.0,
        'verified': bool(rows) and passed == len(rows),
        'detail': (f'{passed}/{len(rows)} functions agree; {failed} diverge; '
                   f'{blocked} blocked (no generated body, harness failure, '
                   f'original fault, or synthetic-state limit)'),
        'addresses': rows,
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


def frida_trace(argv, timeout, outdir, env_extra=None):
    outdir.mkdir(parents=True, exist_ok=True)
    script = outdir / 'trace.js'
    log = outdir / 'run.log'
    script.write_text(FRIDA_SCRIPT)
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    # Frida 17 resumes spawned processes by default; --no-pause no longer exists.
    cmd = ['frida', '-f', str(argv[0]), *map(str, argv[1:]),
           '-l', str(script), '-q', '-t', str(timeout), '--kill-on-exit']
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        text = (p.stdout or '') + '\n--- stderr ---\n' + (p.stderr or '')
        code = p.returncode
    except subprocess.TimeoutExpired as e:
        text = (e.stdout or b'').decode(errors='replace') + '\n[timeout]\n' + \
               (e.stderr or b'').decode(errors='replace')
        code = 124
    log.write_text(f'exit={code}\n$ ' + ' '.join(map(str, cmd)) + '\n' + text)
    return log, code


def normalize(line):
    s = line.replace(str(Path.home()), '~')
    # ASLR addresses are not stable output; compare event names and values.
    s = re.sub(r'0x[0-9a-fA-F]{6,}', 'PTR', s)
    parts = s.split()
    return ' '.join(p.rsplit('/', 1)[-1] if '/' in p else p for p in parts)


def run_verification(pe, rebuilt, args, timeout, outdir):
    if shutil.which('frida') is None:
        return {'engine': 'Frida', 'blocked': True, 'reason': 'frida not found',
                'percent': 0.0, 'checked': 0, 'passed': 0, 'verified': False}
    runner = windows_runner()
    if not runner:
        return {'engine': 'Frida', 'blocked': True,
                'reason': 'no Windows runtime for the original executable (set RECOMP_WINDOWS_RUNNER)',
                'percent': 0.0, 'checked': 0, 'passed': 0, 'verified': False}
    env = {}
    prefix = wine_prefix()
    if prefix:
        env['WINEPREFIX'] = prefix
    olog, ocode = frida_trace([*runner, str(pe), *args], timeout, outdir / 'original', env)
    rlog, rcode = frida_trace([str(rebuilt), *args], timeout, outdir / 'rebuilt', env)
    oe = [normalize(x) for x in olog.read_text(errors='replace').splitlines()
          if x.startswith('RECOMP_EVT ')]
    re = [normalize(x) for x in rlog.read_text(errors='replace').splitlines()
          if x.startswith('RECOMP_EVT ')]
    logs = (olog.read_text(errors='replace') + '\n' + rlog.read_text(errors='replace'))
    attach_error = next((l for l in logs.splitlines() if 'Failed to attach' in l), '')
    if attach_error or not oe:
        # Events from one side only are evidence of a broken oracle, not a
        # divergence: the original process never produced a comparable stream.
        reason = (f'Frida attach denied by the OS ({attach_error.strip()}); run parity is BLOCKED'
                  if attach_error else
                  'the original emitted no Frida events; run parity is BLOCKED')
        return {
            'engine': 'Frida', 'percent': 0.0, 'checked': 0, 'passed': 0,
            'blocked': True, 'original_exit': ocode, 'rebuilt_exit': rcode,
            'reason': reason,
            'detail': f'0 comparable events; original exit {ocode}, rebuilt exit {rcode}',
            'logs': [str(olog), str(rlog)], 'verified': False,
        }
    checked = max(len(oe), len(re))
    passed = sum(1 for a, b in zip(oe, re) if a == b)
    reason = '' if passed == checked else f'{checked - passed} normalized event(s) diverged'
    return {
        'engine': 'Frida', 'percent': round(100.0 * passed / checked, 1) if checked else 0.0,
        'checked': checked, 'passed': passed, 'blocked': False,
        'original_exit': ocode, 'rebuilt_exit': rcode,
        'reason': reason,
        'detail': f'{passed}/{checked} normalized events agree; original exit {ocode}, rebuilt exit {rcode}',
        'logs': [str(olog), str(rlog)], 'verified': checked > 0 and passed == checked,
    }


def main():
    ap = argparse.ArgumentParser(description='general Unicorn + Frida verification')
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
    addrs = function_addresses(project, a.address, a.max_functions, a.all_functions)
    src = source_verification(project, pe, addrs, a.cases)
    rebuilt = Path(a.binary).resolve() if a.binary else rebuilt_binary(project, report)
    run = {'engine': 'Frida', 'blocked': True, 'percent': 0.0,
           'reason': 'rebuilt binary not found (manifest.json binary path or build/<name>)',
           'checked': 0, 'passed': 0, 'verified': False}
    if rebuilt is not None:
        run = run_verification(pe, rebuilt, a.args, a.timeout, project / 'reports' / 'frida')
    out = {
        'schema': 1, 'project': str(project), 'input': str(pe),
        'source_percent': src['percent'], 'source': src,
        'run_percent': run['percent'], 'run': run,
        'verified': src['verified'] and run['verified'],
        'honesty': 'Measured over named checks; no scenario oracle means no game PASS.',
    }
    dest = Path(a.json) if a.json else project / 'reports' / 'verify.json'
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2) + '\n')
    print(f'source {src["percent"]}%  run {run["percent"]}%  -> {dest}')
    return 0 if out['verified'] else 2


if __name__ == '__main__':
    sys.exit(main())
