/*
 * ole32.c - OLE32 and OLEAUT32: COM's bookkeeping, and a way for a host to
 * serve COM objects of its own.
 *
 * A COM object a guest holds is guest memory whose first dword points at a
 * vtable of method addresses; on win32hle those are shim VAs, so a guest's
 * `call [eax+0Ch]` dispatches to C (hle_com_vtable builds one from a shim
 * table). CoCreateInstance asks the classes a host registered
 * (hle_com_register_class), and otherwise answers REGDB_E_CLASSNOTREG, as for
 * a COM server that is not installed.
 *
 * Structured storage, OLE embedding and type libraries are not here: they
 * fail with E_NOTIMPL.
 *
 * The DirectMusic COM class and method table below adapt HenryKun55's
 * igi-recomp shim/src/ole32.c (commit 278eaf6394f7f42c7de6cffef2cf982154f59899),
 * Copyright (c) 2026 HenryKun55, under MIT; see LICENSES/igi-recomp-MIT.txt.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "win32hle.h"

#define S_OK                 0u
#define E_NOTIMPL            0x80004001u
#define E_NOINTERFACE        0x80004002u
#define E_FAIL               0x80004005u
#define REGDB_E_CLASSNOTREG  0x80040154u
#define CO_E_CLASSSTRING     0x800401F3u

/* DirectMusic's COM class is part of DirectX 7.  A headless runtime can
 * expose its interface for capability checks while reporting playback and
 * device operations as unsupported. */
static const uint8_t CLSID_DIRECTMUSIC[16] = {
    0x10, 0x9F, 0x6B, 0x63, 0x7D, 0x0C, 0xD1, 0x11,
    0x95, 0xB2, 0x00, 0x20, 0xAF, 0xDC, 0x74, 0x21
};
static const uint8_t IID_DIRECTMUSIC[16] = {
    0x5A, 0x11, 0x36, 0x65, 0x2D, 0x7B, 0xD2, 0x11,
    0xBA, 0x18, 0x00, 0x00, 0xF8, 0x75, 0xAC, 0x12
};
static const uint8_t IID_DIRECTMUSIC8[16] = {
    0xF7, 0x29, 0x36, 0x2D, 0x3D, 0x81, 0x39, 0x49,
    0x85, 0x08, 0xF0, 0x5C, 0x6B, 0x75, 0xFD, 0x97
};
static const uint8_t IID_IUNKNOWN[16] = {
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46
};

/* ---- GUIDs ---- */
void hle_guid_string(const uint8_t *g, char *out) {      /* {xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx} */
    sprintf(out, "{%08X-%04X-%04X-%02X%02X-%02X%02X%02X%02X%02X%02X}", *(const uint32_t *)g,
            *(const uint16_t *)(g + 4), *(const uint16_t *)(g + 6), g[8], g[9], g[10], g[11], g[12], g[13], g[14], g[15]);
}
static int parse_guid(const char *s, uint8_t *g) {
    unsigned a, b, c, d[8];
    if (sscanf(s, "{%8x-%4x-%4x-%2x%2x-%2x%2x%2x%2x%2x%2x}", &a, &b, &c, &d[0], &d[1], &d[2], &d[3], &d[4], &d[5], &d[6], &d[7]) != 11)
        return 0;
    *(uint32_t *)g = a, *(uint16_t *)(g + 4) = (uint16_t)b, *(uint16_t *)(g + 6) = (uint16_t)c;
    for (int i = 0; i < 8; i++) g[8 + i] = (uint8_t)d[i];
    return 1;
}

/* ---- host-served classes ---- */
#define MAX_CLASSES 16
static struct { uint8_t clsid[16]; uint32_t (*create)(const uint8_t *iid, uint32_t *out); } g_class[MAX_CLASSES];
static int g_nclass;
void hle_com_register_class(const uint8_t *clsid, uint32_t (*create)(const uint8_t *iid, uint32_t *out)) {
    if (g_nclass < MAX_CLASSES) memcpy(g_class[g_nclass].clsid, clsid, 16), g_class[g_nclass++].create = create;
}

