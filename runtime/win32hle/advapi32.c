/*
 * advapi32.c - the registry, and the small system DLLs a game links for one
 * or two calls each: COMCTL32 (its version and InitCommonControls),
 * VERSION (file version resources) and SHELL32.
 *
 * The registry is in memory: a tree of keys, each with named values. A host
 * seeds what the game expects an installer to have written
 * (hle_reg_set_string/_dword), and may give it a file to persist to
 * (hle_reg_load/hle_reg_save: one "key\name=type:value" per line).
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>
#include "win32hle.h"

#define ERROR_SUCCESS        0u
#define ERROR_INVALID_PARAMETER 87u
#define ERROR_NOT_ENOUGH_MEMORY 8u
#define ERROR_FILE_NOT_FOUND 2u
#define ERROR_MORE_DATA    234u
#define ERROR_NO_MORE_ITEMS 259u
#define REG_SZ     1u
#define REG_BINARY 3u
#define REG_DWORD  4u

typedef struct value { char name[128]; uint32_t type, size; uint8_t *data; struct value *next; } value_t;
typedef struct key { char name[128]; struct key *parent, *child, *next; value_t *values; } rkey_t;
static rkey_t g_roots[6];                         /* HKCR HKCU HKLM HKU HKPD HKCC */
static const char *const g_root_names[6] = { "HKEY_CLASSES_ROOT", "HKEY_CURRENT_USER", "HKEY_LOCAL_MACHINE",
                                             "HKEY_USERS", "HKEY_PERFORMANCE_DATA", "HKEY_CURRENT_CONFIG" };
static const char *g_reg_file;

static rkey_t *root_of(uint32_t h) {
    if (h >= 0x80000000u && h <= 0x80000005u) return &g_roots[h - 0x80000000u];
    return (rkey_t *)hle_handle_obj(h, HLE_H_REGKEY);
}
static rkey_t *subkey(rkey_t *k, const char *path, int create) {
    char part[128];
    while (k && path && *path) {
        size_t n = strcspn(path, "\\");
        if (n >= sizeof part) n = sizeof part - 1;
        memcpy(part, path, n), part[n] = 0;
        path += n;
        while (*path == '\\') path++;
        if (!part[0]) continue;
        rkey_t *c = k->child;
        while (c && strcasecmp(c->name, part)) c = c->next;
        if (!c && create) {
            c = (rkey_t *)calloc(1, sizeof *c);
            snprintf(c->name, sizeof c->name, "%s", part);
            c->parent = k, c->next = k->child, k->child = c;
        }
        k = c;
    }
    return k;
}
static value_t *find_value(rkey_t *k, const char *name, int create) {
    value_t *v = k->values;
    if (!name) name = "";
    while (v && strcasecmp(v->name, name)) v = v->next;
    if (!v && create) {
        v = (value_t *)calloc(1, sizeof *v);
        snprintf(v->name, sizeof v->name, "%s", name);
        v->next = k->values, k->values = v;
    }
    return v;
}
static void set_value(rkey_t *k, const char *name, uint32_t type, const void *data, uint32_t size) {
    value_t *v = find_value(k, name, 1);
    free(v->data);
    v->data = (uint8_t *)malloc(size + 2);
    memcpy(v->data, data, size);
    v->data[size] = v->data[size + 1] = 0;
    v->type = type, v->size = size;
}

static int root_index(const char *name) {
    for (int i = 0; i < 6; i++) if (!strcasecmp(name, g_root_names[i])) return i;
    if (!strcasecmp(name, "HKLM")) return 2;
    if (!strcasecmp(name, "HKCU")) return 1;
    return -1;
}
void hle_reg_set_string(uint32_t root, const char *path, const char *name, const char *s) {
    set_value(subkey(root_of(root), path, 1), name, REG_SZ, s, (uint32_t)strlen(s) + 1);
}
void hle_reg_set_dword(uint32_t root, const char *path, const char *name, uint32_t v) {
    set_value(subkey(root_of(root), path, 1), name, REG_DWORD, &v, 4);
}

