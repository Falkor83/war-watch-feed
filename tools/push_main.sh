#!/usr/bin/env bash
# Commit this job's files and push them to main. When another job pushed meanwhile, start again from GitHub's newest
# main and put this job's part back, so neither job's work is lost:
#   daily: its files are copied back; feed.json keeps the focus updates made while the ratings ran (focus.py --keep)
#   focus: the saved focus update is applied again to the newer feed.json (focus.py --apply)
# Usage: tools/push_main.sh daily|focus "commit message" file...
set -uo pipefail
role="$1"; msg="$2"; shift 2
git config user.name "War Watch $role"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
save=$(mktemp -d)
for f in "$@"; do [ -e "$f" ] && cp -r --parents "$f" "$save/"; done
for i in 1 2 3 4 5 6; do
  for f in "$@"; do if [ -e "$f" ] || git ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then git add -A -- "$f"; fi; done
  if git diff --cached --quiet; then echo "[OK] Nothing changed"; exit 0; fi
  git commit -q -m "$msg"
  if git push -q origin HEAD:main; then echo "[OK] Pushed"; exit 0; fi
  echo "[WARN] push refused (another job pushed meanwhile, or a GitHub hiccup); redoing on top of the newest main ($i)"
  sleep $((i * 10))
  git fetch -q origin main || continue
  git show FETCH_HEAD:feed.json > "$save/theirs-feed.json" 2>/dev/null || true
  git reset -q --hard FETCH_HEAD
  if [ "$role" = daily ]; then
    for f in "$@"; do if [ -e "$save/$f" ]; then rm -rf "$f"; cp -r "$save/$f" "$f"; fi; done
    python3 tools/focus.py --keep "$save/theirs-feed.json"
  else
    python3 tools/focus.py --apply
  fi
done
echo "[ERROR] could not push to main"; exit 1
