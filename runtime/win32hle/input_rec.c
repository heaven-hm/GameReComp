/*
 * input_rec.c - record the input a run saw, and replay it into another run.
 *
 * A replay is only a replay if the second run receives the same input as the
 * first. This records **what the input shims handed the guest** - the key state
 * array, the mouse state, and each event the queue delivered - and feeds those
 * same values back when asked. It is a transcript of the guest's input, not a
 * capture of physical devices: the point is that a replay is exact, not that a
 * recording looks like a human played it.
 *
 *   RECOMP_INPUT_RECORD=<file>   record what the input shims return
 *   RECOMP_INPUT=<file>          replay that recording instead of reading the host
 *
 * The transcript is text, one record per line - "keys <hex>", "mouse <hex>",
 * "event <hex>" - so it can be read, diffed and hand-edited. Records are taken
 * in call order, which is what makes replay exact: the clock and the call cap
 * already make a run's call order reproducible, and this rides on that.
 *
 * What it does not cover, and says so: DirectInput (dinput.c) reads SDL's live
 * state on its own path, so a DirectInput game's device reads are not in this
 * transcript yet. Recording is off unless asked, so an ordinary run and every
 * recorded baseline are untouched.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "win32hle.h"

static FILE *g_rec;                  /* the recording being written */
static FILE *g_rep;                  /* the recording being replayed */
static int g_init = -1;

void hle_clock_config(char *buf, size_t n);       /* clock.c */

/* What this recorder covers, and what it does not. Written into every
 * recording: a transcript of three keypresses that looked like "a gameplay
 * scenario" would be the same overclaim as calling a title screen a working
 * game. */
#define INPUT_COVERS "SDL_GetKeyState SDL_PollEvent SDL_GetMouseState"
#define INPUT_NOT_COVERS "DirectInput GetDeviceState/GetDeviceData, SDL joystick, raw input, window messages"

static void write_header(FILE *f) {
    char clk[32];
    hle_clock_config(clk, sizeof clk);
    fprintf(f, "# recon-input v1\n");
    fprintf(f, "# covers: %s\n", INPUT_COVERS);
    fprintf(f, "# not_covers: %s\n", INPUT_NOT_COVERS);
    fprintf(f, "# clock: %s\n", clk);
    fprintf(f, "# call_cap: %u\n", hle_call_cap);
    fprintf(f, "# call_at_start: %u\n", hle_call_count);
    fflush(f);
}

static void check_header(FILE *f) {
    char line[512], clk[32];
    hle_clock_config(clk, sizeof clk);
    while (fgets(line, sizeof line, f)) {
        if (line[0] != '#') { fseek(f, -((long)strlen(line)), SEEK_CUR); return; }
        char val[128];
        if (sscanf(line, "# clock: %127s", val) == 1 && strcmp(val, clk))
            fprintf(stderr, "[input] the recording was made with clock %s and this run has %s: "
                            "the replay is not reproducible. Set RECOMP_CLOCK=%s.\n", val, clk, val);
        if (sscanf(line, "# call_cap: %127s", val) == 1) {
            unsigned cap = (unsigned)strtoul(val, NULL, 0);
            if (cap != hle_call_cap)
                fprintf(stderr, "[input] the recording was made with call cap %u and this run has %u: "
                                "a capped replay stops somewhere else.\n", cap, hle_call_cap);
        }
        if (sscanf(line, "# covers: %127[^\n]", val) == 1)
            fprintf(stderr, "[input] replaying a recording that covers: %s\n", val);
    }
}

static void input_rec_init(void) {
    if (g_init >= 0) return;
    g_init = 0;
    const char *rec = getenv("RECOMP_INPUT_RECORD");
    const char *rep = getenv("RECOMP_INPUT");
    if (rec && *rec) {
        g_rec = fopen(rec, "w");
        if (!g_rec) fprintf(stderr, "[input] cannot write the recording %s\n", rec);
        else write_header(g_rec);
    }
    if (rep && *rep) {
        g_rep = fopen(rep, "r");
        if (!g_rep) fprintf(stderr, "[input] cannot read the recording %s\n", rep);
        else check_header(g_rep);
    }
}

/* Re-read the environment and close whatever is open. For probes and tests,
 * which switch between recording and replaying in one process; a normal run
 * reads the environment once. */
void hle_input_reset(void) {
    if (g_rec) fclose(g_rec);
    if (g_rep) fclose(g_rep);
    g_rec = g_rep = NULL;
    g_init = -1;
}

int hle_input_recording(void) { input_rec_init(); return g_rec != NULL; }
int hle_input_replaying(void) { input_rec_init(); return g_rep != NULL; }

static void put_hex(FILE *f, const uint8_t *d, size_t n) {
    for (size_t i = 0; i < n; i++) fprintf(f, "%02x", d[i]);
}

static int get_hex(const char *line, uint8_t *d, size_t n) {
    if (strlen(line) < n * 2) return 0;
    for (size_t i = 0; i < n; i++) {
        unsigned v;
        if (sscanf(line + i * 2, "%2x", &v) != 1) return 0;
        d[i] = (uint8_t)v;
    }
    return 1;
}

/* Record one input value. Called by the shim that produced it. */
void hle_input_record(const char *what, const void *data, size_t n) {
    input_rec_init();
    if (!g_rec) return;
    fprintf(g_rec, "%s ", what);
    put_hex(g_rec, data, n);
    fputc('\n', g_rec);
    fflush(g_rec);                   /* a crash mid-run still leaves the transcript */
}

/* Replay one input value: 1 and the value filled in if the transcript has a
 * record of this kind, 0 if it does not - the caller then falls back to the
 * host, so a partial transcript degrades rather than fabricating input. */
int hle_input_replay(const char *what, void *data, size_t n) {
    input_rec_init();
    if (!g_rep) return 0;
    char line[4096], kind[32];
    while (fgets(line, sizeof line, g_rep)) {
        line[strcspn(line, "\r\n")] = 0;
        if (sscanf(line, "%31s", kind) != 1) continue;
        if (strcmp(kind, what)) return 0;        /* out of step: stop, do not guess */
        if (!get_hex(line + strlen(kind) + (line[strlen(kind)] ? 1 : 0), data, n)) return 0;
        return 1;
    }
    return 0;
}
