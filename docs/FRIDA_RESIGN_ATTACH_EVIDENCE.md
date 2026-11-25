# Frida attach on macOS arm64 after ad-hoc re-signing

Issue #171 recorded `PermissionDeniedError` for both the original PE32 under
Highball/Wine and the rebuilt native binary. The official Frida troubleshooting
guide documents `task_for_pid` / signing requirements on macOS and gives the
system-level `security authorizationdb write system.privilege.taskport allow`
workaround, which weakens security and needs root. That command was **not**
run.

A non-invasive alternative was tested instead. A *copy* of an already rebuilt
native game was ad-hoc signed with the `com.apple.security.get-task-allow`
entitlement and launched normally:

```sh
cp /path/to/rebuilt-binary /tmp/frida-resign/target-copy
cat > /tmp/frida-resign/entitlements.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>com.apple.security.get-task-allow</key><true/>
</dict></plist>
EOF
codesign -f -s - --entitlements /tmp/frida-resign/entitlements.plist \
  /tmp/frida-resign/target-copy
```

Then, with the copy running:

```python
import frida
session = frida.attach(pid)            # succeeded
script = session.create_script(
  "send({op:'modules', names:Process.enumerateModules().slice(0,8).map(m=>m.name), "
  "arch:Process.arch, platform:Process.platform});")
# on_message captured: choc-copy, libSDL2_ttf-2.0.0.dylib, libSDL2-2.0.0.dylib,
# libavformat.63.1.102.dylib, libavcodec.63.1.102.dylib,
# libswresample.7.1.102.dylib, libavutil.61.1.102.dylib, Cocoa
# arch=arm64, platform=darwin
```

Recorded verbatim in `proof/frida/attach-success.json`: Frida 17.24.3,
`attach: OK`, eight enumerated modules. The rebuilt binary SHA-256 is
`c4c000a39369c3964cac0ab11bbba86e8fcd1d2d1980f2aa2f5db14f9640a411`.

`tools/frida_resign_evidence.py` verifies such a record fail-closed: it
recomputes the target hash from bytes, requires a genuine attach OK plus a
module list, and never emits a dual-engine MATCH or a gameplay claim.

**Limits**: this proves a Frida attach to a locally re-signed rebuilt binary,
not an attach to the original PE32 under Wine and not a Frida+Unicorn parity
result. No system security setting was changed.
