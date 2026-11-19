#ifndef _WASM_TIME_H
#define _WASM_TIME_H

typedef long time_t;

#define CLOCK_REALTIME  0
#define CLOCK_MONOTONIC 1

struct timespec {
    long tv_sec;
    long tv_nsec;
};

struct tm {
    int tm_sec, tm_min, tm_hour, tm_mday, tm_mon, tm_year, tm_wday, tm_yday, tm_isdst;
};

int clock_gettime(int clk_id, struct timespec *tp);
time_t time(time_t *tloc);

#endif
