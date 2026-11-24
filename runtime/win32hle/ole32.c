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
 * Structured storage itself lives in storage.c (compound files on disk and
 * on an ILockBytes); the in-memory byte-array object that storages are built
 * on, and a bind context for moniker binding, are here. OLE embedding and
 * type libraries are not here: they fail with E_NOTIMPL.
 *
 * CreateBindCtx follows Wine's dlls/ole32/bindctx.c (LGPL-2.1+): a NULL out
 * or a non-zero reserved value is E_INVALIDARG, the context starts with
 * BIND_OPTS {cbStruct=32, grfMode=STGM_READWRITE, CLSCTX_SERVER, locale},
 * IBindCtx::EnumObjectParam is E_NOTIMPL (as in native OLE), and a missing
 * key is E_FAIL. CreateILockBytesOnHGlobal follows the Microsoft API contract
 * (coml2api.h): hGlobal==NULL allocates a fresh guest-heap byte array, a
 * supplied handle is used in place, fDeleteOnRelease controls ownership, and
 * region locking is refused (STG_E_INVALIDFUNCTION). OleGetIconOfClass returns
 * NULL, the documented "no icon" answer and exactly what Wine/ReactOS's
 * ole2stubs.c do in a headless build. See docs/MAXCOMP_B_PROVENANCE.md.
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
#define S_FALSE              1u
#define E_NOTIMPL            0x80004001u
#define E_NOINTERFACE        0x80004002u
#define E_POINTER            0x80004003u
#define E_INVALIDARG         0x80070057u
#define E_FAIL               0x80004005u
#define REGDB_E_CLASSNOTREG  0x80040154u
#define CO_E_CLASSSTRING     0x800401F3u
#define MK_E_NOTBOUND        0x800401E9u
#define STGM_READWRITE       0x2u
#define CLSCTX_SERVER        0x7u                   /* inproc | local | handler */
#define STG_E_INVALIDFUNCTION 0x80030001u
#define STG_E_INVALIDPOINTER  0x80030009u

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
/* Interface identity: a side table keeps IID -> interface VA out of the guest
 * object layout, so existing DirectX objects are unchanged.  Objects that
 * never bind an IID keep the legacy single-interface answer. */
#define MAX_COM_IIDS 256
static struct { uint32_t obj, iface; uint8_t iid[16]; } g_iids[MAX_COM_IIDS];
static int g_niids;

int hle_com_iid_equal(const uint8_t *a, const uint8_t *b) {
    return a && b && memcmp(a, b, 16) == 0;
}
void hle_com_bind_iid(uint32_t obj, const uint8_t *iid, uint32_t iface) {
    if (!obj || !iid || !iface) return;
    for (int i = 0; i < g_niids; i++)
        if (g_iids[i].obj == obj && !memcmp(g_iids[i].iid, iid, 16)) {
            g_iids[i].iface = iface;
            return;
        }
    if (g_niids < MAX_COM_IIDS) {
        g_iids[g_niids].obj = obj, g_iids[g_niids].iface = iface;
        memcpy(g_iids[g_niids].iid, iid, 16);
        g_niids++;
    }
}
void hle_com_unbind_iids(uint32_t obj) {
    int w = 0;
    for (int i = 0; i < g_niids; i++) {
        if (g_iids[i].obj == obj) continue;
        if (w != i) g_iids[w] = g_iids[i];
        w++;
    }
    g_niids = w;
}
static int bound_iids(uint32_t obj) {
    for (int i = 0; i < g_niids; i++) if (g_iids[i].obj == obj) return 1;
    return 0;
}

/* IUnknown for host-served objects: interface identity is compared against the
 * bound IID(s); unsupported interfaces return E_NOINTERFACE with a NULL out.
 * A NULL out or iid is E_POINTER, as required by the COM contract. */
