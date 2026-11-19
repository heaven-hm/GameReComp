"""autorecomp command line: PE -> source -> native binary -> run -> verify.

The CLI is fail-closed about what it can do: a target platform that is not
the build host is refused instead of silently producing something else, an
unknown stage name is refused instead of guessed, and the output mode maps
onto an explicit stage plan printed by --dry-run.
"""

import argparse
import json
import os
import platform
import re
import sys
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import gamesdb  # noqa: E402
from tools.autorecomp import __version__  # noqa: E402
from tools.autorecomp.pipeline import (STAGES, STAGE_WEIGHTS,  # noqa: E402
                                      Pipeline, run_pipeline)

EXIT_VERIFIED = 0
EXIT_STAGE_FAILED = 2
EXIT_UNVERIFIED = 3
EXIT_UNSUPPORTED = 4

# Output mode -> the stages that produce that output. "source" stops after
# the generated C project exists; "binary" adds compile; "both"/"full" is
# the complete pipeline including run + verify. Aliases exist because
# people ask for "source only" and "full recomp" and mean exactly these.
# The report stage is always appended by run_pipeline, so the plan below
# omits it.
_SOURCE_STAGES = ["inspect", "recover", "translate", "project"]
_BINARY_STAGES = ["inspect", "recover", "translate", "project", "compile"]
OUT_MODE_STAGES = {
    "source": _SOURCE_STAGES,
    "source-only": _SOURCE_STAGES,
    "binary": _BINARY_STAGES,
    "binary-only": _BINARY_STAGES,
    "both": list(STAGES),
    "full": list(STAGES),
}

# What the pipeline reconstructs: a Windows PE32 game for the 32-bit x86
# guest ISA, rebuilt as a native host executable. Anything else is refused
# with the reason, not silently misread.
SUPPORTED_INPUT_OS = ("windows",)
SUPPORTED_INPUT_ARCH = ("x86",)

_MACHINE_ARCH_NAMES = {
    0x014C: "x86", 0x8664: "x86_64",
    0x01C0: "arm", 0x01C4: "arm",
    0xAA64: "arm64", 0x0200: "ia64",
}

# Hosts this pipeline is implemented against. Cross-compiling is NOT
# implemented: the pipeline compiles with the host toolchain through CMake
# and SDL detection, and a foreign --target would silently build for the
# host anyway, so it is refused rather than mislabeled in the report.
SUPPORTED_TARGETS = {
    ("linux", "x86_64"), ("linux", "arm64"),
    ("macos", "x86_64"), ("macos", "arm64"),
    ("windows", "x86_64"), ("windows", "arm64"),
    ("web", "wasm32"),
}

_HOST_OS_NAMES = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}
_HOST_ARCH_NAMES = {
    "x86_64": "x86_64", "amd64": "x86_64",
    "arm64": "arm64", "aarch64": "arm64",
}

_HOST_ARCHS = ("x86_64", "arm64")

_BAR_WIDTH = 24
_TICK_SECONDS = 0.2


def _host_pair():
    """What this machine is, in the vocabulary of --target-os/--target-arch.

    An unrecognized OS or CPU lands as its raw name; _resolve_target then
    refuses it, which is honest: the pipeline was never run on it.
    """
    os_name = _HOST_OS_NAMES.get(platform.system())
    machine = platform.machine().lower()
    return os_name, _HOST_ARCH_NAMES.get(machine, machine)


