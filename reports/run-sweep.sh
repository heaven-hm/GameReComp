#!/bin/sh
# End-to-end sweep: every PE in the local corpus through the RevEng pipeline.
#
#   SMALL_GAMES_PATH=/path/to/games sh reports/run-sweep.sh [timeout-seconds]
#
# One line per input, read from the project's own reports/report.json -- never
# from the pipeline's stdout, which prints progress JSON as well as the final
# report. Logs land in reports/corpus/sweep-<date>.log.
set -u
cd "$(dirname "$0")/.." || exit 1
ROOT=${SMALL_GAMES_PATH:?set SMALL_GAMES_PATH to the corpus root}
STAMP=$(date -u +%Y-%m-%d)
LOG="reports/corpus/sweep-$STAMP.log"
TIMEOUT=${1:-20}

: > "$LOG"
say() { printf '%s\n' "$*" | tee -a "$LOG"; }

say "===== RevEng sweep $(date -u +%Y-%m-%dT%H:%M:%SZ) root=$ROOT timeout=${TIMEOUT}s"

find "$ROOT" -type f -name '*.exe' | sort | while IFS= read -r exe; do
  rel=${exe#"$ROOT"/}
  assets=$(dirname "$exe")
  start=$(date +%s)
  ./RevEng "$exe" --assets-dir "$assets" --timeout "$TIMEOUT" --no-table \
      > /dev/null 2>&1
  rc=$?
  end=$(date +%s)
  # The authoritative numbers are the ones the run itself wrote.
  line=$(python3 - "$rel" "$rc" "$((end-start))" <<'PY'
import json, sys
from pathlib import Path
rel, rc, secs = sys.argv[1], sys.argv[2], sys.argv[3]
name = Path(rel).parent.name
project = Path("projects") / name.replace("_", "-")
report = project / "reports" / "report.json"
try:
    data = json.loads(report.read_text())
except (OSError, ValueError):
    print(f"{rel:<46s} rc={rc:<3s} {secs:>4s}s no report.json")
    raise SystemExit(0)
stages = {s.get("name"): s for s in data.get("stages", [])}
failed = [n for n, s in stages.items() if s.get("status") == "failed"]
run = (stages.get("run") or {}).get("data") or {}
print(f"{rel:<46s} rc={rc:<3s} {secs:>4s}s "
      f"score={data.get('score', '?'):<4} "
      f"{(data.get('outcome') or {}).get('status', '?'):<14} "
      f"unresolved={run.get('unresolved_import_count', '-')!s:<4} "
      f"failed={','.join(failed) or '-'}")
PY
)
  say "$line"
done

say "===== done $(date -u +%Y-%m-%dT%H:%M:%SZ)"
