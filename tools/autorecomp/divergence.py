"""divergence.py -- locate the first place two runs stop matching.

The suite can say a rebuilt game *runs*; it cannot say where it stops matching
something else. This finds that place, and says what kind of difference it is.

Two runs are compared as **call traces**: the sequence of imports the guest
dispatched, in order. A trace is what `RECOMP_TRACE=1` already writes to stderr
(`[hle] Name`), so no runtime change is needed to use this, and a trace recorded
elsewhere -- a checkpoint file from the original on a Windows host, for instance
-- can be fed in the same way as long as it has one line per call with the name
on it.

What it reports:

  * the index of the first differing call, with a few calls of context on both
    sides, so the divergence can be read rather than guessed at;
  * whether the difference is **timing-only** or **behavioural**. This matters
    more than it sounds: measured on this corpus, a startup-dominated run
    (ikachan) is byte-identical across runs, while a frame-loop run (Doukutsu)
    diverges around call 118k on a clock poll -- the game asked `timeGetTime`
    twice where the other run went straight on to the next frame. Reporting that
    as a behavioural divergence would be wrong, and a tool that cries wolf is
    turned off. So a divergence whose differing calls are all clock, sleep or
    poll calls is classified `timing`, and only the rest are `behavioural`;
  * when one trace is a prefix of the other, that it ended early, and what the
    longer one did next -- which is the shape of a crash or an early exit.

It also **bisects runs**, which is the half that locates rather than reports:
`--bisect-run` runs the two binaries again and again with the runtime's
`RECOMP_CALL_CAP` set to a different call count each time, and binary-searches
the smallest cap at which their traces stop matching. The invariant that makes
bisection valid is that matching is monotone in the cap: if two runs agree up to
call N they agree up to every smaller count, so the search converges on the call
where they part. With `RECOMP_CLOCK=virtual` the two runs are comparable in the
first place, which is why the clock had to exist before this could.

    python3 -m tools.autorecomp.divergence base.trace cand.trace
    python3 -m tools.autorecomp.divergence --bisect-run \
        --baseline-binary A --candidate-binary B --exe game.exe --cwd DIR --hi 400000
    python3 -m tools.autorecomp.divergence --selftest
"""

import argparse
import re
import sys

# Calls that make a trace differ without the guest's behaviour differing: the
# clock, sleeps, and the polling that reads them. Anything not in here counts as
# a real difference.
TIMING_CALLS = {
    "GetTickCount", "GetTickCount64", "timeGetTime", "timeGetSystemTime",
    "QueryPerformanceCounter", "QueryPerformanceFrequency", "Sleep", "SleepEx",
    "GetSystemTime", "GetLocalTime", "GetSystemTimeAsFileTime", "time", "_time64",
    "SDL_GetTicks", "SDL_Delay", "SDL_WaitEvent", "WaitMessage", "MsgWaitForMultipleObjects",
}

LINE = re.compile(r"^\[hle\]\s+(\S+)\s*$")


def parse(path):
    """The call names in a trace, in order.

    Accepts a RECOMP_TRACE capture (`[hle] Name`) and any line whose last field
    looks like a call name, so a checkpoint file with extra columns
    (a sequence number, registers) can be compared as well. Lines that are not
    call records are ignored, so stderr can be piped in whole."""
    names = []
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            m = LINE.match(line.rstrip("\n"))
            if m:
                names.append(m.group(1))
                continue
            parts = line.split()
            if len(parts) >= 2 and not line.startswith("["):
                # a checkpoint line: "seq Name eip esp ..." -- take the name
                names.append(parts[1])
    return names


def first_divergence(a, b):
    """(index, call_a, call_b) of the first difference, or None if one trace is
    a prefix of the other (which the caller reports as an early end)."""
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            return i, a[i], b[i]
    return None


