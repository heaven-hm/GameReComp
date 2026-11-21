/*
 * win32hle_selftest.c - exercises the host spine and the shim ABI with a
 * hand-written synthetic "guest" in the global-register lifted style, so the
 * whole guest<->native<->guest path is proven with no game binary and no
 * display. This is the win32hle CI green signal (build -m32, or 64-bit with
 * guest memory based: build_selftests.sh).
 *
 * The guest functions below are what the lifter emits by hand: they drive g_esp
 * and g_eax directly, push stdcall arguments, and call imports through
 * RECOMP_ICALL against a synthetic shim VA. A real lift produces thousands of
 * these; three are enough to check the contract.
 */
#define RECOMP_GENERATED_CODE   /* make eax/esp/... alias the g_* register file */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}

/* The per-function prologue the lifter emits (lift32.py): the flag locals the
 * RECOMP_FLAGS_IN/OUT in RECOMP_ENTER/RECOMP_ICALL read and write, then
 * RECOMP_ENTER to publish g_cur_func and import the caller's flags. */
#define GUEST_PROLOGUE(va)              \
    uint32_t _cf = 0;                   \
    uint32_t _flag_k = FK_NONE;         \
    uint32_t _flag_a = 0, _flag_b = 0;  \
    RECOMP_ENTER(va)

/* Resolved shim VAs, filled in main() before any guest runs. */
static uint32_t VA_HeapAlloc, VA_lstrlenA, VA_GetTickCount;
static uint32_t VA_CreateDIBSection, VA_StretchDIBits, VA_RaiseException;
#define VA_ADD       0x00401000u   /* guest_add's dispatch VA (callback target) */
#define VA_D_TEST    0x00403000u   /* synthetic DMD SEH test entry */
#define VA_D_FINALLY 0x00403100u   /* synthetic DMD finally block */
static int g_eh_landed, g_d_finally_ran, g_d_caught;
static uint32_t g_d_stub_va, g_d_exc_obj, g_d_args_va;

/* host framebuffer the DIB blits land on, and the guest's drawing scratch */
#define FB_W 8
#define FB_H 8
static uint32_t g_fb[FB_W * FB_H];
void hle_gdi_set_target(uint32_t *pixels, int w, int h);

/* What the guest reads and writes is guest memory (hle_alloc), as a lifted
 * program's own data would be. */
static uint8_t  *g_bmi;          /* BITMAPINFOHEADER: 4x4, top-down, 32bpp */
static uint32_t *g_dibbits_slot; /* CreateDIBSection writes the pixel ptr here */
static uint32_t src_pixel(int x, int y) { return 0xFF000000u | (uint32_t)((x << 4) | y); }

/* guest: p = HeapAlloc(heap, 0, 64); for (i<16) p[i]=i*i; return p;
 * stdcall args pushed right-to-left, then RECOMP_ICALL pushes the ret addr. */
static void guest_alloc_and_fill(void) {
    GUEST_PROLOGUE(0x00402000u);
    uint32_t heap = 1;
    PUSH32(esp, 64);            /* size  (arg2) */
    PUSH32(esp, 0);             /* flags (arg1) */
    PUSH32(esp, heap);          /* hHeap (arg0) */
    RECOMP_ICALL(VA_HeapAlloc);
    uint32_t p = eax;
    for (uint32_t i = 0; i < 16; i++) MEM32(p + 4 * i) = i * i;
    eax = p;                    /* return the pointer */
}

/* guest: return lstrlenA(str), str passed in esi by the caller. */
static void guest_strlen(void) {
    GUEST_PROLOGUE(0x00402100u);
    PUSH32(esp, esi);           /* lpString (arg0) */
    RECOMP_ICALL(VA_lstrlenA);
    /* eax already holds the length */
}

/* guest: build a 4x4 top-down 32bpp DIB, draw a pattern into it, and
 * StretchDIBits it 2x into the 8x8 framebuffer. Mirrors a software renderer's
 * frame: draw into DIB bits, blit to the device. */
