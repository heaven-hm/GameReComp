"""Deterministic guest-pointer validation tests for native32's C++ throw matcher.

The matcher in ``runtime/native32/eh32.c`` reads MSVC ThrowInfo straight from
guest memory. This test compiles the REAL validation helpers and ``catchable``
out of that source into a small portable harness: a flat guest-memory model, a
configurable guest image span, and ``MEM32`` / ``native32_in_guest`` stubs.
Each case feeds a fixed memory image and checks the returned CatchableType,
the exact refusal reason, and how many guest reads happened. A read outside
the modelled span aborts the harness, so an unvalidated dereference cannot
pass by quietly reading host address zero.

No game, Wine prefix, Windows DLL or live injection is involved: every input
is synthetic and the outcome is one deterministic process run.

Run with: python3 -m pytest tests/test_native32_eh32_validation.py -q
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "runtime/native32/eh32.c"

BASE = 0x00400000
SPAN = 0x10000
INFO = BASE + 0x1000
ARR = BASE + 0x2000
CT = BASE + 0x3000
TD = BASE + 0x4000
TYPE = BASE + 0x5000
NAME = ".?AVGuestError@@"

HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define GUEST_BASE 0x00400000u
#define MEM_ARENA 0x100000u
static uint8_t g_mem[MEM_ARENA];
static uint32_t g_span = 0x10000u;
static unsigned long g_reads;

/* Only addresses that survived the source's validation may reach here. */
static uint32_t mem_read32(uint32_t va) {
    g_reads++;
    if ((va & 3u) || va < GUEST_BASE || va >= GUEST_BASE + MEM_ARENA) {
        fprintf(stderr, "UNVALIDATED_DWORD_READ %08X\n", va);
        exit(3);
    }
    uint32_t value;
    memcpy(&value, g_mem + (va - GUEST_BASE), sizeof value);
    return value;
}
#define MEM32(va) mem_read32((uint32_t)(va))

static int native32_in_guest(uint32_t va) {
    return va >= GUEST_BASE && va < GUEST_BASE + g_span;
}

/* The source casts guest VAs to char* before strcmp. Convert them back and
 * count every byte the name comparison touches. */
static int guest_strcmp(uint32_t a, uint32_t b) {
    unsigned i;
    for (i = 0; i < 256u; i++) {
        uint8_t ca, cb;
        if (a + i < GUEST_BASE || a + i >= GUEST_BASE + MEM_ARENA ||
            b + i < GUEST_BASE || b + i >= GUEST_BASE + MEM_ARENA) {
            fprintf(stderr, "UNVALIDATED_NAME_READ %08X %08X\n", a + i, b + i);
            exit(4);
        }
        ca = g_mem[a + i - GUEST_BASE];
        cb = g_mem[b + i - GUEST_BASE];
        g_reads++;
        if (ca != cb) return (int)ca - (int)cb;
        if (!ca) return 0;
    }
    return -1;  /* no terminator in range: treat as unequal */
}
#define strcmp(a, b) guest_strcmp((uint32_t)(uintptr_t)(a), (uint32_t)(uintptr_t)(b))

static int cmd_is(const char* a, const char* b) {
    while (*a && *a == *b) { a++; b++; }
    return *a == *b;
}

/*BLOCK*/

int main(void) {
    char cmd[64], text[256];
    while (scanf("%63s", cmd) == 1) {
        unsigned long va, value;
        if (cmd_is(cmd, "span")) {
            if (scanf("%lx", &value) != 1) return 2;
            g_span = (uint32_t)value;
        } else if (cmd_is(cmd, "mem")) {
            uint32_t word;
            if (scanf("%lx %lx", &va, &value) != 2) return 2;
            if (va < GUEST_BASE || va + 4u > GUEST_BASE + MEM_ARENA) return 2;
            word = (uint32_t)value;
            memcpy(g_mem + (va - GUEST_BASE), &word, sizeof word);
        } else if (cmd_is(cmd, "str")) {
            size_t n;
            if (scanf("%lx %255s", &va, text) != 2) return 2;
            if (va < GUEST_BASE || va + 256u > GUEST_BASE + MEM_ARENA) return 2;
            n = strlen(text);
            memcpy(g_mem + (va - GUEST_BASE), text, n + 1u);
        } else if (cmd_is(cmd, "call")) {
            unsigned long info, type;
            const char* why = NULL;
            uint32_t ct;
            if (scanf("%lx %lx", &info, &type) != 2) return 2;
            g_reads = 0;
            ct = catchable((uint32_t)info, (uint32_t)type, &why);
            printf("RESULT %08lX %s %lu\n", (unsigned long)ct,
                   why ? why : "none", g_reads);
            fflush(stdout);
        } else if (cmd_is(cmd, "quit")) {
            break;
        } else {
            return 2;
        }
    }
    return 0;
}
"""