/* A vtable of shim VAs, in guest-readable memory (it lives for the run). */
uint32_t hle_com_vtable(const win32hle_shim *methods) {
    int n = 0;
    while (methods[n].name) n++;
    uint32_t *vt = (uint32_t *)hle_alloc(4u * (uint32_t)n + 4u);
    win32hle_register(methods);
    for (int i = 0; i < n; i++) vt[i] = hle_resolve(methods[i].name);
    return hle_va(vt);
}
/* An object: the vtable pointer, a reference count, then `size` - 8 bytes of
 * the host's own state. Returns the object's GUEST VA, like every other
 * factory here: hle_com_vtable also returns a VA, and the guest dereferences
 * both as addresses. (A based host's hle_alloc hands out a host pointer, so
 * returning that would name a different object to the guest.) */
uint32_t hle_com_new(uint32_t vtable, uint32_t size) {
    uint32_t *o = (uint32_t *)hle_alloc(size < 8 ? 8 : size);
    o[0] = vtable, o[1] = 1;
    return hle_va(o);
}
void hle_com_free(uint32_t obj) { hle_free(hle_ptr(obj)); }
/* IUnknown for such objects: any interface is this one (a host serves one
 * interface per object), counted at +4, freed at zero. */
void hle_com_QueryInterface(void) {                      /* (this, iid, &out) */
    uint32_t self = A32(0);
    if (A32(2)) MEM32(A32(2)) = self;
    MEM32(self + 4)++;
    RET(S_OK, 3);
}
void hle_com_AddRef(void) { RET(++MEM32(A32(0) + 4), 1); }
void hle_com_Release(void) {
    uint32_t self = A32(0), n = --MEM32(self + 4);
    if (!n) hle_com_free(self);
    RET(n, 1);
}

/* Class objects the guest registered itself (CoRegisterClassObject): a game
 * that serves its own classes in process (Tiberian Sun's locomotors) creates
 * them through COM, so CoCreateInstance has to find its factories. */
#define MAX_GUEST_CLASSES 512                    /* Red Alert 2: past 64, its saved classes and locomotors */
static struct { uint8_t clsid[16]; uint32_t unk, cookie; } g_guest_class[MAX_GUEST_CLASSES];
static uint32_t g_next_cookie = 0x100;
static const uint8_t IID_IClassFactory[16] = { 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                                               0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46 };
static uint32_t vcall(uint32_t obj, int slot, int nargs, const uint32_t *args) {
    uint32_t a[8] = { obj };
    for (int i = 0; i < nargs; i++) a[1 + i] = args[i];
    return hle_call_guest(MEM32(obj + 4u * (uint32_t)slot), nargs + 1, a);
}

static void dm_QueryInterface(void) {
    const uint8_t *iid = (const uint8_t *)APTR(1);
    if (!iid || (memcmp(iid, IID_IUNKNOWN, 16) && memcmp(iid, IID_DIRECTMUSIC, 16) &&
                 memcmp(iid, IID_DIRECTMUSIC8, 16))) {
        if (A32(2)) MEM32(A32(2)) = 0;
        RET(E_NOINTERFACE, 3);
    }
    MEM32(A32(0) + 4)++;
    MEM32(A32(2)) = A32(0);
    RET(S_OK, 3);
}
static void dm_unsupported_2(void) { RET(E_NOTIMPL, 2); }
static void dm_unsupported_3(void) { RET(E_NOTIMPL, 3); }
static void dm_unsupported_4(void) { RET(E_NOTIMPL, 4); }
static void dm_unsupported_5(void) { RET(E_NOTIMPL, 5); }
static void dm_unsupported_external_clock(void) { RET(E_NOTIMPL, 2); }
static uint32_t directmusic_vtable(void) {
    static uint32_t vt;
    static const win32hle_shim methods[] = {
        { "IDirectMusic::QueryInterface", dm_QueryInterface },
        { "IDirectMusic::AddRef", hle_com_AddRef }, { "IDirectMusic::Release", hle_com_Release },
        { "IDirectMusic::EnumPort", dm_unsupported_3 },
        { "IDirectMusic::CreateMusicBuffer", dm_unsupported_4 },
        { "IDirectMusic::CreatePort", dm_unsupported_5 },
        { "IDirectMusic::EnumMasterClock", dm_unsupported_3 },
        { "IDirectMusic::GetMasterClock", dm_unsupported_3 },
        { "IDirectMusic::SetMasterClock", dm_unsupported_2 },
        { "IDirectMusic::Activate", dm_unsupported_2 },
        { "IDirectMusic::GetDefaultPort", dm_unsupported_2 },
        { "IDirectMusic::SetDirectSound", dm_unsupported_3 },
        { "IDirectMusic8::SetExternalMasterClock", dm_unsupported_external_clock },
        { NULL, NULL }
    };
    if (!vt) vt = hle_com_vtable(methods);
    return vt;
}