/* the file: HKEY_LOCAL_MACHINE\SOFTWARE\x\name=sz:text | dword:0x1 | hex:0a0b */
void hle_reg_load(const char *file) {
    FILE *f = fopen(file, "r");
    char line[2048];
    g_reg_file = file;
    while (f && fgets(line, sizeof line, f)) {
        line[strcspn(line, "\r\n")] = 0;
        char *eq = strchr(line, '='), *colon = eq ? strchr(eq, ':') : NULL;
        if (!eq || !colon) continue;
        *eq = 0, *colon = 0;
        char *bs = strchr(line, '\\'), *name = strrchr(line, '\\');
        if (!bs || !name) continue;
        *bs = 0, *name++ = 0;
        int r = root_index(line);
        if (r < 0) continue;
        rkey_t *k = subkey(&g_roots[r], name == bs + 1 ? "" : bs + 1, 1);
        if (!strcmp(eq + 1, "sz")) set_value(k, name, REG_SZ, colon + 1, (uint32_t)strlen(colon + 1) + 1);
        else if (!strcmp(eq + 1, "dword")) { uint32_t v = (uint32_t)strtoul(colon + 1, NULL, 0); set_value(k, name, REG_DWORD, &v, 4); }
        else {
            uint8_t buf[1024];
            uint32_t n = 0;
            for (const char *p = colon + 1; p[0] && p[1] && n < sizeof buf; p += 2) { unsigned b; sscanf(p, "%2x", &b); buf[n++] = (uint8_t)b; }
            set_value(k, name, REG_BINARY, buf, n);
        }
    }
    if (f) fclose(f);
}
static void save_key(FILE *f, rkey_t *k, const char *path) {
    for (value_t *v = k->values; v; v = v->next) {
        fprintf(f, "%s\\%s=", path, v->name);
        if (v->type == REG_SZ) fprintf(f, "sz:%s\n", (const char *)v->data);
        else if (v->type == REG_DWORD && v->size == 4) fprintf(f, "dword:0x%X\n", *(uint32_t *)v->data);
        else { fprintf(f, "hex:"); for (uint32_t i = 0; i < v->size; i++) fprintf(f, "%02x", v->data[i]); fprintf(f, "\n"); }
    }
    for (rkey_t *c = k->child; c; c = c->next) {
        char p[1024];
        snprintf(p, sizeof p, "%s\\%s", path, c->name);
        save_key(f, c, p);
    }
}
static void reg_save(void) {
    if (!g_reg_file) return;
    FILE *f = fopen(g_reg_file, "w");
    if (!f) return;
    for (int i = 0; i < 6; i++) save_key(f, &g_roots[i], g_root_names[i]);
    fclose(f);
}

