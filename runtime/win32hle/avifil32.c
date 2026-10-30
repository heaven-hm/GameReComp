/* Video-for-Windows compatibility when no AVI codec backend is present.
 * File/stream discovery fails with the documented AVI error family and
 * decompression reports unsupported; optional movie playback can be skipped
 * without leaving imports unbound or corrupting the guest stack. */
#include <stdint.h>
#include "win32hle.h"

#define AVIERR_BADHANDLE   0x8004406Cu
#define AVIERR_FILEOPEN    0x8004406Fu
#define ICERR_UNSUPPORTED  0xFFFFFFFFu

static void avi_file_open(void) { if (A32(0)) MEM32(A32(0))=0; RET(AVIERR_FILEOPEN,4); }
static void avi_file_get_stream(void) { if (A32(1)) MEM32(A32(1))=0; RET(AVIERR_BADHANDLE,4); }
static void avi_file_info(void) { RET(AVIERR_BADHANDLE,3); }
static void avi_file_init(void) { RETV(0); }
static void avi_file_exit(void) { RETV(0); }
static void avi_file_release(void) { RET(0,1); }
static void avi_stream_info(void) { RET(AVIERR_BADHANDLE,3); }
static void avi_stream_release(void) { RET(0,1); }
static void avi_stream_read_format(void) {
    if (A32(3)) MEM32(A32(3))=0;
    RET(AVIERR_BADHANDLE,4);
}
static void avi_stream_read(void) {
    if (A32(5)) MEM32(A32(5))=0;
    if (A32(6)) MEM32(A32(6))=0;
    RET(AVIERR_BADHANDLE,7);
}
static void ic_locate(void) { RET(0,5); }
static void ic_send_message(void) { RET(ICERR_UNSUPPORTED,4); }
static void ic_close(void) { RET(0,1); }
static void ic_decompress(void) { RET(ICERR_UNSUPPORTED,6); }

const win32hle_shim win32hle_avifil32[] = {
    {"AVIFileOpenA",avi_file_open}, {"AVIFileGetStream",avi_file_get_stream},
    {"AVIFileInfoA",avi_file_info}, {"AVIFileInit",avi_file_init},
    {"AVIFileExit",avi_file_exit}, {"AVIFileRelease",avi_file_release},
    {"AVIStreamInfoA",avi_stream_info}, {"AVIStreamRelease",avi_stream_release},
    {"AVIStreamReadFormat",avi_stream_read_format}, {"AVIStreamRead",avi_stream_read},
    {"ICLocate",ic_locate}, {"ICSendMessage",ic_send_message},
    {"ICClose",ic_close}, {"ICDecompress",ic_decompress}, {0,0}
};