static void o_CoCreateInstance(void) {                   /* (clsid, outer, ctx, iid, &out) */
    const uint8_t *clsid = (const uint8_t *)APTR(0);
    char s[40];
    if (A32(4)) MEM32(A32(4)) = 0;
    if (clsid && !memcmp(clsid, CLSID_DIRECTMUSIC, 16)) {
        const uint8_t *iid = (const uint8_t *)APTR(3);
        if (!iid || (memcmp(iid, IID_IUNKNOWN, 16) && memcmp(iid, IID_DIRECTMUSIC, 16) &&
                     memcmp(iid, IID_DIRECTMUSIC8, 16)))
            RET(E_NOINTERFACE, 5);
        uint32_t obj = hle_com_new(directmusic_vtable(), 8);
        if (A32(4)) MEM32(A32(4)) = obj;
        RET(S_OK, 5);
    }
    for (int i = 0; clsid && i < g_nclass; i++)
        if (!memcmp(g_class[i].clsid, clsid, 16)) RET(g_class[i].create((const uint8_t *)APTR(3), (uint32_t *)APTR(4)), 5);
    for (int i = 0; clsid && i < MAX_GUEST_CLASSES; i++)
        if (g_guest_class[i].unk && !memcmp(g_guest_class[i].clsid, clsid, 16)) {
            uint32_t mark = hle_scratch_mark(), cf = hle_scratch(4);
            uint32_t qi[2] = { hle_scratch_copy(IID_IClassFactory, 16), cf };
            uint32_t hr = vcall(g_guest_class[i].unk, 0, 2, qi);           /* QueryInterface */
            uint32_t f = MEM32(cf);
            hle_scratch_release(mark);
            if (hr) RET(hr, 5);
            uint32_t ci[3] = { A32(1), A32(3), A32(4) };
            hr = vcall(f, 3, 3, ci);                                        /* IClassFactory::CreateInstance */
            vcall(f, 2, 0, NULL);                                           /* Release */
            RET(hr, 5);
        }
    if (clsid) hle_guid_string(clsid, s);
    fprintf(stderr, "[ole32] CoCreateInstance(%s): no such class on this host\n", clsid ? s : "(null)");
    RET(REGDB_E_CLASSNOTREG, 5);
}
static void o_OleInitialize(void)   { RET(S_OK, 1); }
static void o_OleUninitialize(void) { RETV(0); }
static void o_CoInitialize(void)    { RET(S_OK, 1); }
static void o_CoUninitialize(void)  { RETV(0); }
static void o_CoRegisterClassObject(void) {              /* (clsid, unk, ctx, flags, &cookie) */
    for (int i = 0; i < MAX_GUEST_CLASSES; i++)
        if (!g_guest_class[i].unk) {
            memcpy(g_guest_class[i].clsid, APTR(0), 16);
            g_guest_class[i].unk = A32(1), g_guest_class[i].cookie = g_next_cookie++;
            vcall(A32(1), 1, 0, NULL);                   /* AddRef: COM holds it */
            if (A32(4)) MEM32(A32(4)) = g_guest_class[i].cookie;
            RET(S_OK, 5);
        }
    fprintf(stderr, "[ole32] CoRegisterClassObject: more than %d classes\n", MAX_GUEST_CLASSES);
    RET(0x8007000Eu, 5);                                 /* E_OUTOFMEMORY */
}
static void o_CoRevokeClassObject(void) {
    for (int i = 0; i < MAX_GUEST_CLASSES; i++)
        if (g_guest_class[i].unk && g_guest_class[i].cookie == A32(0)) {
            uint32_t u = g_guest_class[i].unk;
            g_guest_class[i].unk = 0;
            vcall(u, 2, 0, NULL);                        /* Release */
            RET(S_OK, 1);
        }
    RET(0x80040011u /* CO_E_OBJNOTREG */, 1);
}
static void o_CoDisconnectObject(void)    { RET(S_OK, 2); }
static void o_CoFileTimeNow(void) {
    struct timespec ts; clock_gettime(CLOCK_REALTIME, &ts);
    uint64_t ft = ((uint64_t)ts.tv_sec + 11644473600ull) * 10000000ull + (uint64_t)ts.tv_nsec / 100u;
    if (A32(0)) MEM32(A32(0)) = (uint32_t)ft, MEM32(A32(0) + 4) = (uint32_t)(ft >> 32);
    RET(S_OK, 1);
}
static void o_CLSIDFromString(void) {                    /* (wide string, &clsid) */
    const uint16_t *w = (const uint16_t *)APTR(0);
    char s[64];
    int k = 0;
    for (; w && w[k] && k < 63; k++) s[k] = (char)w[k];
    s[k] = 0;
    RET(parse_guid(s, (uint8_t *)APTR(1)) ? S_OK : CO_E_CLASSSTRING, 2);
}
static void o_StringFromGUID2(void) {                    /* (guid, wide buf, n) -> chars incl. NUL */
    char s[40];
    uint16_t *w = (uint16_t *)APTR(1);
    hle_guid_string((const uint8_t *)APTR(0), s);
    if ((int)A32(2) < 39) RET(0, 3);
    for (int i = 0; i < 39; i++) w[i] = (uint8_t)s[i];
    RET(39, 3);
}
static void o_StringFromCLSID(void) {                    /* (clsid, &wide string) */
    char s[40];
    uint16_t *w = (uint16_t *)hle_alloc(80);
    hle_guid_string((const uint8_t *)APTR(0), s);
    for (int i = 0; i < 39; i++) w[i] = (uint8_t)s[i];
    if (A32(1)) MEM32(A32(1)) = hle_va(w);
    RET(S_OK, 2);
}
/* OleRun: an object a host serves is in process, so always running. */
static void o_OleRun(void) { RET(A32(0) ? S_OK : E_FAIL, 1); }
static void o_notimpl_1(void) { RET(E_NOTIMPL, 1); }
static void o_notimpl_2(void) { RET(E_NOTIMPL, 2); }
static void o_notimpl_4(void) { RET(E_NOTIMPL, 4); }