static uint32_t open_key(uint32_t parent, const char *path, uint32_t out, int create) {
    if (getenv("HLE_REGTRACE")) fprintf(stderr, "[reg] %s key \"%s\"\n", create ? "create" : "open", path ? path : "(null)");
    rkey_t *p = root_of(parent);
    if (!p) return 6u;                                   /* ERROR_INVALID_HANDLE */
    rkey_t *k = subkey(p, path, create);
    if (!k) { if (out) MEM32(out) = 0; return ERROR_FILE_NOT_FOUND; }
    if (out) MEM32(out) = hle_handle_alloc(HLE_H_REGKEY, k);
    return ERROR_SUCCESS;
}
static void r_RegOpenKeyExA(void)   { RET(open_key(A32(0), ASTR(1), A32(4), 0), 5); }   /* (key, sub, opts, sam, &out) */
static void r_RegOpenKeyA(void)     { RET(open_key(A32(0), ASTR(1), A32(2), 0), 3); }
static void r_RegCreateKeyA(void)   { RET(open_key(A32(0), ASTR(1), A32(2), 1), 3); }
static void r_RegCreateKeyExA(void) {                    /* (key, sub, 0, class, opts, sam, sa, &out, &disp) */
    rkey_t *p = root_of(A32(0));
    int existed = p && subkey(p, ASTR(1), 0) != NULL;
    uint32_t r = open_key(A32(0), ASTR(1), A32(7), 1);
    if (A32(8)) MEM32(A32(8)) = existed ? 2u : 1u;       /* REG_OPENED_EXISTING_KEY / CREATED_NEW_KEY */
    RET(r, 9);
}
static void r_RegCloseKey(void) { hle_handle_close(A32(0)); RET(ERROR_SUCCESS, 1); }
static void r_RegQueryValueExA(void) {                   /* (key, name, 0, &type, data, &size) */
    rkey_t *k = root_of(A32(0));
    value_t *v = k ? find_value(k, ASTR(1), 0) : NULL;
    if (getenv("HLE_REGTRACE")) fprintf(stderr, "[reg] query \"%s\" -> %s\n", ASTR(1) ? ASTR(1) : "(default)", v ? "found" : "MISSING");
    if (!v) RET(ERROR_FILE_NOT_FOUND, 6);
    if (A32(3)) MEM32(A32(3)) = v->type;
    uint32_t have = A32(5) ? MEM32(A32(5)) : 0;
    if (A32(5)) MEM32(A32(5)) = v->size;
    if (!A32(4)) RET(ERROR_SUCCESS, 6);
    if (have < v->size) RET(ERROR_MORE_DATA, 6);
    memcpy(APTR(4), v->data, v->size);
    RET(ERROR_SUCCESS, 6);
}
static void r_RegQueryValueExW(void) {
    char name[128];
    const uint16_t *w = (const uint16_t *)APTR(1);
    if (w && !hle_wide_to_ansi(w, name, sizeof name)) RET(ERROR_INVALID_PARAMETER, 6);
    rkey_t *k = root_of(A32(0));
    value_t *v = k ? find_value(k, w ? name : NULL, 0) : NULL;
    if (!v) RET(ERROR_FILE_NOT_FOUND, 6);
    if (A32(3)) MEM32(A32(3)) = v->type;
    uint32_t have = A32(5) ? MEM32(A32(5)) : 0;
    if (A32(5)) MEM32(A32(5)) = v->size;
    if (!A32(4)) RET(ERROR_SUCCESS, 6);
    if (have < v->size) RET(ERROR_MORE_DATA, 6);
    memcpy(APTR(4), v->data, v->size);
    RET(ERROR_SUCCESS, 6);
}
static void r_RegSetValueExA(void) {                     /* (key, name, 0, type, data, size) */
    rkey_t *k = root_of(A32(0));
    if (!k) RET(6u, 6);
    set_value(k, ASTR(1), A32(3), APTR(4), A32(5));
    reg_save();
    RET(ERROR_SUCCESS, 6);
}
static void r_RegDeleteValueA(void) {
    rkey_t *k = root_of(A32(0));
    for (value_t **v = k ? &k->values : NULL; v && *v; v = &(*v)->next)
        if (!strcasecmp((*v)->name, ASTR(1) ? ASTR(1) : "")) { value_t *d = *v; *v = d->next; free(d->data); free(d); reg_save(); RET(0, 2); }
    RET(ERROR_FILE_NOT_FOUND, 2);
}
static void r_RegDeleteValueW(void) {
    char name[128];
    const uint16_t *w = (const uint16_t *)APTR(1);
    if (w && !hle_wide_to_ansi(w, name, sizeof name)) RET(ERROR_INVALID_PARAMETER, 2);
    rkey_t *k = root_of(A32(0));
    for (value_t **v = k ? &k->values : NULL; v && *v; v = &(*v)->next)
        if (!strcasecmp((*v)->name, w ? name : "")) { value_t *d = *v; *v = d->next; free(d->data); free(d); reg_save(); RET(0, 2); }
    RET(ERROR_FILE_NOT_FOUND, 2);
}
static void r_RegDeleteKeyA(void) {                      /* (key, sub): a key without subkeys */
    rkey_t *k = root_of(A32(0)), *d = k ? subkey(k, ASTR(1), 0) : NULL;
    if (!d || d->child || !d->parent) RET(ERROR_FILE_NOT_FOUND, 2);
    for (rkey_t **c = &d->parent->child; *c; c = &(*c)->next) if (*c == d) { *c = d->next; break; }
    reg_save();
    RET(ERROR_SUCCESS, 2);
}
static void r_RegEnumKeyExA(void) {                      /* (key, index, name, &nameN, 0, class, &classN, &time) */
    rkey_t *k = root_of(A32(0)), *c = k ? k->child : NULL;
    for (uint32_t i = 0; c && i < A32(1); i++) c = c->next;
    if (!c) RET(ERROR_NO_MORE_ITEMS, 8);
    uint32_t n = (uint32_t)strlen(c->name);
    if (MEM32(A32(3)) <= n) RET(ERROR_MORE_DATA, 8);
    memcpy(ASTR(2), c->name, n + 1);
    MEM32(A32(3)) = n;
    RET(ERROR_SUCCESS, 8);
}
static void r_RegEnumKeyA(void) {                        /* (key, index, name, n) */
    rkey_t *k = root_of(A32(0)), *c = k ? k->child : NULL;
    for (uint32_t i = 0; c && i < A32(1); i++) c = c->next;
    if (!c) RET(ERROR_NO_MORE_ITEMS, 4);
    snprintf(ASTR(2), A32(3), "%s", c->name);
    RET(ERROR_SUCCESS, 4);
}
static void r_RegQueryInfoKeyA(void) {                   /* (key, class, &classN, 0, &subkeys, &maxSub, &maxClass, &values, &maxName, &maxData, &sec, &time) */
    rkey_t *k = root_of(A32(0));
    if (!k) RET(6u, 12);
    uint32_t nsub = 0, maxsub = 0, nval = 0, maxname = 0, maxdata = 0;
    for (rkey_t *c = k->child; c; c = c->next) { nsub++; if (strlen(c->name) > maxsub) maxsub = (uint32_t)strlen(c->name); }
    for (value_t *v = k->values; v; v = v->next) { nval++; if (strlen(v->name) > maxname) maxname = (uint32_t)strlen(v->name); if (v->size > maxdata) maxdata = v->size; }
    if (A32(2)) MEM32(A32(2)) = 0;
    if (A32(4)) MEM32(A32(4)) = nsub;
    if (A32(5)) MEM32(A32(5)) = maxsub;
    if (A32(6)) MEM32(A32(6)) = 0;
    if (A32(7)) MEM32(A32(7)) = nval;
    if (A32(8)) MEM32(A32(8)) = maxname;
    if (A32(9)) MEM32(A32(9)) = maxdata;
    if (A32(10)) MEM32(A32(10)) = 0;
    if (A32(11)) MEM32(A32(11)) = 0, MEM32(A32(11) + 4) = 0;
    RET(ERROR_SUCCESS, 12);
}
static void r_GetUserNameA(void) { if (A32(1) && MEM32(A32(1)) > 6) { strcpy(ASTR(0), "player"); MEM32(A32(1)) = 7; RET(1, 2); } RET(0, 2); }

/* ---- COMCTL32 ---- */
static void c_InitCommonControls(void) { RETV(0); }
static void c_InitCommonControlsEx(void) { RET(1, 1); }
static void c_DllGetVersion(void) {                      /* (&DLLVERSIONINFO): cbSize, major, minor, build, platform */
    uint32_t d = A32(0);
    if (d) MEM32(d + 4) = 5, MEM32(d + 8) = 81, MEM32(d + 12) = 4704, MEM32(d + 16) = 1;
    RET(0, 1);
}
static void c_ret0_0(void) { RET(0, 0); }
static void c_ret0_1(void) { RET(0, 1); }
static void c_ret0_2(void) { RET(0, 2); }
static void c_ret0_3(void) { RET(0, 3); }
static void c_ret0_4(void) { RET(0, 4); }

