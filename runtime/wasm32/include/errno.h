#ifndef _WASM_ERRNO_H
#define _WASM_ERRNO_H

extern int errno;

#define EPERM   1
#define ENOENT  2
#define EIO     5
#define EBADF   9
#define ENOMEM  12
#define EACCES  13
#define EEXIST  17
#define EINVAL  22
#define EMFILE  24
#define ERANGE  34

#endif
