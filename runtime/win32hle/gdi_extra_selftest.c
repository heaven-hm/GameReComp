/*
 * gdi_extra_selftest.c - CreateBitmapIndirect, the bitmap-object API the GDI
 * subset was missing, driven through hle_call_guest exactly as a game calls
 * an import. It checks three things that a fake shim would get wrong:
 *
 *   - the handle is a real GDI object: GetObjectA/SelectObject/DeleteObject in
 *     gdidc.c see it, because it comes from the shared object table;
 *   - the pixels are real storage: a DC can SetPixel/GetPixel through it, and
 *     a caller-supplied guest buffer is really written to;
 *   - bad geometry and invalid handles fail honestly (0 / no object), rather
 *     than returning a phantom success handle.
 */
#include <stdint.h>
#include <stdio.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static int fails;
static void ok(const char *what, int cond) { if (!cond) { printf("FAIL %s\n", what); fails++; } }
static uint32_t call(const char *name, int n, const uint32_t *a) {
    uint32_t va = hle_resolve(name);
    if (!va) { printf("FAIL unresolved %s\n", name); fails++; return 0; }
    return hle_call_guest(va, n, a);
}

/* A 24-byte BITMAP in guest memory: type, w, h, widthBytes, planes, bpp, bits. */
static uint32_t put_bitmap(int w, int h, int planes, int bpp, uint32_t bits) {
    uint32_t va = hle_va(hle_alloc(24));
    MEM32(va + 0) = 0;
    MEM32(va + 4) = (uint32_t)w;
    MEM32(va + 8) = (uint32_t)h;
    MEM32(va + 12) = (uint32_t)(w * (bpp > 0 ? bpp / 8 : 1));
    MEM16(va + 16) = (uint16_t)planes;
    MEM16(va + 18) = (uint16_t)bpp;
    MEM32(va + 20) = bits;
    return va;
}

int main(void) {
    win32hle_register(win32hle_gdi32);
    win32hle_register(win32hle_gdidc);              /* GetObjectA/SelectObject/DeleteObject */
    mach_enter();

    /* ---- honest failures: no struct, impossible geometry, oversized ---- */
    ok("NULL lpbm -> 0", call("CreateBitmapIndirect", 1, (uint32_t[]){ 0 }) == 0);
    uint32_t badw  = put_bitmap(0, 2, 1, 32, 0);
    uint32_t badh  = put_bitmap(2, -1, 1, 32, 0);
    uint32_t badpl = put_bitmap(2, 2, 2, 32, 0);
    uint32_t badbp = put_bitmap(2, 2, 1, 7, 0);
    uint32_t huge  = put_bitmap(0x10000, 0x10000, 1, 32, 0);
    ok("zero width -> 0", call("CreateBitmapIndirect", 1, &badw) == 0);
    ok("negative height -> 0", call("CreateBitmapIndirect", 1, &badh) == 0);
    ok("planes != 1 -> 0", call("CreateBitmapIndirect", 1, &badpl) == 0);
    ok("unsupported bpp -> 0", call("CreateBitmapIndirect", 1, &badbp) == 0);
    ok("absurd size -> 0", call("CreateBitmapIndirect", 1, &huge) == 0);

    /* ---- a real bitmap object, with storage we own ---- */
    uint32_t bm = put_bitmap(2, 2, 1, 32, 0);
    uint32_t hbmp = call("CreateBitmapIndirect", 1, &bm);
    ok("CreateBitmapIndirect returns a handle", hbmp != 0);
    uint32_t out = hle_va(hle_alloc(24));
    ok("GetObjectA size query -> BITMAP (24)", call("GetObjectA", 3, (uint32_t[]){ hbmp, 0, 0 }) == 24);
    ok("GetObjectA fills the BITMAP",
        call("GetObjectA", 3, (uint32_t[]){ hbmp, 24, out }) == 24 &&
        MEM32(out + 4) == 2 && MEM32(out + 8) == 2 &&
        MEM16(out + 16) == 1 && MEM16(out + 18) == 32 && MEM32(out + 20) == 0);

    uint32_t memdc = call("CreateCompatibleDC", 1, (uint32_t[]){ 0 });
    ok("memory DC", memdc != 0);
    ok("SelectObject returns a previous object", call("SelectObject", 2, (uint32_t[]){ memdc, hbmp }) != 0);
    ok("SetPixel 32bpp", call("SetPixel", 4, (uint32_t[]){ memdc, 1, 1, 0x00FF8000u }) == 0x00FF8000u);
    /* SetPixel takes a COLORREF (0x00BBGGRR); a 32bpp surface stores RRGGBB,
     * so 0x00FF8000 (green=0x80, blue=0xFF) lands as 0x000080FF. */
    ok("GetPixel reads real pixels", call("GetPixel", 3, (uint32_t[]){ memdc, 1, 1 }) == 0x000080FFu);
    ok("untouched pixel is zero", call("GetPixel", 3, (uint32_t[]){ memdc, 0, 0 }) == 0);

    /* ---- a caller-supplied guest buffer is really used ---- */
    uint32_t guestbits = hle_va(hle_alloc(2 * 2 * 4));
    uint32_t bm2 = put_bitmap(2, 2, 1, 32, guestbits);
    uint32_t hbmp2 = call("CreateBitmapIndirect", 1, &bm2);
    ok("CreateBitmapIndirect with caller bits", hbmp2 != 0);
    uint32_t out2 = hle_va(hle_alloc(24));
    ok("GetObjectA reports caller bmBits",
        call("GetObjectA", 3, (uint32_t[]){ hbmp2, 24, out2 }) == 24 && MEM32(out2 + 20) == guestbits);
    uint32_t memdc2 = call("CreateCompatibleDC", 1, (uint32_t[]){ 0 });
    ok("second memory DC", memdc2 != 0);
    call("SelectObject", 2, (uint32_t[]){ memdc2, hbmp2 });
    call("SetPixel", 4, (uint32_t[]){ memdc2, 0, 0, 0x11223344u });
    ok("SetPixel lands in the caller's buffer", MEM32(guestbits) == 0x00443322u);

    /* ---- honest invalid-handle errors ---- */
    ok("GetObjectA(NULL) -> 0", call("GetObjectA", 3, (uint32_t[]){ 0, 24, out }) == 0);
    ok("GetObjectA(bogus) -> 0", call("GetObjectA", 3, (uint32_t[]){ 0x0DB1FFFFu, 24, out }) == 0);
    ok("DeleteObject(bitmap) -> 1", call("DeleteObject", 1, &hbmp) == 1);
    ok("GetObjectA(deleted) -> 0", call("GetObjectA", 3, (uint32_t[]){ hbmp, 24, out }) == 0);
    ok("DeleteObject(caller-bits bitmap) -> 1", call("DeleteObject", 1, &hbmp2) == 1);
    ok("GetObjectA(deleted2) -> 0", call("GetObjectA", 3, (uint32_t[]){ hbmp2, 24, out }) == 0);
    ok("DeleteDC -> 1", call("DeleteDC", 1, &memdc) == 1);
    ok("DeleteDC2 -> 1", call("DeleteDC", 1, &memdc2) == 1);

    mach_leave();
    printf("%s: gdi_extra_selftest: %d failure(s)\n", fails ? "FAIL" : "ok", fails);
    return fails != 0;
}