def _pe_identity(pe_path):
    """(os, arch) from the binary's own headers, or (None, reason).

    Reads only the header bytes: MZ magic, e_lfanew, the PE signature and
    the COFF machine type. The guest OS comes from the format itself --
    PE means Windows -- so there is nothing to guess and nothing to trust
    from the file name.
    """
    try:
        with open(pe_path, "rb") as fh:
            head = fh.read(4096)
    except OSError as exc:
        return None, f"cannot read the input: {exc}"
    if len(head) < 0x40 or head[:2] != b"MZ":
        return None, "not an MZ/PE executable"
    e_lfanew = int.from_bytes(head[0x3C:0x40], "little")
    if e_lfanew + 6 > len(head) or head[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
        return None, ("MZ header but no PE signature: DOS MZ, NE or LE "
                      "executables are not supported; this pipeline "
                      "reconstructs Windows PE32 games")
    machine = int.from_bytes(head[e_lfanew + 4:e_lfanew + 6], "little")
    return ("windows", _MACHINE_ARCH_NAMES.get(machine, hex(machine))), None


def _resolve_input(pe_path, os_choice, arch_choice):
    """Validate the declared input identity against the binary itself.

    Returns ((os, arch), None) on success and (None, reason) on refusal.
    'auto' means: read it from the header. An explicit declaration that
    disagrees with the bytes is refused -- a tool that guesses instead of
    checking is how the wrong binary gets reconstructed with confidence.
    """
    identity, reason = _pe_identity(pe_path)
    if reason:
        return None, reason
    in_os, in_arch = identity
    if os_choice != "auto" and os_choice != in_os:
        return None, (f"--input-os {os_choice} but the binary's header says "
                      f"{in_os}; refusing to reconstruct on a guess")
    if arch_choice != "auto" and arch_choice != in_arch:
        return None, (f"--input-arch {arch_choice} but the binary's header "
                      f"says {in_arch}; refusing to reconstruct on a guess")
    if in_os not in SUPPORTED_INPUT_OS:
        return None, f"unsupported input OS: {in_os}"
    if in_arch not in SUPPORTED_INPUT_ARCH:
        return None, (f"unsupported input architecture: {in_arch} "
                      f"(this pipeline reconstructs PE32 "
                      f"{'/'.join(SUPPORTED_INPUT_ARCH)} guests; a 64-bit "
                      f"build of the same game, if one exists, would be a "
                      f"different binary to reconstruct -- 64-bit guest "
                      f"support is planned, not implemented)")
    return (in_os, in_arch), None


def _resolve_target(os_choice, arch_choice):
    """Map the host/alias choices onto a concrete (os, arch) pair.

    Returns ((os, arch), None) when the target is buildable here, and
    (None, reason) when it is not, or the host itself is unknown.
    """
    norm_os = "web" if os_choice == "wasm" else os_choice
    if (norm_os == "web" and arch_choice in ("host", "wasm32", "wasm")) or (
            arch_choice in ("wasm32", "wasm") and norm_os in ("host", "web")):
        return ("web", "wasm32"), None
    host_os, host_arch = _host_pair()
    if host_os is None or host_arch not in _HOST_ARCHS:
        # The machine name is either unknown or not one we ever built on;
        # refuse rather than guess at its ABI.
        return None, (
            f"unsupported build host: {platform.system()}/{platform.machine()}")
    if norm_os != "host" or arch_choice != "host":
        requested = (
            host_os if norm_os == "host" else norm_os,
            host_arch if arch_choice == "host" else arch_choice)
        if requested != (host_os, host_arch):
            return None, (
                f"cross-target build requested ({requested[0]}/{requested[1]}) "
                f"on host ({host_os}/{host_arch}); the pipeline compiles with "
                f"the host toolchain only -- run it on the target OS or omit "
                f"--target-os/--target-arch")
        return requested, None
    return (host_os, host_arch), None


def _stage_list(text):
    """Split 'a,b,c' into clean names; argparse calls this per flag."""
    return [part.strip() for part in text.split(",") if part.strip()]


def _resolve_stages(args, error):
    """Turn --out-mode/--stages/--skip-stages into the canonical stage plan."""
    if args.stages and args.out_mode is not None:
        error("--stages and --out-mode are mutually exclusive "
              "(--out-mode is shorthand for a stage plan)")
        return None
    if args.stages and args.skip_stages:
        error("--stages and --skip-stages are mutually exclusive")
        return None
    if args.stages:
        unknown = [s for s in args.stages if s not in STAGES]
        if unknown:
            error(f"unknown stage(s): {', '.join(unknown)}; "
                  f"known stages: {', '.join(STAGES)}")
            return None
        selected = [s for s in STAGES if s in args.stages]
        return selected
    mode = args.out_mode or "both"
    selected = list(OUT_MODE_STAGES[mode])
    if args.skip_stages:
        unknown = [s for s in args.skip_stages if s not in STAGES]
        if unknown:
            error(f"unknown stage(s): {', '.join(unknown)}; "
                  f"known stages: {', '.join(STAGES)}")
            return None
        skipped = set(args.skip_stages)
        missing = skipped - set(selected)
        if "report" in skipped:
            error("--skip-stages cannot remove report: the run must stay "
                  "inspectable")
            return None
        selected = [s for s in selected if s not in skipped]
        if not selected:
            error(f"--skip-stages left no stages to run (mode {mode})")
            return None
        if missing:
            print(f"note: --skip-stages ignored stage(s) not in mode {mode}: "
                  f"{', '.join(sorted(missing))}", file=sys.stderr)
    return selected


def _database_path(pe_path):
    for env in ("SMALL_GAMES_PATH", "LARGE_GAMES_PATH"):
        root = os.environ.get(env)
        if not root:
            continue
        try:
            return str(Path(pe_path).resolve().relative_to(
                Path(root).expanduser().resolve()))
        except (ValueError, OSError):
            continue
    return Path(pe_path).name


def _brief(text, limit=160):
    first = str(text).strip().splitlines()[0] if str(text).strip() else ""
    return first if len(first) <= limit else first[:limit - 3] + "..."


class _StageProgress:
    """Stage-level progress driven by the pipeline's event callbacks.

    Two renderings, one code path:

    - plain (a pipe, a CI log, --no-progress): the traditional
      `==> stage` / `    ok   1.2s stage: message` lines.
    - bar (a TTY): one weighted completion bar over the whole plan, redrawn
      in place, with the active stage's elapsed time updated by a small
      ticker thread. Percentages count finished stages only -- there is no
      invented within-stage progress to fake.

    All output goes to stderr so stdout stays free for --json and for
    piping the run's own captured output.
    """

    def __init__(self, stages, stream=None, bar=None, verbose=False,
                 tick=True):
        self.stream = stream if stream is not None else sys.stderr
        if bar is None:
            try:
                bar = bool(self.stream.isatty())
            except Exception:
                bar = False
        self.bar = bool(bar)
        self.verbose = bool(verbose)
        self.weights = {s: STAGE_WEIGHTS.get(s, 0) for s in stages}
        self.total = sum(self.weights.values())
        self.done = 0
        self.active = None
        self._t0 = None
        self._stop = threading.Event()
        self._ticker = None
        self._tick_enabled = bool(tick and self.bar)

    # -- event entry point ------------------------------------------------
    def __call__(self, evt):
        event = evt.get("event")
        # stage-start carries "stage"; stage-end re-emits the record under
        # "name". The old CLI read only "stage" here, KeyError'd on every
        # stage-end, and _emit swallowed it -- so those lines never printed.
        name = evt.get("stage") or evt.get("name")
        if event == "stage-start":
            self._start(evt, name)
        elif event == "stage-end":
            self._end(evt, name)

    # -- rendering --------------------------------------------------------
    def _pct(self):
        if not self.total:
            return 100
        return min(100, self.done * 100 // self.total)

    def _bar_text(self):
        filled = self.total and (_BAR_WIDTH * self.done // self.total)
        return "#" * filled + "-" * (_BAR_WIDTH - filled)

    def _write_active(self, final=False):
        elapsed = time.time() - self._t0 if self._t0 else 0.0
        line = (f"{self.active:<9s} [{self._bar_text()}] "
                f"{self._pct():3d}% {elapsed:6.1f}s")
        suffix = "\n" if final else ""
        self.stream.write("\r" + line + "\x1b[K" + suffix)
        self.stream.flush()

    def _write_end(self, evt, name):
        mark = {"ok": "ok", "failed": "FAIL", "skipped": "skipped"}.get(
            evt.get("status", "?"), str(evt.get("status", "?")))
        seconds = evt.get("seconds", 0.0)
        line = (f"{mark:<9s} [{self._bar_text()}] {self._pct():3d}% "
                f"{seconds:6.1f}s {name}")
        self.stream.write("\r" + line + "\x1b[K\n")
        if self.verbose and evt.get("message"):
            for chunk in str(evt["message"]).strip().splitlines()[:4]:
                self.stream.write(f"          {_brief(chunk)}\n")
        self.stream.flush()

    # -- event handlers ---------------------------------------------------
    def _start(self, evt, name):
        self._stop_ticker()
        self.active = name
        self._t0 = time.time()
        if self.bar:
            self._write_active()
            if self._tick_enabled:
                self._start_ticker()
        else:
            self.stream.write(f"==> {name}\n")
            self.stream.flush()

    def _end(self, evt, name):
        self._stop_ticker()
        status = evt.get("status", "?")
        if status != "skipped":
            self.done += self.weights.get(name, 0)
        if self.bar:
            self._write_end(evt, name)
        else:
            mark = "ok" if status == "ok" else status
            self.stream.write(
                f"    {mark:<7s} {evt.get('seconds', 0):6.1f}s  "
                f"{name}: {_brief(evt.get('message', ''))}\n")
            self.stream.flush()
        self.active = None
        self._t0 = None

    # -- ticker -----------------------------------------------------------
    def _start_ticker(self):
        self._stop = threading.Event()
        self._ticker = threading.Thread(target=self._tick, daemon=True)
        self._ticker.start()

    def _tick(self):
        while not self._stop.wait(_TICK_SECONDS):
            self._write_active()

    def _stop_ticker(self):
        if self._ticker is not None:
            self._stop.set()
            self._ticker.join(timeout=1.0)
            self._ticker = None


def build_parser():
    parser = argparse.ArgumentParser(
        prog="GameReComp",
        description="Windows PE -> PCRecomp C -> native executable -> run -> "
                    "verification reports. Game data stays where the user "
                    "supplied it; nothing is downloaded.")
    parser.add_argument("pe_file", nargs="?", default=None,
                        help="original Windows .exe (PE32)")
    parser.add_argument("--project-dir", help="output project directory "
                        "(default: projects/<name>)")
    parser.add_argument("--assets-dir", help="working directory for the "
                        "rebuilt binary (default: the executable's directory)")
    parser.add_argument("--out-mode", choices=sorted(OUT_MODE_STAGES),
                        default=None,
                        help="what to produce: source-only (generated C "
                             "project), binary-only (add the compiled native "
                             "executable), full (everything: run + "
                             "verification). The short names source, binary "
                             "and both are the same thing; default full")
    parser.add_argument("--stages", metavar="A,B,...", type=_stage_list,
                        help="run exactly these stages, in canonical order "
                             "(overrides --out-mode; report always runs last "
                             "so every run stays inspectable)")
    parser.add_argument("--skip-stages", metavar="A,B,...", type=_stage_list,
                        help="skip stages of the selected plan (report cannot "
                             "be skipped)")
    parser.add_argument("--list-stages", action="store_true",
                        help="print the stage plan with weights and exit")
    parser.add_argument("--input-os", "--in-os", dest="input_os",
                        choices=["auto", "windows"], default=None,
                        help="what the input binary is (omit to assume 'auto': "
                             "the PE header is read; an explicit declaration "
                             "that disagrees with the bytes is refused). "
                             "Defaults to auto -- but pass it explicitly, with "
                             "--input-arch, --target-os and --target-arch, to "
                             "silence the missing-identity warning")
    parser.add_argument("--input-arch", "--in-arch", dest="input_arch",
                        choices=["auto", "x86"], default=None,
                        help="guest CPU architecture of the input (omit to "
                             "assume 'auto' from the PE header; the pipeline "
                             "reconstructs 32-bit x86 guests). Defaults to "
                             "auto -- but pass it explicitly to silence the "
                             "missing-identity warning")
    parser.add_argument("--target-os", "--output-os", "--out-os",
                        dest="target_os",
                        choices=["host", "linux", "macos", "windows", "web", "wasm"],
                        default=None,
                        help="output platform (omit to assume 'host'; use 'web' "
                             "for WebAssembly web apps; --output-os and --out-os "
                             "are aliases). Foreign OSes are refused.")
    parser.add_argument("--target-arch", "--output-arch", "--out-arch",
                        dest="target_arch",
                        choices=["host", "x86_64", "arm64", "wasm32"], default=None,
                        help="output CPU architecture (omit to assume 'host'; use "
                             "'wasm32' for WebAssembly; --output-arch and "
                             "--out-arch are aliases).")
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="seconds to let the rebuilt binary run (default 30)")
    parser.add_argument("--interactive", action="store_true",
                        help="use real SDL video/audio instead of dummy drivers")
    parser.add_argument("--oracle", help="JSON file with process-level oracle "
                        "checks: exit_code, stdout_contains, stderr_contains")
    parser.add_argument("--jobs", type=int, help="parallel compile jobs")
    parser.add_argument("--split", type=int, default=1000,
                        help="functions per generated source unit (default 1000)")
    parser.add_argument("--split-bytes", type=int, default=8 * 1024 * 1024,
                        help="soft cap on generated text per source unit "
                             "(default 8 MiB)")
    parser.add_argument("--build-timeout", type=float, default=None,
                        help="hard deadline for configure+build in seconds "
                             "(default: adaptive, 600 s + 5 s/MiB of generated "
                             "source capped at 3600 s; 0 disables)")
    parser.add_argument("--reuse-build", action="store_true",
                        help="adopt the existing build/<name> binary when its "
                             "recorded source digest matches the freshly "
                             "generated source, skipping recompilation; "
                             "refuses (fail-closed) on any mismatch")
    parser.add_argument("--highlevel", metavar="DIR",
                        help="directory of typed replacement modules "
                             "(default: highlevel/ in this repository). A "
                             "module names original addresses and provides real "
                             "C; the pipeline generates the guest-ABI adapters, "
                             "compiles them into the same executable and keeps "
                             "the mechanical body as sub_XXXXXXXX__lowlevel")
    parser.add_argument("--import-trace", action="store_true",
                        help="record every import the rebuilt process dispatches "
                             "(RECOMP_TRACE) and put the call profile in the "
                             "report; evidence that a run reached a game loop "
                             "rather than a spin, at some cost in speed")
    parser.add_argument("--fresh-build", action="store_true",
                        help="delete build/ before configuring, so a cached "
                             "CMakeCache.txt cannot pin paths from an "
                             "uninstalled or moved dependency (Homebrew keg "
                             "moves are the common case)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the resolved plan (input, target, stages, "
                             "output directories) and exit without running")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--no-run", action="store_true",
                        help="build but do not execute the rebuilt binary")
    parser.add_argument("--no-table", action="store_true",
                        help="do not record this run in table.md")
    parser.add_argument("--no-progress", action="store_true",
                        help="plain stage lines instead of the interactive "
                             "progress bar (the default when stdout/stderr is "
                             "not a TTY)")
    parser.add_argument("--verbose", action="store_true",
                        help="show each stage's message under the progress bar")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress per-stage progress; print only the "
                             "final summary")
    parser.add_argument("--json", action="store_true",
                        help="print the final report JSON")
    parser.add_argument("--version", action="version",
                        version=f"GameReComp {__version__}")
    return parser


