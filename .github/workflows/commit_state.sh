#!/usr/bin/env bash
# Commits content_log.json and data/bot_state.json if they changed, retrying
# the push a few times in case another HelloFides job (scheduled post or poll)
# pushed a state update at the same moment.
set -uo pipefail

git config user.name "HelloFides Bot"
git config user.email "bot@hellofides.local"

git add content_log.json data/bot_state.json data/content_queue.json data/provider_state.json

if git diff --cached --quiet; then
  echo "No bot state changes to commit."
  exit 0
fi

git commit -m "Update bot state [skip ci]"

for attempt in 1 2 3 4 5; do
  if git push origin "HEAD:${GITHUB_REF_NAME}"; then
    echo "Pushed bot state."
    exit 0
  fi
  echo "Push rejected (attempt $attempt), pulling latest and retrying..."
  git pull --rebase origin "${GITHUB_REF_NAME}"
  sleep $(( (RANDOM % 5) + 2 ))
done

echo "Failed to push bot state after several attempts." >&2
exit 1
