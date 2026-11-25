# Issue #11 — controller capability evidence

Host inventory here has keyboard/mouse and no real joystick, force-feedback,
XInput or Raw Input device. `runtime/win32hle/dinput.c` registers keyboard and
mouse DInput 3/5/7; joystick/FFB are absent. `tools/controller_capability_audit.py`
reads CLI-supplied device and selftest records and emits `SUPPORTED` only with a
real device plus passing test evidence; otherwise `DEFERRED`/`UNSUPPORTED` with
exact reasons. Registration alone never yields `SUPPORTED`, and no joystick,
FFB or XInput capability is asserted without hardware.

Limits: no controller hardware was available, so this is not a controller
implementation or gameplay claim; issue #11 stays open.