/* ---- VERSION: no version resources here ---- */
static void v_GetFileVersionInfoSizeA(void) { if (A32(1)) MEM32(A32(1)) = 0; hle_set_last_error(1813u); RET(0, 2); }
static void v_GetFileVersionInfoA(void) { hle_set_last_error(1813u); RET(0, 4); }
static void v_VerQueryValueA(void) { if (A32(3)) MEM32(A32(3)) = 0; RET(0, 4); }

/* ---- SHELL32 ---- */
static void s_FindExecutableA(void) { if (A32(2)) MEM8(A32(2)) = 0; RET(31u, 3); }   /* SE_ERR_NOASSOC */
static void s_ShellExecuteA(void) { fprintf(stderr, "[shell32] ShellExecuteA(%s): not on this host\n", ASTR(2) ? ASTR(2) : ""); RET(31u, 6); }
/* There is no native folder picker in the portable HLE.  Returning NULL is
 * the documented SHBrowseForFolderW result when the user cancels; callers must
 * handle it before asking SHGetPathFromIDListW for a path. */
static void s_SHBrowseForFolderW(void) { RET(0, 1); }
static void s_SHGetPathFromIDListW(void) {
    if (A32(1)) MEM16(A32(1)) = 0;
    RET(0, 2);                                           /* invalid/null PIDL */
}
static void s_SHBrowseForFolderA(void) { RET(0, 1); }    /* user cancelled */
static void s_SHGetPathFromIDListA(void) { if (A32(1)) MEM8(A32(1)) = 0; RET(0, 2); }
static void s_SHGetMalloc(void) { if (A32(0)) MEM32(A32(0)) = 0; RET(0x8007000Eu, 1); }
static void s_SHGetDesktopFolder(void) { if (A32(0)) MEM32(A32(0)) = 0; RET(0x8007000Eu, 1); }
static void s_WNetGetConnectionA(void) { RET(2250u, 3); } /* ERROR_NOT_CONNECTED */

/* CommandLineToArgvW(lpCmdLine, &argc): split a command line the way Windows
 * does - whitespace separates, a quote toggles the quoted state, and a run of n
 * backslashes before a quote means n/2 backslashes plus either a literal quote
 * (n odd) or a quote that toggles (n even) - and hand back a LocalAlloc'd block
 * holding the pointer array followed by the strings, which is one allocation
 * the caller frees with LocalFree, exactly as the API promises.  A NULL command
 * line fails with ERROR_INVALID_PARAMETER and no block. */
#define CMDLINE_MAX_ARGS 256
static int cmdline_parse(const uint16_t *s, uint16_t *out, int cap, int *start, int *len, int max_args) {
    int nargs = 0, o = 0;
    const uint16_t *p = s;
    while (*p == ' ' || *p == '\t') p++;
    while (*p && nargs < max_args) {
        int quoted = 0;
        start[nargs] = o;
        while (*p) {
            if (!quoted && (*p == ' ' || *p == '\t')) break;
            if (*p == '\\') {
                int nbs = 0;
                while (*p == '\\') { nbs++; p++; }
                if (*p == '"') {
                    for (int i = 0; i < nbs / 2 && o < cap; i++) out[o++] = '\\';
                    if (nbs % 2) { if (o < cap) out[o++] = '"'; p++; }
                    else { quoted = !quoted; p++; }
                } else {
                    for (int i = 0; i < nbs && o < cap; i++) out[o++] = '\\';
                }
            } else if (*p == '"') {
                quoted = !quoted;
                p++;
            } else {
                if (o < cap) out[o++] = *p;
                p++;
            }
        }
        len[nargs] = o - start[nargs];
        nargs++;
        while (*p == ' ' || *p == '\t') p++;
    }
    return nargs;
}
static void s_CommandLineToArgvW(void) {                 /* (lpCmdLine, &argc) */
    const uint16_t *cmd = (const uint16_t *)APTR(0);
    uint32_t pn = A32(1);
    if (!cmd || !pn) { hle_set_last_error(ERROR_INVALID_PARAMETER); RET(0, 2); }
    int n = 0;
    while (cmd[n]) n++;
    if (n > 32768) { hle_set_last_error(ERROR_INVALID_PARAMETER); RET(0, 2); }
    uint16_t *chars = (uint16_t *)malloc(((size_t)n + 1) * sizeof(uint16_t));
    int *start = (int *)malloc(sizeof(int) * CMDLINE_MAX_ARGS), *len = (int *)malloc(sizeof(int) * CMDLINE_MAX_ARGS);
    if (!chars || !start || !len) { free(chars); free(start); free(len); hle_set_last_error(ERROR_NOT_ENOUGH_MEMORY); RET(0, 2); }
    int nargs = cmdline_parse(cmd, chars, n, start, len, CMDLINE_MAX_ARGS);
    size_t bytes = (size_t)nargs * 4;
    for (int i = 0; i < nargs; i++) bytes += ((size_t)len[i] + 1) * 2;
    uint32_t block = hle_va(hle_alloc((uint32_t)bytes));
    if (!block) { free(chars); free(start); free(len); hle_set_last_error(ERROR_NOT_ENOUGH_MEMORY); RET(0, 2); }
    uint32_t str = block + (uint32_t)nargs * 4;
    for (int i = 0; i < nargs; i++) {
        MEM32(block + (uint32_t)i * 4) = str;
        for (int c = 0; c < len[i]; c++) MEM16(str + (uint32_t)c * 2) = chars[start[i] + c];
        MEM16(str + (uint32_t)len[i] * 2) = 0;
        str += ((uint32_t)len[i] + 1) * 2;
    }
    free(chars); free(start); free(len);
    MEM32(pn) = (uint32_t)nargs;
    RET(block, 2);
}

