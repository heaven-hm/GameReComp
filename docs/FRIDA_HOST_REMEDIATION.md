# Frida host remediation for issue #171

## Status

**#171 is OPEN and stays OPEN until a real attach succeeds and is independently
verified by the repository's harness.** This page and
`tools/frida_host_remediation.py` only rank host remedies from recorded error
signatures. They do not attach to a process, do not produce a dual-engine
result, and do not change any issue state. A written `PASS` in a probe record is
an ignored claim, never evidence.

Recorded failures on macOS 27.2.1 arm64:

| Host | Frida | Target | Recorded signature |
|---|---|---|---|
| macOS 27.2.1 arm64 | 17.24.3, 17.27.5 | `/bin/sleep` | `PermissionDeniedError: unable to access process with pid ... from the current user account` |
| macOS 27.2.1 arm64 | 17.24.3, 17.27.5 | Wine | `NotSupportedError: unexpected error while probing dyld of target process` |

Both versions failing means the dyld failure is not explained by an outdated
client alone.

## Authoritative guidance

- Frida's macOS setup: local attach goes through `task_for_pid`, and taskgated
  prompts when the tool runs from a GUI session as the local user. SIP may also
  need to be disabled. <https://frida.re/docs/examples/macos/>
- Frida troubleshooting: an SSH/headless session cannot answer the taskgate
  dialog; `sudo security authorizationdb write system.privilege.taskport allow`
  is the documented (security-weakening) fallback. On Linux the equivalent
  failure is `PTRACE_ATTACH failed`, remedied with
  `sudo sysctl kernel.yama.ptrace_scope=0` or root.
  <https://frida.re/docs/troubleshooting/>
- Frida #3420: the dyld probing failure appeared for several users on macOS
  15.4 after Apple's dyld changes and was marked completed after a client
  upgrade. <https://github.com/frida/frida/issues/3420>
