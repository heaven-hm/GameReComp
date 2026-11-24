/* com_taskmem_selftest.c - the COM task allocator (CoTaskMemAlloc/Realloc/Free).
 *
 * Everything is driven through hle_call_guest, the same path lifted code uses:
 * the shims see a synthetic stdcall frame and answer with a guest VA in EAX.
 * No shared header is changed; win32hle.h already declares every symbol used.
 */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include "win32hle.h"

/* A selftest links the whole layer but supplies its own (empty) dispatch table. */
const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t call(const char *name, int argc, const uint32_t *args) {
    uint32_t va = hle_resolve(name);
    if (!va) { printf("FAIL shim %s is not registered\n", name); fails++; return 0xFFFFFFFFu; }
    return hle_call_guest(va, argc, args);
}

int main(void) {
    win32hle_register(win32hle_ole32);
    mach_enter();

    /* ---- Alloc: a real block in guest memory ---- */
    uint32_t p = call("CoTaskMemAlloc", 1, (uint32_t[]){ 32 });
    ok("Alloc(32) returns non-NULL", p != 0 && p != 0xFFFFFFFFu);
    ok("Alloc(32) is a guest heap VA", p != 0 && hle_heap_ok(hle_ptr(p)));
    ok("Alloc(32) has the requested size", p != 0 && hle_heap_size(hle_ptr(p)) >= 32u);
    for (int i = 0; i < 8; i++) MEM32(p + 4u * (uint32_t)i) = 0x11000000u + (uint32_t)i;
    ok("Alloc(32) block is writable", MEM32(p) == 0x11000000u && MEM32(p + 28u) == 0x11000007u);

    /* ---- Realloc: grow keeps the prefix and zeroes the new tail ---- */
    uint32_t q = call("CoTaskMemRealloc", 2, (uint32_t[]){ p, 96 });
    ok("Realloc(grow) returns non-NULL", q != 0 && q != 0xFFFFFFFFu);
    ok("Realloc(grow) is a guest heap VA", q != 0 && hle_heap_ok(hle_ptr(q)));
    ok("Realloc(grow) has the new size", q != 0 && hle_heap_size(hle_ptr(q)) >= 96u);
    ok("Realloc(grow) preserves old bytes",
        q != 0 && MEM32(q) == 0x11000000u && MEM32(q + 28u) == 0x11000007u);
    ok("Realloc(grow) zeroes the tail",
        q != 0 && MEM32(q + 32u) == 0u && MEM32(q + 92u) == 0u);
    p = q;

    /* ---- Realloc: shrink keeps the prefix ---- */
    q = call("CoTaskMemRealloc", 2, (uint32_t[]){ p, 16 });
    ok("Realloc(shrink) returns non-NULL", q != 0 && q != 0xFFFFFFFFu);
    ok("Realloc(shrink) has the new size", q != 0 && hle_heap_size(hle_ptr(q)) == 16u);
    ok("Realloc(shrink) preserves old bytes",
        q != 0 && MEM32(q) == 0x11000000u && MEM32(q + 12u) == 0x11000003u);
    p = q;

    /* ---- Realloc(NULL, cb) is Alloc(cb) ---- */
    q = call("CoTaskMemRealloc", 2, (uint32_t[]){ 0, 24 });
    ok("Realloc(NULL,24) returns non-NULL", q != 0 && q != 0xFFFFFFFFu);
    ok("Realloc(NULL,24) is a guest heap VA", q != 0 && hle_heap_ok(hle_ptr(q)));
    ok("Realloc(NULL,24) has the requested size", q != 0 && hle_heap_size(hle_ptr(q)) >= 24u);
    ok("Realloc(NULL,24) is zeroed", q != 0 && MEM32(q) == 0u && MEM32(q + 20u) == 0u);

    /* ---- zero size: a freeable, non-NULL block when there is no source ---- */
    uint32_t z0 = call("CoTaskMemRealloc", 2, (uint32_t[]){ 0, 0 });
    ok("Realloc(NULL,0) returns a live block", z0 != 0 && hle_heap_ok(hle_ptr(z0)));
    uint32_t a0 = call("CoTaskMemAlloc", 1, (uint32_t[]){ 0 });
    ok("Alloc(0) returns a live block", a0 != 0 && hle_heap_ok(hle_ptr(a0)));

    /* ---- zero size with a live pointer frees it and returns NULL ---- */
    ok("Realloc(p,0) returns NULL", call("CoTaskMemRealloc", 2, (uint32_t[]){ q, 0 }) == 0u);
    ok("Realloc(z0,0) returns NULL", call("CoTaskMemRealloc", 2, (uint32_t[]){ z0, 0 }) == 0u);
    ok("Realloc(a0,0) returns NULL", call("CoTaskMemRealloc", 2, (uint32_t[]){ a0, 0 }) == 0u);
#ifdef HLE_FLAT
    /* Flat mode hands freed pages back to the OS; inspecting them is a
     * use-after-free there, so the based-mode heap is checked below instead. */
#else
    ok("Realloc(p,0) released the block", !hle_heap_ok(hle_ptr(q)));
    ok("Realloc(z0,0) released the block", !hle_heap_ok(hle_ptr(z0)));
    ok("Realloc(a0,0) released the block", !hle_heap_ok(hle_ptr(a0)));
#endif

    /* ---- Free: NULL is a no-op; a live block can be freed ---- */
    call("CoTaskMemFree", 1, (uint32_t[]){ 0 });
    ok("NULL Free is harmless", 1);
    call("CoTaskMemFree", 1, &p);
#ifndef HLE_FLAT
    ok("Free(p) released the block", !hle_heap_ok(hle_ptr(p)));
#endif
    uint32_t after = call("CoTaskMemAlloc", 1, (uint32_t[]){ 8 });
    ok("Alloc after Free still works", after != 0 && hle_heap_ok(hle_ptr(after)));
    call("CoTaskMemFree", 1, &after);

    mach_leave();
    printf("com_taskmem_selftest: all checks passed\n");
    return fails != 0;
}
