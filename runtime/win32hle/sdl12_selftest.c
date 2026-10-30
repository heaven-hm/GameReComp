/* SDL 1.2-on-SDL2 regression: guest surfaces/events work headless and SDL's
 * cdecl imports leave their arguments for the lifted caller to discard. */
#define RECOMP_GENERATED_CODE
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <SDL2/SDL.h>
#include "win32hle.h"

#define ENTRY_VA 0x00401000u
static int fails;
static uint32_t va_delay, cdecl_ok;
static void ok(const char *w, int c) { if (!c) { printf("FAIL %s\n", w); fails++; } }
static uint16_t u16(uint32_t p) { return (uint16_t)(MEM8(p) | ((uint16_t)MEM8(p + 1) << 8)); }
#define GUEST_PROLOGUE(va) \
    uint32_t _cf=0; uint32_t _flag_k=FK_NONE; uint32_t _flag_a=0,_flag_b=0; RECOMP_ENTER(va)

static void guest_cdecl(void) {
    GUEST_PROLOGUE(ENTRY_VA);
    uint32_t before = esp;
    PUSH32(esp, 0); RECOMP_ICALL(va_delay);
    cdecl_ok = esp == before - 4;      /* SDL_Delay is cdecl: argument remains */
    esp += 4;                          /* caller's add esp, 4 */
}
const recomp_dispatch_entry_t recomp_dispatch_table[] = { { ENTRY_VA, guest_cdecl } };
const uint32_t recomp_dispatch_count = 1;
static uint32_t call(const char *name, int n, const uint32_t *a) { return hle_call_guest(hle_resolve(name), n, a); }

int main(void) {
    setenv("SDL_VIDEODRIVER", "dummy", 1);
    win32hle_register(win32hle_sdl12);
    va_delay = hle_resolve("SDL_Delay");
    ok("SDL imports resolve", va_delay && hle_resolve("SDL_SetVideoMode") && hle_resolve("SDL_PollEvent"));
    hle_call_guest(ENTRY_VA, 0, NULL);
    ok("SDL cdecl leaves caller argument", cdecl_ok);
    ok("SDL_Init", call("SDL_Init", 1, (uint32_t[]){ SDL_INIT_VIDEO }) == 0);
    uint32_t video = call("SDL_SetVideoMode", 4, (uint32_t[]){ 8, 8, 32, 0 });
    ok("SDL_SetVideoMode", video != 0 && MEM32(video + 16) != 0);
    ok("SDL_GetVideoSurface", call("SDL_GetVideoSurface", 0, NULL) == video);
    ok("SDL pixel format masks", MEM32(MEM32(video + 4) + 16) == 0x00FF0000u && MEM32(MEM32(video + 4) + 28) == 0xFF000000u);
    ok("SDL_FillRect", call("SDL_FillRect", 3, (uint32_t[]){ video, 0, 0xFF102030u }) == 0);
    ok("SDL_FillRect wrote pixels", MEM32(MEM32(video + 16)) == 0xFF102030u);
    uint32_t src = call("SDL_CreateRGBSurface", 8, (uint32_t[]){ 0, 2, 2, 32, 0x00FF0000u, 0x0000FF00u, 0x000000FFu, 0xFF000000u });
    ok("SDL_CreateRGBSurface", src != 0);
    call("SDL_FillRect", 3, (uint32_t[]){ src, 0, 0xFFA0B0C0u });
    uint32_t *dstrect = hle_alloc(8); dstrect[0] = (2u << 16) | 3u; dstrect[1] = (2u << 16) | 2u;
    ok("SDL_BlitSurface", call("SDL_BlitSurface", 4, (uint32_t[]){ src, 0, video, hle_va(dstrect) }) == 0);
    ok("SDL_BlitSurface copied", MEM32(MEM32(video + 16) + 3 * 4 + 2 * 32) == 0xFFA0B0C0u);
    SDL_Event e; SDL_zero(e); e.type = SDL_KEYDOWN; e.key.state = SDL_PRESSED; e.key.keysym.sym = SDLK_a; SDL_PushEvent(&e);
    uint8_t *event = hle_alloc(32);
    int saw_key = 0;
    for (int i = 0; i < 16 && call("SDL_PollEvent", 1, (uint32_t[]){ hle_va(event) }); i++)
        if (MEM32(hle_va(event)) == 2 && u16(hle_va(event) + 12) == SDLK_a) saw_key = 1;
    ok("SDL_PollEvent", saw_key);
    call("SDL_FreeSurface", 1, &src);
    call("SDL_Quit", 0, NULL);
    if (!fails) printf("sdl12_selftest: all checks passed\n");
    return fails != 0;
}