- Frida #3391 and #2772: `NotSupportedError: unexpected error while probing
  dyld of target process` is reported across Frida versions and macOS builds,
  including translated processes.
  <https://github.com/frida/frida/issues/3391>,
  <https://github.com/frida/frida/issues/2772>
- friTap's macOS platform guide: on Apple silicon every binary in `/bin`,
  `/usr/bin`, `/usr/sbin` and `/System` is an arm64e **platform binary**. SIP off
  is not enough — the kernel kills `frida-helper` and Frida reports
  `PermissionDeniedError: oops, frida-helper appears to have crashed`. An ad-hoc
  signed copy (`codesign -f -s -`) avoids this without root or reboot; the
  Rosetta route does not work because Frida 17 fails on translated processes
  with the dyld probing error. Hardened runtime, library validation and a
  missing `com.apple.security.get-task-allow` entitlement also block injection.
  <https://fkie-cad.github.io/friTap/platforms/macos/>
- Apple: hardened runtime enables library validation by default, and the three
  relevant exceptions are `com.apple.security.cs.get-task-allow` (debugger
  attach), `com.apple.security.cs.disable-library-validation` and
  `com.apple.security.cs.allow-dyld-environment-variables`.
  <https://developer.apple.com/documentation/xcode/configuring-the-hardened-runtime.md>
- Frida #3752: on macOS 26.x the pip `_frida.abi3.so` carries the
  `0x20000` library-validation signature bit, which can make shared-cache
  `Interceptor.attach` hooks install but never fire.
  <https://github.com/frida/frida/issues/3752>
- Linux Yama: `ptrace_scope` 1 permits only descendants unless
  `CAP_SYS_PTRACE` is held; mode 3 cannot be changed.
  <https://www.kernel.org/doc/html/latest/admin-guide/LSM/Yama.html>

## Ranked checklist

Run commands on a disposable test host. The tool prints the same checklist,
with expected success and expected still-blocked signatures for every step.

### `PermissionDeniedError` / `unable to access process with pid ... from the current user account`

1. Run the probe from Terminal.app as the local user so taskgate can prompt;
   do not run it over SSH.
2. Inspect the host and target:
   `csrutil status`, `codesign -d -vvv "$TARGET" 2>&1 | grep -Ei 'flags|runtime|identifier'`,
   `codesign -d --entitlements - "$TARGET" 2>&1 | grep -i get-task-allow`.
3. Only for headless/SSH hosts, with an explicit security warning:
   `sudo security authorizationdb write system.privilege.taskport allow`.
4. For a target you control, add `com.apple.security.get-task-allow` with
   `PlistBuddy` and `codesign -f -s - --entitlements entitlements.plist "$TARGET"`.
5. Avoid `task_for_pid` entirely on a native Linux x86_64 runner.

Expected still-blocked signature: the same `PermissionDeniedError` from step 1.

### `PermissionDeniedError` / `oops, frida-helper appears to have crashed`

1. Confirm the platform binary:
   `codesign -dvvv /bin/sleep 2>&1 | grep -Ei 'flags|platform identifier|identifier'`.
   `Platform identifier=<n>` confirms the arm64e platform-binary case.
2. Copy, thin and ad-hoc sign it, then attach to the copy:
   `WORKDIR=$(mktemp -d)`, `cp /bin/sleep "$WORKDIR/sleep"`,
   `lipo -thin arm64e ...`, `codesign -f -s - "$WORKDIR/sleep-copy"`,
   `python3 -m tools.frida_probe --pid "$PID" --hook ExitProcess --timeout 5`.
3. Use a non-platform build (for example a Homebrew binary) instead.
4. Only if the original binary is mandatory: SIP off in Recovery, then
   `sudo nvram boot-args="-arm64e_preview_abi thid_should_crash=0 tss_should_crash=0"`,
   reboot, and run the probe with `sudo`.
5. Move the Wine attach to a native Linux x86_64 runner.

Expected still-blocked signature: the helper-crash `PermissionDeniedError` for
steps 1 and 3, or the dyld probing error if the replacement is translated.

### `NotSupportedError` / `unexpected error while probing dyld of target process`

1. Remove Rosetta from the path: `file "$TARGET"`, `lipo -info "$TARGET"`, and
   use a native arm64 slice. Wine under Rosetta is the likely cause of the
   recorded Wine failure.
2. Retry in a clean environment:
   `python3 -m venv .venv-frida`, `python3 -m pip install -U --ignore-installed frida frida-tools`.
   Upstream #3420 saw success after an upgrade, but 17.24.3 and 17.27.5 already
   failed here, so this is a check rather than an expected fix.
3. Inspect the `_frida.abi3.so` signature flags for `0x20002`; a shared-cache
   hook that installs but never fires is a related follow-up, not a proven
   cause of the probing error.
4. Run the Wine attach on native Linux x86_64:
   `cat /proc/sys/kernel/yama/ptrace_scope`, `sudo sysctl kernel.yama.ptrace_scope=0`.
5. For a relaunchable target, use Frida Gadget with a `listen` configuration on
   `127.0.0.1:27042` and `DYLD_INSERT_LIBRARIES` instead of an attach.
   <https://frida.re/docs/gadget/>

Expected still-blocked signature: the same dyld probing `NotSupportedError`
until the target architecture and signing path actually change.

### Linux `SystemError` / `attach_to_process PTRACE_ATTACH failed: 1`

1. `cat /proc/sys/kernel/yama/ptrace_scope`, then
   `sudo sysctl kernel.yama.ptrace_scope=0` (mode 3 is irreversible).
2. Run Frida as root / with `CAP_SYS_PTRACE`.
3. Use `python3 -m tools.frida_probe --spawn ...` so Frida is the parent.
4. Use Gadget via `LD_PRELOAD` for a relaunchable target.

## CLI contract

```sh
python3 -m tools.frida_host_remediation \
  --probe evidence/frida/sleep-17.24.3.json \
  --probe evidence/frida/wine-17.27.5.json \
  --out evidence/frida/remediation.md \
  --json evidence/frida/remediation.json
```

- Reads only CLI-supplied probe records; no `frida` import, process launch,
  socket, network request or `gh` call.
- Exit `0` when a known signature produced a ranked checklist, `1` when no
  confident remedy exists (manual escalation), `2` for bad or missing input.
- Always reports `attach_proven: false`, `dual-engine: NOT PROVEN`, and issue
  #171 as OPEN. It emits no GitHub auto-closing keyword.
- Reports are byte-stable: no clock, environment value or directory walk order
  is used.

A successful live attach must be recorded by the repository's own Frida harness
and verified against a real Unicorn result before anyone treats #171 as done.
This page alone is not that evidence.