static void guest_draw(void) {
    GUEST_PROLOGUE(0x00402200u);
    /* CreateDIBSection(hdc=0, &bmi, usage=0, &bits_slot, hSection=0, offset=0) */
    PUSH32(esp, 0);                                   /* offset   */
    PUSH32(esp, 0);                                   /* hSection */
    PUSH32(esp, hle_va(g_dibbits_slot));              /* ppvBits  */
    PUSH32(esp, 0);                                   /* usage    */
    PUSH32(esp, hle_va(g_bmi));                       /* pbmi     */
    PUSH32(esp, 0);                                   /* hdc      */
    RECOMP_ICALL(VA_CreateDIBSection);
    uint32_t bits = *g_dibbits_slot;
    for (uint32_t y = 0; y < 4; y++)
        for (uint32_t x = 0; x < 4; x++)
            MEM32(bits + 4 * (y * 4 + x)) = src_pixel((int)x, (int)y);

    /* StretchDIBits(hdc,0,0,8,8, 0,0,4,4, bits, &bmi, 0, SRCCOPY=0xCC0020) */
    PUSH32(esp, 0x00CC0020u);                         /* rop     */
    PUSH32(esp, 0);                                   /* usage   */
    PUSH32(esp, hle_va(g_bmi));                       /* bmi     */
    PUSH32(esp, bits);                                /* bits    */
    PUSH32(esp, 4); PUSH32(esp, 4);                   /* hS, wS  (pushed hS then wS) */
    PUSH32(esp, 0); PUSH32(esp, 0);                   /* yS, xS  */
    PUSH32(esp, 8); PUSH32(esp, 8);                   /* hD, wD  */
    PUSH32(esp, 0); PUSH32(esp, 0);                   /* yD, xD  */
    PUSH32(esp, 0);                                   /* hdc     */
    RECOMP_ICALL(VA_StretchDIBits);
}

/* guest callback: int add(int a, int b) in stdcall. Reads its two args off the
 * stack (ret addr at esp), returns a+b, and pops them (ret 8). */
static void guest_add(void) {
    GUEST_PROLOGUE(VA_ADD);
    uint32_t a = MEM32(esp + 4), b = MEM32(esp + 8);
    eax = a + b;
    esp += 4 + 8;               /* pop ret addr + 2 args (callee cleans: stdcall) */
}

/* Generated-style MSVC EH landing pad.  A real exception dispatcher selects
 * a catch continuation and resumes this checkpoint with its guest address. */
static void guest_eh_checkpoint(void) {
    GUEST_PROLOGUE(0x00402300u);
    uint32_t _itail_tgt = 0;
    RECOMP_EH_ENTER();
    if (!g_eh_landed) {
        recomp_ehframe_t *owner = recomp_eh_owner(esp - 1u);
        if (owner) recomp_eh_resume(owner, 0x00402340u);
    }
    goto _eh_done;
  _ljump:
    switch (_itail_tgt) {
      case 0x00402340u: goto L_00402340;
      default: goto _eh_done;
    }
  L_00402340:
    g_eh_landed = 1;
  _eh_done:
    RECOMP_EH_LEAVE();
}

static void d_finally_fn(void) {
    GUEST_PROLOGUE(VA_D_FINALLY);
    g_d_finally_ran++;
    esp += 4u;
}