/* ---- OLEAUT32: BSTRs (length-prefixed wide strings) and VARIANTs ---- */
static void a_SysAllocString(void) {
    const uint16_t *w = (const uint16_t *)APTR(0);
    uint32_t n = 0;
    if (!w) RET(0, 1);
    while (w[n]) n++;
    uint8_t *b = (uint8_t *)hle_alloc(4 + 2 * n + 2);
    *(uint32_t *)b = 2 * n;
    memcpy(b + 4, w, 2 * n);
    RETP(b + 4, 1);
}
static void a_SysFreeString(void) { if (A32(0)) hle_free((uint8_t *)APTR(0) - 4); RETV(1); }
static void a_VariantInit(void)   { if (A32(0)) memset(APTR(0), 0, 16); RETV(1); }
static void a_VariantClear(void)  { if (A32(0)) memset(APTR(0), 0, 16); RET(S_OK, 1); }
static void a_VariantChangeType(void) { RET(E_NOTIMPL, 4); }
static void a_GetErrorInfo(void)  { if (A32(1)) MEM32(A32(1)) = 0; RET(1u /* S_FALSE */, 2); }

/* CoCreateGuid(&guid): a random (version 4) GUID, from the kernel's randomness. */
static void o_CoCreateGuid(void) {
    uint8_t *g = (uint8_t *)APTR(0);
    FILE *f = fopen("/dev/urandom", "rb");
    if (!g) RET(0x80070057u, 1);                         /* E_INVALIDARG */
    if (!f || fread(g, 1, 16, f) != 16) for (int i = 0; i < 16; i++) g[i] = (uint8_t)rand();
    if (f) fclose(f);
    g[7] = (uint8_t)((g[7] & 0x0F) | 0x40), g[8] = (uint8_t)((g[8] & 0x3F) | 0x80);
    RET(S_OK, 1);
}