static char g_dropped_file[1024];

void hle_set_dropped_file(const char *guest_path) {
    snprintf(g_dropped_file, sizeof g_dropped_file, "%s", guest_path ? guest_path : "");
}

/* DragAcceptFiles registers a window as a drop target. */
static void s_DragAcceptFiles(void) { RETV(2); }

/* DragQueryFileA(hDrop, iFile, buf, size) answers count (iFile == 0xFFFFFFFF)
 * or copies the dropped guest file name dropped onto the SDL window. */
static void s_DragQueryFileA(void) {
    uint32_t which = A32(1);
    if (which == 0xFFFFFFFFu) RET(g_dropped_file[0] ? 1u : 0u, 4);
    char *buf = ASTR(2);
    uint32_t cap = A32(3);
    if (!g_dropped_file[0] || which > 0) {
        if (buf && cap) buf[0] = 0;
        RET(0, 4);
    }
    uint32_t len = (uint32_t)strlen(g_dropped_file);
    if (!buf || !cap) RET(len, 4);
    uint32_t cp = len < cap - 1 ? len : cap - 1;
    memcpy(buf, g_dropped_file, cp);
    buf[cp] = 0;
    RET(cp, 4);
}
static void s_DragFinish(void) { g_dropped_file[0] = 0; RETV(1); }

/* ---- SHLWAPI ---- */
/* PathRemoveFileSpecA(path): strip the last component, in place. Returns TRUE
 * when it removed something, FALSE when there was nothing to remove -- the
 * documented contract, and the reason a caller can use it to walk up a path. */
static void s_PathRemoveFileSpecA(void) {
    char *p = ASTR(0);
    if (!p) RET(0, 1);
    size_t n = strlen(p);
    while (n && (p[n - 1] == '\\' || p[n - 1] == '/')) p[--n] = 0;   /* a trailing separator is not a file */
    char *slash = NULL;
    for (char *q = p; *q; q++) if (*q == '\\' || *q == '/') slash = q;
    if (!slash) RET(0, 1);                                /* already a bare name */
    if (slash == p) { p[1] = 0; RET(1, 1); }              /* "C:\file" -> "C:\" */
    *slash = 0;
    RET(1, 1);
}

/* -------------------------------------------------------------------------- */
/* Security, SID, Token, Safer, Crypto RNG, Shell, ImageList shims            */
/* -------------------------------------------------------------------------- */

static uint32_t alloc_default_sid(uint32_t sub0, uint32_t sub1, int count) {
    if (count < 1) count = 1;
    if (count > 8) count = 8;
    uint32_t va = hle_va(hle_alloc((uint32_t)(8 + 4 * count)));
    if (!va) return 0;
    uint8_t *p = (uint8_t *)hle_ptr(va);
    memset(p, 0, (size_t)(8 + 4 * count));
    p[0] = 1;                  /* Revision */
    p[1] = (uint8_t)count;     /* SubAuthorityCount */
    p[7] = 5;                  /* SECURITY_NT_AUTHORITY */
    MEM32(va + 8) = sub0;
    if (count > 1) MEM32(va + 12) = sub1;
    return va;
}

static uint32_t sid_byte_len(const uint8_t *sid) {
    if (!sid) return 0;
    return (uint32_t)(8 + 4 * (sid[1] > 15 ? 15 : sid[1]));
}

static void fill_random_bytes(uint8_t *buf, uint32_t len) {
    if (!buf || !len) return;
    int fd = open("/dev/urandom", O_RDONLY);
    uint32_t got = 0;
    if (fd >= 0) {
        while (got < len) {
            ssize_t r = read(fd, buf + got, len - got);
            if (r <= 0) break;
            got += (uint32_t)r;
        }
        close(fd);
    }
    if (got < len) arc4random_buf(buf + got, len - got);
}

static void adv_RegFlushKey(void) { (void)A32(0); RET(0, 1); }

static void adv_AllocateAndInitializeSid(void) {
    uint32_t auth_va = A32(0), cnt = A32(1) & 0xffu, out_va = A32(10);
    if (!out_va) { RET(0, 11); return; }
    if (cnt > 8) cnt = 8;
    uint32_t sid_va = hle_va(hle_alloc(8u + 4u * cnt));
    if (!sid_va) { hle_set_last_error(8); RET(0, 11); return; }
    uint8_t *sid = (uint8_t *)hle_ptr(sid_va);
    sid[0] = 1;
    sid[1] = (uint8_t)cnt;
    if (auth_va) memcpy(sid + 2, hle_ptr(auth_va), 6);
    for (uint32_t i = 0; i < cnt; i++) MEM32(sid_va + 8u + 4u * i) = A32(2 + (int)i);
    MEM32(out_va) = sid_va;
    RET(1, 11);
}

