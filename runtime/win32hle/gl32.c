/*
 * gl32.c - OPENGL32.DLL and GLU32.DLL imports on the host's OpenGL.
 *
 * The window and the context are sdl12.c's: SDL_SetVideoMode with SDL_OPENGL
 * creates an SDL_WINDOW_OPENGL window and an SDL_GLContext (sdl12.c's
 * hle_sdl12_gl_active() says whether one exists).  Every entry point here is
 * resolved from the host's own OpenGL through SDL_GL_GetProcAddress and
 * forwarded, so a lifted game's glBegin/glVertex3f/... really draw into that
 * context and SDL_GL_SwapBuffers really presents.
 *
 * The Windows ABI is __stdcall, not cdecl: OpenGL32.DLL and GLU32.DLL are
 * WINAPI (APIENTRY) exports, so these shims use RET(value, argc) and pop their
 * own arguments.  The original binaries confirm it - gunroar/gr.exe calls its
 * glVertex3f thunk with three floats on the stack and never adds esp back
 * afterwards (the next instruction indexes [esp+0x44]).
 *
 * What a missing entry point answers: an unresolved gl* name cannot draw, so
 * the call is reported on stderr once (never silently) and the shim answers
 * what "no OpenGL" truthfully is - GL_INVALID_OPERATION from glGetError, 0
 * names from glGenTextures, 0 lists from glGenLists, NULL from glGetString -
 * rather than pretending the work happened.  A guest with a live context never
 * takes that path.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <dlfcn.h>
#include <SDL2/SDL.h>
#include "win32hle.h"

/* sdl12.c owns the mode: the context exists only after SDL_SetVideoMode with
 * SDL_OPENGL, and a gl* call without one is what the warnings below report. */
extern int  hle_sdl12_gl_active(void);

#define GL_INVALID_OPERATION 0x0502u
#define GLU_INVALID_OPERATION 100904