static uint32_t ole_alloc_bstr(const uint16_t *src, uint32_t len) {
    uint32_t bytes = (len + 1u) * 2u;
    uint32_t base = hle_va(hle_alloc(bytes + 4u));
    if (!base) return 0;
    MEM32(base) = len * 2u;
    uint32_t bstr = base + 4u;
    uint8_t *dst = (uint8_t *)hle_ptr(bstr);
    if (src && len) memcpy(dst, src, len * 2u);
    else memset(dst, 0, len * 2u);
    MEM16(bstr + len * 2u) = 0;
    return bstr;
}

static void o_CoGetClassObject(void) {
    if (A32(4)) MEM32(A32(4)) = 0;
    RET(0x80040111u /* CLASS_E_CLASSNOTAVAILABLE */, 5);
}
static void o_CoTaskMemAlloc(void) {
    uint32_t sz = A32(0);
    void *p = hle_alloc(sz ? sz : 1u);
    RET(p ? hle_va(p) : 0u, 1);
}
static void o_CoTaskMemFree(void) { if (A32(0)) hle_free(APTR(0)); RETV(1); }
static void o_CreateStreamOnHGlobal(void) {
    if (A32(2)) MEM32(A32(2)) = 0;
    RET(0x80004001u /* E_NOTIMPL */, 3);
}
static void o_IsAccelerator(void) { RET(0, 4); }
static void o_IsEqualGUID(void) {
    uint8_t *g1 = (uint8_t *)APTR(0), *g2 = (uint8_t *)APTR(1);
    RET((g1 && g2 && memcmp(g1, g2, 16) == 0) ? 1u : 0u, 2);
}
static void o_OleDraw(void) { RET(0x80004001u /* E_NOTIMPL */, 4); }
static void o_OleSetMenuDescriptor(void) { RET(S_OK, 5); }
static void o_ProgIDFromCLSID(void) {
    if (A32(1)) MEM32(A32(1)) = 0;
    RET(0x80040154u /* REGDB_E_CLASSNOTREG */, 2);
}

static void a_SysAllocStringLen(void) {
    const uint16_t *src = (const uint16_t *)APTR(0);
    RET(ole_alloc_bstr(src, A32(1)), 2);
}

static void a_SysReAllocStringLen(void) {
    uint32_t p_va = A32(0), str_va = A32(1), len = A32(2);
    if (!p_va) { RET(0, 3); return; }
    uint32_t old = MEM32(p_va);
    const uint16_t *src = str_va ? (const uint16_t *)hle_ptr(str_va) : NULL;
    uint32_t nb = ole_alloc_bstr(src, len);
    if (!nb) { RET(0, 3); return; }
    if (old > 4u) hle_free((uint8_t *)hle_ptr(old) - 4);
    MEM32(p_va) = nb;
    RET(1, 3);
}