static void adv_GetLengthSid(void) {
    uint8_t *sid = (uint8_t *)APTR(0);
    RET(sid ? sid_byte_len(sid) : 8u, 1);
}

static void adv_CopySid(void) {
    uint32_t dst_len = A32(0);
    uint8_t *dst = (uint8_t *)APTR(1), *src = (uint8_t *)APTR(2);
    if (!dst || !src) { RET(0, 3); return; }
    uint32_t need = sid_byte_len(src);
    if (dst_len < need) { hle_set_last_error(122); RET(0, 3); return; }
    memcpy(dst, src, need);
    RET(1, 3);
}

static void adv_EqualSid(void) {
    uint8_t *s1 = (uint8_t *)APTR(0), *s2 = (uint8_t *)APTR(1);
    if (!s1 || !s2) { RET(0, 2); return; }
    uint32_t l1 = sid_byte_len(s1), l2 = sid_byte_len(s2);
    RET(l1 == l2 && memcmp(s1, s2, l1) == 0 ? 1u : 0u, 2);
}

static void adv_InitializeSecurityDescriptor(void) {
    uint8_t *sd = (uint8_t *)APTR(0);
    uint32_t rev = A32(1);
    if (!sd || rev != 1) { RET(0, 2); return; }
    memset(sd, 0, 20);
    sd[0] = 1;
    RET(1, 2);
}

static void adv_SetSecurityDescriptorDacl(void) {
    uint8_t *sd = (uint8_t *)APTR(0);
    if (!sd) { RET(0, 4); return; }
    if (A32(1)) MEM16(A32(0) + 2) |= 0x0004u; /* SE_DACL_PRESENT */
    else        MEM16(A32(0) + 2) &= (uint16_t)~0x0004u;
    MEM32(A32(0) + 16) = A32(2);
    RET(1, 4);
}

static void adv_SetSecurityDescriptorOwner(void) {
    if (!APTR(0)) { RET(0, 3); return; }
    MEM32(A32(0) + 4) = A32(1);
    RET(1, 3);
}

static void adv_OpenProcessToken(void) {
    uint32_t out_va = A32(2);
    if (!out_va) { RET(0, 3); return; }
    MEM32(out_va) = 0xCAFE0001u;
    RET(1, 3);
}

static void adv_GetTokenInformation(void) {
    uint32_t cls = A32(1), buf_va = A32(2), buf_len = A32(3), ret_va = A32(4);
    uint32_t need = 4;
    if (cls == 1 /* TokenUser */ || cls == 5 /* TokenOwner */ || cls == 4 /* TokenPrimaryGroup */) need = (cls == 1) ? 24u : 20u;
    else if (cls == 2 /* TokenGroups */) need = 16u;
    else if (cls == 3 /* TokenPrivileges */) need = 4u;
    if (ret_va) MEM32(ret_va) = need;
    if (!buf_va || buf_len < need) { hle_set_last_error(122); RET(0, 5); return; }
    memset(hle_ptr(buf_va), 0, buf_len);
    if (cls == 1 /* TokenUser */) {
        uint32_t sid_va = buf_va + 8;
        uint8_t *sid = (uint8_t *)hle_ptr(sid_va);
        sid[0] = 1; sid[1] = 2; sid[7] = 5;
        MEM32(sid_va + 8) = 21; MEM32(sid_va + 12) = 1000;
        MEM32(buf_va) = sid_va;
        MEM32(buf_va + 4) = 0;
    } else if (cls == 5 || cls == 4) {
        uint32_t sid_va = buf_va + 4;
        uint8_t *sid = (uint8_t *)hle_ptr(sid_va);
        sid[0] = 1; sid[1] = 2; sid[7] = 5;
        MEM32(sid_va + 8) = 21; MEM32(sid_va + 12) = (cls == 5) ? 1000u : 513u;
        MEM32(buf_va) = sid_va;
    } else if (cls == 18 /* TokenElevationType */) {
        MEM32(buf_va) = 1; /* TokenElevationTypeDefault */
    }
    RET(1, 5);
}

static void adv_SetTokenInformation(void) { RET(1, 4); }

static void adv_DuplicateToken(void) {
    if (A32(2)) MEM32(A32(2)) = 0xCAFE0002u;
    RET(A32(2) ? 1u : 0u, 3);
}

static void adv_CheckTokenMembership(void) {
    if (!A32(2)) { RET(0, 3); return; }
    MEM32(A32(2)) = 0; /* non-elevated user */
    RET(1, 3);
}

static void adv_AccessCheck(void) {
    uint32_t desired = A32(2), granted_va = A32(6), status_va = A32(7);
    if (granted_va) MEM32(granted_va) = desired ? desired : 0x001F0FFFu;
    if (status_va) MEM32(status_va) = 1;
    RET(1, 8);
}

static void adv_GetSecurityInfo(void) {
    uint32_t p_owner = A32(3), p_group = A32(4), p_dacl = A32(5), p_sacl = A32(6), p_sd = A32(7);
    uint32_t sd_va = hle_va(hle_alloc(20));
    if (sd_va) { memset(hle_ptr(sd_va), 0, 20); *(uint8_t *)hle_ptr(sd_va) = 1; }
    if (p_owner) MEM32(p_owner) = alloc_default_sid(21, 1000, 2);
    if (p_group) MEM32(p_group) = alloc_default_sid(21, 513, 2);
    if (p_dacl)  MEM32(p_dacl)  = 0;
    if (p_sacl)  MEM32(p_sacl)  = 0;
    if (p_sd)    MEM32(p_sd)    = sd_va;
    RET(0 /* ERROR_SUCCESS */, 8);
}

