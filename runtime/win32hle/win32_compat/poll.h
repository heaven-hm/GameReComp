/* MinGW-w64 has no <poll.h>: poll() maps to WSAPoll (link with ws2_32). */
#ifndef WIN32_COMPAT_POLL_H
#define WIN32_COMPAT_POLL_H
#include "win32_port.h"
#endif
