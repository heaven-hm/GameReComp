/* Entry point for the rebuilt game.
 * recomp_host_main() maps the image and runs the recompiled entry point until
 * the guest exits. The image is embedded at build time, so the original
 * Windows executable is NOT needed at run time. When the game needs its own
 * data files, pass the game's directory as the first argument; otherwise just
 * run it.
 *
 * Strict binding is the default: an unresolved import fails the boot. Setting
 * RECOMP_PERMISSIVE=1 binds unresolved imports to the runtime's self-naming
 * stub instead, so the guest runs until it actually calls one and then aborts
 * naming that import. That is the bring-up loop for a new title, and what
 * tools/autorecomp/diagnose.py drives. It is a diagnostic, not a way to run a
 * game: the stub cannot return, so the run always stops at the first
 * unimplemented import.
 *
 * RECOMP_TRACE=1 logs every import dispatch; without it a crash in lifted code
 * gives no clue which shim ran last.
 */
#include "host.h"

int main(int argc, char **argv) {
    return recomp_host_main(argc, argv);
}
