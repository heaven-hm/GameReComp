/* ole32_extra_selftest.c - guest-ABI checks for the OLE COM helper objects
 * that block GameMaker 7 titles: IBindCtx, ILockBytes on HGLOBAL and
 * OleGetIconOfClass. Every call goes through hle_call_guest on the shim's
 * import VA, and every COM method through the object's guest vtable, so this
 * exercises exactly what lifted code does (including based-host addressing). */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

#define S_OKVAL        0u
#define S_FALSEVAL     1u
#define E_NOINTERFACE  0x80004002u
#define E_POINTERVAL   0x80004003u
#define E_INVALIDARG   0x80070057u
#define E_FAILVAL      0x80004005u
#define E_NOTIMPLVAL   0x80004001u
#define MK_E_NOTBOUND  0x800401E9u
#define STG_E_INVALIDFUNCTION 0x80030001u

/* Out parameters must be guest VAs: on a based host, hle_va() of a host stack
 * address is fatal. These dwords live in the guest heap for the whole test. */
static uint32_t p_out, p_qi, p_n, p_got, p_iface;
#define out    MEM32(p_out)
#define qi_out MEM32(p_qi)
#define n      MEM32(p_n)
#define got    MEM32(p_got)
#define iface  MEM32(p_iface)

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { fprintf(stderr, "FAIL %s\n", what); fails++; }
}
static uint32_t call(const char *name, int argc, const uint32_t *a) {
    return hle_call_guest(hle_resolve(name), argc, a);
}
/* COM method call: slot is the vtable index (0-based, not counting `this`). */
static uint32_t vc(uint32_t obj, int slot, int nargs, const uint32_t *a) {
    uint32_t args[8] = { obj };
    for (int i = 0; i < nargs; i++) args[1 + i] = a[i];
    return hle_call_guest(MEM32(MEM32(obj) + 4u * (uint32_t)slot), nargs + 1, args);
}
static uint32_t guest_copy(const void *p, uint32_t len) {
    uint32_t va = hle_va(hle_alloc(len));
    memcpy(hle_ptr(va), p, len);
    return va;
}
static uint32_t guest_wstr(const wchar_t *s) {
    uint32_t len = 0;
    while (s[len]) len++;
    uint32_t va = hle_va(hle_alloc(2u * (len + 1u)));
    for (uint32_t i = 0; i <= len; i++) MEM16(va + 2u * i) = (uint16_t)s[i];
    return va;
}
static uint32_t iid_va(uint32_t data1) {
    uint8_t g[16] = { 0 };
    memcpy(g, &data1, 4);
    g[8] = 0xC0; g[15] = 0x46;
    return guest_copy(g, 16);
}
/* A minimal guest-served COM object to put in a bind context. */
static uint32_t dummy_obj(void) {
    static const win32hle_shim methods[] = {
        { "OleExtra::QueryInterface", hle_com_QueryInterface },
        { "OleExtra::AddRef", hle_com_AddRef },
        { "OleExtra::Release", hle_com_Release }, { 0, 0 }
    };
    return hle_com_new(hle_com_vtable(methods), 8);
}