static void adv_SaferCreateLevel(void) {
    if (A32(3)) MEM32(A32(3)) = 0xCAFE0010u;
    RET(A32(3) ? 1u : 0u, 5);
}

static void adv_SaferComputeTokenFromLevel(void) {
    if (A32(2)) MEM32(A32(2)) = 0xCAFE0011u;
    RET(1, 5);
}

static void adv_CryptAcquireContextA(void) {
    if (!A32(0)) { RET(0, 5); return; }
    MEM32(A32(0)) = 0xC0DE0001u;
    RET(1, 5);
}

static void adv_CryptGenRandom(void) {
    uint32_t len = A32(1);
    uint8_t *buf = (uint8_t *)APTR(2);
    if (!buf && len) { RET(0, 3); return; }
    fill_random_bytes(buf, len);
    RET(1, 3);
}

static void adv_CryptReleaseContext(void) { RET(1, 2); }

static void adv_SystemFunction036(void) {
    uint8_t *buf = (uint8_t *)APTR(0);
    uint32_t len = A32(1);
    if (!buf && len) { RET(0, 2); return; }
    fill_random_bytes(buf, len);
    RET(1, 2);
}

static void s_Shell_NotifyIconA(void) { RET(1, 2); }
static void s_SHChangeNotify(void) { RETV(4); }

/* Minimal ImageList table so ComCtl32 list-views / tree-views behave cleanly. */
#define MAX_IMAGELISTS 64
typedef struct {
    int used, cx, cy, count;
    uint32_t bkcolor;
} il_entry_t;
static il_entry_t g_imagelists[MAX_IMAGELISTS];

static il_entry_t *il_find(uint32_t h) {
    if (h < 0x10000u || h >= 0x10000u + MAX_IMAGELISTS) return NULL;
    il_entry_t *e = &g_imagelists[h - 0x10000u];
    return e->used ? e : NULL;
}

static void il_Create(void) {
    for (int i = 0; i < MAX_IMAGELISTS; i++) {
        if (!g_imagelists[i].used) {
            g_imagelists[i].used = 1;
            g_imagelists[i].cx = (int)A32(0);
            g_imagelists[i].cy = (int)A32(1);
            g_imagelists[i].count = 0;
            g_imagelists[i].bkcolor = 0xFFFFFFFFu; /* CLR_NONE */
            RET(0x10000u + (uint32_t)i, 5);
            return;
        }
    }
    RET(0, 5);
}
static void il_Destroy(void) {
    il_entry_t *e = il_find(A32(0));
    if (e) e->used = 0;
    RET(1, 1);
}
static void il_Add(void) {
    il_entry_t *e = il_find(A32(0));
    int idx = e ? e->count++ : 0;
    RET((uint32_t)idx, 3);
}
static void il_ReplaceIcon(void) {
    il_entry_t *e = il_find(A32(0));
    int idx = (int)A32(1);
    if (!e) { RET((uint32_t)-1, 3); return; }
    if (idx < 0) idx = e->count++;
    RET((uint32_t)idx, 3);
}
static void il_GetImageCount(void) {
    il_entry_t *e = il_find(A32(0));
    RET(e ? (uint32_t)e->count : 0u, 1);
}
static void il_GetIconSize(void) {
    il_entry_t *e = il_find(A32(0));
    if (!e) { RET(0, 3); return; }
    if (A32(1)) MEM32(A32(1)) = (uint32_t)e->cx;
    if (A32(2)) MEM32(A32(2)) = (uint32_t)e->cy;
    RET(1, 3);
}
static void il_SetIconSize(void) {
    il_entry_t *e = il_find(A32(0));
    if (!e) { RET(0, 3); return; }
    e->cx = (int)A32(1); e->cy = (int)A32(2); e->count = 0;
    RET(1, 3);
}
static void il_GetBkColor(void) {
    il_entry_t *e = il_find(A32(0));
    RET(e ? e->bkcolor : 0xFFFFFFFFu, 1);
}
static void il_SetBkColor(void) {
    il_entry_t *e = il_find(A32(0));
    uint32_t prev = e ? e->bkcolor : 0xFFFFFFFFu;
    if (e) e->bkcolor = A32(1);
    RET(prev, 2);
}
static void il_Draw(void) { RET(1, 6); }
static void il_DrawEx(void) { RET(1, 10); }
static void il_Remove(void) {
    il_entry_t *e = il_find(A32(0));
    int idx = (int)A32(1);
    if (e) {
        if (idx < 0) e->count = 0;
        else if (e->count > 0) e->count--;
    }
    RET(1, 2);
}
static void il_GetDragImage(void) {
    if (A32(0)) { MEM32(A32(0)) = 0; MEM32(A32(0) + 4) = 0; }
    if (A32(1)) { MEM32(A32(1)) = 0; MEM32(A32(1) + 4) = 0; }
    RET(0, 2);
}
static void il_SetDragCursorImage(void) { RET(1, 4); }

