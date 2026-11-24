/* MinGW-w64 has no <sys/mman.h>: route the runtime's include to the real
 * VirtualAlloc / MapViewOfFileEx implementation in win32_port.c. */
#ifndef WIN32_COMPAT_SYS_MMAN_H
#define WIN32_COMPAT_SYS_MMAN_H
#include "win32_port.h"
#endif
