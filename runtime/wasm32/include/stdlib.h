#ifndef _WASM_STDLIB_H
#define _WASM_STDLIB_H

#include <stddef.h>

void *malloc(size_t size);
void *calloc(size_t nmemb, size_t size);
void *realloc(void *ptr, size_t size);
void  free(void *ptr);

void  abort(void);
void  exit(int status);

int   abs(int x);
int   atoi(const char *s);
long  atol(const char *s);
long  strtol(const char *s, char **endp, int base);
unsigned long strtoul(const char *s, char **endp, int base);
double strtod(const char *s, char **endp);
double atof(const char *s);

int   rand(void);
void  srand(unsigned int seed);
void  qsort(void *base, size_t nmemb, size_t size, int (*compar)(const void *, const void *));
void *bsearch(const void *key, const void *base, size_t nmemb, size_t size, int (*compar)(const void *, const void *));

#define RAND_MAX 0x7fffffff

#endif
