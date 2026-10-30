/*
 * kernel32_atom.c - the Win32 local atom table: AddAtomA, FindAtomA,
 * GetAtomNameA, DeleteAtom and InitAtomTable.
 *
 * A 1990s engine interns its window-class names, property names and resource
 * keys as atoms; the ANSI variants are case-insensitive, so "Foo" and "FOO"
 * are the same atom. This is that table, for real: an atom is a stable 16-bit
 * value from the Win32 application range (0xC000 and up), interning is
 * case-insensitive, every AddAtomA bumps a reference count and DeleteAtom only
 * frees the entry when the count reaches zero. The values of live entries never
 * move, so an atom a game stored earlier still names the same string after
 * other entries have come and gone.
 *
 * The interned strings are copied into guest memory (hle_guest_strdup): a host
 * pointer never lands in guest space, and GetAtomNameA copies the string out
 * through hle_ptr into the caller's own buffer, refusing with
 * ERROR_INSUFFICIENT_BUFFER when that buffer is too small (rather than
 * truncating and reporting the truncated length, which would be a silent lie).
 *
 * The Global* atom calls (GlobalAddAtomA, GlobalFindAtomA, GlobalGetAtomNameA,
 * GlobalDeleteAtom) are NOT here: kernel32.c already answers them from its own
 * table, and it is registered first, so a second registration in this file
 * would never be reached. Local and global atoms are therefore two tables
 * here - which is exactly what they are on Windows, where AddAtom and
 * GlobalAddAtom keep separate namespaces and a string added with one is not
 * found by the other. What kernel32.c's global table does not do is fold case
 * or honour the reference count; that is a pre-existing gap in that file, left
 * alone here rather than papered over with a duplicate table.
 *
 * The shims run under the machine lock (every import does), so the table needs
 * no lock of its own.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include "win32hle.h"

#define ERROR_INVALID_PARAMETER    87u
#define ERROR_INVALID_HANDLE        6u
#define ERROR_INSUFFICIENT_BUFFER 122u
#define ERROR_NOT_ENOUGH_MEMORY     8u

/* The Win32 application-atom range: the first atom handed out is 0xC000. */
#define ATOM_BASE 0xC000u
#define ATOM_MAX  4096

/* A slot's name is the guest VA of the interned string, or 0 when the slot is
 * free. Slots are reused but never moved: a live atom's value is its index. */
typedef struct { uint32_t name; int refs; } atom_slot;
static atom_slot g_atom[ATOM_MAX];
static int g_atom_n;                    /* high-water mark of used slots */

static const char *atom_name(uint32_t a) {
    if (a < ATOM_BASE) return NULL;
    uint32_t i = a - ATOM_BASE;
    if (i >= (uint32_t)g_atom_n || !g_atom[i].name) return NULL;
    return (const char *)hle_ptr(g_atom[i].name);
}

/* The slot for s, taken or free, or NULL: the caller adds a reference. */
static atom_slot *atom_lookup(const char *s) {
    for (int i = 0; i < g_atom_n; i++)
        if (g_atom[i].name && !strcasecmp((const char *)hle_ptr(g_atom[i].name), s))
            return &g_atom[i];
    return NULL;
}

static uint32_t atom_add(const char *s) {
    if (!s || !*s) { hle_set_last_error(ERROR_INVALID_PARAMETER); return 0; }
    atom_slot *e = atom_lookup(s);
    if (e) { e->refs++; return ATOM_BASE + (uint32_t)(e - g_atom); }
    for (int i = 0; i < g_atom_n; i++)                  /* a freed slot comes back */
        if (!g_atom[i].name) e = &g_atom[i];
    if (!e) {
        if (g_atom_n >= ATOM_MAX) { hle_set_last_error(ERROR_NOT_ENOUGH_MEMORY); return 0; }
        e = &g_atom[g_atom_n++];
    }
    uint32_t va = hle_guest_strdup(s);                  /* the guest keeps the spelling it first used */
    if (!va) { hle_set_last_error(ERROR_NOT_ENOUGH_MEMORY); return 0; }
    e->name = va, e->refs = 1;
    return ATOM_BASE + (uint32_t)(e - g_atom);
}

/* AddAtomA(lpString): the atom for a string, interning it if new. */
static void k_AddAtomA(void) {
    RET(atom_add(ASTR(0)), 1);
}

/* FindAtomA(lpString): the atom for a string that was added, or 0. No
 * reference is taken: a FindAtomA does not keep the atom alive. */
static void k_FindAtomA(void) {
    const char *s = ASTR(0);
    if (s) {
        atom_slot *e = atom_lookup(s);
        if (e) RET(ATOM_BASE + (uint32_t)(e - g_atom), 1);
    }
    RET(0, 1);
}

/* GetAtomNameA(atom, buf, size): the string, into the caller's buffer, with the
 * length (not counting the NUL) returned. A bad atom, a missing buffer or one
 * too small for the string and its NUL all fail with 0 and the matching last
 * error. */
static void k_GetAtomNameA(void) {
    uint32_t a = A32(0), buf = A32(1);
    int size = (int)A32(2);
    const char *n = atom_name(a);
    if (!n) { hle_set_last_error(ERROR_INVALID_HANDLE); RET(0, 3); }
    if (!buf || size <= 0) { hle_set_last_error(ERROR_INVALID_PARAMETER); RET(0, 3); }
    size_t len = strlen(n);
    if ((uint64_t)len + 1 > (uint64_t)size) { hle_set_last_error(ERROR_INSUFFICIENT_BUFFER); RET(0, 3); }
    memcpy(hle_ptr(buf), n, len + 1);
    RET((uint32_t)len, 3);
}

/* DeleteAtom(atom): one reference fewer; the entry is freed at zero and the
 * call returns 0. An atom that is not ours (or already gone) comes back
 * unchanged, as Windows returns it. */
static void k_DeleteAtom(void) {
    uint32_t a = A32(0);
    if (a >= ATOM_BASE && (uint32_t)(a - ATOM_BASE) < (uint32_t)g_atom_n) {
        atom_slot *e = &g_atom[a - ATOM_BASE];
        if (e->name) {
            if (--e->refs <= 0) {
                hle_free(hle_ptr(e->name));
                e->name = 0, e->refs = 0;
            }
            RET(0, 1);
        }
    }
    RET(a, 1);
}

/* InitAtomTable(nSize): the size hint is accepted and ignored - this table
 * grows on demand - and TRUE is the truth: the table is ours and there is
 * nothing here that can fail. */
static void k_InitAtomTable(void) {
    RET(1, 1);
}

const win32hle_shim win32hle_kernel32_atom[] = {
    { "AddAtomA",      k_AddAtomA },
    { "FindAtomA",     k_FindAtomA },
    { "GetAtomNameA",  k_GetAtomNameA },
    { "DeleteAtom",    k_DeleteAtom },
    { "InitAtomTable", k_InitAtomTable },
    { 0, 0 }
};