static void guest_d_eh_test(void) {
    GUEST_PROLOGUE(VA_D_TEST);
    uint32_t _itail_tgt = 0;
    RECOMP_EH_ENTER();
    PUSH32(esp, ebp);
    ebp = esp;
    uint32_t prev = MEM32(FS_BASE);
    PUSH32(esp, 1u);                  /* active scope 1 (inner finally -> scope 0 catch) */
    PUSH32(esp, g_d_stub_va);
    PUSH32(esp, prev);
    MEM32(FS_BASE) = esp;
    esp -= 0x20u;
    PUSH32(esp, g_d_args_va);
    PUSH32(esp, 1u);
    PUSH32(esp, 1u);
    PUSH32(esp, 0xE0440001u);
    RECOMP_ICALL(VA_RaiseException);
    goto _d_done;
  _ljump:
    switch (_itail_tgt) {
      case 0x00403080u: goto L_00403080;
      default: goto _d_done;
    }
  L_00403080:
    /* Never read non-volatile locals modified after setjmp (C longjmp rule). */
    g_d_caught = (MEM32(ebp - 0x1Cu) == g_d_exc_obj &&
                  MEM32(FS_BASE) == (ebp - 12u) &&
                  MEM32(ebp - 4u) == 0xFFFFFFFFu &&
                  esp == ebp - 0x2Cu);
    MEM32(FS_BASE) = MEM32(ebp - 12u);
    esp = ebp;
    ebp = POP32_VAL(esp);
  _d_done:
    RECOMP_EH_LEAVE();
}

/* Dispatch table the host searches (sorted by address). guest_add is reachable
 * as a callback through hle_call_guest. */
const recomp_dispatch_entry_t recomp_dispatch_table[] = {
    { VA_ADD, guest_add },
    { VA_D_TEST, guest_d_eh_test },
    { VA_D_FINALLY, d_finally_fn },
};
const uint32_t recomp_dispatch_count = 3;

