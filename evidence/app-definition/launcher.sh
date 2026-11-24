#!/bin/sh
# Launch the recompiled native game from the bundle's Resources folder, where
# its data files live. Same binary and cwd as the verified /tmp run.
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../Resources" || exit 1
exec "$HERE/Doukutsu" "$@"
