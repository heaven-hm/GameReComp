/* d3d9_adapter_id_selftest.c - deterministic fixture for the D3D9
 * GetAdapterIdentifier slot.
 *
 * This is a pure descriptive query: it must NOT answer D3DERR_NOTAVAILABLE
 * the way the old d3d9_na4 slot did. The fixture drives the registered shim
 * both through hle_call_guest (HRESULT / struct) and directly with a synthetic
 * guest stack (exact stdcall purge), so a wrong RET() pop count is visible.
 * No renderer is involved: the identifier is filled from the frontend's own
 * shared-display identity and the physical-device/WHQL fields stay honest. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "win32hle.h"
#include "host.h"

#define D3D_OK_VALUE                0u
#define E_POINTER_VALUE             0x80004003u
#define D3DERR_INVALIDCALL_VALUE    0x8876086Cu
#define D3DERR_NOTAVAILABLE_VALUE   0x8876086Au
#define D3D_SDK_VERSION_VALUE       32u
#define D3DENUM_WHQL_LEVEL_VALUE    2u

/* D3DADAPTER_IDENTIFIER9 fixed offsets and size (public D3D9 ABI). */
#define ID_SIZE      1100u
#define DRIVER_OFF   0u
#define DESC_OFF     512u
#define NAME_OFF     1024u
#define DRIVER_VER_OFF 1056u
#define VENDOR_OFF   1064u
#define GUID_OFF     1080u
#define WHQL_OFF     1096u

static const char EXPECT_DRIVER[] = "win32hle";
static const char EXPECT_DESC[] =
    "GameReComp D3D9 frontend (shared presenter, no rasteriser)";
static const char EXPECT_NAME[] = "d3d9";

/* Standalone host: no lifted dispatch table is linked into this build. */
const recomp_dispatch_entry_t recomp_dispatch_table[1];
const uint32_t recomp_dispatch_count = 0;

static int fails;
static void ok(const char *what, int cond) {
    if (!cond) { printf("FAIL %s\n", what); fails++; }
}
/* Poison the structure plus two guard bytes so an overrun is observable. */
static void poison(uint8_t *p) { memset(p, 0xCD, ID_SIZE + 2u); }
static uint32_t vtcall(uint32_t vt, unsigned slot, int argc, const uint32_t *args) {
    return hle_call_guest(MEM32(vt + 4u * slot), argc, args);
}
/* Call the registered shim the way lifted code does: synthetic return address
 * plus args on a guest stack, then measure how many words it popped.
 * hle_call_guest restores the register file and would hide a wrong purge. */
static uint32_t shim_pops(const char *name, int argc, const uint32_t *args,
                          uint32_t *out_eax) {
    uint32_t va = hle_resolve(name);
    recomp_func_t fn = va ? recomp_lookup_import(va) : 0;
    if (!fn) return 0xFFFFFFFFu;
    uint32_t saved_esp = g_esp, saved_eax = g_eax;
    uint32_t scratch = hle_va(hle_alloc(64));
    g_esp = scratch + 32;
    for (int i = argc - 1; i >= 0; --i) { g_esp -= 4; MEM32(g_esp) = args[i]; }
    g_esp -= 4; MEM32(g_esp) = RECOMP_RETADDR;
    uint32_t esp_in = g_esp;
    fn();
    uint32_t words = (g_esp - esp_in) / 4u;
    if (out_eax) *out_eax = g_eax;
    g_esp = saved_esp; g_eax = saved_eax;
    return words;
}

