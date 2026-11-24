/*
 * gdi_more_selftest.c - three genuinely missing GDI text/brush APIs now in
 * gdidc.c, driven through hle_call_guest exactly as a game calls an import.
 *
 *   - GetObjectW: a wide object query. It writes real guest memory: a
 *     LOGBRUSH for a brush, a LOGFONTW with a UTF-16 face name for a font,
 *     and it reports a packed-DIB brush's lbStyle/lbHatch. Bad handles and
 *     short buffers get honest answers (0 / truncated length).
 *   - GetTextExtentExPointW: wide text measurement that really fills the
 *     guest's nFit array, per-glyph dx array and SIZE; bad counts fail (0)
 *     without touching the outputs.
 *   - CreateDIBPatternBrushPt: a brush built from a packed DIB in guest
 *     memory. The pixels are copied into the brush object (so later guest
 *     writes cannot change it), GetObjectW reports it, FillRect tiles it,
 *     and malformed DIBs fail with 0 instead of a phantom handle.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
static uint32_t call(const char *name, int argc, const uint32_t *args) {
    uint32_t fn = hle_resolve(name);
    if (!fn) { printf("FAIL unresolved %s\n", name); fails++; return 0; }
    return hle_call_guest(fn, argc, args);
}
static uint32_t wide(const char *s) {                     /* guest UTF-16 string */
    size_t n = strlen(s) + 1, va = hle_va(hle_alloc((uint32_t)(n * 2u)));
    uint16_t *w = (uint16_t *)hle_ptr(va);
    for (size_t i = 0; i < n; i++) w[i] = (uint16_t)(unsigned char)s[i];
    return va;
}
static uint32_t guest_bytes(const void *src, uint32_t n) {
    uint32_t va = hle_va(hle_alloc(n));
    memcpy(hle_ptr(va), src, n);
    return va;
}

/* A 2x2, bottom-up, 32bpp packed DIB. Guest row 0 is the bottom row. */
static uint32_t packed_dib(uint32_t *bits_out) {
    uint32_t dib = hle_va(hle_alloc(40 + 16));
    memset(hle_ptr(dib), 0, 40 + 16);
    MEM32(dib + 0) = 40; MEM32(dib + 4) = 2; MEM32(dib + 8) = 2;
    MEM16(dib + 12) = 1; MEM16(dib + 14) = 32; MEM32(dib + 16) = 0;
    uint32_t bits = dib + 40;                            /* 0x00RRGGBB */
    MEM32(bits + 0)  = 0x000000FFu;                      /* bottom-left: blue  */
    MEM32(bits + 4)  = 0x00FFFFFFu;                      /* bottom-right: white */
    MEM32(bits + 8)  = 0x00FF0000u;                      /* top-left: red      */
    MEM32(bits + 12) = 0x0000FF00u;                      /* top-right: green   */
    if (bits_out) *bits_out = bits;
    return dib;
}

