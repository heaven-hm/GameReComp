# 100 concrete ways to unblock the remaining verdicts

This is an execution list, not a claim that any blocker is resolved. No verdict changes without measured evidence.

## Winamp: 235 unresolved imports

1. Generate safe-default shims for all 235 remaining import names.
2. Map each CRT import to the host libc equivalent instead of a stub.
3. Implement SHLWAPI path helpers (PathFindFileName, PathStripToRoot).
4. Implement SHGetFolderPath/SHGetSpecialFolderPath with a guest C: root.
5. Implement ShellExecuteW by opening the target through the host shell.
6. Implement IIDFromString with a small GUID table.
7. Implement ImageList_GetIcon and the COMCTL32 image list subset.
8. Provide MSVCP140 locale facet stubs with valid vtable-shaped objects.
9. Provide VCRUNTIME140 exception helpers (__CxxFrameHandler3, _purecall).
10. Add api-ms-win-crt-string wide-string functions on host wchar helpers.
11. Add api-ms-win-crt-stdio wide fopen/fgetws on host FILE* wrappers.
12. Add api-ms-win-crt-math missing intrinsics on host libm.
13. Add api-ms-win-crt-time localtime64/gmtime64 wrappers.
14. Add api-ms-win-crt-locale create_locale/wsetlocale state.
15. Bind imports by ordinal as well as by name for SHLWAPI/OLEAUT32.
16. Log every unresolved call with arguments before aborting.
17. Add a permissive mode that returns 0 instead of aborting on a stub call.
18. Seed a Winamp settings registry so startup skips the failing path.
19. Provide the original Winamp DLLs beside the rebuilt binary from the bottle.
20. Use the Highball Windows bottle as the guest DLL source at launch.

## IDM: unhandled guest C++ exception (SEH)

21. Implement _CxxThrowException to record the throw without host abort.
22. Implement __CxxFrameHandler3 to walk the guest SEH chain.
23. Map MSVC exception codes to guest handler dispatch.
24. Add a top-level guest SEH try frame around the entry point.
25. Catch RaiseException(0xC06D007F) and terminate cleanly with a reason.
26. Stub LoadLibrary("wintrust.dll") with a synthetic module.
27. Answer GetProcAddress for SetFileInformationByHandle.
28. Answer GetProcAddress for CreateActCtxW/ActivateActCtx/ReleaseActCtx.
29. Answer GetProcAddress for NotifyWinEvent.
30. Answer GetProcAddress for IsWow64Process2.
31. Answer GetProcAddress for CoInitializeSecurity.
32. Seed IDM registry keys so entitlement checks take a known branch.
33. Provide a fake IDM license/config file in the guest C: directory.
34. Run the entry point with a saved wine trace to find the throwing call.
35. Skip the failing function by patching its IAT slot to a no-op.
36. Add a guest-side fatal-error dialog stub instead of process abort.
37. Use the original Windows DLLs from the installer alongside the port.
38. Feed the rebuilt binary a dataset that avoids the license dialog.
39. Instrument the guest with a call trace before the exception address.
40. Switch the compile mode to retain SEH metadata for the failing unit.

## TrackMania ESWC/Forever: low-memory build

41. Compile oversized translation units one at a time.
42. Lower clang optimization for the three largest units.
43. Use GCC instead of clang for the oversized units.
44. Split the generated C by function ranges before compiling.
45. Increase the host swap budget for the linker.
46. Use -Os for the failed units.
47. Drop debug info from the failed units.
48. Build with -fno-function-sections to reduce metadata.
49. Link with a memory-efficient linker (lld).
50. Skip optional high-level units that are not needed to boot.
51. Free other build artifacts before the retry.
52. Run the build with a single job to cap peak RSS.
53. Cache successful object files across retries.
54. Use a scratch volume with more free space.
55. Compile only the entry-point dependency closure first.
56. Stub the three oversized units with register-transfer C.
57. Reduce the recover function set to reachable functions.
58. Use the Forever build instead of ESWC if it is smaller.
59. Record the exact clang/GCC error and target that flag specifically.
60. Accept a PARTIAL launch as the honest unblock if no binary is possible.

## Ghostty / Notes / Calculator: no GUI frame

61. Run the Windows PE build inside Highball and capture its window with CUA.
62. Run the Linux ELF build under a Linux VM and capture a frame.
63. Use Xvfb plus a screenshot helper for the Linux build.
64. Capture the macOS native app window with CUA while it runs.
65. Use RECOMP_SCREENSHOT to dump a frame from the runtime.
66. Force a frame dump after the first present call.
67. Add a frame proof JSON with distinct-colour count and hash.
68. Reject blank/one-colour frames automatically.
69. Label process-only runs as PARTIAL, never PASS.
70. Attach the captured frame to the GAME_MATRIX row.
71. Repeat the capture with a window size that avoids letterboxing.
72. Record the command and environment beside the frame.
73. Store the frame bytes in the repository, not a temp path.
74. Rehash the committed frame after any conversion.
75. Compare the native frame against an original-Windows oracle if supplied.
76. If no oracle exists, keep verdict PARTIAL explicitly.
77. Attach stdout/stderr of the run with the frame.
78. Add the frame to the full-width gallery.
79. Use a deterministic seed so the frame reproduces.
80. Publish the capture script so the frame can be regenerated.

## General blocker process

81. Write the exact blocker reason into the report before attempting fixes.
82. Reproduce with the smallest possible input.
83. Bisect the failing commit with git bisect.
84. Run the failing stage under a debugger or trace.
85. Add a regression test that fails on the current code.
86. Fix the smallest cause that makes the test pass.
87. Re-run the full stage after the fix.
88. Capture a before/after log with exit codes.
89. Commit the fix with evidence under heaven-hm.
90. Open a PR and wait for CI.
91. Merge only after CI and review.
92. Update GAME_MATRIX with the new honest verdict.
93. Delete temporary artifacts after merging.
94. Keep PARTIAL/BLOCKED where no oracle exists.
95. Never turn a build success into a gameplay PASS.
96. Prefer a real shim over a fabricated success return.
97. Document safe-default stubs as capability-unavailable.
98. Ask for the original game assets when the blocker is missing data.
99. Use the retail executable as the source of truth for constants.
100. Record every unblocking attempt, including failed ones.