int main(void) {
    recomp_host_init();
    win32hle_register(win32hle_d3d9);
    mach_enter();

    uint32_t d3d = hle_call_guest(hle_resolve("Direct3DCreate9"), 1,
                                  (uint32_t[]){ D3D_SDK_VERSION_VALUE });
    ok("factory returns object", d3d != 0 && d3d != 0xFFFFFFFFu);
    uint32_t vt = MEM32(d3d);
    ok("object vtable set", vt != 0);

    /* Slot 5 is GetAdapterIdentifier and resolves to a real named shim. */
    const char *slot_name = "D3D9::Object::GetAdapterIdentifier";
    ok("GetAdapterIdentifier vtable slot registered", hle_resolve(slot_name) != 0);
    ok("GetAdapterIdentifier at slot 5", MEM32(vt + 4u * 5u) == hle_resolve(slot_name));

    uint32_t buf = hle_va(hle_alloc(ID_SIZE + 64u));
    uint8_t *p = hle_ptr(buf);

    /* Error codes: NULL out, bad adapter, bad flag bits. */
    uint32_t hr = 0;
    ok("NULL pIdentifier is E_POINTER", vtcall(vt, 5, 4,
       (uint32_t[]){ d3d, 0u, 0u, 0u }) == E_POINTER_VALUE);
    ok("bad adapter is INVALIDCALL", vtcall(vt, 5, 4,
       (uint32_t[]){ d3d, 1u, 0u, buf }) == D3DERR_INVALIDCALL_VALUE);
    ok("bad flag bits are INVALIDCALL", vtcall(vt, 5, 4,
       (uint32_t[]){ d3d, 0u, 4u, buf }) == D3DERR_INVALIDCALL_VALUE);
    /* The old slot answered NOTAVAILABLE for a valid query; prove that is gone. */
    poison(p);
    uint32_t got = vtcall(vt, 5, 4, (uint32_t[]){ d3d, 0u, 0u, buf });
    ok("valid query is D3D_OK (not NOTAVAILABLE)", got == D3D_OK_VALUE);
    ok("valid query is not D3DERR_NOTAVAILABLE", got != D3DERR_NOTAVAILABLE_VALUE);

    /* Filled fields: three fixed ASCII strings, NUL-terminated in place. */
    ok("Driver string", memcmp(p + DRIVER_OFF, EXPECT_DRIVER, sizeof EXPECT_DRIVER) == 0);
    ok("Description string", memcmp(p + DESC_OFF, EXPECT_DESC, sizeof EXPECT_DESC) == 0);
    ok("DeviceName string", memcmp(p + NAME_OFF, EXPECT_NAME, sizeof EXPECT_NAME) == 0);
    ok("Driver->Description gap zeroed",
       p[DRIVER_OFF + sizeof EXPECT_DRIVER] == 0 &&
       p[DESC_OFF - 1u] == 0);
    ok("DeviceName tail zeroed", p[NAME_OFF + sizeof EXPECT_NAME] == 0);
    /* Physical-device, driver-version, GUID and WHQL fields stay honest zero. */
    {
        int zero = 1;
        for (unsigned i = DRIVER_VER_OFF; i < VENDOR_OFF; i++) if (p[i]) zero = 0;
        for (unsigned i = VENDOR_OFF; i < GUID_OFF; i++) if (p[i]) zero = 0;
        for (unsigned i = GUID_OFF; i < WHQL_OFF; i++) if (p[i]) zero = 0;
        if (MEM32(buf + WHQL_OFF) != 0u) zero = 0;
        ok("physical/WHQL fields are zero", zero);
    }
    /* Nothing was written past the documented 1100-byte structure. */
    ok("no write past struct", p[ID_SIZE] == 0xCD && p[ID_SIZE + 1u] == 0xCD);

    /* D3DENUM_WHQL_LEVEL is an accepted flag. */
    poison(p);
    ok("D3DENUM_WHQL_LEVEL flag accepted", vtcall(vt, 5, 4,
       (uint32_t[]){ d3d, 0u, D3DENUM_WHQL_LEVEL_VALUE, buf }) == D3D_OK_VALUE);
    ok("WHQL struct still filled", memcmp(p + DRIVER_OFF, EXPECT_DRIVER,
       sizeof EXPECT_DRIVER) == 0 && MEM32(buf + WHQL_OFF) == 0u);

    /* Exact stdcall purges: ret + this + 3 args = 5 words on every path. */
    ok("purge on success", shim_pops(slot_name, 4,
       (uint32_t[]){ d3d, 0u, 0u, buf }, &hr) == 5u);
    ok("success returns D3D_OK", hr == D3D_OK_VALUE);
    ok("purge on NULL out", shim_pops(slot_name, 4,
       (uint32_t[]){ d3d, 0u, 0u, 0u }, &hr) == 5u);
    ok("NULL out returns E_POINTER", hr == E_POINTER_VALUE);
    ok("purge on bad adapter", shim_pops(slot_name, 4,
       (uint32_t[]){ d3d, 1u, 0u, buf }, &hr) == 5u);
    ok("bad adapter returns INVALIDCALL", hr == D3DERR_INVALIDCALL_VALUE);
    ok("purge on bad flags", shim_pops(slot_name, 4,
       (uint32_t[]){ d3d, 0u, 4u, buf }, &hr) == 5u);
    ok("bad flags returns INVALIDCALL", hr == D3DERR_INVALIDCALL_VALUE);

    ok("D3D9 final Release destroys", vtcall(vt, 2, 1, &d3d) == 0u);

    mach_leave();
    if (!fails) printf("d3d9_adapter_id_selftest: all checks passed\n");
    else printf("d3d9_adapter_id_selftest: %d check(s) failed\n", fails);
    return fails != 0;
}
