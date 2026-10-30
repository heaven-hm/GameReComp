#!/bin/sh
# Per-agent git identity: commit as the repo owner, sign, and credit the agent.
#
#   sh tools/agent-commit.sh codex  "runtime: ..." file1 file2 ...
#   sh tools/agent-commit.sh claude "docs: ..."    file1 file2 ...
#
# Author is always the account owner (so GitHub attributes the commit to
# heaven-hm); the Committer is the agent, which is what `git log --format=%cn`
# and the Contributors section read. Signing is SSH (user.signingkey in the
# repo config); verify with `git log --show-signature -1`.
set -eu

AGENT=${1:?usage: agent-commit.sh <codex|claude|lead> <message> [paths...]}
MESSAGE=${2:?usage: agent-commit.sh <codex|claude|lead> <message> [paths...]}
shift 2

case "$AGENT" in
  codex)  NAME="Codex";       EMAIL="codex@openai.com" ;;
  claude) NAME="Claude Code"; EMAIL="noreply@anthropic.com" ;;
  lead)   NAME="Heaven-HM";   EMAIL="93740747+heaven-hm@users.noreply.github.com" ;;
  *) echo "unknown agent: $AGENT (use codex, claude or lead)" >&2; exit 2 ;;
esac

if [ "$#" -gt 0 ]; then
  git add -- "$@"
fi

GIT_COMMITTER_NAME="$NAME" GIT_COMMITTER_EMAIL="$EMAIL" \
  git commit -m "$MESSAGE"

echo "--- committed ---"
git log --format='%h %an <%ae> | committer %cn <%ce> | %G? %GS' -1