def _print_stage_plan(stream, selected):
    total = sum(STAGE_WEIGHTS.get(s, 0) for s in selected)
    for name in selected:
        stream.write(f"  {name:<9s} weight {STAGE_WEIGHTS.get(name, 0):3d}\n")
    stream.write(f"  total weight {total}\n")


def _print_plan(args, pe_path, selected, target):
    project_dir = (Path(args.project_dir).expanduser() if args.project_dir
                   else Path(projectgen_default(pe_path)))
    os_name, arch = target
    identity, _ = _pe_identity(pe_path)
    in_os, in_arch = identity if identity else ("unknown", "unknown")
    in_os_decl = args.input_os if args.input_os is not None else "auto"
    in_arch_decl = args.input_arch if args.input_arch is not None else "auto"
    out_os_decl = args.target_os if args.target_os is not None else "host"
    out_arch_decl = args.target_arch if args.target_arch is not None else "host"
    print("plan (dry run; nothing was built):")
    print(f"  input:       {pe_path}")
    print(f"  input os:    {in_os} ({in_os_decl})")
    print(f"  input arch:  {in_arch} ({in_arch_decl})")
    print(f"  name:        {pe_path.stem}")
    kind = "web" if os_name == "web" else "native"
    print(f"  output:      {os_name}/{arch} {kind} "
          f"({out_os_decl}/{out_arch_decl})")
    print(f"  out-mode:    {args.out_mode or ('stages' if args.stages else 'both')}")
    print(f"  project dir: {project_dir}")
    print(f"  assets dir:  "
          f"{Path(args.assets_dir).expanduser() if args.assets_dir else pe_path.parent}")
    print(f"  timeout:     {args.timeout:g}s  "
          f"headless: {not args.interactive}  jobs: {args.jobs or 'default'}")
    print("  stages:")
    _print_stage_plan(sys.stdout, selected)


