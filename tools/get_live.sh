#!/usr/bin/env bash
# Restore the `live` branch (worldwide headlines, translation cache and ledger) into live/.
# Only a branch that doesn't exist yet counts as a first run. Any other failure is retried, then stops the job: before
# 2026-11-07 a GitHub hiccup here looked like a first run, so the translation ledger restarted at $0 and an empty cache
# was force-pushed over the real one.
set -uo pipefail
mkdir -p live
rc=0; git ls-remote --exit-code --heads origin live >/dev/null 2>&1 || rc=$?
if [ "$rc" -eq 2 ]; then echo "[WARN] first run: no live branch yet"; exit 0; fi
for i in 1 2 3; do
  if [ "$rc" -eq 0 ] && git fetch -q --depth 1 origin live && git archive FETCH_HEAD | tar -x -C live; then
    echo "[OK] live state restored ($(ls live | wc -l) files)"; exit 0
  fi
  echo "[WARN] couldn't get the live branch (try $i of 3); retrying in $((i * 20)) s"; sleep $((i * 20))
  rc=0; git ls-remote --exit-code --heads origin live >/dev/null 2>&1 || rc=$?
  if [ "$rc" -eq 2 ]; then echo "[WARN] first run: no live branch yet"; exit 0; fi
done
echo "[ERROR] couldn't restore the live branch; stopping rather than starting over from an empty state"
exit 1
