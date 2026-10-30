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

# "[hle] Name" (the default) or "[hle] <guest tid> Name" (RECOMP_TRACE_TID=1).
LINE = re.compile(r"^\[hle\]\s+(?:(\d+)\s+)?(\S+)\s*$")
# the checkpoint line the runtime prints at RECOMP_CALL_CAP:
#   [determinism] state <fnv1a> cap <n> ranges <r>
STATE = re.compile(r"^\[determinism\] state ([0-9a-fA-F]+) cap (\d+) ranges (\d+)")


def parse(path):
    """The (guest thread id, call name) pairs in a trace, in order.

    The thread id is None for the default untagged format, and kept when the
    trace was recorded with RECOMP_TRACE_TID=1. Accepts a RECOMP_TRACE capture
    (`[hle] Name` or `[hle] 260 Name`) and any line whose last field looks like a
    call name, so a checkpoint file with extra columns can be compared as well.
    Lines that are not call records are ignored, so stderr can be piped in."""
    calls = []
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            m = LINE.match(line.rstrip("\n"))
            if m:
                calls.append((int(m.group(1)) if m.group(1) else None, m.group(2)))
                continue
            parts = line.split()
            if len(parts) >= 2 and not line.startswith("["):
                # a checkpoint line: "seq Name eip esp ..." -- take the name
                calls.append((None, parts[1]))
    return calls


def by_thread(calls):
    """{thread id: [call names]}, in the order each thread made them. An
    untagged trace is one thread."""
    out = {}
    for tid, name in calls:
        out.setdefault(tid, []).append(name)
    return out


def first_divergence(a, b):
    """(index, value_a, value_b) of the first difference, or None if one trace
    is a prefix of the other."""
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            return i, a[i], b[i]
    return None


def as_calls(trace):
    """A trace as (thread id, name) pairs: bare names are accepted too, so a
    caller (and the selftest) can compare plain sequences."""
    return [item if isinstance(item, tuple) else (None, item) for item in trace]


def is_tagged(calls):
    calls = as_calls(calls)
    return any(tid is not None for tid, _ in calls)


def locate(a, b, prefix=False):
    """Where two traces first part, per thread when they are thread-tagged.

    Returns (kind, payload). `kind` is "same" when the traces match, "early"
    when one is a prefix of the other, and "diverge" with payload
    (thread, index, call_a, call_b) otherwise. Comparison is per thread when
    both traces carry thread ids: two concurrent threads interleave differently
    on every run, so a single global sequence cannot be stable even when each
    thread's own behaviour is, and comparing thread by thread is the only
    comparison that means anything."""
    a, b = as_calls(a), as_calls(b)
    if is_tagged(a) or is_tagged(b):
        ta, tb = by_thread(a), by_thread(b)
        problems = []
        for tid in sorted(set(ta) | set(tb), key=lambda t: (t is None, t)):
            if tid not in tb:
                problems.append((tid, "only in the first trace"))
                continue
            if tid not in ta:
                problems.append((tid, "only in the second trace"))
                continue
            # A thread whose sequence is a strict prefix of the other's is a
            # divergence too: comparing only the common prefix would call a run
            # that stopped short "the same".
            if len(ta[tid]) != len(tb[tid]) and not prefix:
                problems.append((tid, (min(len(ta[tid]), len(tb[tid])),
                                       f"{len(ta[tid])} calls", f"{len(tb[tid])} calls")))
                continue
            d = first_divergence(ta[tid], tb[tid])
            if d is not None:
                problems.append((tid, d))
        if not problems:
            if prefix:
                return "same", (sum(min(len(ta[t]), len(tb.get(t, []))) for t in ta), len(ta))
            if len(a) == len(b):
                return "same", (sum(len(v) for v in ta.values()), len(ta))
            short, long_ = (a, b) if len(a) < len(b) else (b, a)
            return "early", (len(short), long_[len(short)][1])
        first = problems[0]
        if isinstance(first[1], str):          # a thread present in only one trace
            return "diverge", (first[0], None, first[1], None)
        tid, (i, ca, cb) = first
        return "diverge", (tid, i, ca, cb)
    d = first_divergence([n for _, n in a], [n for _, n in b])
    if d is not None:
        return "diverge", (None, d[0], d[1], d[2])
    if len(a) == len(b):
        return "same", (len(a), 1)
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return "early", (len(short), long_[len(short)][1])


def thread_sequences(calls):
    """The per-thread name sequences, or the single sequence when untagged."""
    if is_tagged(calls):
        return by_thread(calls)
    return {None: [n for _, n in calls]}