def _identity_defaults(args, stream=None):
    """Fill in the omitted input/output identity knobs, warning about each.

    The four knobs are independent and all defaultable: input OS and arch
    come from the PE header ('auto'), output OS and arch default to this
    build host. Leaving them out is fine -- that is what makes the common
    "reconstruct this exe for this machine" run one argument -- but the
    result is an assumption, not a statement by the caller, so it is
    reported once on stderr naming every omitted knob. Passing any of them
    explicitly stays silent for that knob; passing all four says nothing.
    """
    stream = sys.stderr if stream is None else stream
    omitted = []
    if args.input_os is None:
        args.input_os = "auto"
        omitted.append("--input-os (assuming auto, from the PE header)")
    if args.input_arch is None:
        args.input_arch = "auto"
        omitted.append("--input-arch (assuming auto, from the PE header)")
    if args.target_os is None:
        args.target_os = "host"
        omitted.append("--target-os (assuming host)")
    if args.target_arch is None:
        args.target_arch = "host"
        omitted.append("--target-arch (assuming host)")
    if not omitted:
        return args
    host_os, host_arch = _host_pair()
    effective_out = (
        host_os if args.target_os == "host" else args.target_os,
        host_arch if args.target_arch == "host" else args.target_arch)
    print("warning: input/output OS and architecture were not fully "
          "specified; assuming input windows/x86 -> output "
          f"{effective_out[0]}/{effective_out[1]} (this host).", file=stream)
    for line in omitted:
        print(f"         omitted: {line}", file=stream)
    print("         pass --input-os, --input-arch, --target-os and "
          "--target-arch explicitly to declare them and silence this "
          "warning.", file=stream)
    return args