def classify(a, b, index, lookahead=64):
    """timing, timing-initiated-but-persistent, or behavioural.

    Judged on the calls around the divergence *and* on whether the traces match
    once timing calls are removed, because the two answers differ and the
    difference is not cosmetic. A clock poll shifting by one call can resync
    immediately -- that is genuinely timing. But a clock difference can also
    change which branch the guest takes and never resync: measured on Doukutsu,
    two runs diverge at call 117,955 on a clock poll, resync locally for ~700
    calls, and then differ for good at call 118,727 (one run in GetWindowRect
    where the other is in its message loop). Calling that "timing" because the
    first difference was a clock call would hide a real, persistent divergence
    behind a shrug, so the whole traces are checked, not just the window."""
    window_a = a[index:index + lookahead]
    window_b = b[index:index + lookahead]
    if not window_a or not window_b:
        return "behavioural", "one trace ended here"
    if window_a[0] not in TIMING_CALLS and window_b[0] not in TIMING_CALLS:
        return "behavioural", "the differing calls are not clock, sleep or poll calls"
    # the local picture: do the sequences line up again once timing calls go?
    filtered_a = [n for n in window_a if n not in TIMING_CALLS]
    filtered_b = [n for n in window_b if n not in TIMING_CALLS]
    local = (filtered_a and filtered_b and
             filtered_a[:len(filtered_b)] == filtered_b[:len(filtered_a)])
    if not local:
        return "behavioural", ("the divergence starts in a clock call but the calls "
                              "around it do not line up once timing calls are removed")
    # the global picture: does the whole trace match with timing calls removed?
    whole_a = [n for n in a if n not in TIMING_CALLS]
    whole_b = [n for n in b if n not in TIMING_CALLS]
    later = first_divergence(whole_a, whole_b)
    if later is None:
        return "timing", ("the differing calls are clock/sleep/poll calls and the whole "
                          "traces match once they are removed")
    li = later[0]
    return "timing-initiated but persistent", (
        f"the first difference is a clock call and resyncs locally, but the traces "
        f"still differ at normalized call {li} ({later[1]!r} vs {later[2]!r}) - the "
        f"clock difference changed a branch and never came back")


def report(a, b, label_a="baseline", label_b="candidate", context=5):
    """The divergence report, as text."""
    out = []
    d = first_divergence(a, b)
    out.append(f"{label_a}: {len(a)} calls | {label_b}: {len(b)} calls")
    if d is None:
        if len(a) == len(b):
            out.append("the two traces are identical: no divergence")
            return 0, out
        short, long_, which = (a, b, label_b) if len(a) < len(b) else (b, a, label_a)
        out.append(f"no divergence in the common prefix of {len(short)} calls; "
                   f"{which} ended there and the other continued with {long_[len(short)]!r}")
        out.append(f"  last calls of the shorter trace: {', '.join(short[-context:])}")
        return 1, out
    index, ca, cb = d
    kind, why = classify(a, b, index)
    out.append(f"first divergence at call {index}: {label_a} has {ca!r}, {label_b} has {cb!r}")
    out.append(f"  classified: {kind.upper()} -- {why}")
    lo = max(0, index - context)
    out.append(f"  context ({lo}..{index + context}):")
    for i in range(lo, min(max(len(a), len(b)), index + context)):
        out.append(f"    {i:8d}  {a[i] if i < len(a) else '--':34s} | {b[i] if i < len(b) else '--'}")
    return (1 if kind == "timing" else 2), out


def run_capped(binary, exe, cwd, cap, env_extra, timeout):
    """Run a binary with the call cap set, and return its call trace.

    The cap is the runtime's: RECOMP_CALL_CAP=N stops after N shim dispatches,
    prints a marker and exits 70, so a capped run is never mistaken for a real
    outcome. The trace is what the run did before stopping."""
    import os
    import subprocess
    env = dict(os.environ)
    env["RECOMP_TRACE"] = "1"
    env["RECOMP_CALL_CAP"] = str(cap)
    env.setdefault("SDL_VIDEODRIVER", "dummy")
    env.setdefault("SDL_AUDIODRIVER", "dummy")
    for k, v in (env_extra or {}).items():
        env[k] = v
    try:
        p = subprocess.run([binary, exe], cwd=cwd, env=env, capture_output=True, timeout=timeout)
        err, rc = p.stderr.decode("utf-8", "replace"), p.returncode
    except subprocess.TimeoutExpired as e:
        err = (e.stderr or b"").decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        rc = None
    names = [m.group(1) for line in err.splitlines() if (m := LINE.match(line.rstrip("\n")))]
    return names, rc


