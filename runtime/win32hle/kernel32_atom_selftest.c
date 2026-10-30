/*
 * kernel32_atom_selftest.c - drives the Win32 local atom table from a
 * synthetic lifted guest: AddAtomA interns case-insensitively, FindAtomA
 * answers for a known string and 0 for an unknown one, GetAtomNameA copies the
 * first spelling out and refuses a buffer that is one byte too small with
 * ERROR_INSUFFICIENT_BUFFER, DeleteAtom reference-counts (the entry only goes
 * away at zero), live atoms keep their value, and InitAtomTable succeeds.
 * Also checks the documented separation from kernel32.c's Global* table.
 *
 * These shims are stdcall, so the callee pops its arguments: the helper below
 * does not adjust the guest stack after the call.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "win32hle.h"

#define ENTRY_VA 0x00401000u

/* kernel32_atom.c's shim table (win32hle.h declares it once the host is wired). */
extern const win32hle_shim win32hle_kernel32_atom[];

static int fails;
static void ok(const char *w, int c) { if (!c) { printf("FAIL %s\n", w); fails++; } }

/* RECOMP_ICALL publishes the arithmetic flags through these; a generated body
 * declares them itself, and scall() below is not one, so they live here. */
static uint32_t _flag_k = FK_NONE, _flag_a, _flag_b, _cf;

#define GUEST_PROLOGUE(va) \
    uint32_t _cf = 0; uint32_t _flag_k = FK_NONE; uint32_t _flag_a = 0, _flag_b = 0; RECOMP_ENTER(va); \
    (void)_cf, (void)_flag_k, (void)_flag_a, (void)_flag_b

/* Call a stdcall shim by name with guest-stack arguments. */
static uint32_t scall(const char *name, int nargs, const uint32_t *a) {
    uint32_t va = hle_resolve(name);
    if (!va) hle_fatal("selftest: no shim named %s", name);
    for (int i = nargs - 1; i >= 0; i--) PUSH32(esp, a[i]);
    RECOMP_ICALL(va);
    return eax;                                  /* the shim popped ret + its arguments */
}

/* ---- guest strings and buffers (VAs) ---- */
static uint32_t g_hello, g_hello_upper, g_mixed, g_mixed_upper, g_world, g_absent;
static uint32_t g_local_only, g_buf, g_buf2;

/* ---- what the guest observed ---- */
static uint32_t g_a1, g_a2, g_a3, g_find, g_absent_find, g_name_len, g_name_ok;
static uint32_t g_small_ret, g_small_err, g_exact_ret, g_bad_atom, g_del_ret;
static uint32_t g_still_there, g_free_find, g_free_name, g_world_atom, g_mixed_ok;
static uint32_t g_init_ret, g_del_bad, g_global_find, g_global_ret;
static uint32_t g_local_before, g_local_after;

static void guest(void) {
    GUEST_PROLOGUE(ENTRY_VA);
    uint32_t a[4];

    /* the same string, three spellings, is one atom */
    a[0] = g_hello;       g_a1 = scall("AddAtomA", 1, a);
    a[0] = g_hello_upper; g_a2 = scall("AddAtomA", 1, a);
    a[0] = g_hello;       g_a3 = scall("AddAtomA", 1, a);
    a[0] = g_hello_upper; g_find = scall("FindAtomA", 1, a);
    a[0] = g_absent;      g_absent_find = scall("FindAtomA", 1, a);

    /* the name comes back with the spelling that was added first */
    a[0] = g_a1, a[1] = g_buf, a[2] = 16;
    g_name_len = scall("GetAtomNameA", 3, a);
    g_name_ok = g_name_len == 5 && !strcmp((char *)hle_ptr(g_buf), "Hello");

    /* one byte too small fails with ERROR_INSUFFICIENT_BUFFER; exactly enough succeeds */
    a[0] = g_a1, a[1] = g_buf2, a[2] = 5;
    g_small_ret = scall("GetAtomNameA", 3, a);
    g_small_err = hle_get_last_error();
    a[0] = g_a1, a[1] = g_buf2, a[2] = 6;
    g_exact_ret = scall("GetAtomNameA", 3, a);
    a[0] = 0xBFFFu, a[1] = g_buf, a[2] = 16;         /* below the atom range */
    g_bad_atom = scall("GetAtomNameA", 3, a);

    /* a second string is a different atom, and the first keeps its value */
    a[0] = g_world; g_world_atom = scall("AddAtomA", 1, a);

    /* a different string, added in two spellings, keeps the first spelling */
    a[0] = g_mixed;       scall("AddAtomA", 1, a);
    a[0] = g_mixed_upper; uint32_t am = scall("AddAtomA", 1, a);
    a[0] = am, a[1] = g_buf, a[2] = 16;
    scall("GetAtomNameA", 3, a);
    g_mixed_ok = !strcmp((char *)hle_ptr(g_buf), "Mixed");

    /* the reference count: one delete leaves it alive (three adds), the rest free it */
    a[0] = g_a1; g_del_ret = scall("DeleteAtom", 1, a);
    a[0] = g_hello;
    g_still_there = scall("FindAtomA", 1, a);
    a[0] = g_a1; scall("DeleteAtom", 1, a);
    a[0] = g_a1; scall("DeleteAtom", 1, a);
    a[0] = g_hello;
    g_free_find = scall("FindAtomA", 1, a);
    a[0] = g_a1, a[1] = g_buf, a[2] = 16;
    g_free_name = scall("GetAtomNameA", 3, a);

    /* an atom that was never ours comes back unchanged */
    a[0] = 0x1234u; g_del_bad = scall("DeleteAtom", 1, a);

    /* the table is ours: the size hint is accepted */
    a[0] = 1024; g_init_ret = scall("InitAtomTable", 1, a);

    /* the documented separation: a local atom is not in kernel32.c's global table */
    a[0] = g_local_only; g_local_before = scall("FindAtomA", 1, a);
    a[0] = g_local_only; scall("AddAtomA", 1, a);
    a[0] = g_local_only; g_local_after = scall("FindAtomA", 1, a);
    a[0] = g_local_only; g_global_find = scall("GlobalFindAtomA", 1, a);
    a[0] = g_local_only; g_global_ret = scall("GlobalAddAtomA", 1, a);

    eax = 0;
}