def _function_end(src: str, signature: str) -> int:
    """Index just past the closing brace of the first function named here."""
    start = src.index(signature)
    brace = src.index("{", start)
    depth = 0
    for pos in range(brace, len(src)):
        if src[pos] == "{":
            depth += 1
        elif src[pos] == "}":
            depth -= 1
            if depth == 0:
                return pos + 1
    raise AssertionError(f"unterminated function: {signature}")


def validation_block(src: str) -> str:
    """The source's own validation helpers and catchable(), verbatim."""
    start = src.index("#define EH_MAX_CATCHABLE")
    end = _function_end(src, "static uint32_t catchable(")
    return src[start:end] + "\n"


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("C compiler not available")
    folder = tmp_path_factory.mktemp("native32_eh32")
    src = folder / "harness.c"
    exe = folder / "harness"
    source = SOURCE.read_text(encoding="utf-8")
    src.write_text(HARNESS.replace("/*BLOCK*/", validation_block(source)),
                   encoding="utf-8")
    build = subprocess.run(
        [cc, "-std=c11", "-O0", "-Wall", "-Wextra", "-Wno-unused-parameter",
         "-o", str(exe), str(src)], capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    return exe


def call(exe, *, info=INFO, type=TYPE, dwords=(), strings=(), span=SPAN):
    """Run one fixed guest-memory case through the compiled matcher."""
    commands = [f"span {span:x}"]
    for va, value in dwords:
        if BASE <= va < BASE + 0x100000 - 4:  # only modelled memory
            commands.append(f"mem {va:x} {value & 0xFFFFFFFF:x}")
    for va, text in strings:
        commands.append(f"str {va:x} {text}")
    commands += [f"call {info:x} {type:x}", "quit"]
    proc = subprocess.run([str(exe)], input="\n".join(commands) + "\n",
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    results = [line for line in proc.stdout.splitlines()
               if line.startswith("RESULT ")]
    assert len(results) == 1, proc.stdout
    # Reasons contain spaces; reads is the last field.
    prefix, reads = results[0].rsplit(" ", 1)
    _, ct, why = prefix.split(" ", 2)
    return {"ct": int(ct, 16),
            "why": None if why == "none" else why,
            "reads": int(reads)}


def table(*, count=1, ct=CT, td=TD, arr=ARR):
    """A well-formed ThrowInfo with one CatchableType and a RTTI name."""
    dwords = [(INFO + 12, arr), (arr, count)]
    if ct is not None:
        dwords.append((arr + 4, ct))  # the hostile value itself lives here
        if BASE <= ct < BASE + SPAN:
            dwords += [(ct, 0), (ct + 4, td)]
    if td is not None and BASE <= td < BASE + SPAN:
        dwords.append((td, 0xAABBCCDD))  # TypeDescriptor hash word
    dwords.append((TYPE, 0x11223344))
    strings = []
    if td is not None and BASE <= td + 8 < BASE + SPAN:
        strings.append((td + 8, NAME))
    if BASE <= TYPE + 8 < BASE + SPAN:
        strings.append((TYPE + 8, NAME))
    return dwords, strings


class TestValidCatchable:
    def test_identity_match_returns_catchable_type_without_a_name_read(self, harness):
        dwords, strings = table(td=TYPE)
        result = call(harness, dwords=dwords, strings=strings)
        assert result["ct"] == CT
        assert result["why"] is None

    def test_name_match_returns_catchable_type(self, harness):
        dwords, strings = table()
        result = call(harness, dwords=dwords, strings=strings)
        assert result["ct"] == CT
        assert result["why"] is None
        assert result["reads"] > 7  # the inline names were compared

    def test_different_name_is_an_ordinary_no_match_not_a_refusal(self, harness):
        dwords, strings = table()
        strings = [(va, ".?AVOther@@" if va == TD + 8 else text)
                   for va, text in strings]
        result = call(harness, dwords=dwords, strings=strings)
        assert result["ct"] == 0
        assert result["why"] is None

    def test_unnamed_catch_does_not_consult_a_type(self, harness):
        dwords, strings = table()
        result = call(harness, type=0, dwords=dwords, strings=strings)
        assert result["ct"] == 0
        assert result["why"] is None


class TestNullAndInvalidPointers:
    @pytest.mark.parametrize("info, reason", [
        (0, "null pointer in C++ ThrowInfo"),
        (BASE + 1, "misaligned pointer in C++ ThrowInfo"),
        (BASE + SPAN, "C++ ThrowInfo pointer outside guest image span"),
        (0xDEADBEE0, "C++ ThrowInfo pointer outside guest image span"),
    ])
    def test_info_is_validated_before_info_plus_12(self, harness, info, reason):
        result = call(harness, info=info)
        assert result["ct"] == 0
        assert result["why"] == reason
        assert result["reads"] == 0  # no dereference happened at all

    @pytest.mark.parametrize("arr, reason", [
        (0, "null pointer in C++ ThrowInfo"),
        (ARR + 1, "misaligned pointer in C++ ThrowInfo"),
        (BASE + SPAN + 0x40, "C++ ThrowInfo pointer outside guest image span"),
    ])
    def test_array_is_validated_before_its_count(self, harness, arr, reason):
        dwords, strings = table(arr=arr)
        result = call(harness, dwords=dwords, strings=strings)
        assert result["ct"] == 0
        assert result["why"] == reason
        assert result["reads"] == 2  # info, info+12; never the bad array

    @pytest.mark.parametrize("ct, reason", [
        (0, "null pointer in C++ ThrowInfo"),
        (CT + 1, "misaligned pointer in C++ ThrowInfo"),
        (BASE + SPAN + 0x40, "C++ ThrowInfo pointer outside guest image span"),
    ])
    def test_catchable_type_pointer_is_validated(self, harness, ct, reason):
        dwords, strings = table(ct=ct, td=0)
        # ARR+4 holds the hostile pointer; no writes at ct itself.
        dwords = [(va, value) for va, value in dwords
                  if va not in (ct, ct + 4)]
        result = call(harness, dwords=dwords, strings=strings)
        assert result["ct"] == 0
        assert result["why"] == reason
        assert result["reads"] == 5  # stops at the pointer slot, not at ct

    @pytest.mark.parametrize("td, reason", [
        (0, "null TypeDescriptor in C++ ThrowInfo"),
        (TD + 1, "misaligned pointer in C++ ThrowInfo"),
        (BASE + SPAN + 0x40, "C++ ThrowInfo pointer outside guest image span"),
    ])
    def test_type_descriptor_is_validated(self, harness, td, reason):
        dwords, strings = table(td=td)
        result = call(harness, dwords=dwords, strings=strings)
        assert result["ct"] == 0
        assert result["why"] == reason

    @pytest.mark.parametrize("type, reason", [
        (TYPE + 1, "misaligned pointer in C++ ThrowInfo"),
        (BASE + SPAN + 0x40, "C++ ThrowInfo pointer outside guest image span"),
    ])
    def test_wanted_type_pointer_is_validated(self, harness, type, reason):
        dwords, strings = table()
        result = call(harness, type=type, dwords=dwords, strings=strings)
        assert result["ct"] == 0
        assert result["why"] == reason
        assert result["reads"] == 3  # info, info+12, count; the bad type was never read

    def test_name_at_the_end_of_the_span_is_refused(self, harness):
        # td itself is a valid dword, but its inline name crosses the span.
        td = BASE + SPAN - 4
        dwords = [(INFO + 12, ARR), (ARR, 1), (ARR + 4, CT),
                  (CT, 0), (CT + 4, td), (td, 0)]
        result = call(harness, dwords=dwords)
        assert result["ct"] == 0
        assert result["why"] == "type name outside guest image span"


class TestHostileCounts:
    @pytest.mark.parametrize("count", [0xFFFFFFFF, 0x80000000, 4097])
    def test_negative_or_oversized_count_is_refused_without_iterating(
            self, harness, count):
        dwords, strings = table(count=count)
        result = call(harness, dwords=dwords, strings=strings)
        assert result["ct"] == 0
        assert result["why"] == "hostile C++ catchable count"
        assert result["reads"] == 3  # info, info+12, count; loop never ran

    def test_bound_is_the_source_constant(self):
        src = SOURCE.read_text(encoding="utf-8")
        match = re.search(r"#define\s+EH_MAX_CATCHABLE\s+(\d+)u\b", src)
        assert match, "no fixed catchable bound in the source"
        assert int(match.group(1)) == 4097 - 1

    def test_signed_count_check_is_in_the_source(self):
        block = validation_block(SOURCE.read_text(encoding="utf-8"))
        assert "n < 0" in block
        assert "(uint32_t)n > EH_MAX_CATCHABLE" in block
        assert block.count("= MEM32(") == 1  # only inside eh_guest_dword
        assert "MEM32(arr" not in block
        assert "MEM32(ct" not in block


class TestSourceWiring:
    def test_every_dword_read_goes_through_validated_span_checks(self):
        block = validation_block(SOURCE.read_text(encoding="utf-8"))
        assert "native32_in_guest(va)" in block
        assert "va & 3u" in block
        assert "if (!va)" in block
        assert block.count("strcmp(") == 1

    def test_refusal_reasons_are_reported_by_the_shim(self):
        src = SOURCE.read_text(encoding="utf-8")
        assert "const char** why" in src
        assert 'if (why) fprintf(stderr, "[eh32] C++ throw refused: %s\\n", why);' in src
        assert "if (type && !ct && why && *why) return 0;" in src