def report(a, b, label_a="baseline", label_b="candidate", context=5, prefix=False):
    """The divergence report, as text."""
    out = []
    a, b = as_calls(a), as_calls(b)
    kind, payload = locate(a, b, prefix)
    if is_tagged(a) or is_tagged(b):
        ta, tb = by_thread(a), by_thread(b)
        out.append(f"{label_a}: {len(a)} calls on {len(ta)} thread(s) | "
                   f"{label_b}: {len(b)} calls on {len(tb)} thread(s)")
    else:
        out.append(f"{label_a}: {len(a)} calls | {label_b}: {len(b)} calls")
    if kind == "same":
        out.append("the two traces are identical: no divergence")
        return 0, out
    if kind == "early":
        n, nxt = payload
        out.append(f"no divergence in the common prefix of {n} calls; one trace ended "
                   f"there and the other continued with {nxt!r}")
        return 1, out
    tid, index, ca, cb = payload
    where = f"thread {tid}, call {index}" if tid is not None else f"call {index}"
    if index is None:
        out.append(f"divergence at {where}: {ca} / {cb}")
        return 2, out
    # classify on the sequences that actually differ
    sa, sb = thread_sequences(a), thread_sequences(b)
    seq_a, seq_b = sa.get(tid, []), sb.get(tid, [])
    ckind, why = classify(seq_a, seq_b, index)
    out.append(f"first divergence at {where}: {label_a} has {ca!r}, {label_b} has {cb!r}")
    out.append(f"  classified: {ckind.upper()} -- {why}")
    lo = max(0, index - context)
    out.append(f"  context ({lo}..{index + context}):")
    for i in range(lo, min(max(len(seq_a), len(seq_b)), index + context)):
        out.append(f"    {i:8d}  {seq_a[i] if i < len(seq_a) else '--':34s} | "
                   f"{seq_b[i] if i < len(seq_b) else '--'}")
    return (1 if ckind == "timing" else 2), out


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
    calls, state = [], None
    for line in err.splitlines():
        line = line.rstrip("\n")
        if m := STATE.match(line):
            state = {"hash": m.group(1).lower(), "cap": int(m.group(2)), "ranges": int(m.group(3))}
        elif m := LINE.match(line):
            calls.append((int(m.group(1)) if m.group(1) else None, m.group(2)))
    return {"calls": calls, "rc": rc, "state": state}


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
        """A signature for "how they differ at this cap", or None if they match.

        State first: the checkpoint hashes the registers and flags plus whatever
        memory ranges RECOMP_STATE_RANGE names, so a state difference is the
        stronger claim - two runs can make the same calls and still be in
        different states. A registers-only fingerprint (ranges 0) is not a state
        comparison, so it only counts when the sequences also differ."""
        A = run_capped(baseline_binary, exe, cwd, cap, env_extra, timeout)
        B = run_capped(candidate_binary, exe, cwd, cap, env_extra, timeout)
        a, b = A["calls"], B["calls"]
        sa, sb = A["state"], B["state"]
        if sa and sb and sa["ranges"] and sb["ranges"]:
            if sa["hash"] != sb["hash"] or sa["ranges"] != sb["ranges"]:
                return ("state", sa["hash"], sb["hash"], sa["ranges"], sb["ranges"]), A, B
        kind, payload = locate(a, b)
        if kind == "same":
            return None, A, B
        if kind == "early":
            return ("early", payload[0]), A, B
        tid, idx, _, _ = payload
        return ("seq", tid, idx), A, B

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
    sigs = [s[0] for s in samples]
    matched = [i for i in sigs if i is None]
    differed = [i for i in sigs if i is not None]
    if matched:
        trace(f"  NOT REPRODUCIBLE: at cap {hi}, {len(matched)} of {len(sigs)} pairs matched "
              f"and {len(differed)} differed ({sigs}).")
        trace(f"  The runs sometimes agree and sometimes do not, so there is no single call "
              f"to point at: this is nondeterminism between runs - thread interleaving, or a "
              f"clock that is not fully controlled - not a located divergence. A single-pair "
              f"bisection would have reported a number here that means nothing.")
        return hi
    if len(set(differed)) != 1:
        trace(f"  NOT REPRODUCIBLE: the divergence lands in different places each time "
              f"({differed}); nothing stable to point at.")
        return hi
    sig = differed[0]
    if sig[0] == "state":
        _, ha, hb, ra, rb = sig
        trace(f"  reproducible: STATE divergence at cap {hi}")
        trace(f"    baseline  state {ha}  ranges {ra}")
        trace(f"    candidate state {hb}  ranges {rb}")
        trace(f"  the two runs make the same calls up to that cap and are still in "
              f"different states, so the difference is in the state and not in the sequence.")
        return hi
    trace(f"  reproducible: every pair diverges at {sig[1:]}")
    code, lines = report(samples[0][1]["calls"], samples[0][2]["calls"], "baseline", "candidate", 4)
    for line in lines:
        trace("  " + line)
    if samples[0][1]["state"] and samples[0][1]["state"]["ranges"] == 0:
        trace("  (registers only - ranges 0 - so this is a sequence comparison, not a state one;")
        trace("   set RECOMP_STATE_RANGE=base:length to make the checkpoint cover memory)")
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
    ap.add_argument("--prefix", action="store_true",
                    help="per thread, compare only up to the shorter trace: the question a replay "
                         "fidelity check asks, since a global call cap splits between threads")
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
    ap.add_argument("--state-range",
                    help="guest memory ranges for the checkpoint fingerprint, base:length[,base:length] "
                         "(default: registers only, which is NOT a state comparison)")
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
        env_extra = {"RECOMP_STATE_RANGE": args.state_range} if args.state_range else None
        hi = bisect_run(args.baseline_binary, args.candidate_binary, args.exe,
                        args.cwd or ".", args.lo, args.hi, env_extra, args.timeout, args.repeat)
        return 2 if hi is not None else 0
    if not args.baseline or not args.candidate:
        ap.error("two trace files are needed (or --selftest)")
    la, lb = ("baseline", "candidate")
    if args.labels and "," in args.labels:
        la, lb = args.labels.split(",", 1)
    code, lines = report(parse(args.baseline), parse(args.candidate), la, lb, args.context, args.prefix)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
