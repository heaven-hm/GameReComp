#!/usr/bin/env python3
"""Repeatable E2E scenario matrix: CLI ``inspect --json`` + GUI selftests.

Runs the real GameReComp CLI and GUI binaries headlessly over every PE32
corpus file supplied on the command line. Input paths are *only* taken from
CLI arguments (``--exe`` / ``--project``); nothing is hardcoded. For each PE
input the matrix records the exact argv and exit code of both:

  * ``<cli> inspect <exe> --json --repo <repo>``
  * ``<gui> --selftest-pe <exe>``                 (the same PE input)

Optional ``--project`` inputs additionally exercise the GUI's other
deterministic selftest mode: ``<gui> --selftest <project> --repo <repo>``.

Both binaries call the same PE header inspector, so the report cross-checks
that they classify each input identically (SUPPORTED / UNSUPPORTED / INVALID)
and that each binary's own exit code agrees with its printed output. The single
output JSON is deterministic: no wall-clock timestamps, stable input order,
and exact commands preserved as argv lists. Nothing from the corpus is
executed; these are fail-closed header checks, not compatibility proofs.

Usage:
  python3 -m tools.e2e_scenarios --cli <bin> --gui <bin> --repo <dir> \
      --exe <file> [--exe ...] [--project <dir> ...] --out <report.json>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

SCHEMA_VERSION = 1

CLI_INSPECT = 'cli_inspect_json'
GUI_PE = 'gui_selftest_pe'
GUI_PROJECT = 'gui_selftest_project'

SUPPORTED = 'SUPPORTED'
UNSUPPORTED = 'UNSUPPORTED'
INVALID = 'INVALID'
INCONSISTENT = 'INCONSISTENT'

# Exit-code contract shared by run_inspect_cmd() and run_selftest_pe().
_EXIT_CLASS = {0: SUPPORTED, 1: INVALID, 2: UNSUPPORTED}
_RUNNER_ERRORS = ('TIMEOUT', 'SPAWN_ERROR', None)


def run(cmd: list[str], timeout: int = 30, env: dict | None = None) -> dict:
    """Execute one command, recording its exact argv and observed exit code."""
    rec: dict = {'command': list(cmd), 'exit_code': None, 'stdout': '',
                 'error': None}
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True,
                              errors='replace', timeout=timeout, check=False,
                              env=env)
        rec['exit_code'] = proc.returncode
        rec['stdout'] = (proc.stdout or '')[-16384:]
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or b''
        if isinstance(out, bytes):
            out = out.decode('utf-8', 'replace')
        rec['exit_code'] = 'TIMEOUT'
        rec['stdout'] = out[-16384:]
    except OSError as exc:
        rec['exit_code'] = 'SPAWN_ERROR'
        rec['error'] = f'{type(exc).__name__}: {exc}'
    return rec


def sha256(path: Path) -> str | None:
    """Hash a supplied file, or return None if it cannot be read."""
    try:
        digest = hashlib.sha256()
        with open(path, 'rb') as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b''):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def build_plan(cli: Path, gui: Path, repo: Path, exes: list[Path],
               projects: list[Path]) -> list[dict]:
    """Build scenario skeletons with exact commands, without executing them.

    Pure (aside from existence/hash checks) so the command construction and
    the "paths come only from arguments" contract are directly unit-testable.
    """
    scenarios: list[dict] = []
    for index, exe in enumerate(exes, 1):
        exe = Path(exe)
        exists = exe.is_file()
        scenarios.append({
            'id': f'pe-{index:04d}',
            'kind': 'pe',
            'input': str(exe),
            'input_exists': exists,
            'sha256': sha256(exe) if exists else None,
            'steps': [
                {'name': CLI_INSPECT,
                 'command': [str(cli), 'inspect', str(exe), '--json',
                             '--repo', str(repo)]},
                {'name': GUI_PE,
                 'command': [str(gui), '--selftest-pe', str(exe)]},
            ],
        })
    for index, project in enumerate(projects, 1):
        project = Path(project)
        scenarios.append({
            'id': f'project-{index:04d}',
            'kind': 'project',
            'input': str(project),
            'input_exists': project.is_dir(),
            'sha256': None,
            'steps': [{
                'name': GUI_PROJECT,
                'command': [str(gui), '--selftest', str(project),
                            '--repo', str(repo)],
            }],
        })
    return scenarios


def classify_exit_code(exit_code) -> str | None:
    """Map the shared 0/1/2 exit-code contract to a stable class."""
    return _EXIT_CLASS.get(exit_code) if isinstance(exit_code, int) else None


def _parse_json(text: str):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def classify_cli(step: dict) -> str | None:
    """Classify a CLI ``inspect --json`` step; flag payload/exit disagreement."""
    rc_class = classify_exit_code(step.get('exit_code'))
    payload = _parse_json(step.get('stdout', ''))
    payload_class = None
    if isinstance(payload, dict):
        if payload.get('ok') is False:
            payload_class = INVALID
        elif payload.get('pe32_x86') is True:
            payload_class = SUPPORTED
        elif payload.get('ok') is True and payload.get('pe32_x86') is False:
            payload_class = UNSUPPORTED
    if payload_class and rc_class and payload_class != rc_class:
        return INCONSISTENT
    return payload_class or rc_class


def gui_text_class(text: str) -> str | None:
    """Classify the human-readable GUI selftest line (checked before verdict)."""
    if 'not usable' in text:
        return INVALID
    if 'NOT a supported target' in text:
        return UNSUPPORTED
    if 'supported target' in text:
        return SUPPORTED
    return None


def classify_gui(step: dict) -> str | None:
    """Classify a GUI ``--selftest-pe`` step; flag text/exit disagreement."""
    rc_class = classify_exit_code(step.get('exit_code'))
    text_class = gui_text_class(step.get('stdout', ''))
    if text_class and rc_class and text_class != rc_class:
        return INCONSISTENT
    return text_class or rc_class


def evaluate(scenario: dict) -> dict:
    """Fill classifications, agreement and verdict into one scenario.

    Mutates and returns the same dict. A scenario PASSES when both binaries ran
    and independently agree on the classification; supported, unsupported and
    invalid inputs are all valid outcomes. A runner error, an internal
    inconsistency, or a CLI/GUI disagreement is a FAIL. A missing supplied
    input is a SKIP and is never counted as a pass.
    """
    if not scenario['input_exists']:
        scenario.update({'cli_class': None, 'gui_class': None,
                         'agreement': None, 'verdict': 'SKIP'})
        if scenario['kind'] == 'pe':
            scenario.update({'matrix': {'input': scenario['input'],
                                        'sha256': scenario['sha256'],
                                        'cli': None, 'gui': None,
                                        'classification': None,
                                        'verdict': 'SKIP'}})
        return scenario

    steps = {step['name']: step for step in scenario['steps']}
    runner_error = any(step.get('exit_code') in _RUNNER_ERRORS
                       for step in scenario['steps'])

    cli_class = classify_cli(steps[CLI_INSPECT]) if CLI_INSPECT in steps else None
    gui_class = classify_gui(steps[GUI_PE]) if GUI_PE in steps else None

    if scenario['kind'] == 'pe':
        agreement = bool(cli_class and gui_class and
                         cli_class == gui_class and
                         cli_class != INCONSISTENT and
                         gui_class != INCONSISTENT)
        verdict = 'PASS' if (not runner_error and agreement) else 'FAIL'
        classification = cli_class if agreement else None
        scenario['matrix'] = {
            'input': scenario['input'], 'sha256': scenario['sha256'],
            'cli': steps[CLI_INSPECT]['exit_code'],
            'gui': steps[GUI_PE]['exit_code'],
            'classification': classification, 'verdict': verdict,
        }
    else:  # project selftest: deterministic success is exit code 0
        project_step = steps[GUI_PROJECT]
        cli_class, gui_class, agreement = None, None, None
        verdict = ('PASS' if (not runner_error and
                             project_step.get('exit_code') == 0) else 'FAIL')

    scenario.update({'cli_class': cli_class, 'gui_class': gui_class,
                     'agreement': agreement, 'verdict': verdict})
    return scenario


def build_report(cli: Path, gui: Path, repo: Path, exes: list[Path],
                 projects: list[Path], timeout: int = 30,
                 env: dict | None = None) -> dict:
    """Execute the full matrix and return the single deterministic report."""
    scenarios = build_plan(cli, gui, repo, exes, projects)
    for scenario in scenarios:
        if not scenario['input_exists']:
            evaluate(scenario)
            continue
        for step in scenario['steps']:
            step.update(run(step['command'], timeout=timeout, env=env))
        evaluate(scenario)

    verdicts = [s['verdict'] for s in scenarios]
    summary = {
        'scenarios': len(scenarios),
        'pass': verdicts.count('PASS'),
        'fail': verdicts.count('FAIL'),
        'skip': verdicts.count('SKIP'),
        'cli_gui_disagreements': sum(1 for s in scenarios
                                     if s['agreement'] is False),
    }
    return {
        'schema': SCHEMA_VERSION,
        'tool': 'tools/e2e_scenarios.py',
        'what_it_proves': ('CLI and GUI fail-closed PE header checks agree on '
                           'each supplied input; no corpus file is executed.'),
        'inputs': {
            'cli': str(cli), 'gui': str(gui), 'repo': str(repo),
            'timeout_seconds': timeout,
            'pe_files': [str(Path(e)) for e in exes],
            'projects': [str(Path(p)) for p in projects],
        },
        'scenarios': scenarios,
        'matrix': [s['matrix'] for s in scenarios if 'matrix' in s],
        'summary': summary,
        'verdict': 'PASS' if (scenarios and summary['fail'] == 0
                              and summary['pass'] > 0) else 'FAIL',
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--cli', required=True, type=Path,
                    help='game-recomp-cli binary')
    ap.add_argument('--gui', required=True, type=Path,
                    help='game-recomp-gui binary')
    ap.add_argument('--repo', required=True, type=Path,
                    help='repository root passed to both binaries')
    ap.add_argument('--exe', action='append', type=Path, default=[],
                    help='PE32 corpus file to exercise (repeat; from args only)')
    ap.add_argument('--project', action='append', type=Path, default=[],
                    help='project dir for the GUI project selftest (repeat)')
    ap.add_argument('--out', required=True, type=Path,
                    help='single JSON report to write')
    ap.add_argument('--timeout', type=int, default=30,
                    help='per-command timeout in seconds (default: 30)')
    args = ap.parse_args(argv)

    report = build_report(args.cli, args.gui, args.repo, args.exe,
                          args.project, timeout=args.timeout)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')

    for row in report['matrix']:
        print(f"{row['input']:<48} {str(row['cli']):>5} "
              f"{str(row['gui']):>5} {row['verdict']}", flush=True)
    summary = report['summary']
    print(f"scenarios={summary['scenarios']} pass={summary['pass']} "
          f"fail={summary['fail']} skip={summary['skip']} "
          f"verdict={report['verdict']}", flush=True)
    print(f'report: {args.out}', flush=True)
    return 0 if report['verdict'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