const recomp_dispatch_entry_t recomp_dispatch_table[] = { { ENTRY_VA, guest } };
const uint32_t recomp_dispatch_count = 1;

int main(void) {
    g_hello = hle_guest_strdup("Hello");
    g_hello_upper = hle_guest_strdup("HELLO");
    g_mixed = hle_guest_strdup("Mixed");
    g_mixed_upper = hle_guest_strdup("MIXED");
    g_world = hle_guest_strdup("World");
    g_absent = hle_guest_strdup("nothing-here");
    g_local_only = hle_guest_strdup("local-only-string");
    g_buf = hle_va(hle_alloc(64));
    g_buf2 = hle_va(hle_alloc(16));

    win32hle_register(win32hle_kernel32);        /* for the Global* cross-check */
    win32hle_register(win32hle_kernel32_atom);

    int n = 0, bad = 0;
    for (const win32hle_shim *s = win32hle_kernel32_atom; s->name; s++) {
        n++;
        if (!hle_resolve(s->name)) { printf("FAIL unresolved shim %s\n", s->name); bad++; }
    }
    ok("every atom shim resolves", bad == 0 && n == 5);

    mach_enter();
    guest();
    mach_leave();

    ok("AddAtomA returns an atom in the 0xC000 range",
       g_a1 >= 0xC000u && g_a1 <= 0xFFFFu);
    ok("AddAtomA is case-insensitive and idempotent",
       g_a1 == g_a2 && g_a2 == g_a3 && g_find == g_a1);
    ok("FindAtomA returns 0 for a string never added", g_absent_find == 0);
    ok("GetAtomNameA copies the first spelling out", g_name_ok);
    ok("GetAtomNameA fails with ERROR_INSUFFICIENT_BUFFER (122) when short",
       g_small_ret == 0 && g_small_err == 122);
    ok("GetAtomNameA succeeds when the buffer just fits", g_exact_ret == 5);
    ok("GetAtomNameA returns 0 for a value below the atom range", g_bad_atom == 0);
    ok("a second string is a different atom", g_world_atom != 0 && g_world_atom != g_a1);
    ok("the first spelling wins for a later different spelling", g_mixed_ok);
    ok("DeleteAtom returns 0 and the atom survives while referenced",
       g_del_ret == 0 && g_still_there == g_a1);
    ok("the entry is freed when the count reaches zero",
       g_free_find == 0 && g_free_name == 0);
    ok("DeleteAtom on a value that is not an atom returns it unchanged", g_del_bad == 0x1234u);
    ok("InitAtomTable succeeds", g_init_ret == 1);
    ok("FindAtomA is 0 before a string is added and the atom after",
       g_local_before == 0 && g_local_after >= 0xC000u);
    ok("a local atom is not visible to GlobalFindAtomA (separate tables, as on Windows)",
       g_global_find == 0);
    ok("GlobalAddAtomA interns in kernel32.c's own table", g_global_ret >= 0xC000u);

    if (fails == 0) printf("kernel32_atom_selftest: all checks passed\n");
    return fails != 0;
}