static void a_VariantCopy(void) {
    uint8_t *dst = (uint8_t *)APTR(0), *src = (uint8_t *)APTR(1);
    if (!dst || !src) { RET(0x80070057u /* E_INVALIDARG */, 2); return; }
    if (dst != src) memcpy(dst, src, 16);
    RET(S_OK, 2);
}

static void a_VariantChangeTypeEx(void) {
    uint32_t dst_va = A32(0), src_va = A32(1), vt = A32(4) & 0xffffu;
    if (!dst_va || !src_va) { RET(0x80070057u, 5); return; }
    if (dst_va != src_va) memcpy(hle_ptr(dst_va), hle_ptr(src_va), 16);
    MEM16(dst_va) = (uint16_t)vt;
    RET(S_OK, 5);
}

static uint32_t vt_elem_size(uint32_t vt) {
    switch (vt & 0xfffu) {
    case 16: case 17: case 11: return 1; /* I1, UI1 (2 for BOOL? wait VARIANT_BOOL=2; 1 is safe for UI1, BOOL=2) */
    case 2: case 18: return 2;
    case 3: case 4: case 19: case 22: case 23: case 8: case 9: case 13: return 4;
    case 5: case 6: case 7: case 20: case 21: return 8;
    case 12: case 14: return 16;
    default: return 4;
    }
}

static uint32_t safearray_offset(uint32_t psa_va, const int32_t *idx, int *ok) {
    uint16_t dims = MEM16(psa_va);
    uint32_t cb = MEM32(psa_va + 4);
    if (dims == 0 || !idx) { *ok = 0; return 0; }
    uint32_t stride = 1, linear = 0;
    for (uint16_t d = 0; d < dims; d++) {
        uint32_t b_va = psa_va + 16u + 8u * d;
        uint32_t cnt = MEM32(b_va);
        int32_t lb = (int32_t)MEM32(b_va + 4);
        int32_t rel = idx[d] - lb;
        if (rel < 0 || (uint32_t)rel >= cnt) { *ok = 0; return 0; }
        linear += (uint32_t)rel * stride;
        stride *= cnt;
    }
    *ok = 1;
    return linear * cb;
}

static void a_SafeArrayCreate(void) {
    uint32_t vt = A32(0) & 0xffffu, cdims = A32(1) & 0xffffu, bounds_va = A32(2);
    if (cdims == 0 || cdims > 8 || !bounds_va) { RET(0, 3); return; }
    uint32_t cb = ((vt & 0xfffu) == 11) ? 2u : vt_elem_size(vt);
    uint32_t sa_va = hle_va(hle_alloc(16u + 8u * cdims));
    if (!sa_va) { RET(0, 3); return; }
    uint32_t total = 1;
    for (uint32_t i = 0; i < cdims; i++) {
        uint32_t ce = MEM32(bounds_va + 8u * i);
        uint32_t lb = MEM32(bounds_va + 8u * i + 4u);
        MEM32(sa_va + 16u + 8u * i) = ce;
        MEM32(sa_va + 16u + 8u * i + 4u) = lb;
        total *= ce ? ce : 1u;
    }
    uint32_t data_va = hle_va(hle_alloc(total * cb));
    if (data_va) memset(hle_ptr(data_va), 0, total * cb);
    MEM16(sa_va) = (uint16_t)cdims;
    MEM16(sa_va + 2) = 0;
    MEM32(sa_va + 4) = cb;
    MEM32(sa_va + 8) = 0;
    MEM32(sa_va + 12) = data_va;
    RET(sa_va, 3);
}