def bisect_run(baseline_binary, candidate_binary, exe, cwd, lo, hi, env_extra=None,
               timeout=120.0, repeat=2, trace=print):
    """The smallest call cap at which the two runs *reproducibly* stop matching.

    Matching is monotone in the cap - both runs execute the same prefix, so if
    they agree up to N they agree up to anything smaller - which is what makes
    this a bisection rather than a scan.

    `repeat` matters more than it looks. A divergence that is *nondeterministic*
    (two threads interleaving differently) lands at a different call every run,
    so a single pair per cap would report a precise-looking threshold that is
    really noise. Each cap is therefore run `repeat` times and only counts as
    differing if every pair differs, and the divergence index is checked for
    stability across those pairs: if it moves, the answer is "not reproducible",
    not a location."""

    def pair(cap):
        a, _ = run_capped(baseline_binary, exe, cwd, cap, env_extra, timeout)
        b, _ = run_capped(candidate_binary, exe, cwd, cap, env_extra, timeout)
        d = first_divergence(a, b)
        return (d[0] if d is not None else (None if len(a) == len(b) else min(len(a), len(b)))), a, b

    trace(f"bisecting between cap {lo} and {hi}, {repeat} pair(s) per cap")
    idx, a, b = pair(hi)
    if idx is None:
        trace(f"the runs match all the way to cap {hi}: nothing to bisect")
        return None
    steps = 0
    while hi - lo > 1:
        mid = (lo + hi) // 2
        idxs = [pair(mid)[0] for _ in range(repeat)]
        steps += 1
        differs = all(i is not None for i in idxs)
        trace(f"  cap {mid:8d}: {'DIFFER' if differs else 'match'} at {idxs}  ({steps} step)")
        if differs:
            hi = mid
        else:
            lo = mid
    trace(f"first cap where they differ: {hi} (they matched at {lo})")
    # The verdict needs more than one pair, because a divergence that is
    # intermittent looks exactly like a located one from a single sample.
    samples = [pair(hi) for _ in range(max(repeat, 2))]
    idxs = [s[0] for s in samples]
    matched = [i for i in idxs if i is None]
    differed = [i for i in idxs if i is not None]
    if matched:
        trace(f"  NOT REPRODUCIBLE: at cap {hi}, {len(matched)} of {len(idxs)} pairs matched "
              f"and {len(differed)} differed ({idxs}).")
        trace(f"  The runs sometimes agree and sometimes do not, so there is no single call "
              f"to point at: this is nondeterminism between runs - thread interleaving, or a "
              f"clock that is not fully controlled - not a located divergence. A single-pair "
              f"bisection would have reported a number here that means nothing.")
        return hi
    if len(set(differed)) != 1:
        trace(f"  NOT REPRODUCIBLE: the divergence lands at different calls each time "
              f"({differed}); nothing stable to point at.")
        return hi
    trace(f"  reproducible: every pair diverges at call {differed[0]}")
    code, lines = report(samples[0][1], samples[0][2], "baseline", "candidate", 4)
    for line in lines:
        trace("  " + line)
    code, lines = report(a, b, "baseline", "candidate", 4)
    for line in lines:
        trace("  " + line)
    return hi


