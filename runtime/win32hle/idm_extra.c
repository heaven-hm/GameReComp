/*
 * idm_extra.c - safe-default bring-up shims for IDM's Win32 imports.
 * These bind the IAT only. FALSE/0 means "capability unavailable"; none of
 * these is a verified Win32 implementation and no behaviour is claimed.
 */
#include <stdint.h>
#include "win32hle.h"

static void ex_idm_AdjustTokenPrivileges(void) { RET(1u, 6); }
static void ex_idm_CloseServiceHandle(void) { RET(1u, 1); }
static void ex_idm_CreateProcessAsUserW(void) { RET(0u, 11); }
static void ex_idm_DuplicateTokenEx(void) { RET(0u, 6); }
static void ex_idm_GetSidSubAuthority(void) { RET(0u, 2); }
static void ex_idm_GetSidSubAuthorityCount(void) { RET(0u, 1); }
static void ex_idm_GetUserNameW(void) { RET(0u, 2); }
static void ex_idm_LookupPrivilegeValueA(void) { RET(0u, 3); }
static void ex_idm_OpenSCManagerA(void) { RET(0u, 3); }
static void ex_idm_OpenServiceA(void) { RET(0u, 3); }
static void ex_idm_RegCreateKeyExW(void) { RET(0u, 9); }
static void ex_idm_RegEnumValueA(void) { RET(259u, 8); }
static void ex_idm_RegLoadKeyA(void) { RET(0u, 3); }
static void ex_idm_RegNotifyChangeKeyValue(void) { RET(0u, 5); }
static void ex_idm_RegQueryValueA(void) { RET(0u, 4); }
static void ex_idm_RegRestoreKeyA(void) { RET(0u, 3); }
static void ex_idm_RegSaveKeyA(void) { RET(0u, 3); }
static void ex_idm_RegSetValueExW(void) { RET(0u, 6); }
static void ex_idm_ImageList_GetIcon(void) { RET(0u, 3); }
static void ex_idm_GetFileTitleA(void) { RET(0u, 3); }
static void ex_idm_CopyMetaFileA(void) { RET(0u, 2); }
static void ex_idm_EnumFontFamiliesExA(void) { RET(0u, 5); }
static void ex_idm_Escape(void) { RET(0u, 5); }
static void ex_idm_ExtSelectClipRgn(void) { RET(0u, 3); }
static void ex_idm_GetMapMode(void) { RET(0u, 1); }
static void ex_idm_GetObjectW(void) { RET(0u, 3); }
static void ex_idm_GetViewportExtEx(void) { RET(0u, 2); }
static void ex_idm_GetWindowExtEx(void) { RET(0u, 2); }
static void ex_idm_OffsetViewportOrgEx(void) { RET(0u, 4); }
static void ex_idm_PtVisible(void) { RET(0u, 3); }
static void ex_idm_ScaleViewportExtEx(void) { RET(0u, 6); }
static void ex_idm_ScaleWindowExtEx(void) { RET(0u, 6); }
static void ex_idm_CancelWaitableTimer(void) { RET(1u, 1); }
static void ex_idm_ConvertDefaultLocale(void) { RET(0u, 1); }
static void ex_idm_CreateSemaphoreW(void) { RET(0u, 4); }
static void ex_idm_CreateWaitableTimerA(void) { RET(0u, 3); }
static void ex_idm_EnumResourceLanguagesA(void) { RET(0u, 5); }
static void ex_idm_FindResourceExA(void) { RET(0u, 4); }
static void ex_idm_FormatMessageW(void) { RET(0u, 7); }
static void ex_idm_GetDriveTypeW(void) { RET(3u, 1); }
static void ex_idm_GetProfileIntA(void) { RET(A32(2), 3); }
static void ex_idm_GetSystemDirectoryW(void) { RET(0u, 2); }
static void ex_idm_GetWindowsDirectoryW(void) { RET(0u, 2); }
static void ex_idm_LoadLibraryW(void) { RET(0u, 1); }
static void ex_idm_MoveFileExW(void) { RET(0u, 3); }
static void ex_idm_ProcessIdToSessionId(void) { RET(0u, 2); }
static void ex_idm_SetFileAttributesW(void) { RET(0u, 2); }
static void ex_idm_SetThreadExecutionState(void) { RET(0u, 1); }
static void ex_idm_SetWaitableTimer(void) { RET(0u, 6); }
static void ex_idm_PathFindExtensionA(void) { RET(A32(0), 1); }
static void ex_idm_PathFindFileNameA(void) { RET(A32(0), 1); }
static void ex_idm_PathIsUNCA(void) { RET(0u, 1); }
static void ex_idm_PathStripToRootA(void) { RET(A32(0), 1); }
static void ex_idm_StrCmpLogicalW(void) { RET(0u, 2); }
static void ex_idm_ChangeClipboardChain(void) { RET(0u, 2); }
static void ex_idm_CharUpperBuffW(void) { RET(0u, 2); }
static void ex_idm_CopyAcceleratorTableA(void) { RET(0u, 3); }
static void ex_idm_CopyIcon(void) { RET(0u, 1); }
static void ex_idm_DialogBoxIndirectParamW(void) { RET(0u, 5); }
static void ex_idm_DrawStateA(void) { RET(0u, 10); }
static void ex_idm_DrawTextExA(void) { RET(0u, 6); }
static void ex_idm_ExitWindowsEx(void) { RET(0u, 2); }
static void ex_idm_GetClassInfoExA(void) { RET(0u, 3); }
static void ex_idm_GetClassLongA(void) { RET(0u, 2); }
static void ex_idm_GetComboBoxInfo(void) { RET(0u, 2); }
static void ex_idm_GetDlgItemTextW(void) { RET(0u, 4); }
static void ex_idm_GetMenuCheckMarkDimensions(void) { RET(0u, 0); }
static void ex_idm_GetMenuItemInfoW(void) { RET(0u, 4); }
static void ex_idm_GetNextDlgGroupItem(void) { RET(0u, 3); }
static void ex_idm_GrayStringA(void) { RET(0u, 9); }
static void ex_idm_InvalidateRgn(void) { RET(0u, 3); }
static void ex_idm_MessageBoxW(void) { RET(0u, 4); }
static void ex_idm_ModifyMenuW(void) { RET(0u, 5); }
static void ex_idm_SendMessageW(void) { RET(0u, 4); }
static void ex_idm_SetClipboardViewer(void) { RET(0u, 1); }
static void ex_idm_SetLayeredWindowAttributes(void) { RET(0u, 4); }
static void ex_idm_SetMenuItemBitmaps(void) { RET(0u, 5); }
static void ex_idm_SetPropW(void) { RET(0u, 3); }
static void ex_idm_SetWindowContextHelpId(void) { RET(0u, 2); }