static void a_SafeArrayRedim(void) {
    uint32_t sa_va = A32(0), b_va = A32(1);
    if (!sa_va || !b_va) { RET(0x80070057u, 2); return; }
    uint16_t cdims = MEM16(sa_va);
    if (cdims == 0) { RET(0x80070057u, 2); return; }
    uint32_t cb = MEM32(sa_va + 4);
    uint32_t old_last = MEM32(sa_va + 16u + 8u * (cdims - 1));
    uint32_t new_last = MEM32(b_va);
    MEM32(sa_va + 16u + 8u * (cdims - 1)) = new_last;
    MEM32(sa_va + 16u + 8u * (cdims - 1) + 4u) = MEM32(b_va + 4u);
    uint32_t new_total = 1, old_total = 1;
    for (uint16_t d = 0; d < cdims; d++) {
        uint32_t ce = MEM32(sa_va + 16u + 8u * d);
        new_total *= ce;
        old_total *= (d == cdims - 1) ? old_last : ce;
    }
    uint32_t ndata = hle_va(hle_alloc(new_total ? new_total * cb : cb));
    uint32_t odata = MEM32(sa_va + 12);
    if (ndata) {
        memset(hle_ptr(ndata), 0, new_total ? new_total * cb : cb);
        uint32_t cp = (old_total < new_total ? old_total : new_total) * cb;
        if (odata && cp) memcpy(hle_ptr(ndata), hle_ptr(odata), cp);
    }
    if (odata) hle_free(hle_ptr(odata));
    MEM32(sa_va + 12) = ndata;
    RET(S_OK, 2);
}

static void a_SafeArrayGetLBound(void) {
    uint32_t sa_va = A32(0), dim = A32(1), out_va = A32(2);
    if (!sa_va || !out_va || dim < 1 || dim > MEM16(sa_va)) { RET(0x80070057u, 3); return; }
    MEM32(out_va) = MEM32(sa_va + 16u + 8u * (dim - 1) + 4u);
    RET(S_OK, 3);
}

static void a_SafeArrayGetUBound(void) {
    uint32_t sa_va = A32(0), dim = A32(1), out_va = A32(2);
    if (!sa_va || !out_va || dim < 1 || dim > MEM16(sa_va)) { RET(0x80070057u, 3); return; }
    uint32_t cnt = MEM32(sa_va + 16u + 8u * (dim - 1));
    int32_t lb = (int32_t)MEM32(sa_va + 16u + 8u * (dim - 1) + 4u);
    MEM32(out_va) = (uint32_t)(lb + (int32_t)cnt - 1);
    RET(S_OK, 3);
}

static void a_SafeArrayPtrOfIndex(void) {
    uint32_t sa_va = A32(0), idx_va = A32(1), ppv_va = A32(2);
    if (!sa_va || !ppv_va) { RET(0x80070057u, 3); return; }
    int ok = 0;
    uint32_t off = safearray_offset(sa_va, (const int32_t *)hle_ptr(idx_va), &ok);
    uint32_t data = MEM32(sa_va + 12);
    if (!ok || !data) { RET(0x8002000Bu /* DISP_E_BADINDEX */, 3); return; }
    MEM32(ppv_va) = data + off;
    RET(S_OK, 3);
}

static void a_SafeArrayGetElement(void) {
    uint32_t sa_va = A32(0), idx_va = A32(1), pv_va = A32(2);
    if (!sa_va || !pv_va) { RET(0x80070057u, 3); return; }
    int ok = 0;
    uint32_t off = safearray_offset(sa_va, (const int32_t *)hle_ptr(idx_va), &ok);
    uint32_t data = MEM32(sa_va + 12), cb = MEM32(sa_va + 4);
    if (!ok || !data) { RET(0x8002000Bu, 3); return; }
    memcpy(hle_ptr(pv_va), (uint8_t *)hle_ptr(data) + off, cb);
    RET(S_OK, 3);
}

static void a_SafeArrayPutElement(void) {
    uint32_t sa_va = A32(0), idx_va = A32(1), pv_va = A32(2);
    if (!sa_va || !pv_va) { RET(0x80070057u, 3); return; }
    int ok = 0;
    uint32_t off = safearray_offset(sa_va, (const int32_t *)hle_ptr(idx_va), &ok);
    uint32_t data = MEM32(sa_va + 12), cb = MEM32(sa_va + 4);
    if (!ok || !data) { RET(0x8002000Bu, 3); return; }
    memcpy((uint8_t *)hle_ptr(data) + off, hle_ptr(pv_va), cb);
    RET(S_OK, 3);
}

