#ifndef _WASM_SETJMP_H
#define _WASM_SETJMP_H

typedef struct {
    unsigned int words[8];
} jmp_buf[1];

static inline int setjmp(jmp_buf env) {
    (void)env;
    return 0;
}

static inline void longjmp(jmp_buf env, int val) {
    (void)env;
    (void)val;
}

#endif