int main(void) {
    win32hle_register(win32hle_ole32);
    mach_enter();
    p_out = hle_va(hle_alloc(4)); p_qi = hle_va(hle_alloc(4));
    p_n = hle_va(hle_alloc(4));   p_got = hle_va(hle_alloc(4));
    p_iface = hle_va(hle_alloc(4));

    ok("CreateBindCtx registered", hle_resolve("CreateBindCtx") != 0);
    ok("CreateILockBytesOnHGlobal registered", hle_resolve("CreateILockBytesOnHGlobal") != 0);
    ok("OleGetIconOfClass registered", hle_resolve("OleGetIconOfClass") != 0);

    /* ---- IBindCtx ---- */
    ok("CreateBindCtx(0) -> S_OK",
       call("CreateBindCtx", 2, (uint32_t[]){ 0, p_out }) == S_OKVAL);
    ok("bind context object returned", out != 0 && MEM32(out + 4) == 1);
    uint32_t ctx = out;
    uint32_t iid_unknown = iid_va(0), iid_bindctx = iid_va(0x0Eu), iid_bad = iid_va(0x99u);

    ok("QI(IID_IBindCtx)", vc(ctx, 0, 2, (uint32_t[]){ iid_bindctx, p_qi }) == S_OKVAL && qi_out == ctx);
    vc(ctx, 2, 0, NULL);                                               /* undo AddRef */
    ok("QI(IUnknown)", vc(ctx, 0, 2, (uint32_t[]){ iid_unknown, p_qi }) == S_OKVAL && qi_out == ctx);
    vc(ctx, 2, 0, NULL);
    qi_out = 0xDEADBEEFu;
    ok("QI(unknown IID)", vc(ctx, 0, 2, (uint32_t[]){ iid_bad, p_qi }) == E_NOINTERFACE && qi_out == 0);

    uint32_t opts = hle_va(hle_alloc(40));
    MEM32(opts) = 16;
    ok("GetBindOptions defaults", vc(ctx, 7, 1, (uint32_t[]){ opts }) == S_OKVAL);
    ok("  cbStruct clamped to caller size", MEM32(opts) == 16);
    ok("  grfFlags = 0", MEM32(opts + 4) == 0);
    ok("  grfMode = STGM_READWRITE", MEM32(opts + 8) == 2);
    ok("  deadline = 0", MEM32(opts + 12) == 0);
    MEM32(opts) = 16, MEM32(opts + 4) = 0x10, MEM32(opts + 8) = 0, MEM32(opts + 12) = 123;
    ok("SetBindOptions", vc(ctx, 6, 1, (uint32_t[]){ opts }) == S_OKVAL);
    MEM32(opts) = 16;
    vc(ctx, 7, 1, (uint32_t[]){ opts });
    ok("Set/Get round trip", MEM32(opts + 4) == 0x10 && MEM32(opts + 12) == 123);
    MEM32(opts) = 64;
    ok("oversized BIND_OPTS refused", vc(ctx, 6, 1, (uint32_t[]){ opts }) == E_INVALIDARG);

    uint32_t key = guest_wstr(L"abc"), other = guest_wstr(L"xyz");
    uint32_t dummy = dummy_obj();
    ok("dummy object starts at 1 ref", MEM32(dummy + 4) == 1);
    ok("RegisterObjectParam", vc(ctx, 9, 2, (uint32_t[]){ key, dummy }) == S_OKVAL && MEM32(dummy + 4) == 2);
    ok("GetObjectParam", vc(ctx, 10, 2, (uint32_t[]){ key, p_got }) == S_OKVAL && got == dummy && MEM32(dummy + 4) == 3);
    vc(dummy, 2, 0, NULL);
    got = 0xDEADBEEFu;
    ok("unknown key -> E_FAIL, out zeroed", vc(ctx, 10, 2, (uint32_t[]){ other, p_got }) == E_FAILVAL && got == 0);
    ok("RevokeObjectParam", vc(ctx, 12, 1, (uint32_t[]){ key }) == S_OKVAL && MEM32(dummy + 4) == 1);
    ok("revoke missing key -> E_FAIL", vc(ctx, 12, 1, (uint32_t[]){ key }) == E_FAILVAL);
    ok("RegisterObjectBound", vc(ctx, 3, 1, (uint32_t[]){ dummy }) == S_OKVAL && MEM32(dummy + 4) == 2);
    ok("RevokeObjectBound", vc(ctx, 4, 1, (uint32_t[]){ dummy }) == S_OKVAL && MEM32(dummy + 4) == 1);
    ok("revoke unbound -> MK_E_NOTBOUND", vc(ctx, 4, 1, (uint32_t[]){ dummy }) == MK_E_NOTBOUND);
    iface = 1;
    ok("EnumObjectParam -> E_NOTIMPL", vc(ctx, 11, 1, (uint32_t[]){ p_iface }) == E_NOTIMPLVAL && iface == 0);
    iface = 1;
    ok("GetRunningObjectTable -> E_NOTIMPL", vc(ctx, 8, 1, (uint32_t[]){ p_iface }) == E_NOTIMPLVAL && iface == 0);
    ok("ReleaseBoundObjects empty -> S_OK", vc(ctx, 5, 0, NULL) == S_OKVAL);
    ok("final bind context Release -> 0", vc(ctx, 2, 0, NULL) == 0);
    vc(dummy, 2, 0, NULL);

    out = 0xDEADBEEFu;
    ok("CreateBindCtx(reserved!=0) -> E_INVALIDARG",
       call("CreateBindCtx", 2, (uint32_t[]){ 1, p_out }) == E_INVALIDARG && out == 0);
    ok("CreateBindCtx(NULL out) -> E_INVALIDARG",
       call("CreateBindCtx", 2, (uint32_t[]){ 0, 0 }) == E_INVALIDARG);

    /* ---- ILockBytes ---- */
    ok("CreateILockBytesOnHGlobal(NULL) -> S_OK",
       call("CreateILockBytesOnHGlobal", 3, (uint32_t[]){ 0, 1, p_out }) == S_OKVAL);
    ok("lock bytes object returned", out != 0 && MEM32(out + 4) == 1);
    uint32_t lb = out;
    ok("QI(IID_ILockBytes)", vc(lb, 0, 2, (uint32_t[]){ iid_va(0x0Au), p_qi }) == S_OKVAL && qi_out == lb);
    vc(lb, 2, 0, NULL);

    uint32_t src = guest_copy("HELLO", 5), src2 = guest_copy("WORLD", 5);
    uint32_t dst = hle_va(hle_alloc(32));
    ok("WriteAt(0)", vc(lb, 4, 5, (uint32_t[]){ 0, 0, src, 5, p_n }) == S_OKVAL && n == 5);
    ok("WriteAt(8)", vc(lb, 4, 5, (uint32_t[]){ 8, 0, src2, 5, p_n }) == S_OKVAL && n == 5);
    ok("ReadAt(0,13)", vc(lb, 3, 5, (uint32_t[]){ 0, 0, dst, 13, p_n }) == S_OKVAL && n == 13);
    ok("  gap is zero-filled", memcmp(hle_ptr(dst), "HELLO\0\0\0WORLD", 13) == 0);
    ok("ReadAt(11,5) -> S_FALSE, 2 bytes",
       vc(lb, 3, 5, (uint32_t[]){ 11, 0, dst, 5, p_n }) == S_FALSEVAL && n == 2);
    ok("SetSize(4)", vc(lb, 6, 2, (uint32_t[]){ 4, 0 }) == S_OKVAL);
    uint32_t stat = hle_va(hle_alloc(80));
    ok("Stat after SetSize(4)", vc(lb, 9, 2, (uint32_t[]){ stat, 0 }) == S_OKVAL);
    ok("  STGTY_LOCKBYTES", MEM32(stat + 4) == 3);
    ok("  cbSize = 4", MEM32(stat + 8) == 4);
    ok("  grfMode = STGM_READWRITE", MEM32(stat + 40) == 2);
    ok("  no region locking", MEM32(stat + 44) == 0);
    ok("SetSize(20)", vc(lb, 6, 2, (uint32_t[]){ 20, 0 }) == S_OKVAL);
    vc(lb, 9, 2, (uint32_t[]){ stat, 0 });
    ok("  cbSize = 20", MEM32(stat + 8) == 20);
    ok("Flush", vc(lb, 5, 0, NULL) == S_OKVAL);
    ok("LockRegion refused", vc(lb, 7, 5, (uint32_t[]){ 0, 0, 4, 0, 1 }) == STG_E_INVALIDFUNCTION);
    ok("UnlockRegion refused", vc(lb, 8, 5, (uint32_t[]){ 0, 0, 4, 0, 1 }) == STG_E_INVALIDFUNCTION);
    ok("Stat(NULL) -> E_POINTER", vc(lb, 9, 2, (uint32_t[]){ 0, 0 }) == E_POINTERVAL);
    ok("final lock bytes Release -> 0", vc(lb, 2, 0, NULL) == 0);
    out = 0xDEADBEEFu;
    ok("CreateILockBytesOnHGlobal(NULL out) -> E_INVALIDARG",
       call("CreateILockBytesOnHGlobal", 3, (uint32_t[]){ 0, 1, 0 }) == E_INVALIDARG);

    /* ---- OleGetIconOfClass: no icon registry in a headless host ---- */
    uint32_t clsid = guest_copy("0123456789abcdef", 16);
    ok("OleGetIconOfClass -> NULL", call("OleGetIconOfClass", 3, (uint32_t[]){ clsid, 0, 0 }) == 0);
    ok("OleGetIconOfClass(NULL) -> NULL", call("OleGetIconOfClass", 3, (uint32_t[]){ 0, 0, 0 }) == 0);

    mach_leave();
    fprintf(stderr, "ole32_extra_selftest: %s\n", fails ? "FAILED" : "all checks passed");
    return fails != 0;
}
