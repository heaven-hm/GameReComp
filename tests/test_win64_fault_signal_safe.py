"""Issue #338: guest_fault() in win64_runner.c must be async-signal-safe.

The SIGSEGV/SIGBUS handler used to call fprintf()/fflush(), which take stdio
locks and may allocate. A guest fault raised while a host shim holds those
locks deadlocked instead of reporting the fault. This module guards both the
source shape (no stdio in the handler, constant/sig_atomic_t state only) and
the runtime behaviour (a real raise(SIGSEGV/SIGBUS) emits the safe message and
exits with 128 + signal).

Run with: python3 -m pytest tests/test_win64_fault_signal_safe.py -q
"""

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime" / "recomp64_cpu"
RUNNER = RUNTIME / "win64_runner.c"


def handler_body(text):
    """Return the exact source of guest_fault() (brace-matched)."""
    start = text.index("static void guest_fault(")
    brace = text.index("{", start)
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[brace:i + 1]
    raise AssertionError("unterminated guest_fault handler")


def fault_section(text):
    """The handler plus the helpers it calls - the whole signal-handler path."""
    start = text.index("/* ---- async-signal-safe fault reporting")
    end = text.index("/* x87 is not modelled")
    return text[start:end]


HARNESS = r'''
/* Compile the REAL handler, not a copy: rename its main, then drive it. */
#define main runner_main
#include "win64_runner.c"
#undef main

#include <stdlib.h>

/* Deterministic guest register state, so the emitted hex digits are known. */
int recomp64_load_image(const char *path, CPU *c, uint64_t *entry_out) {
    (void)path;
    memset(c, 0, sizeof *c);
    c->rip = 0x123456789abcdef0ULL;
    c->rsp = 0x00000000deadbeefULL;
    c->rax = 0x1122334455667788ULL;
    c->rbx = 0xfedcba9876543210ULL;
    c->rcx = 0x000000000000002aULL;
    c->rdx = 0x00000000cafebabeULL;
    c->rsi = 0x0000000000000007ULL;
    c->rdi = 0x0000000000000009ULL;
    *entry_out = 0;
    const char *s = getenv("WIN64_FAULT_HARNESS_SIG");
    raise(s ? atoi(s) : SIGSEGV);
    return 0; /* unreachable: the handler _exit()s */
}

void dispatch(CPU *c, uint64_t target) { (void)c; (void)target; }

int main(void) {
    char *argv[] = { "harness", "fake.exe" };
    return runner_main(2, argv);
}
'''

# Minimal stand-in for the generated recomp_dispatch.h; win64_runner.c only
# needs the header to exist because dispatch() itself is declared in cpu64.h.
DISPATCH_HEADER = "#pragma once\n"


class FaultSignalSafeTests(unittest.TestCase):
    def test_handler_has_no_stdio_or_allocator_calls(self):
        section = fault_section(RUNNER.read_text())
        calls = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", section))
        unsafe = {"fprintf", "fflush", "printf", "sprintf", "snprintf",
                  "malloc", "calloc", "realloc", "free", "abort", "exit"}
        self.assertFalse(calls & unsafe,
                         f"handler calls async-signal-unsafe functions: {sorted(calls & unsafe)}")
        self.assertIn("write", calls)
        self.assertIn("_exit", calls)

    def test_handler_uses_write_of_static_text_and_stack_hex(self):
        text = RUNNER.read_text()
        body = handler_body(text)
        self.assertIn("write(STDERR_FILENO", fault_section(text))
        self.assertIn("static const char FAULT_", text)
        self.assertIn("fault_hex", body)
        self.assertIn("_exit(128 + sig)", body)
        # The message fragments must be constants, never mutable buffers in
        # static storage that another thread could be rewriting.
        fragments = re.findall(r"static const char (FAULT_[A-Z_]+)\[\]\s*=", text)
        self.assertGreaterEqual(len(fragments), 10)

    def test_reused_state_is_constant_or_sig_atomic_t(self):
        text = RUNNER.read_text()
        self.assertIn("static volatile CPU * const g_fault_cpu = &guest_cpu;", text)
        self.assertIn("static volatile sig_atomic_t g_fault_reporting;", text)
        # No plain mutable pointer handed to the handler.
        self.assertNotIn("static volatile CPU *g_fault_cpu;", text)

    def test_real_signal_raises_emit_safe_diagnostics(self):
        cc = shutil.which("cc") or shutil.which("clang")
        if not cc:
            self.skipTest("no C compiler available")
        with tempfile.TemporaryDirectory(prefix="win64-fault-338-") as td:
            tmp = Path(td)
            (tmp / "recomp_dispatch.h").write_text(DISPATCH_HEADER)
            harness_c = tmp / "harness.c"
            harness_c.write_text(HARNESS)
            exe = tmp / "harness"
            cmd = [cc, "-w", "-O0", "-I", str(tmp), "-I", str(RUNTIME),
                   str(harness_c), "-o", str(exe)]
            build = subprocess.run([str(c) for c in cmd], capture_output=True,
                                   text=True, timeout=60)
            self.assertEqual(build.returncode, 0,
                             f"harness build failed:\n{build.stdout}\n{build.stderr}")

            for signo in (signal.SIGSEGV, signal.SIGBUS):
                if signo is None:  # pragma: no cover - non-Unix platforms
                    continue
                with self.subTest(signal=signal.Signals(signo).name):
                    env = dict(os.environ)
                    env["WIN64_FAULT_HARNESS_SIG"] = str(int(signo))
                    run = subprocess.run([str(exe)], capture_output=True,
                                         env=env, timeout=30)
                    err = run.stderr.decode(errors="replace")
                    self.assertEqual(run.returncode, 128 + int(signo),
                                     f"exit={run.returncode}, stderr={err!r}")
                    self.assertIn("[fault] signal ", err)
                    # Known register values from the stub must appear as hex.
                    for expected in ("rip=0x123456789abcdef0",
                                     "rsp=0x00000000deadbeef",
                                     "rax=0x1122334455667788",
                                     "rdi=0x0000000000000009"):
                        self.assertIn(expected, err)
                    self.assertNotIn("RECOMP_TODO", err)


if __name__ == "__main__":
    unittest.main()