/* One cached host entry point per shim, resolved on first call. */
#define GLPROC(fn)                                        \
    static void *p;                                       \
    static int tried;                                     \
    if (!tried) {                                         \
        tried = 1;                                        \
        p = SDL_GL_GetProcAddress(#fn);                   \
        if (!p) gl_missing(#fn);                          \
    }
static void gl_missing(const char *name) {
    fprintf(stderr, "[gl] %s: the host OpenGL has no such entry point%s\n",
            name, hle_sdl12_gl_active() ? "" : " (and no GL context is current)");
}

/* Arguments: OpenGL passes ints and floats as one 32-bit word, doubles as two.
 * A32(i) reads word i, AF(i) the float in it, AD(i) the double spanning words
 * i and i+1 (x86 pushes a double as its low then high half). */
static float AF(int i) { float f; uint32_t v = A32(i); memcpy(&f, &v, 4); return f; }
static double AD(int i) { double d; uint64_t v = (uint64_t)A32(i) | ((uint64_t)A32(i + 1) << 32); memcpy(&d, &v, 8); return d; }

/* ---- immediate-mode drawing ---- */
static void s_glBegin(void)          { GLPROC(glBegin);          if (p) ((void (*)(unsigned))p)(A32(0)); RETV(1); }
static void s_glEnd(void)            { GLPROC(glEnd);            if (p) ((void (*)(void))p)(); RETV(0); }
static void s_glVertex2f(void)       { GLPROC(glVertex2f);       if (p) ((void (*)(float, float))p)(AF(0), AF(1)); RETV(2); }
static void s_glVertex3f(void)       { GLPROC(glVertex3f);       if (p) ((void (*)(float, float, float))p)(AF(0), AF(1), AF(2)); RETV(3); }
static void s_glColor3f(void)        { GLPROC(glColor3f);        if (p) ((void (*)(float, float, float))p)(AF(0), AF(1), AF(2)); RETV(3); }
static void s_glColor4f(void)        { GLPROC(glColor4f);        if (p) ((void (*)(float, float, float, float))p)(AF(0), AF(1), AF(2), AF(3)); RETV(4); }
static void s_glColor4ub(void)       { GLPROC(glColor4ub);       if (p) ((void (*)(unsigned char, unsigned char, unsigned char, unsigned char))p)((unsigned char)A32(0), (unsigned char)A32(1), (unsigned char)A32(2), (unsigned char)A32(3)); RETV(4); }
static void s_glTexCoord2f(void)     { GLPROC(glTexCoord2f);     if (p) ((void (*)(float, float))p)(AF(0), AF(1)); RETV(2); }
static void s_glRasterPos2i(void)    { GLPROC(glRasterPos2i);    if (p) ((void (*)(int, int))p)((int)A32(0), (int)A32(1)); RETV(2); }
static void s_glDrawPixels(void)     { GLPROC(glDrawPixels);     if (p) ((void (*)(int, int, unsigned, unsigned, const void *))p)((int)A32(0), (int)A32(1), A32(2), A32(3), hle_ptr(A32(4))); RETV(5); }
static void s_glReadPixels(void)     { GLPROC(glReadPixels);     if (p) ((void (*)(int, int, int, int, unsigned, unsigned, void *))p)((int)A32(0), (int)A32(1), (int)A32(2), (int)A32(3), A32(4), A32(5), hle_ptr(A32(6))); RETV(7); }

/* ---- state and transforms ---- */
static void s_glClear(void)          { GLPROC(glClear);          if (p) ((void (*)(unsigned))p)(A32(0)); RETV(1); }
static void s_glClearColor(void)     { GLPROC(glClearColor);     if (p) ((void (*)(float, float, float, float))p)(AF(0), AF(1), AF(2), AF(3)); RETV(4); }
static void s_glEnable(void)         { GLPROC(glEnable);         if (p) ((void (*)(unsigned))p)(A32(0)); RETV(1); }
static void s_glDisable(void)        { GLPROC(glDisable);        if (p) ((void (*)(unsigned))p)(A32(0)); RETV(1); }
static void s_glBlendFunc(void)      { GLPROC(glBlendFunc);      if (p) ((void (*)(unsigned, unsigned))p)(A32(0), A32(1)); RETV(2); }
static void s_glDepthFunc(void)      { GLPROC(glDepthFunc);      if (p) ((void (*)(unsigned))p)(A32(0)); RETV(1); }
static void s_glDepthMask(void)      { GLPROC(glDepthMask);      if (p) ((void (*)(unsigned char))p)((unsigned char)A32(0)); RETV(1); }
static void s_glShadeModel(void)     { GLPROC(glShadeModel);     if (p) ((void (*)(unsigned))p)(A32(0)); RETV(1); }
static void s_glPixelStorei(void)    { GLPROC(glPixelStorei);    if (p) ((void (*)(unsigned, int))p)(A32(0), (int)A32(1)); RETV(2); }
static void s_glPolygonMode(void)    { GLPROC(glPolygonMode);    if (p) ((void (*)(unsigned, unsigned))p)(A32(0), A32(1)); RETV(2); }
static void s_glScissor(void)        { GLPROC(glScissor);        if (p) ((void (*)(int, int, int, int))p)((int)A32(0), (int)A32(1), (int)A32(2), (int)A32(3)); RETV(4); }
static void s_glViewport(void)       { GLPROC(glViewport);       if (p) ((void (*)(int, int, int, int))p)((int)A32(0), (int)A32(1), (int)A32(2), (int)A32(3)); RETV(4); }
static void s_glLineWidth(void)      { GLPROC(glLineWidth);      if (p) ((void (*)(float))p)(AF(0)); RETV(1); }
static void s_glPointSize(void)      { GLPROC(glPointSize);      if (p) ((void (*)(float))p)(AF(0)); RETV(1); }
static void s_glMatrixMode(void)     { GLPROC(glMatrixMode);     if (p) ((void (*)(unsigned))p)(A32(0)); RETV(1); }
static void s_glLoadIdentity(void)   { GLPROC(glLoadIdentity);   if (p) ((void (*)(void))p)(); RETV(0); }
static void s_glPushMatrix(void)     { GLPROC(glPushMatrix);     if (p) ((void (*)(void))p)(); RETV(0); }
static void s_glPopMatrix(void)      { GLPROC(glPopMatrix);      if (p) ((void (*)(void))p)(); RETV(0); }
static void s_glTranslatef(void)     { GLPROC(glTranslatef);     if (p) ((void (*)(float, float, float))p)(AF(0), AF(1), AF(2)); RETV(3); }
static void s_glRotatef(void)        { GLPROC(glRotatef);        if (p) ((void (*)(float, float, float, float))p)(AF(0), AF(1), AF(2), AF(3)); RETV(4); }
static void s_glScalef(void)         { GLPROC(glScalef);         if (p) ((void (*)(float, float, float))p)(AF(0), AF(1), AF(2)); RETV(3); }
static void s_glOrtho(void)          { GLPROC(glOrtho);          if (p) ((void (*)(double, double, double, double, double, double))p)(AD(0), AD(2), AD(4), AD(6), AD(8), AD(10)); RETV(12); }
static void s_glFrustum(void)        { GLPROC(glFrustum);        if (p) ((void (*)(double, double, double, double, double, double))p)(AD(0), AD(2), AD(4), AD(6), AD(8), AD(10)); RETV(12); }
static void s_glFlush(void)          { GLPROC(glFlush);          if (p) ((void (*)(void))p)(); RETV(0); }
static void s_glFinish(void)         { GLPROC(glFinish);         if (p) ((void (*)(void))p)(); RETV(0); }

/* ---- display lists ---- */
static void s_glNewList(void)        { GLPROC(glNewList);        if (p) ((void (*)(unsigned, unsigned))p)(A32(0), A32(1)); RETV(2); }
static void s_glEndList(void)        { GLPROC(glEndList);        if (p) ((void (*)(void))p)(); RETV(0); }
static void s_glCallList(void)       { GLPROC(glCallList);       if (p) ((void (*)(unsigned))p)(A32(0)); RETV(1); }
static void s_glDeleteLists(void)    { GLPROC(glDeleteLists);    if (p) ((void (*)(unsigned, int))p)(A32(0), (int)A32(1)); RETV(2); }
static void s_glGenLists(void)       { GLPROC(glGenLists);       RET(p ? ((unsigned (*)(int))p)((int)A32(0)) : 0u, 1); }   /* 0 lists, not a fake name */

/* ---- textures ---- */
static void s_glBindTexture(void)    { GLPROC(glBindTexture);    if (p) ((void (*)(unsigned, unsigned))p)(A32(0), A32(1)); RETV(2); }
static void s_glDeleteTextures(void) { GLPROC(glDeleteTextures); if (p) ((void (*)(int, const unsigned *))p)((int)A32(0), (const unsigned *)hle_ptr(A32(1))); RETV(2); }
static void s_glTexParameteri(void)  { GLPROC(glTexParameteri);  if (p) ((void (*)(unsigned, unsigned, int))p)(A32(0), A32(1), (int)A32(2)); RETV(3); }
static void s_glTexEnvi(void)        { GLPROC(glTexEnvi);        if (p) ((void (*)(unsigned, unsigned, int))p)(A32(0), A32(1), (int)A32(2)); RETV(3); }
static void s_glTexImage2D(void)     { GLPROC(glTexImage2D);     if (p) ((void (*)(unsigned, int, int, int, int, int, unsigned, unsigned, const void *))p)(A32(0), (int)A32(1), (int)A32(2), (int)A32(3), (int)A32(4), (int)A32(5), A32(6), A32(7), hle_ptr(A32(8))); RETV(9); }
static void s_glCopyTexImage2D(void) { GLPROC(glCopyTexImage2D); if (p) ((void (*)(unsigned, int, int, int, int, int, int, int))p)(A32(0), (int)A32(1), (int)A32(2), (int)A32(3), (int)A32(4), (int)A32(5), (int)A32(6), (int)A32(7)); RETV(8); }
static void s_glGenTextures(void) {
    GLPROC(glGenTextures);
    if (p) ((void (*)(int, unsigned *))p)((int)A32(0), (unsigned *)hle_ptr(A32(1)));
    else if (A32(1)) memset(hle_ptr(A32(1)), 0, (size_t)((int)A32(0) > 0 ? (int)A32(0) : 0) * sizeof(unsigned));   /* no names generated */
    RETV(2);
}

/* ---- queries ---- */
static void s_glGetError(void)       { GLPROC(glGetError);       RET((p ? ((unsigned (*)(void))p)() : GL_INVALID_OPERATION), 0); }
static void s_glGetIntegerv(void) {
    GLPROC(glGetIntegerv);
    if (p) ((void (*)(unsigned, int *))p)(A32(0), (int *)hle_ptr(A32(1)));
    else if (A32(1)) MEM32(A32(1)) = 0;
    RETV(2);
}
/* glGetString answers a host pointer, which lifted code cannot read: the string
 * is copied into guest memory and that address is returned. */
static void s_glGetString(void) {
    GLPROC(glGetString);
    if (!p) RET(0u, 1);
    const unsigned char *s = ((const unsigned char *(*)(unsigned))p)((unsigned)A32(0));
    RET(s ? hle_guest_strdup((const char *)s) : 0u, 1);
}

/* ---- GLU ----
 * GLU lives in the GL library on some platforms (macOS's OpenGL framework) and
 * in its own on others (libGLU), so it is looked up through the same GL proc
 * address first and then dlopen'd by its usual names.  Absent GLU answers the
 * GLU error code, not success. */
static void *glu_proc(const char *name) {
    void *p = SDL_GL_GetProcAddress(name);
    if (p) return p;
    static void *lib; static int tried;
    if (!tried) {
        tried = 1;
        const char *names[] = { "libGLU.so.1", "libGLU.so", "libGLU.dylib",
                                "/opt/homebrew/lib/libGLU.dylib", "/usr/local/lib/libGLU.dylib",
                                "/System/Library/Frameworks/OpenGL.framework/OpenGL", NULL };
        for (int i = 0; names[i] && !lib; i++) lib = dlopen(names[i], RTLD_LAZY | RTLD_LOCAL);
    }
    return lib ? dlsym(lib, name) : NULL;
}
static void s_gluBuild2DMipmaps(void) {
    static void *p; static int tried;
    if (!tried) { tried = 1; p = glu_proc("gluBuild2DMipmaps"); if (!p) gl_missing("gluBuild2DMipmaps"); }
    if (p) RET(((int (*)(unsigned, int, int, int, unsigned, unsigned, const void *))p)(A32(0), (int)A32(1), (int)A32(2), (int)A32(3), A32(4), A32(5), hle_ptr(A32(6))), 7);
    RET(GLU_INVALID_OPERATION, 7);
}
static void s_gluPerspective(void) {
    static void *p; static int tried;
    if (!tried) { tried = 1; p = glu_proc("gluPerspective"); if (!p) gl_missing("gluPerspective"); }
    if (p) ((void (*)(double, double, double, double))p)(AD(0), AD(2), AD(4), AD(6));
    RETV(8);
}
static void s_gluOrtho2D(void) {
    static void *p; static int tried;
    if (!tried) { tried = 1; p = glu_proc("gluOrtho2D"); if (!p) gl_missing("gluOrtho2D"); }
    if (p) ((void (*)(double, double, double, double))p)(AD(0), AD(2), AD(4), AD(6));
    RETV(8);
}
static void s_gluLookAt(void) {
    static void *p; static int tried;
    if (!tried) { tried = 1; p = glu_proc("gluLookAt"); if (!p) gl_missing("gluLookAt"); }
    if (p) ((void (*)(double, double, double, double, double, double, double, double, double))p)(
                AD(0), AD(2), AD(4), AD(6), AD(8), AD(10), AD(12), AD(14), AD(16));
    RETV(18);
}
/* gluErrorString's answers for the four GLU codes are fixed strings; with GLU
 * present its own function is used. */
static void s_gluErrorString(void) {
    static void *p; static int tried;
    if (!tried) { tried = 1; p = glu_proc("gluErrorString"); }
    if (p) { const char *s = (const char *)((const unsigned char *(*)(unsigned))p)(A32(0)); RET(s ? hle_guest_strdup(s) : 0u, 1); }
    const char *s = NULL;
    switch (A32(0)) {
    case 100900: s = "invalid enumerant"; break;
    case 100901: s = "invalid value"; break;
    case 100902: s = "out of memory"; break;
    case 100904: s = "invalid operation"; break;
    default: break;
    }
    RET(s ? hle_guest_strdup(s) : 0u, 1);
}

const win32hle_shim win32hle_gl32[] = {
    { "glBegin", s_glBegin }, { "glEnd", s_glEnd },
    { "glVertex2f", s_glVertex2f }, { "glVertex3f", s_glVertex3f },
    { "glColor3f", s_glColor3f }, { "glColor4f", s_glColor4f }, { "glColor4ub", s_glColor4ub },
    { "glTexCoord2f", s_glTexCoord2f }, { "glRasterPos2i", s_glRasterPos2i },
    { "glDrawPixels", s_glDrawPixels }, { "glReadPixels", s_glReadPixels },
    { "glClear", s_glClear }, { "glClearColor", s_glClearColor },
    { "glEnable", s_glEnable }, { "glDisable", s_glDisable }, { "glBlendFunc", s_glBlendFunc },
    { "glDepthFunc", s_glDepthFunc }, { "glDepthMask", s_glDepthMask }, { "glShadeModel", s_glShadeModel },
    { "glPixelStorei", s_glPixelStorei }, { "glPolygonMode", s_glPolygonMode }, { "glScissor", s_glScissor },
    { "glViewport", s_glViewport }, { "glLineWidth", s_glLineWidth }, { "glPointSize", s_glPointSize },
    { "glMatrixMode", s_glMatrixMode }, { "glLoadIdentity", s_glLoadIdentity },
    { "glPushMatrix", s_glPushMatrix }, { "glPopMatrix", s_glPopMatrix },
    { "glTranslatef", s_glTranslatef }, { "glRotatef", s_glRotatef }, { "glScalef", s_glScalef },
    { "glOrtho", s_glOrtho }, { "glFrustum", s_glFrustum },
    { "glFlush", s_glFlush }, { "glFinish", s_glFinish },
    { "glNewList", s_glNewList }, { "glEndList", s_glEndList }, { "glCallList", s_glCallList },
    { "glGenLists", s_glGenLists }, { "glDeleteLists", s_glDeleteLists },
    { "glBindTexture", s_glBindTexture }, { "glGenTextures", s_glGenTextures },
    { "glDeleteTextures", s_glDeleteTextures }, { "glTexParameteri", s_glTexParameteri },
    { "glTexEnvi", s_glTexEnvi }, { "glTexImage2D", s_glTexImage2D }, { "glCopyTexImage2D", s_glCopyTexImage2D },
    { "glGetError", s_glGetError }, { "glGetString", s_glGetString }, { "glGetIntegerv", s_glGetIntegerv },
    { "gluBuild2DMipmaps", s_gluBuild2DMipmaps }, { "gluPerspective", s_gluPerspective },
    { "gluOrtho2D", s_gluOrtho2D }, { "gluLookAt", s_gluLookAt }, { "gluErrorString", s_gluErrorString },
    { 0, 0 }
};
