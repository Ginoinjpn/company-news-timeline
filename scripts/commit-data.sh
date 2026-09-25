#!/usr/bin/env bash
# docs/data の変更をコミットして push する。同時実行で push が拒否されたら rebase して再試行する。
set -euo pipefail
message="${1:-Update news data}"
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add docs/data
if git diff --cached --quiet; then
  echo "変更なし"
  exit 0
fi
git commit -q -m "$message"
for attempt in 1 2 3 4 5; do
  if git push -q origin HEAD:main; then
    exit 0
  fi
  git pull -q --rebase origin main
  sleep $((attempt * 5))
done
echo "push に失敗しました" >&2
exit 1