void hle_com_QueryInterface(void) {                      /* (this, iid, &out) */
    uint32_t self = A32(0), iid = A32(1), out = A32(2);
    if (!out || !iid) { if (out) MEM32(out) = 0; RET(0x80004003u, 3); } /* E_POINTER */
    if (bound_iids(self)) {
        const uint8_t *g = hle_ptr(iid);
        for (int i = 0; i < g_niids; i++)
            if (g_iids[i].obj == self && hle_com_iid_equal(g, g_iids[i].iid)) {
                uint32_t iface = g_iids[i].iface;
                MEM32(out) = iface;
                MEM32(iface + 4)++;
                RET(S_OK, 3);
            }
        MEM32(out) = 0;
        RET(0x80004002u, 3);                              /* E_NOINTERFACE */
    }
    MEM32(out) = self;
    MEM32(self + 4)++;
    RET(S_OK, 3);
}
void hle_com_AddRef(void) { RET(++MEM32(A32(0) + 4), 1); }
void hle_com_Release(void) {
    uint32_t self = A32(0), n = --MEM32(self + 4);
    if (!n) { hle_com_unbind_iids(self); hle_com_free(self); }
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
/* Call a slot on a COM interface the *guest* handed us (the class object from
 * CoRegisterClassObject, or the IClassFactory its QI returned). A guest object
 * keeps its vtable VA in dword 0 and each vtable slot holds the guest VA of the
 * lifted method, so reaching a method needs two reads: first the vtable, then
 * the slot. Reading MEM32(obj + 4*slot) instead names an object *field* (the
 * refcount at +4, host state at +8) as the function, which either dies with
 * "no lifted function" or calls the wrong method. IClassFactory slots follow
 * the public COM ABI: 0 QueryInterface, 1 AddRef, 2 Release, 3 CreateInstance,
 * 4 LockServer (also recorded in tools/lift/name_lift.py's COM_SLOTS). */
static uint32_t vcall(uint32_t obj, int slot, int nargs, const uint32_t *args) {
    uint32_t a[8] = { obj };
    for (int i = 0; i < nargs; i++) a[1 + i] = args[i];
    return hle_call_guest(MEM32(MEM32(obj) + 4u * (uint32_t)slot), nargs + 1, a);
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
/* COM initialisation state is per guest thread. The apartment model is the
 * only part of COM initialisation this layer models: repeated initialisation
 * with the same model returns S_FALSE and a different model is refused with
 * RPC_E_CHANGED_MODE, as Win32 does. No host COM apartment is created. */
static __thread unsigned g_com_init_count;
static __thread uint32_t g_com_model;         /* COINIT_APARTMENTTHREADED bit */
static uint32_t com_init(uint32_t flags) {
    uint32_t model = flags & 0x2u;              /* COINIT_APARTMENTTHREADED */
    if (g_com_init_count) {
        if (g_com_model != model) return 0x80010106u;    /* RPC_E_CHANGED_MODE */
        g_com_init_count++;
        return 1u;                              /* S_FALSE */
    }
    g_com_model = model;
    g_com_init_count = 1;
    return S_OK;
}
static void o_CoInitializeEx(void) {            /* (reserved, dwCoInit) */
    if (A32(0)) { RET(0x80070057u, 2); }        /* E_INVALIDARG: must be NULL */
    RET(com_init(A32(1)), 2);
}
static void o_CoInitialize(void) {              /* (reserved) */
    if (A32(0)) { RET(0x80070057u, 1); }
    RET(com_init(0x2u), 1);                     /* COINIT_APARTMENTTHREADED */
}
static void o_CoUninitialize(void) {
    if (g_com_init_count) {
        if (!--g_com_init_count) g_com_model = 0;
    }
    RETV(0);
}
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
/* ---- the COM task allocator ----
 * Its blocks come from the same guest heap as Heap/Local/Global (guest_mem.c),
 * so every pointer handed back is a guest VA (hle_va/RETP), never a host
 * address. Windows' task allocator follows the process heap: Alloc(0) still
 * returns a non-NULL block the caller must free; Realloc(NULL, cb) is
 * Alloc(cb); Realloc(pv, 0) frees pv and returns NULL. hle_heap_realloc keeps
 * min(old, cb) bytes and zeroes the growth, so a larger block reads back with
 * its old contents intact and a clean tail. */
static void o_CoTaskMemAlloc(void) {                     /* (cb) -> pv */
    uint32_t cb = A32(0);
    RETP(hle_alloc(cb ? cb : 1u), 1);
}
static void o_CoTaskMemRealloc(void) {                   /* (pv, cb) -> pv */
    uint32_t pv = A32(0), cb = A32(1);
    void *p;
    if (!pv) p = hle_alloc(cb ? cb : 1u);                /* NULL source: like Alloc(cb) */
    else if (!cb) { hle_free(APTR(0)); p = NULL; }       /* zero size: free it, return NULL */
    else p = hle_heap_realloc(APTR(0), cb, 1);          /* grow/shrink within the guest heap */
    RETP(p, 2);
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

/* ---- COM objects built here: ILockBytes and IBindCtx ----
 * Both are ordinary COM objects in guest memory (vt + refcount + host state at
 * +8), so storage.c can call their methods through the very same vtable a
 * lifted guest would use. All method shims use the guest stack, never a host
 * pointer as a guest address. */
static uint32_t com_vcall(uint32_t obj, int slot, int nargs, const uint32_t *args) {
    uint32_t a[8] = { obj };
    for (int i = 0; i < nargs && i < 7; i++) a[1 + i] = args[i];
    /* obj[0] is the vtable pointer; vtable[slot] is the method's import VA. */
    return hle_call_guest(MEM32(MEM32(obj) + 4u * (uint32_t)slot), nargs + 1, a);
}
static uint32_t iid_data1(uint32_t iid) {
    if (!iid) return 0xFFFFFFFFu;
    static const uint8_t tail[8] = { 0xC0, 0, 0, 0, 0, 0, 0, 0x46 };
    const uint8_t *g = (const uint8_t *)hle_ptr(iid);
    if (!g || *(const uint32_t *)(g + 4) || memcmp(g + 8, tail, 8)) return 0xFFFFFFFFu;
    return *(const uint32_t *)g;
}
static uint32_t wstr_len(uint32_t s) {
    uint32_t n = 0;
    if (!s) return 0;
    while (MEM16(s + 2u * n)) n++;
    return n;
}
static int wstr_eq(uint32_t a, uint32_t b) {
    uint32_t n = wstr_len(a);
    if (n != wstr_len(b)) return 0;
    for (uint32_t i = 0; i < n; i++) if (MEM16(a + 2u * i) != MEM16(b + 2u * i)) return 0;
    return 1;
}
static uint32_t wstr_dup(uint32_t s) {
    uint32_t n = wstr_len(s), va = hle_va(hle_alloc(2u * (n + 1u)));
    if (!va) return 0;
    for (uint32_t i = 0; i < n; i++) MEM16(va + 2u * i) = MEM16(s + 2u * i);
    return va;
}

/* ---- ILockBytes on a guest HGLOBAL ----
 * Vtable order is IUnknown (0-2) then ReadAt, WriteAt, Flush, SetSize,
 * LockRegion, UnlockRegion, Stat (3-9). ULARGE_INTEGER arguments are two
 * little-endian dwords on x86, low first. */
typedef struct { uint8_t *buf; uint32_t size, cap, hglobal, del; } lb_t;
#define LB(self) ((lb_t *)hle_ptr(MEM32((self) + 8)))

static void lb_QueryInterface(void) {
    uint32_t d1 = iid_data1(A32(1)), out = A32(2);
    if (!out) { RET(E_POINTER, 3); }
    MEM32(out) = 0;
    if (d1 == 0 || d1 == 0x0Au) {                    /* IUnknown / ILockBytes */
        MEM32(out) = A32(0);
        MEM32(A32(0) + 4)++;
        RET(S_OK, 3);
    }
    RET(E_NOINTERFACE, 3);
}
static void lb_AddRef(void) { RET(++MEM32(A32(0) + 4), 1); }
static void lb_Release(void) {
    uint32_t self = A32(0), n = --MEM32(self + 4);
    if (!n) {
        lb_t *s = LB(self);
        if (s) {
            if (s->del && s->buf) hle_free(s->buf);
            hle_free(hle_ptr(MEM32(self + 8)));
        }
        hle_com_free(self);
    }
    RET(n, 1);
}
static void lb_ReadAt(void) {                       /* (this, off.lo, off.hi, pv, cb, &read) */
    lb_t *s = LB(A32(0));
    uint64_t off = (uint64_t)A32(2) << 32 | A32(1);
    uint32_t cb = A32(4), n = 0;
    if (A32(5)) MEM32(A32(5)) = 0;
    if (s && off < s->size) {
        uint64_t avail = s->size - off;
        n = cb < avail ? cb : (uint32_t)avail;
        if (n && A32(3)) memcpy(hle_ptr(A32(3)), s->buf + (size_t)off, n);
    }
    if (A32(5)) MEM32(A32(5)) = n;
    RET(n < cb ? S_FALSE : S_OK, 6);
}
static void lb_WriteAt(void) {                      /* (this, off.lo, off.hi, pv, cb, &written) */
    lb_t *s = LB(A32(0));
    uint64_t off = (uint64_t)A32(2) << 32 | A32(1);
    uint32_t cb = A32(4), n = cb;
    if (A32(5)) MEM32(A32(5)) = 0;
    if (!s) { RET(STG_E_INVALIDPOINTER, 6); }
    if (off > 0xFFFFFFFFull || off + cb > 0xFFFFFFFEull) { RET(STG_E_INVALIDFUNCTION, 6); }
    if (off + cb > s->cap) {
        uint32_t cap = (uint32_t)(off + cb);
            uint8_t *b = (uint8_t *)hle_heap_realloc(s->buf, cap, 1);
            if (!b) RET(0x80030070u /* STG_E_MEDIUMFULL */, 6);
        s->buf = b, s->cap = cap;
    }
    if (n && A32(3)) memcpy(s->buf + (size_t)off, hle_ptr(A32(3)), n);
    if (off + cb > s->size) s->size = (uint32_t)(off + cb);
    if (A32(5)) MEM32(A32(5)) = n;
    RET(S_OK, 6);
}
static void lb_Flush(void) { RET(S_OK, 1); }
static void lb_SetSize(void) {                      /* (this, cb.lo, cb.hi) */
    lb_t *s = LB(A32(0));
    if (!s || A32(2)) { RET(E_INVALIDARG, 3); }
    if (A32(1) > s->cap) {
        uint8_t *b = (uint8_t *)hle_heap_realloc(s->buf, A32(1), 1);
        if (!b) RET(0x80030070u /* STG_E_MEDIUMFULL */, 3);
        s->buf = b, s->cap = A32(1);
    }
    s->size = A32(1);
    RET(S_OK, 3);
}
static void lb_no_lock(void) { RET(STG_E_INVALIDFUNCTION, 6); }  /* LockRegion/UnlockRegion */
static void lb_Stat(void) {                         /* (this, STATSTG, flag) */
    lb_t *s = LB(A32(0));
    uint32_t st = A32(1);
    if (!st) { RET(E_POINTER, 3); }
    memset(hle_ptr(st), 0, 72);
    MEM32(st + 4) = 3u;                              /* STGTY_LOCKBYTES */
    MEM32(st + 8) = s ? s->size : 0;
    MEM32(st + 12) = 0;
    MEM32(st + 40) = STGM_READWRITE;                 /* grfMode */
    MEM32(st + 44) = 0;                              /* no region locking */
    RET(S_OK, 3);
}
static uint32_t lockbytes_vtable(void) {
    static uint32_t vt;
    static const win32hle_shim methods[] = {
        { "ILockBytes::QueryInterface", lb_QueryInterface },
        { "ILockBytes::AddRef", lb_AddRef }, { "ILockBytes::Release", lb_Release },
        { "ILockBytes::ReadAt", lb_ReadAt }, { "ILockBytes::WriteAt", lb_WriteAt },
        { "ILockBytes::Flush", lb_Flush }, { "ILockBytes::SetSize", lb_SetSize },
        { "ILockBytes::LockRegion", lb_no_lock }, { "ILockBytes::UnlockRegion", lb_no_lock },
        { "ILockBytes::Stat", lb_Stat }, { 0, 0 }
    };
    if (!vt) vt = hle_com_vtable(methods);
    return vt;
}
static void o_CreateILockBytesOnHGlobal(void) {     /* (hglobal, delete_on_release, &out) */
    uint32_t out = A32(2), hg = A32(0), st_va, obj;
    if (!out) { RET(E_INVALIDARG, 3); }
    MEM32(out) = 0;
    st_va = hle_va(hle_alloc(sizeof(lb_t)));   /* 20 bytes: buf, size, cap, hglobal, del */
    lb_t *s = (lb_t *)hle_ptr(st_va);
    if (hg) {
        uint32_t szva = hle_resolve("GlobalSize");
        uint8_t *p = (uint8_t *)hle_ptr(hg);
        s->buf = p, s->hglobal = hg;
        s->size = s->cap = szva ? hle_call_guest(szva, 1, &hg) : 0;
    } else {
        s->buf = (uint8_t *)hle_alloc(1);            /* empty byte array */
        s->size = s->cap = 0;
    }
    s->del = A32(1) ? 1u : 0u;
    obj = hle_com_new(lockbytes_vtable(), 12);
    MEM32(obj + 8) = st_va;
    MEM32(out) = obj;
    RET(S_OK, 3);
}

/* ---- IBindCtx ----
 * Wine's bind context keeps a table of bound objects and named parameters.
 * We keep the same two notions (bound object / named parameter) in a fixed
 * table in guest memory; BIND_OPTS is BIND_OPTS3-sized (32 bytes) as in Wine's
 * CreateBindCtx. Nothing is fabricated: an unknown key is E_FAIL, an
 * unsupported interface is E_NOINTERFACE, and enumeration is E_NOTIMPL. */
#define BC_MAX 24
typedef struct { uint32_t ref, opts[8], n; struct { uint32_t obj, key; } e[BC_MAX]; } bc_t;
#define BC(self) ((bc_t *)hle_ptr(MEM32((self) + 8)))

static void bc_QueryInterface(void) {
    uint32_t d1 = iid_data1(A32(1)), out = A32(2);
    if (!out) { RET(E_POINTER, 3); }
    MEM32(out) = 0;
    if (d1 == 0 || d1 == 0x0Eu) {                    /* IUnknown / IBindCtx */
        MEM32(out) = A32(0);
        MEM32(A32(0) + 4)++;
        RET(S_OK, 3);
    }
    RET(E_NOINTERFACE, 3);
}
static void bc_AddRef(void) { RET(++MEM32(A32(0) + 4), 1); }
static void bc_Release(void) {
    uint32_t self = A32(0), n = --MEM32(self + 4);
    if (!n) {
        bc_t *c = BC(self);
        if (c) {
            for (uint32_t i = 0; i < c->n; i++) {
                if (c->e[i].obj) com_vcall(c->e[i].obj, 2, 0, NULL);   /* Release */
                if (c->e[i].key) hle_free(hle_ptr(c->e[i].key));
            }
            hle_free(hle_ptr(MEM32(self + 8)));
        }
        hle_com_free(self);
    }
    RET(n, 1);
}
static void bc_RegisterObjectBound(void) {          /* (this, punk) */
    bc_t *c = BC(A32(0));
    if (!A32(1)) { RET(S_OK, 2); }
    if (c && c->n < BC_MAX) {
        c->e[c->n].obj = A32(1), c->e[c->n++].key = 0;
        com_vcall(A32(1), 1, 0, NULL);               /* AddRef */
        RET(S_OK, 2);
    }
    RET(0x8007000Eu /* E_OUTOFMEMORY */, 2);
}
static void bc_RevokeObjectBound(void) {
    bc_t *c = BC(A32(0));
    if (!A32(1)) { RET(E_INVALIDARG, 2); }
    if (c) for (uint32_t i = 0; i < c->n; i++) if (c->e[i].obj == A32(1) && !c->e[i].key) {
        com_vcall(A32(1), 2, 0, NULL);
        for (uint32_t j = i; j + 1 < c->n; j++) c->e[j] = c->e[j + 1];
        c->n--;
        RET(S_OK, 2);
    }
    RET(MK_E_NOTBOUND, 2);
}
static void bc_ReleaseBoundObjects(void) {
    bc_t *c = BC(A32(0));
    if (c) {
        for (uint32_t i = 0; i < c->n; i++) {
            if (c->e[i].obj) com_vcall(c->e[i].obj, 2, 0, NULL);
            if (c->e[i].key) hle_free(hle_ptr(c->e[i].key));
        }
        c->n = 0;
    }
    RET(S_OK, 1);
}
static void bc_SetBindOptions(void) {               /* (this, BIND_OPTS*) */
    bc_t *c = BC(A32(0));
    uint32_t p = A32(1), cb;
    if (!p) { RET(E_POINTER, 2); }
    cb = MEM32(p);
    if (cb > 32) { RET(E_INVALIDARG, 2); }
    if (c) for (uint32_t i = 0; i < cb; i++) ((uint8_t *)c->opts)[i] = (uint8_t)MEM8(p + i);
    RET(S_OK, 2);
}
static void bc_GetBindOptions(void) {
    bc_t *c = BC(A32(0));
    uint32_t p = A32(1), cb;
    if (!p) { RET(E_POINTER, 2); }
    cb = MEM32(p);
    if (cb > 32) cb = 32;
    if (c) for (uint32_t i = 0; i < cb; i++) MEM8(p + i) = ((uint8_t *)c->opts)[i];
    MEM32(p) = cb;
    RET(S_OK, 2);
}
static void bc_GetRunningObjectTable(void) {
    if (A32(1)) MEM32(A32(1)) = 0;
    RET(E_NOTIMPL, 2);
}
static void bc_RegisterObjectParam(void) {          /* (this, wide key, punk) */
    bc_t *c = BC(A32(0));
    if (!A32(2)) { RET(E_INVALIDARG, 3); }
    if (c) {
        for (uint32_t i = 0; i < c->n; i++) if (c->e[i].key && wstr_eq(c->e[i].key, A32(1))) {
            if (c->e[i].obj) com_vcall(c->e[i].obj, 2, 0, NULL);
            c->e[i].obj = A32(2);
            com_vcall(A32(2), 1, 0, NULL);
            RET(S_OK, 3);
        }
        if (c->n < BC_MAX) {
            c->e[c->n].key = wstr_dup(A32(1));
            c->e[c->n].obj = A32(2);
            c->n++;
            com_vcall(A32(2), 1, 0, NULL);
            RET(S_OK, 3);
        }
    }
    RET(0x8007000Eu /* E_OUTOFMEMORY */, 3);
}
static void bc_GetObjectParam(void) {               /* (this, wide key, &punk) */
    bc_t *c = BC(A32(0));
    if (!A32(2)) { RET(E_POINTER, 3); }
    MEM32(A32(2)) = 0;
    if (c) for (uint32_t i = 0; i < c->n; i++) if (c->e[i].key && wstr_eq(c->e[i].key, A32(1))) {
        com_vcall(c->e[i].obj, 1, 0, NULL);
        MEM32(A32(2)) = c->e[i].obj;
        RET(S_OK, 3);
    }
    RET(E_FAIL, 3);
}
static void bc_EnumObjectParam(void) {              /* E_NOTIMPL in native OLE too (Wine) */
    if (A32(1)) MEM32(A32(1)) = 0;
    RET(E_NOTIMPL, 2);
}
static void bc_RevokeObjectParam(void) {
    bc_t *c = BC(A32(0));
    if (c) for (uint32_t i = 0; i < c->n; i++) if (c->e[i].key && wstr_eq(c->e[i].key, A32(1))) {
        if (c->e[i].obj) com_vcall(c->e[i].obj, 2, 0, NULL);
        hle_free(hle_ptr(c->e[i].key));
        for (uint32_t j = i; j + 1 < c->n; j++) c->e[j] = c->e[j + 1];
        c->n--;
        RET(S_OK, 2);
    }
    RET(E_FAIL, 2);
}
static uint32_t bindctx_vtable(void) {
    static uint32_t vt;
    static const win32hle_shim methods[] = {
        { "IBindCtx::QueryInterface", bc_QueryInterface },
        { "IBindCtx::AddRef", bc_AddRef }, { "IBindCtx::Release", bc_Release },
        { "IBindCtx::RegisterObjectBound", bc_RegisterObjectBound },
        { "IBindCtx::RevokeObjectBound", bc_RevokeObjectBound },
        { "IBindCtx::ReleaseBoundObjects", bc_ReleaseBoundObjects },
        { "IBindCtx::SetBindOptions", bc_SetBindOptions },
        { "IBindCtx::GetBindOptions", bc_GetBindOptions },
        { "IBindCtx::GetRunningObjectTable", bc_GetRunningObjectTable },
        { "IBindCtx::RegisterObjectParam", bc_RegisterObjectParam },
        { "IBindCtx::GetObjectParam", bc_GetObjectParam },
        { "IBindCtx::EnumObjectParam", bc_EnumObjectParam },
        { "IBindCtx::RevokeObjectParam", bc_RevokeObjectParam }, { 0, 0 }
    };
    if (!vt) vt = hle_com_vtable(methods);
    return vt;
}
static void o_CreateBindCtx(void) {                 /* (reserved, &out) */
    uint32_t out = A32(1), st_va, obj;
    if (!out) { RET(E_INVALIDARG, 2); }             /* Wine: E_INVALIDARG, not E_POINTER */
    MEM32(out) = 0;
    if (A32(0)) { RET(E_INVALIDARG, 2); }
    st_va = hle_va(hle_alloc(sizeof(bc_t)));
    bc_t *c = (bc_t *)hle_ptr(st_va);
    c->opts[0] = 32;                                /* cbStruct (BIND_OPTS3) */
    c->opts[1] = 0;                                 /* grfFlags */
    c->opts[2] = STGM_READWRITE;                    /* grfMode */
    c->opts[5] = CLSCTX_SERVER;                     /* dwClassContext */
    c->opts[6] = 0x409u;                            /* LOCALE_USER_DEFAULT */
    obj = hle_com_new(bindctx_vtable(), 12);
    MEM32(obj + 8) = st_va;
    MEM32(out) = obj;
    RET(S_OK, 2);
}

/* No icons in a headless host. Windows returns NULL when no class icon can
 * be found; Wine and ReactOS do the same in ole2stubs.c. */
static void o_OleGetIconOfClass(void) { (void)A32(0); (void)A32(1); (void)A32(2); RET(0, 3); }

const win32hle_shim win32hle_ole32[] = {
    { "CoCreateGuid",          o_CoCreateGuid },
    { "CoCreateInstance",      o_CoCreateInstance },
    { "CoGetClassObject",      o_CoGetClassObject },
    { "CoTaskMemAlloc",        o_CoTaskMemAlloc },
    { "CoTaskMemRealloc",      o_CoTaskMemRealloc },
    { "CoTaskMemFree",         o_CoTaskMemFree },
    { "CreateStreamOnHGlobal", o_CreateStreamOnHGlobal },
    { "CreateBindCtx",            o_CreateBindCtx },
    { "CreateILockBytesOnHGlobal", o_CreateILockBytesOnHGlobal },
    { "OleGetIconOfClass",        o_OleGetIconOfClass },
    { "OleInitialize",         o_OleInitialize },
    { "OleUninitialize",       o_OleUninitialize },
    { "CoInitialize",          o_CoInitialize }, { "CoInitializeEx", o_CoInitializeEx },
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