const win32hle_shim win32hle_advapi32[] = {
    { "RegOpenKeyExA", r_RegOpenKeyExA }, { "RegOpenKeyA", r_RegOpenKeyA }, { "RegCreateKeyA", r_RegCreateKeyA },
    { "RegCreateKeyExA", r_RegCreateKeyExA }, { "RegCloseKey", r_RegCloseKey }, { "RegFlushKey", adv_RegFlushKey },
    { "RegQueryValueExA", r_RegQueryValueExA }, { "RegQueryValueExW", r_RegQueryValueExW }, { "RegSetValueExA", r_RegSetValueExA },
    { "RegDeleteValueA", r_RegDeleteValueA }, { "RegDeleteValueW", r_RegDeleteValueW }, { "RegDeleteKeyA", r_RegDeleteKeyA },
    { "RegEnumKeyExA", r_RegEnumKeyExA }, { "RegEnumKeyA", r_RegEnumKeyA }, { "RegQueryInfoKeyA", r_RegQueryInfoKeyA },
    { "GetUserNameA", r_GetUserNameA },
    { "AllocateAndInitializeSid", adv_AllocateAndInitializeSid }, { "CopySid", adv_CopySid },
    { "EqualSid", adv_EqualSid }, { "GetLengthSid", adv_GetLengthSid },
    { "InitializeSecurityDescriptor", adv_InitializeSecurityDescriptor },
    { "SetSecurityDescriptorDacl", adv_SetSecurityDescriptorDacl },
    { "SetSecurityDescriptorOwner", adv_SetSecurityDescriptorOwner },
    { "OpenProcessToken", adv_OpenProcessToken }, { "GetTokenInformation", adv_GetTokenInformation },
    { "SetTokenInformation", adv_SetTokenInformation }, { "DuplicateToken", adv_DuplicateToken },
    { "CheckTokenMembership", adv_CheckTokenMembership }, { "AccessCheck", adv_AccessCheck },
    { "GetSecurityInfo", adv_GetSecurityInfo }, { "SaferCreateLevel", adv_SaferCreateLevel },
    { "SaferComputeTokenFromLevel", adv_SaferComputeTokenFromLevel },
    { "CryptAcquireContextA", adv_CryptAcquireContextA }, { "CryptGenRandom", adv_CryptGenRandom },
    { "CryptReleaseContext", adv_CryptReleaseContext }, { "SystemFunction036", adv_SystemFunction036 },
    { "comctl32.dll#17", c_InitCommonControls }, { "InitCommonControls", c_InitCommonControls },
    { "InitCommonControlsEx", c_InitCommonControlsEx }, { "DllGetVersion", c_DllGetVersion },
    { "ImageList_Create", il_Create }, { "ImageList_Destroy", il_Destroy },
    { "ImageList_Add", il_Add }, { "ImageList_ReplaceIcon", il_ReplaceIcon },
    { "ImageList_GetImageCount", il_GetImageCount }, { "ImageList_GetIconSize", il_GetIconSize },
    { "ImageList_SetIconSize", il_SetIconSize }, { "ImageList_GetBkColor", il_GetBkColor },
    { "ImageList_SetBkColor", il_SetBkColor }, { "ImageList_Draw", il_Draw },
    { "ImageList_DrawEx", il_DrawEx }, { "ImageList_Remove", il_Remove },
    { "ImageList_BeginDrag", c_ret0_4 }, { "ImageList_DragEnter", c_ret0_3 },
    { "ImageList_DragLeave", c_ret0_1 }, { "ImageList_DragMove", c_ret0_2 },
    { "ImageList_DragShowNolock", c_ret0_1 }, { "ImageList_EndDrag", c_ret0_0 },
    { "ImageList_GetDragImage", il_GetDragImage }, { "ImageList_SetDragCursorImage", il_SetDragCursorImage },
    { "ImageList_Read", c_ret0_1 }, { "ImageList_Write", c_ret0_2 },
    { "GetFileVersionInfoSizeA", v_GetFileVersionInfoSizeA }, { "GetFileVersionInfoA", v_GetFileVersionInfoA },
    { "VerQueryValueA", v_VerQueryValueA },
    { "FindExecutableA", s_FindExecutableA }, { "ShellExecuteA", s_ShellExecuteA },
    { "SHBrowseForFolderW", s_SHBrowseForFolderW }, { "SHGetPathFromIDListW", s_SHGetPathFromIDListW },
    { "SHBrowseForFolderA", s_SHBrowseForFolderA }, { "SHGetPathFromIDListA", s_SHGetPathFromIDListA },
    { "SHGetMalloc", s_SHGetMalloc }, { "SHGetDesktopFolder", s_SHGetDesktopFolder },
    { "WNetGetConnectionA", s_WNetGetConnectionA },
    { "CommandLineToArgvW", s_CommandLineToArgvW },
    { "DragAcceptFiles", s_DragAcceptFiles }, { "DragQueryFileA", s_DragQueryFileA },
    { "DragFinish", s_DragFinish },
    { "Shell_NotifyIconA", s_Shell_NotifyIconA }, { "SHChangeNotify", s_SHChangeNotify },
    { "PathRemoveFileSpecA", s_PathRemoveFileSpecA },
    { 0, 0 }
};