int main(void) {
    win32hle_register(win32hle_gdidc);
    mach_enter();

    ok("GetObjectW registered", hle_resolve("GetObjectW") != 0);
    ok("GetTextExtentExPointW registered", hle_resolve("GetTextExtentExPointW") != 0);
    ok("CreateDIBPatternBrushPt registered", hle_resolve("CreateDIBPatternBrushPt") != 0);

    /* ---- GetObjectW: a real brush object's LOGBRUSH ---- */
    uint32_t cr = 0x00123456u;
    uint32_t hb = call("CreateSolidBrush", 1, &cr);
    ok("CreateSolidBrush returns a handle", hb != 0);
    uint32_t lb = hle_va(hle_alloc(64));
    memset(hle_ptr(lb), 0xCC, 64);
    ok("GetObjectW size probe -> LOGBRUSH (12)", call("GetObjectW", 3, (uint32_t[]){ hb, 0, 0 }) == 12);
    ok("GetObjectW fills LOGBRUSH",
        call("GetObjectW", 3, (uint32_t[]){ hb, 12, lb }) == 12 &&
        MEM32(lb) == 0 && MEM32(lb + 4) == cr && MEM32(lb + 8) == 0);
    ok("GetObjectW short buffer truncates", call("GetObjectW", 3, (uint32_t[]){ hb, 4, lb }) == 4 && MEM32(lb) == 0);
    ok("GetObjectW(NULL buffer) -> size", call("GetObjectW", 3, (uint32_t[]){ hb, 12, 0 }) == 12);
    ok("GetObjectW(NULL handle) -> 0", call("GetObjectW", 3, (uint32_t[]){ 0, 12, lb }) == 0);
    ok("GetObjectW(bogus handle) -> 0", call("GetObjectW", 3, (uint32_t[]){ 0x0DB1FFFFu, 12, lb }) == 0);

    /* ---- GetObjectW: a font's LOGFONTW, with a UTF-16 face name ---- */
    uint32_t lf = hle_va(hle_alloc(60));
    memset(hle_ptr(lf), 0, 60);
    MEM32(lf) = (uint32_t)-16;                           /* lfHeight */
    MEM32(lf + 16) = 700;                                /* lfWeight */
    MEM8(lf + 20) = 1;                                   /* lfItalic */
    memcpy(hle_ptr(lf + 28), "R8 Test Face", 13);        /* lfFaceName */
    uint32_t hf = call("CreateFontIndirectA", 1, &lf);
    ok("CreateFontIndirectA returns a font", hf != 0);
    ok("GetObjectW size probe -> LOGFONTW (92)", call("GetObjectW", 3, (uint32_t[]){ hf, 0, 0 }) == 92);
    uint32_t lfw = hle_va(hle_alloc(96));
    memset(hle_ptr(lfw), 0, 96);
    /* GetObjectA/W report the requested em with the opposite sign (−16 in,
     * 16 out), the layer's existing LOGFONT convention; keep asserting it so
     * the refactor cannot change that contract. */
    ok("GetObjectW fills LOGFONTW metrics",
        call("GetObjectW", 3, (uint32_t[]){ hf, 92, lfw }) == 92 &&
        MEM32(lfw) == 16 && MEM32(lfw + 16) == 700 && MEM8(lfw + 20) == 1);
    const char *face = "R8 Test Face";
    const uint16_t *uf = (const uint16_t *)hle_ptr(lfw + 28);
    int face_ok = 1;
    for (size_t i = 0; i < strlen(face); i++) if (uf[i] != (uint16_t)(unsigned char)face[i]) face_ok = 0;
    if (uf[strlen(face)] != 0) face_ok = 0;
    ok("GetObjectW writes a UTF-16 face name", face_ok);
    uint32_t lfa = hle_va(hle_alloc(60));
    memset(hle_ptr(lfa), 0, 60);
    ok("GetObjectA still reports a 60-byte LOGFONTA",
        call("GetObjectA", 3, (uint32_t[]){ hf, 0, 0 }) == 60 &&
        call("GetObjectA", 3, (uint32_t[]){ hf, 60, lfa }) == 60 &&
        strcmp((const char *)hle_ptr(lfa + 28), face) == 0);

    /* ---- GetTextExtentExPointW: real fit/dx/SIZE outputs ---- */
    uint32_t dc = call("CreateCompatibleDC", 1, (uint32_t[]){ 0 });
    ok("memory DC for measurement", dc != 0);
    uint32_t ws = wide("abcdef");
    uint32_t fit = hle_va(hle_alloc(4)), dx = hle_va(hle_alloc(24)), sz = hle_va(hle_alloc(8));
    memset(hle_ptr(fit), 0, 4); memset(hle_ptr(dx), 0, 24); memset(hle_ptr(sz), 0, 8);
    ok("GetTextExtentExPointW succeeds",
        call("GetTextExtentExPointW", 7, (uint32_t[]){ dc, ws, 6, 10000, fit, dx, sz }) == 1);
    ok("nFit is the whole string", MEM32(fit) == 6);
    ok("lpDx advances are cumulative",
        MEM32(dx) > 0 && MEM32(dx + 4) > MEM32(dx) && MEM32(dx + 8) > MEM32(dx + 4) &&
        MEM32(dx + 12) > MEM32(dx + 8) && MEM32(dx + 16) > MEM32(dx + 12) &&
        MEM32(dx + 20) > MEM32(dx + 16));
    ok("SIZE.cx is the total advance, SIZE.cy positive",
        MEM32(sz) == MEM32(dx + 20) && MEM32(sz + 4) > 0);
    uint32_t a1 = MEM32(dx);
    MEM32(fit) = 0xDEADBEEFu;
    ok("GetTextExtentExPointW respects nMaxExtent",
        call("GetTextExtentExPointW", 7, (uint32_t[]){ dc, ws, 6, a1 > 1 ? a1 - 1 : 0, fit, dx, sz }) == 1 &&
        MEM32(fit) < 6);
    MEM32(fit) = 0x12345678u;
    ok("GetTextExtentExPointW(NULL text) -> 0",
        call("GetTextExtentExPointW", 7, (uint32_t[]){ dc, 0, 6, 10000, fit, dx, sz }) == 0);
    ok("GetTextExtentExPointW(negative n) -> 0",
        call("GetTextExtentExPointW", 7, (uint32_t[]){ dc, ws, (uint32_t)-1, 10000, fit, dx, sz }) == 0);
    ok("failed calls leave the outputs untouched", MEM32(fit) == 0x12345678u);
    ok("GetTextExtentExPointW(NULL outputs) still measures",
        call("GetTextExtentExPointW", 7, (uint32_t[]){ dc, ws, 6, 10000, 0, 0, 0 }) == 1);

    /* ---- CreateDIBPatternBrushPt: a real, copied, tiled brush ---- */
    uint32_t bits;
    uint32_t dib = packed_dib(&bits);
    uint32_t hpat = call("CreateDIBPatternBrushPt", 2, (uint32_t[]){ dib, 0 });
    ok("CreateDIBPatternBrushPt returns a handle", hpat != 0);
    ok("pattern brush is a DIBPATTERNPT LOGBRUSH",
        call("GetObjectW", 3, (uint32_t[]){ hpat, 0, 0 }) == 12 &&
        call("GetObjectW", 3, (uint32_t[]){ hpat, 12, lb }) == 12 &&
        MEM32(lb) == 6 && MEM32(lb + 4) == 0 && MEM32(lb + 8) == dib);

    uint32_t bmp = call("CreateCompatibleBitmap", 3, (uint32_t[]){ 0, 4, 4 });
    uint32_t pdc = call("CreateCompatibleDC", 1, (uint32_t[]){ 0 });
    ok("bitmap and DC for FillRect", bmp != 0 && pdc != 0);
    call("SelectObject", 2, (uint32_t[]){ pdc, bmp });
    uint32_t rc = hle_va(hle_alloc(16));
    MEM32(rc) = 0; MEM32(rc + 4) = 0; MEM32(rc + 8) = 4; MEM32(rc + 12) = 4;
    ok("FillRect with a DIB pattern", call("FillRect", 3, (uint32_t[]){ pdc, rc, hpat }) == 1);
    ok("pattern top-left is red", call("GetPixel", 3, (uint32_t[]){ pdc, 0, 0 }) == 0x00FF0000u);
    ok("pattern top-right is green", call("GetPixel", 3, (uint32_t[]){ pdc, 1, 0 }) == 0x0000FF00u);
    ok("pattern bottom-left is blue", call("GetPixel", 3, (uint32_t[]){ pdc, 0, 1 }) == 0x000000FFu);
    ok("pattern tiles at (2,2)", call("GetPixel", 3, (uint32_t[]){ pdc, 2, 2 }) == 0x00FF0000u);
    ok("pattern tiles at (3,3)", call("GetPixel", 3, (uint32_t[]){ pdc, 3, 3 }) == 0x00FFFFFFu);

    /* The brush owns its pixels: changing the guest DIB must not change it. */
    MEM32(bits) = 0x00000000u;                           /* blue -> black */
    uint32_t bmp2 = call("CreateCompatibleBitmap", 3, (uint32_t[]){ 0, 2, 2 });
    uint32_t pdc2 = call("CreateCompatibleDC", 1, (uint32_t[]){ 0 });
    call("SelectObject", 2, (uint32_t[]){ pdc2, bmp2 });
    MEM32(rc + 8) = 2; MEM32(rc + 12) = 2;
    call("FillRect", 3, (uint32_t[]){ pdc2, rc, hpat });
    ok("the brush keeps its own copy of the pixels",
        call("GetPixel", 3, (uint32_t[]){ pdc2, 0, 1 }) == 0x000000FFu);

    /* An 8bpp packed DIB uses the colour table the brush copied from guest
     * memory, and its rows are DWORD-aligned. */
    uint32_t p8 = hle_va(hle_alloc(48 + 8));
    memset(hle_ptr(p8), 0, 48 + 8);
    MEM32(p8) = 40; MEM32(p8 + 4) = 2; MEM32(p8 + 8) = 2;
    MEM16(p8 + 12) = 1; MEM16(p8 + 14) = 8; MEM32(p8 + 32) = 2;
    MEM32(p8 + 40) = 0x00FF0000u;                        /* colour 0: red */
    MEM32(p8 + 44) = 0x000000FFu;                        /* colour 1: blue */
    MEM8(p8 + 48) = 0; MEM8(p8 + 49) = 1;                /* bottom row */
    MEM8(p8 + 52) = 1; MEM8(p8 + 53) = 0;                /* top row */
    uint32_t h8 = call("CreateDIBPatternBrushPt", 2, (uint32_t[]){ p8, 0 });
    ok("8bpp pattern brush created", h8 != 0);
    uint32_t bmp8 = call("CreateCompatibleBitmap", 3, (uint32_t[]){ 0, 2, 2 });
    uint32_t dc8 = call("CreateCompatibleDC", 1, (uint32_t[]){ 0 });
    call("SelectObject", 2, (uint32_t[]){ dc8, bmp8 });
    MEM32(rc + 8) = 2; MEM32(rc + 12) = 2;
    call("FillRect", 3, (uint32_t[]){ dc8, rc, h8 });
    ok("8bpp pattern uses the copied colour table",
        call("GetPixel", 3, (uint32_t[]){ dc8, 0, 0 }) == 0x000000FFu &&
        call("GetPixel", 3, (uint32_t[]){ dc8, 1, 0 }) == 0x00FF0000u &&
        call("GetPixel", 3, (uint32_t[]){ dc8, 0, 1 }) == 0x00FF0000u &&
        call("GetPixel", 3, (uint32_t[]){ dc8, 1, 1 }) == 0x000000FFu);

    /* ---- honest failures, no phantom brush handles ---- */
    ok("NULL packed DIB -> 0", call("CreateDIBPatternBrushPt", 2, (uint32_t[]){ 0, 0 }) == 0);
    uint32_t bad = hle_va(hle_alloc(40 + 16));
    memset(hle_ptr(bad), 0, 40 + 16);
    ok("bad biSize -> 0", call("CreateDIBPatternBrushPt", 2, (uint32_t[]){ bad, 0 }) == 0);
    MEM32(bad) = 40; MEM16(bad + 14) = 32;
    ok("zero width -> 0", call("CreateDIBPatternBrushPt", 2, (uint32_t[]){ bad, 0 }) == 0);
    MEM32(bad + 4) = 2; MEM32(bad + 8) = 2; MEM16(bad + 12) = 1;
    MEM32(bad + 16) = 3;                                 /* BI_BITFIELDS */
    ok("unsupported compression -> 0", call("CreateDIBPatternBrushPt", 2, (uint32_t[]){ bad, 0 }) == 0);
    MEM32(bad + 16) = 0; MEM16(bad + 14) = 16;
    ok("unsupported depth -> 0", call("CreateDIBPatternBrushPt", 2, (uint32_t[]){ bad, 0 }) == 0);
    ok("DIB_PAL_COLORS -> 0", call("CreateDIBPatternBrushPt", 2, (uint32_t[]){ dib, 1 }) == 0);
    ok("DeleteObject(pattern brush) -> 1", call("DeleteObject", 1, &hpat) == 1);
    ok("GetObjectW(deleted pattern) -> 0", call("GetObjectW", 3, (uint32_t[]){ hpat, 12, lb }) == 0);

    call("DeleteObject", 1, &h8);
    call("DeleteDC", 1, &pdc);
    call("DeleteDC", 1, &pdc2);
    call("DeleteDC", 1, &dc8);
    call("DeleteDC", 1, &dc);
    mach_leave();
    printf("%s: gdi_more_selftest: %d failure(s)\n", fails ? "FAIL" : "ok", fails);
    return fails != 0;
}
