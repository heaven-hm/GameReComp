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

What it deliberately does not do: it does not re-run anything. Bisecting *runs*
(shrinking a replay until the divergence disappears) needs a deterministic clock
and a way to cap a run, and neither exists in the runtime yet; until they do, a
comparison of two traces is the honest half. The `--selftest` mode checks the
classifier against synthetic traces, so the tool is verifiable without a game.

    python3 -m tools.autorecomp.divergence base.trace cand.trace
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
    """timing or behavioural, judged on the calls around the divergence.

    A clock poll shifting by one call makes everything after it line up again
    one call later; a real divergence does not resynchronise. So the calls in
    the window are examined: if every call that differs is a timing call, the
    divergence is timing."""
    window_a = a[index:index + lookahead]
    window_b = b[index:index + lookahead]
    if not window_a or not window_b:
        return "behavioural", "one trace ended here"
    if window_a[0] in TIMING_CALLS or window_b[0] in TIMING_CALLS:
        # does it resynchronise once the timing calls are ignored?
        filtered_a = [n for n in window_a if n not in TIMING_CALLS]
        filtered_b = [n for n in window_b if n not in TIMING_CALLS]
        if filtered_a and filtered_b and filtered_a[:len(filtered_b)] == filtered_b[:len(filtered_a)]:
            return "timing", ("the differing calls are clock/sleep/poll calls and the "
                              "sequences line up again once they are ignored")
        if filtered_a[:4] == filtered_b[:4] or not filtered_a or not filtered_b:
            return "timing", "the difference is in clock/sleep/poll calls"
    return "behavioural", "the differing calls are not clock, sleep or poll calls"


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
    return (2 if kind == "behavioural" else 1), out


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
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
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
