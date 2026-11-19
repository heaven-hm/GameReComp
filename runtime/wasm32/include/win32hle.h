#ifndef _WASM_WIN32HLE_H
#define _WASM_WIN32HLE_H

#include <stdint.h>
#include <stddef.h>
#include <stdarg.h>
#include "recomp_types.h"

/* Host JS imports (implemented in run_wasm_node.mjs and index.html Worker) */
__attribute__((import_module("env"), import_name("wasm_host_log")))
void wasm_host_log(int stream, const char *ptr, int len);

__attribute__((import_module("env"), import_name("wasm_host_now_ms")))
double wasm_host_now_ms(void);

__attribute__((import_module("env"), import_name("wasm_host_present_fb")))
void wasm_host_present_fb(const uint32_t *rgba_ptr, int w, int h, const char *title);

/* out_ev[5]: { type, x, y, p1, p2 }
 * type: 0=timeout/timer, 1=mousemove, 2=mousedown, 3=mouseup, 4=keydown, 5=keyup, 6=command, -1=quit */
__attribute__((import_module("env"), import_name("wasm_host_wait_event")))
int wasm_host_wait_event(int timeout_ms, int *out_ev);

/* Companion DLL metadata (matches native win32hle for recomp_companions.c) */
typedef struct {
    const char *name;
    uint32_t    va;
} recomp_export_t;

typedef struct {
    const char            *name;
    uint32_t               image_base;
    void                  *host_addr;
    const unsigned char   *image_data;
    uint32_t               image_size;
    const recomp_export_t *exports;
    uint32_t               export_count;
} recomp_companion_t;

extern const uint32_t recomp_companion_count;
extern const recomp_companion_t *const recomp_companion_list;

/* Embedded PE image (generated in build/recomp_image_wasm.c) */
extern const int recomp_has_embedded_image;
extern const unsigned long recomp_original_image_size;
extern const char recomp_original_name[];
extern const unsigned char recomp_embedded_image[];

/* Linear memory & guest VA mapping helpers */
void    *wasm_alloc_aligned(size_t size, size_t align);
void     wasm_map_guest_region(uint32_t guest_va, uint32_t span);
void    *hle_ptr(uint32_t va);
uint32_t hle_guest_alloc(size_t size);
uint32_t hle_guest_strdup(const char *s);
int      hle_call_guest(uint32_t va, int argc, const uint32_t *argv);
__attribute__((noreturn)) void hle_exit(int code);
int      hle_get_exit_code(void);
int      hle_has_exited(void);
void     hle_logf(int stream, const char *fmt, ...);

/* Argument & return helpers */
static inline uint32_t wasm_arg32(int idx) {
    return MEM32(g_esp + 4u + (uint32_t)idx * 4u);
}
static inline const char *wasm_arg_str(int idx) {
    uint32_t va = wasm_arg32(idx);
    return va ? (const char *)hle_ptr(va) : NULL;
}
#define A32(i)   wasm_arg32(i)
#define ASTR(i)  wasm_arg_str(i)
#define APTR(i)  (wasm_arg32(i) ? hle_ptr(wasm_arg32(i)) : NULL)
#define RETC(v)  do { g_eax = (uint32_t)(v); g_esp += 4u; return; } while (0)
#define RETCV()  do { g_esp += 4u; return; } while (0)
#define RETS(n, v) do { g_eax = (uint32_t)(v); g_esp += 4u + 4u * (uint32_t)(n); return; } while (0)
#define RETSV(n)   do { g_esp += 4u + 4u * (uint32_t)(n); return; } while (0)

/* GDI / User32 subsystem */
void wasm_ui_init(const char *title);
void wasm_ui_enqueue_command(uint32_t cmd_id);
void wasm_ui_present(void);
int  wasm_ui_has_window(void);
recomp_func_t wasm_user32_gdi_lookup(const char *dll, const char *name);

/* Exported WebAssembly surface */
__attribute__((export_name("wasm_init")))        int      wasm_init(void);
__attribute__((export_name("wasm_frame")))       int      wasm_frame(double now_ms);
__attribute__((export_name("wasm_mouse_event"))) void     wasm_mouse_event(int type, int x, int y, int btn);
__attribute__((export_name("wasm_key_event")))   void     wasm_key_event(int down, int vk, int ch);
__attribute__((export_name("wasm_menu_action"))) void     wasm_menu_action(int cmd_id);
__attribute__((export_name("wasm_fb_ptr")))      uint32_t wasm_fb_ptr(void);
__attribute__((export_name("wasm_fb_width")))    int      wasm_fb_width(void);
__attribute__((export_name("wasm_fb_height")))   int      wasm_fb_height(void);

#endif