int main(void) {
    win32hle_register(win32hle_kernel32);
    win32hle_register(win32hle_kernel32_crt);
    win32hle_register(win32hle_gdi32);
    win32hle_register(win32hle_user32);
    VA_HeapAlloc       = hle_resolve("HeapAlloc");
    VA_lstrlenA        = hle_resolve("lstrlenA");
    VA_GetTickCount    = hle_resolve("GetTickCount");
    VA_CreateDIBSection= hle_resolve("CreateDIBSection");
    VA_StretchDIBits   = hle_resolve("StretchDIBits");
    VA_RaiseException  = hle_resolve("RaiseException");
    ok("HeapAlloc resolves",    VA_HeapAlloc != 0);
    ok("lstrlenA resolves",     VA_lstrlenA != 0);
    ok("GetTickCount resolves", VA_GetTickCount != 0);
    ok("gdi32 resolves",        VA_CreateDIBSection != 0 && VA_StretchDIBits != 0);
    ok("unknown import is 0",   hle_resolve("NoSuchApi") == 0);
    ok("hle_name round-trips",  hle_name(VA_HeapAlloc) && !strcmp(hle_name(VA_HeapAlloc), "HeapAlloc"));

    /* Menu submenu removal + positional insertion regression (OrgView pattern):
     * RemoveMenu/DeleteMenu on a parent handle referencing a submenu item ID
     * must mutate the owning submenu rather than indexing parent->it out of bounds. */
    {
        uint32_t VA_CreateMenu = hle_resolve("CreateMenu");
        uint32_t VA_CreatePopupMenu = hle_resolve("CreatePopupMenu");
        uint32_t VA_AppendMenuA = hle_resolve("AppendMenuA");
        uint32_t VA_InsertMenuA = hle_resolve("InsertMenuA");
        uint32_t VA_RemoveMenu = hle_resolve("RemoveMenu");
        uint32_t VA_DeleteMenu = hle_resolve("DeleteMenu");
        uint32_t VA_GetMenuItemCount = hle_resolve("GetMenuItemCount");
        uint32_t VA_GetMenuItemID = hle_resolve("GetMenuItemID");
        uint32_t VA_DestroyMenu = hle_resolve("DestroyMenu");
        uint32_t t1 = hle_guest_strdup("File"), t2 = hle_guest_strdup("A"), t3 = hle_guest_strdup("B"), t4 = hle_guest_strdup("C");
        mach_enter();
        uint32_t hm = hle_call_guest(VA_CreateMenu, 0, NULL);
        uint32_t hs = hle_call_guest(VA_CreatePopupMenu, 0, NULL);
        hle_call_guest(VA_AppendMenuA, 4, (uint32_t[]){ hm, 0x10u, hs, t1 }); /* MF_POPUP */
        hle_call_guest(VA_AppendMenuA, 4, (uint32_t[]){ hs, 0u, 0x9C7Eu, t2 });
        hle_call_guest(VA_AppendMenuA, 4, (uint32_t[]){ hs, 0u, 0x9C80u, t3 });
        hle_call_guest(VA_InsertMenuA, 5, (uint32_t[]){ hs, 1u, 0x400u /* MF_BYPOSITION */, 0x9C7Fu, t4 });
        uint32_t cnt_before = hle_call_guest(VA_GetMenuItemCount, 1, (uint32_t[]){ hs });
        uint32_t mid_id = hle_call_guest(VA_GetMenuItemID, 2, (uint32_t[]){ hs, 1u });
        uint32_t rm_ok = hle_call_guest(VA_RemoveMenu, 3, (uint32_t[]){ hm, 0x9C7Eu, 0u });
        uint32_t del_ok = hle_call_guest(VA_DeleteMenu, 3, (uint32_t[]){ hm, 0x9C80u, 0u });
        uint32_t cnt_after = hle_call_guest(VA_GetMenuItemCount, 1, (uint32_t[]){ hs });
        uint32_t rem_id = hle_call_guest(VA_GetMenuItemID, 2, (uint32_t[]){ hs, 0u });
        hle_call_guest(VA_DestroyMenu, 1, (uint32_t[]){ hs });
        hle_call_guest(VA_DestroyMenu, 1, (uint32_t[]){ hm });
        mach_leave();
        ok("submenu RemoveMenu/DeleteMenu/InsertMenuA",
           hm && hs && cnt_before == 3 && mid_id == 0x9C7Fu && rm_ok == 1 && del_ok == 1 &&
           cnt_after == 1 && rem_id == 0x9C7Fu);
    }

    /* BITMAPINFOHEADER: 4x4, top-down (negative height), 32bpp BI_RGB. */
    g_bmi = (uint8_t *)hle_alloc(40);
    g_dibbits_slot = (uint32_t *)hle_alloc(4);
    g_bmi[0]=40;                                 /* biSize */
    g_bmi[4]=4;                                  /* biWidth = 4 */
    { int32_t h=-4; memcpy(g_bmi+8,&h,4); }      /* biHeight = -4 (top-down) */
    g_bmi[12]=1;                                 /* biPlanes */
    g_bmi[14]=32;                                /* biBitCount */
    hle_gdi_set_target(g_fb, FB_W, FB_H);

    mach_enter();   /* claim the register file / establish this thread's stack+TIB */

    /* 1. guest -> shim: HeapAlloc + fill, check eax and memory. */
    guest_alloc_and_fill();
    uint32_t p = eax;
    ok("HeapAlloc returned memory", p != 0);
    int mem_ok = 1;
    for (uint32_t i = 0; i < 16; i++) if (MEM32(p + 4 * i) != i * i) mem_ok = 0;
    ok("guest wrote through the heap block", mem_ok);

    /* 2. guest -> shim with a pointer arg: lstrlenA. */
    static const char msg[] = "win32hle";
    esi = hle_guest_strdup(msg);
    guest_strlen();
    ok("lstrlenA counted the string", eax == strlen(msg));

    /* 3. the register file is serialised and fs: points at a TIB. */
    ok("TIB self at fs:[0x18]", MEM32(FS_BASE + 0x18) == FS_BASE && FS_BASE != 0);

    /* 4. the renderer seam: guest draws a DIB and StretchDIBits-blits it 2x into
     * the framebuffer. Each source pixel (x,y) must land in the 2x2 block at
     * (2x,2y) (top-down, nearest-neighbour). */
    guest_draw();
    int blit_ok = 1;
    for (int fy = 0; fy < FB_H; fy++)
        for (int fx = 0; fx < FB_W; fx++)
            if (g_fb[fy * FB_W + fx] != src_pixel(fx / 2, fy / 2)) blit_ok = 0;
    ok("StretchDIBits scaled the DIB into the framebuffer", blit_ok);

    guest_eh_checkpoint();
    ok("guest EH checkpoint resumed at its continuation", g_eh_landed);

    /* DMD 0xE0440001 throw -> inner finally -> ClassInfo base match -> catch */
    {
        uint32_t base_ci = hle_va(hle_alloc(0x40));
        uint32_t sub_ci  = hle_va(hle_alloc(0x40));
        uint32_t vtbl    = hle_va(hle_alloc(8));
        uint32_t tbl     = hle_va(hle_alloc(64));
        uint8_t *stub    = (uint8_t *)hle_alloc(16);
        g_d_stub_va = hle_va(stub);
        g_d_exc_obj = hle_va(hle_alloc(16));
        g_d_args_va = hle_va(hle_alloc(8));
        MEM32(sub_ci + 0x28u) = base_ci;         /* ClassInfo.base */
        MEM32(vtbl) = sub_ci;                    /* vtbl[0] = ClassInfo */
        MEM32(g_d_exc_obj) = vtbl;               /* obj->vptr = vtbl */
        MEM32(g_d_args_va) = g_d_exc_obj;        /* args[0] = obj */
        stub[0] = 0xB8u; memcpy(stub + 1, &tbl, 4); stub[5] = 0xE9u;
        MEM32(tbl + 0u) = VA_D_TEST;             /* fptr */
        MEM32(tbl + 4u) = 0x2Cu;                 /* espoffset (0xC record + 0x20 locals) */
        MEM32(tbl + 8u) = 0x60u;                 /* retoffset */
        /* index 0: outer catch(base_ci, bpoff=-0x1c, catch=0x00403080) at cioff=36 */
        MEM32(tbl + 12u) = 0xFFFFFFFFu;
        MEM32(tbl + 16u) = 36u;
        MEM32(tbl + 20u) = 0u;
        /* index 1: inner try-finally (prev=0, finally=VA_D_FINALLY) */
        MEM32(tbl + 24u) = 0u;
        MEM32(tbl + 28u) = 0u;
        MEM32(tbl + 32u) = VA_D_FINALLY;
        /* DCatchInfo at +36 */
        MEM32(tbl + 36u) = 1u;
        MEM32(tbl + 40u) = base_ci;
        MEM32(tbl + 44u) = (uint32_t)(-0x1C);
        MEM32(tbl + 48u) = 0x00403080u;
        guest_d_eh_test();
        ok("DMD RaiseException(0xE0440001) ran finally, matched base ClassInfo, resumed catch",
           g_d_finally_ran == 1 && g_d_caught == 1);
    }

    mach_leave();

    /* 5. native -> guest: call guest_add(40, 2) through the explicit path. */
    uint32_t args[2] = { 40, 2 };
    ok("hle_call_guest ran the callback", hle_call_guest(VA_ADD, 2, args) == 42);

    /* 6. the display modes: the table, and the display's own size in order */
    int m[HLE_DD_MAX_MODES][2], n0 = hle_dd_modes(0, 0, m), n, at = -1, sorted = 1;
    ok("the modes reach 4K", m[n0 - 1][0] == 3840 && m[n0 - 1][1] == 2160);
    ok("a display already in the table is not listed twice", hle_dd_modes(1920, 1080, m) == n0);
    n = hle_dd_modes(1512, 982, m);                      /* a 14" MacBook's logical size */
    for (int i = 0; i < n; i++) {
        if (m[i][0] == 1512 && m[i][1] == 982) at = i;
        if (i && (m[i][0] < m[i - 1][0] || (m[i][0] == m[i - 1][0] && m[i][1] < m[i - 1][1]))) sorted = 0;
    }
    ok("a display's own size is listed, in order", n == n0 + 1 && at >= 0 && sorted);
    ok("a display past 4K is not", hle_dd_modes(5120, 2880, m) == n0);

    if (fails == 0) printf("win32hle_selftest: all checks passed\n");
    return fails != 0;
}