def selftest():
    """Check the classifier on traces whose answer is known by construction."""
    failures = 0

    def check(what, got, want):
        nonlocal failures
        if got != want:
            failures += 1
            print(f"FAIL {what}: got {got!r}, want {want!r}")

    same = ["A", "B", "C"]
    check("identical traces", report(same, same)[0], 0)

    # a pure clock-poll shift: one extra timeGetTime, then everything lines up
    base = ["Init", "Load", "timeGetTime", "Draw", "Flip", "timeGetTime", "Draw", "Flip"]
    cand = ["Init", "Load", "timeGetTime", "timeGetTime", "Draw", "Flip", "timeGetTime", "Draw"]
    code, lines = report(base, cand)
    check("a clock shift is timing", code, 1)
    check("... and says so", "TIMING" in "\n".join(lines), True)

    # a clock difference that changes a branch and never comes back: the shape
    # measured on Doukutsu (diverges on a poll, resyncs locally, then differs for
    # good hundreds of calls later)
    base = (["Frame", "timeGetTime", "Draw"] * 40) + ["GetWindowRect", "Blt"]
    cand = (["Frame", "timeGetTime", "timeGetTime", "Draw"] * 40) + ["PeekMessageA", "Poll"]
    code, lines = report(base, cand)
    check("a clock difference that persists is not called timing", code, 2)
    check("... and says it is persistent", "PERSISTENT" in "\n".join(lines), True)

    # a real behavioural divergence
    base = ["Init", "Load", "Draw", "Flip", "Update"]
    cand = ["Init", "Load", "Draw", "Flip", "SomethingElse"]
    code, lines = report(base, cand)
    check("a logic change is behavioural", code, 2)
    check("... and says so", "BEHAVIOURAL" in "\n".join(lines), True)

    # one run ended early
    code, lines = report(["Init", "Load", "Draw"], ["Init", "Load", "Draw", "Flip"])
    check("an early end is reported", code, 1)
    check("... naming what the other did next", "continued with 'Flip'" in "\n".join(lines), True)

    if failures == 0:
        print("divergence: selftest passed")
    return failures != 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="divergence", description=__doc__.split("\n")[0])
    ap.add_argument("baseline", nargs="?", help="a trace file (RECOMP_TRACE stderr, or a checkpoint file)")
    ap.add_argument("candidate", nargs="?", help="the trace to compare against it")
    ap.add_argument("--selftest", action="store_true", help="check the classifier on synthetic traces")
    ap.add_argument("--context", type=int, default=5, help="calls of context to show (default 5)")
    ap.add_argument("--labels", help="names for the two traces, as A,B (default baseline,candidate)")
    ap.add_argument("--bisect-run", action="store_true",
                    help="binary-search the call cap at which the two binaries stop matching")
    ap.add_argument("--baseline-binary", help="the known-good rebuilt binary (--bisect-run)")
    ap.add_argument("--candidate-binary", help="the binary under test (--bisect-run)")
    ap.add_argument("--exe", help="the original .exe to pass to both (--bisect-run)")
    ap.add_argument("--cwd", help="working directory for both (--bisect-run)")
    ap.add_argument("--lo", type=int, default=0, help="a cap at which they are known to match (default 0)")
    ap.add_argument("--hi", type=int, default=200000, help="a cap at which they differ (default 200000)")
    ap.add_argument("--timeout", type=float, default=120.0, help="per run, seconds (default 120)")
    ap.add_argument("--repeat", type=int, default=2,
                    help="pairs of runs per cap; a cap only counts as differing if every "
                         "pair differs, and a moving index is reported as nondeterminism (default 2)")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.bisect_run:
        for need in ("baseline_binary", "candidate_binary", "exe"):
            if not getattr(args, need):
                ap.error(f"--{need.replace('_', '-')} is needed with --bisect-run")
        hi = bisect_run(args.baseline_binary, args.candidate_binary, args.exe,
                        args.cwd or ".", args.lo, args.hi, None, args.timeout, args.repeat)
        return 2 if hi is not None else 0
    if not args.baseline or not args.candidate:
        ap.error("two trace files are needed (or --selftest)")
    la, lb = ("baseline", "candidate")
    if args.labels and "," in args.labels:
        la, lb = args.labels.split(",", 1)
    code, lines = report(parse(args.baseline), parse(args.candidate), la, lb, args.context)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