def projectgen_default(pe_path):
    # Imported lazily to keep `--version` cheap and dependency-light.
    from tools.autorecomp.projectgen import default_project_dir  # noqa: PLC0415
    return default_project_dir(Path(pe_path).expanduser().resolve())


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "readability":
        from .readability import main as readability_main  # noqa: PLC0415
        return readability_main(argv[1:])

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_stages:
        print(f"stages (prog={parser.prog}):")
        _print_stage_plan(sys.stdout, list(STAGES))
        return 0

    if not args.pe_file:
        parser.error("the following arguments are required: pe_file")

    pe_path = Path(args.pe_file).expanduser()
    if not pe_path.is_file():
        print(f"error: input executable not found: {pe_path}", file=sys.stderr)
        if "..." in str(args.pe_file):
            print("hint: '...' in a path is shorthand, not a real path; pass "
                  "the full absolute path to the .exe", file=sys.stderr)
        if str(args.pe_file).startswith("~"):
            print("hint: '~' at the start of a path needs expanding; the "
                  "shell does it unquoted, so avoid quoting that argument",
                  file=sys.stderr)
        corpus = os.environ.get("SMALL_GAMES_PATH")
        if corpus and not Path(pe_path).is_absolute():
            guess = Path(corpus).expanduser() / pe_path
            if guess.is_file():
                print(f"hint: found it in SMALL_GAMES_PATH: {guess}",
                      file=sys.stderr)
        return EXIT_STAGE_FAILED

    # Fill in any omitted --input-os/--input-arch/--target-os/--target-arch and
    # say so. The defaults are the ones that make the one-argument run work
    # (input identity from the header, output = this host), but they are an
    # assumption the caller did not state, so they are named on stderr.
    args = _identity_defaults(args)

    target, target_error = _resolve_target(args.target_os, args.target_arch)
    if target_error:
        print(f"error: {target_error}", file=sys.stderr)
        return EXIT_UNSUPPORTED

    input_identity, input_error = _resolve_input(
        pe_path, args.input_os, args.input_arch)
    if input_error:
        print(f"error: {input_error}", file=sys.stderr)
        return EXIT_UNSUPPORTED

    selected = _resolve_stages(args, parser.error)
    if selected is None:
        return EXIT_STAGE_FAILED

    if args.dry_run:
        _print_plan(args, pe_path, selected, target)
        return 0

    allow_compile = "compile" in selected
    allow_run = "run" in selected

    if args.quiet:
        on_event = None
    else:
        # None lets the renderer auto-detect: bar on a TTY, plain lines in a
        # pipe or CI log. --no-progress forces plain either way.
        on_event = _StageProgress(
            selected, bar=(False if args.no_progress else None),
            verbose=args.verbose)

    pipeline = Pipeline(
        pe_path, project_dir=args.project_dir, assets_dir=args.assets_dir,
        timeout=args.timeout, headless=not args.interactive,
        oracle=args.oracle, jobs=args.jobs, split=args.split,
        split_bytes=args.split_bytes, build_timeout=args.build_timeout,
        build=allow_compile and not args.no_build,
        run_binary=allow_run and not args.no_run,
        reuse_build=args.reuse_build, fresh_build=args.fresh_build,
        import_trace=args.import_trace, highlevel_dir=args.highlevel,
        target=target, on_event=on_event)
    try:
        run_pipeline(pipeline, stages=selected)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130

    report_path = pipeline.project_dir / "reports" / "report.json"
    report = {}
    try:
        report = json.loads(report_path.read_text())
    except (OSError, ValueError):
        pass
    verification = (report.get("outcome") or {}).get("status", "not-run")
    score = report.get("score", pipeline.score())
    failed = [s["name"] for s in pipeline.stages if s["status"] == "failed"]

    # --json promises machine-readable stdout: the human summary goes to
    # stderr so `./GameReComp game.exe --json > report.json` is valid JSON,
    # not a summary with JSON appended.
    summary = sys.stderr if args.json else sys.stdout

    print(file=summary)
    print(f"project:      {pipeline.project_dir}", file=summary)
    print(f"input:        {input_identity[0]}/{input_identity[1]} "
          f"({pe_path.name})", file=summary)
    print(f"output:       {target[0]}/{target[1]} native", file=summary)
    print(f"source code:  {pipeline.project_dir / 'src' / 'recomp' / 'gen'}",
          file=summary)
    if pipeline.binary and Path(pipeline.binary).exists():
        print(f"binary:       {pipeline.binary}", file=summary)
    stage_summary = ", ".join(
        f"{s['name']}({s['status']})" for s in pipeline.stages
        if s["name"] in selected or s["name"] == "report")
    print(f"stages:       {stage_summary}", file=summary)
    print(f"score:        {score}/100", file=summary)
    print(f"verification: {verification}", file=summary)
    if failed:
        print(f"failed stages: {', '.join(failed)}", file=summary)
    print(f"report:       {pipeline.project_dir / 'reports' / 'report.html'}",
          file=summary)

    if not args.no_table and (pipeline.project_dir / "reports" / "report.json").exists():
        try:
            gamesdb.record_result(pipeline.project_dir,
                                  relative_path=_database_path(pe_path))
            table = gamesdb.write_table()
            gamesdb.write_matrix()
            rendered = gamesdb.render_matrix()
            m = re.search(r"## This repository \(measured\)\n\n(.*?)\n\n## This repository "
                          r"\(not measured\)", rendered, re.S)
            if m:
                gamesdb.sync_readme(m.group(1).strip())
            print(f"database:     {table}", file=summary)
        except (OSError, ValueError) as exc:
            print(f"warning: could not update table.md: {exc}", file=sys.stderr)

    if args.json:
        print(json.dumps(report, indent=2))

    if failed:
        return EXIT_STAGE_FAILED
    if "verify" not in selected:
        # A source/binary/partial run succeeded at everything it promised;
        # it did not run, so it is not a gameplay claim either.
        return EXIT_VERIFIED
    if verification in ("scenario-pass", "verified-playable-scope"):
        return EXIT_VERIFIED
    return EXIT_UNVERIFIED


if __name__ == "__main__":
    sys.exit(main())