static void a_GetActiveObject(void) {
    if (A32(2)) MEM32(A32(2)) = 0;
    RET(0x800401E3u /* MK_E_UNAVAILABLE */, 3);
}

const win32hle_shim win32hle_ole32[] = {
    { "CoCreateGuid",          o_CoCreateGuid },
    { "CoCreateInstance",      o_CoCreateInstance },
    { "CoGetClassObject",      o_CoGetClassObject },
    { "CoTaskMemAlloc",        o_CoTaskMemAlloc },
    { "CoTaskMemFree",         o_CoTaskMemFree },
    { "CreateStreamOnHGlobal", o_CreateStreamOnHGlobal },
    { "OleInitialize",         o_OleInitialize },
    { "OleUninitialize",       o_OleUninitialize },
    { "CoInitialize",          o_CoInitialize },
    { "CoUninitialize",        o_CoUninitialize },
    { "CoRegisterClassObject", o_CoRegisterClassObject },
    { "CoRevokeClassObject",   o_CoRevokeClassObject },
    { "CoDisconnectObject",    o_CoDisconnectObject },
    { "CoFileTimeNow",         o_CoFileTimeNow },
    { "CLSIDFromString",       o_CLSIDFromString },
    { "ProgIDFromCLSID",       o_ProgIDFromCLSID },
    { "StringFromGUID2",       o_StringFromGUID2 },
    { "StringFromCLSID",       o_StringFromCLSID },
    { "IsAccelerator",         o_IsAccelerator },
    { "IsEqualGUID",           o_IsEqualGUID },
    { "OleRun",                o_OleRun },
    { "OleDraw",               o_OleDraw },
    { "OleSetMenuDescriptor",  o_OleSetMenuDescriptor },
#define AUT(ord, name, fn) { "oleaut32.dll#" #ord, fn }, { name, fn }
    AUT(2, "SysAllocString",        a_SysAllocString),
    AUT(4, "SysAllocStringLen",     a_SysAllocStringLen),
    AUT(5, "SysReAllocStringLen",   a_SysReAllocStringLen),
    AUT(6, "SysFreeString",         a_SysFreeString),
    AUT(8, "VariantInit",           a_VariantInit),
    AUT(9, "VariantClear",          a_VariantClear),
    AUT(10, "VariantCopy",          a_VariantCopy),
    AUT(11, "VariantCopyInd",       a_VariantCopy),
    AUT(12, "VariantChangeType",    a_VariantChangeType),
    AUT(147, "VariantChangeTypeEx", a_VariantChangeTypeEx),
    AUT(15, "SafeArrayCreate",      a_SafeArrayCreate),
    AUT(19, "SafeArrayGetLBound",   a_SafeArrayGetLBound),
    AUT(20, "SafeArrayGetUBound",   a_SafeArrayGetUBound),
    AUT(25, "SafeArrayGetElement",  a_SafeArrayGetElement),
    AUT(26, "SafeArrayPutElement",  a_SafeArrayPutElement),
    AUT(40, "SafeArrayRedim",       a_SafeArrayRedim),
    AUT(148, "SafeArrayPtrOfIndex", a_SafeArrayPtrOfIndex),
    AUT(35, "GetActiveObject",      a_GetActiveObject),
    AUT(200, "GetErrorInfo",        a_GetErrorInfo),
    AUT(201, "SetErrorInfo",        o_notimpl_2),
    AUT(202, "CreateErrorInfo",     o_notimpl_1),
    AUT(161, "LoadTypeLib",         o_notimpl_2),
    AUT(33, "RegisterActiveObject", o_notimpl_4),
    AUT(34, "RevokeActiveObject",   o_notimpl_2),
    { 0, 0 }
};