const win32hle_shim win32hle_idm_extra[] = {
    { "AdjustTokenPrivileges", ex_idm_AdjustTokenPrivileges },
    { "CloseServiceHandle", ex_idm_CloseServiceHandle },
    { "CreateProcessAsUserW", ex_idm_CreateProcessAsUserW },
    { "DuplicateTokenEx", ex_idm_DuplicateTokenEx },
    { "GetSidSubAuthority", ex_idm_GetSidSubAuthority },
    { "GetSidSubAuthorityCount", ex_idm_GetSidSubAuthorityCount },
    { "GetUserNameW", ex_idm_GetUserNameW },
    { "LookupPrivilegeValueA", ex_idm_LookupPrivilegeValueA },
    { "OpenSCManagerA", ex_idm_OpenSCManagerA },
    { "OpenServiceA", ex_idm_OpenServiceA },
    { "RegCreateKeyExW", ex_idm_RegCreateKeyExW },
    { "RegEnumValueA", ex_idm_RegEnumValueA },
    { "RegLoadKeyA", ex_idm_RegLoadKeyA },
    { "RegNotifyChangeKeyValue", ex_idm_RegNotifyChangeKeyValue },
    { "RegQueryValueA", ex_idm_RegQueryValueA },
    { "RegRestoreKeyA", ex_idm_RegRestoreKeyA },
    { "RegSaveKeyA", ex_idm_RegSaveKeyA },
    { "RegSetValueExW", ex_idm_RegSetValueExW },
    { "ImageList_GetIcon", ex_idm_ImageList_GetIcon },
    { "GetFileTitleA", ex_idm_GetFileTitleA },
    { "CopyMetaFileA", ex_idm_CopyMetaFileA },
    { "EnumFontFamiliesExA", ex_idm_EnumFontFamiliesExA },
    { "Escape", ex_idm_Escape },
    { "ExtSelectClipRgn", ex_idm_ExtSelectClipRgn },
    { "GetMapMode", ex_idm_GetMapMode },
    { "GetObjectW", ex_idm_GetObjectW },
    { "GetViewportExtEx", ex_idm_GetViewportExtEx },
    { "GetWindowExtEx", ex_idm_GetWindowExtEx },
    { "OffsetViewportOrgEx", ex_idm_OffsetViewportOrgEx },
    { "PtVisible", ex_idm_PtVisible },
    { "ScaleViewportExtEx", ex_idm_ScaleViewportExtEx },
    { "ScaleWindowExtEx", ex_idm_ScaleWindowExtEx },
    { "CancelWaitableTimer", ex_idm_CancelWaitableTimer },
    { "ConvertDefaultLocale", ex_idm_ConvertDefaultLocale },
    { "CreateSemaphoreW", ex_idm_CreateSemaphoreW },
    { "CreateWaitableTimerA", ex_idm_CreateWaitableTimerA },
    { "EnumResourceLanguagesA", ex_idm_EnumResourceLanguagesA },
    { "FindResourceExA", ex_idm_FindResourceExA },
    { "FormatMessageW", ex_idm_FormatMessageW },
    { "GetDriveTypeW", ex_idm_GetDriveTypeW },
    { "GetProfileIntA", ex_idm_GetProfileIntA },
    { "GetSystemDirectoryW", ex_idm_GetSystemDirectoryW },
    { "GetWindowsDirectoryW", ex_idm_GetWindowsDirectoryW },
    { "LoadLibraryW", ex_idm_LoadLibraryW },
    { "MoveFileExW", ex_idm_MoveFileExW },
    { "ProcessIdToSessionId", ex_idm_ProcessIdToSessionId },
    { "SetFileAttributesW", ex_idm_SetFileAttributesW },
    { "SetThreadExecutionState", ex_idm_SetThreadExecutionState },
    { "SetWaitableTimer", ex_idm_SetWaitableTimer },
    { "PathFindExtensionA", ex_idm_PathFindExtensionA },
    { "PathFindFileNameA", ex_idm_PathFindFileNameA },
    { "PathIsUNCA", ex_idm_PathIsUNCA },
    { "PathStripToRootA", ex_idm_PathStripToRootA },
    { "StrCmpLogicalW", ex_idm_StrCmpLogicalW },
    { "ChangeClipboardChain", ex_idm_ChangeClipboardChain },
    { "CharUpperBuffW", ex_idm_CharUpperBuffW },
    { "CopyAcceleratorTableA", ex_idm_CopyAcceleratorTableA },
    { "CopyIcon", ex_idm_CopyIcon },
    { "DialogBoxIndirectParamW", ex_idm_DialogBoxIndirectParamW },
    { "DrawStateA", ex_idm_DrawStateA },
    { "DrawTextExA", ex_idm_DrawTextExA },
    { "ExitWindowsEx", ex_idm_ExitWindowsEx },
    { "GetClassInfoExA", ex_idm_GetClassInfoExA },
    { "GetClassLongA", ex_idm_GetClassLongA },
    { "GetComboBoxInfo", ex_idm_GetComboBoxInfo },
    { "GetDlgItemTextW", ex_idm_GetDlgItemTextW },
    { "GetMenuCheckMarkDimensions", ex_idm_GetMenuCheckMarkDimensions },
    { "GetMenuItemInfoW", ex_idm_GetMenuItemInfoW },
    { "GetNextDlgGroupItem", ex_idm_GetNextDlgGroupItem },
    { "GrayStringA", ex_idm_GrayStringA },
    { "InvalidateRgn", ex_idm_InvalidateRgn },
    { "MessageBoxW", ex_idm_MessageBoxW },
    { "ModifyMenuW", ex_idm_ModifyMenuW },
    { "SendMessageW", ex_idm_SendMessageW },
    { "SetClipboardViewer", ex_idm_SetClipboardViewer },
    { "SetLayeredWindowAttributes", ex_idm_SetLayeredWindowAttributes },
    { "SetMenuItemBitmaps", ex_idm_SetMenuItemBitmaps },
    { "SetPropW", ex_idm_SetPropW },
    { "SetWindowContextHelpId", ex_idm_SetWindowContextHelpId },
    { NULL, NULL },
};
