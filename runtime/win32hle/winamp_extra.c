/*
 * winamp_extra.c - bring-up shims for Winamp's wide-character imports.
 *
 * These are SAFE-DEFAULT stubs, not verified Win32 behaviour: they let the
 * lifted executable's import table bind instead of aborting on an unresolved
 * symbol. A shim that returns FALSE/0 means "the capability is unavailable",
 * never "the feature works". This file is intentionally small and explicit so
 * each stub can be replaced with a real implementation with evidence.
 */
#include <stdint.h>
#include "win32hle.h"

/* ADVAPI32 */
static void ex_RegEnumKeyW(void)     { RET(259u, 4); } /* ERROR_NO_MORE_ITEMS */
static void ex_RegDeleteKeyW(void)   { RET(0u, 2); }
static void ex_SetFileSecurityW(void){ RET(0u, 3); }

/* SHELL32 */
static void ex_SHGetSpecialFolderLocation(void) {
    uint32_t *out = (uint32_t *)hle_ptr(A32(2));
    if (out) *out = 0;
    RET(0u, 3);
}
static void ex_SHFileOperationW(void)         { RET(0u, 1); }
static void ex_ShellExecuteExW(void)          { RET(0u, 1); }
static void ex_SHGetFileInfoW(void)           { RET(0u, 5); }

/* OLE32 / COMCTL32 */
static void ex_IIDFromString(void)            { RET(0u, 2); }
static void ex_ImageList_AddMasked(void)      { RET(0u, 3); }

/* USER32: wide string helpers advance by one UTF-16 code unit. */
static void ex_CharPrevW(void) {
    uint32_t cur = A32(1);
    RET(cur >= 2 ? cur - 2 : A32(0), 2);
}
static void ex_CharNextW(void)                { RET(A32(1) + 2, 2); }
static void ex_AppendMenuW(void)              { RET(0u, 4); }
static void ex_CallWindowProcW(void)          { RET(0u, 4); }
static void ex_GetWindowLongW(void)           { RET(0u, 2); }
static void ex_SetClassLongW(void)            { RET(0u, 3); }
static void ex_SystemParametersInfoW(void)    { RET(0u, 4); }
static void ex_DialogBoxParamW(void)          { RET(0u, 5); }
static void ex_SendMessageTimeoutW(void)      { RET(0u, 7); }
static void ex_FindWindowExW(void)            { RET(0u, 4); }
static void ex_SetWindowLongW(void)           { RET(0u, 4); }
static void ex_LoadImageW(void)               { RET(0u, 6); }

/* GDI32 */
static void ex_CreateFontIndirectW(void)      { RET(0u, 1); }
static void ex_DrawTextW(void)               { RET(0u, 5); }

/* KERNEL32 file/path/profile helpers */
static void ex_GetTempFileNameW(void)         { RET(0u, 4); }
static void ex_RemoveDirectoryW(void)         { RET(0u, 1); }
static void ex_lstrcpynW(void)                { RET(A32(0), 3); }
static void ex_GetVersionExW(void)            { RET(0u, 1); }
static void ex_CopyFileW(void)                { RET(0u, 3); }
static void ex_MoveFileW(void)                { RET(0u, 2); }
static void ex_SearchPathW(void)              { RET(0u, 5); }
static void ex_lstrcmpiW(void)                { RET(0u, 2); }
static void ex_lstrcmpW(void)                 { RET(0u, 2); }
static void ex_ExpandEnvironmentStringsW(void){ RET(0u, 3); }
static void ex_WritePrivateProfileStringW(void){ RET(1u, 4); }
static void ex_GetPrivateProfileStringW(void) { RET(0u, 5); }

const win32hle_shim win32hle_winamp_extra[] = {
    { "RegEnumKeyW", ex_RegEnumKeyW },
    { "RegDeleteKeyW", ex_RegDeleteKeyW },
    { "SetFileSecurityW", ex_SetFileSecurityW },
    { "SHGetSpecialFolderLocation", ex_SHGetSpecialFolderLocation },
    { "SHFileOperationW", ex_SHFileOperationW },
    { "ShellExecuteExW", ex_ShellExecuteExW },
    { "SHGetFileInfoW", ex_SHGetFileInfoW },
    { "IIDFromString", ex_IIDFromString },
    { "ImageList_AddMasked", ex_ImageList_AddMasked },
    { "CharPrevW", ex_CharPrevW },
    { "CharNextW", ex_CharNextW },
    { "AppendMenuW", ex_AppendMenuW },
    { "CallWindowProcW", ex_CallWindowProcW },
    { "GetWindowLongW", ex_GetWindowLongW },
    { "SetClassLongW", ex_SetClassLongW },
    { "SystemParametersInfoW", ex_SystemParametersInfoW },
    { "DialogBoxParamW", ex_DialogBoxParamW },
    { "SendMessageTimeoutW", ex_SendMessageTimeoutW },
    { "FindWindowExW", ex_FindWindowExW },
    { "SetWindowLongW", ex_SetWindowLongW },
    { "LoadImageW", ex_LoadImageW },
    { "CreateFontIndirectW", ex_CreateFontIndirectW },
    { "DrawTextW", ex_DrawTextW },
    { "GetTempFileNameW", ex_GetTempFileNameW },
    { "RemoveDirectoryW", ex_RemoveDirectoryW },
    { "lstrcpynW", ex_lstrcpynW },
    { "GetVersionExW", ex_GetVersionExW },
    { "CopyFileW", ex_CopyFileW },
    { "MoveFileW", ex_MoveFileW },
    { "SearchPathW", ex_SearchPathW },
    { "lstrcmpiW", ex_lstrcmpiW },
    { "lstrcmpW", ex_lstrcmpW },
    { "ExpandEnvironmentStringsW", ex_ExpandEnvironmentStringsW },
    { "WritePrivateProfileStringW", ex_WritePrivateProfileStringW },
    { "GetPrivateProfileStringW", ex_GetPrivateProfileStringW },
    { NULL, NULL },
};
