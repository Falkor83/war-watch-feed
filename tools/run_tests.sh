#!/usr/bin/env bash
# Offline tests (fake API, no cost) before a job spends money or publishes. Run from the repo root, after live/ is
# restored from the live branch.
#
# A failure stops the job, unless this exact code (tools/, config/, prompt/, workflows) already passed the tests
# in an earlier run. Then the code didn't change, so the failure is in a test itself (on 2026-11-06 a fixed date in a
# fixture went stale and stopped the hourly and daily jobs for a day): the job goes on, with a warning on the run.
# The hourly job publishes the marker (live/tested_code) with the headlines; the daily job reads it.
# Last, the tests run again with the clock 14 days ahead, to warn about a test going stale before it does.
set -uo pipefail
key=$(git ls-tree -r HEAD tools config prompt .github/workflows | sha1sum | cut -c1-16)
mark=live/tested_code
suites=(tools/test_feed.py tools/test_headlines.py tools/test_focus.py tools/test_actions.py)

if python3 -m unittest "${suites[@]}"; then
  mkdir -p live && echo "$key" > "$mark"
  echo "[OK] tests passed (code $key)"
  if ! python3 tools/check_tests_ahead.py 14 > /tmp/ahead.log 2>&1; then
    tail -n 30 /tmp/ahead.log
    echo "::warning title=A test will fail within 14 days::The tests fail with the clock 14 days ahead (a fixed date in a test). Fix it before then: python3 tools/check_tests_ahead.py 14"
  fi
  exit 0
fi
if [ -f "$mark" ] && [ "$(cat "$mark")" = "$key" ]; then
  echo "::warning title=Tests failed on code that passed them before::The code is unchanged since a run that passed, so a test itself went stale. The job goes on; fix the test (python3 tools/check_tests_ahead.py 0)."
  exit 0
fi
echo "[ERROR] the tests fail on new code (code $key); nothing was collected or spent"
exit 1
