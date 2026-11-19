#ifndef _WASM_MATH_H
#define _WASM_MATH_H

#define NAN       (__builtin_nanf(""))
#define INFINITY  (__builtin_inff())
#define HUGE_VAL  (__builtin_huge_val())

#define isnan(x)     __builtin_isnan(x)
#define isinf(x)     __builtin_isinf(x)
#define isfinite(x)  __builtin_isfinite(x)
#define signbit(x)   __builtin_signbit(x)

static inline double fabs(double x)  { return __builtin_fabs(x); }
static inline float  fabsf(float x)  { return __builtin_fabsf(x); }
static inline double floor(double x) { return __builtin_floor(x); }
static inline float  floorf(float x) { return __builtin_floorf(x); }
static inline double ceil(double x)  { return __builtin_ceil(x); }
static inline float  ceilf(float x)  { return __builtin_ceilf(x); }
static inline double trunc(double x) { return __builtin_trunc(x); }
static inline float  truncf(float x) { return __builtin_truncf(x); }
static inline double sqrt(double x)  { return __builtin_sqrt(x); }
static inline float  sqrtf(float x)  { return __builtin_sqrtf(x); }
static inline double rint(double x)      { return __builtin_rint(x); }
static inline double nearbyint(double x) { return __builtin_nearbyint(x); }
static inline float  nearbyintf(float x) { return __builtin_nearbyintf(x); }
static inline double round(double x)     { return __builtin_round(x); }
static inline float  roundf(float x) { return __builtin_roundf(x); }

double sin(double x);
double cos(double x);
double tan(double x);
double asin(double x);
double acos(double x);
double atan(double x);
double atan2(double y, double x);
double pow(double x, double y);
double exp(double x);
double log(double x);
double log2(double x);
double log10(double x);
double fmod(double x, double y);
double ldexp(double x, int exp);
double frexp(double x, int *exp);

#endif
