#!/usr/bin/env bash
# Persist engine state between GitHub Actions runs on an orphan `data` branch that is
# overwritten (one commit, no history) so the repo never grows.
#   sync_data.sh restore   -> put data/engine.db + reports/ back from the data branch
#   sync_data.sh save      -> write them back (compressed DB, newest reports)
set -euo pipefail
cmd="${1:?restore|save}"
REPO_URL="${SYNC_REMOTE:-https://x-access-token:${GITHUB_TOKEN:-}@github.com/${GITHUB_REPOSITORY:-}.git}"
WORK="$(mktemp -d)"

case "$cmd" in
restore)
  mkdir -p data reports
  if git ls-remote --exit-code --heads "$REPO_URL" data >/dev/null 2>&1; then
    git clone --quiet --depth 1 --branch data "$REPO_URL" "$WORK"
    [ -f "$WORK/engine.db.gz" ] && gunzip -c "$WORK/engine.db.gz" > data/engine.db
    [ -d "$WORK/reports" ] && cp -r "$WORK/reports/." reports/
    echo "restored: $(ls -lh data/engine.db | awk '{print $5}') db, $(ls reports | wc -l) report files"
  else
    echo "::error::no 'data' branch - seed it first (see README: Cloud)"; exit 1
  fi ;;
save)
  [ -f data/engine.db ] || { echo "::error::no database to save"; exit 1; }
  # keep the folder small: drop daily reports older than 60 days
  find reports -name 'daily*_20*.html' -mtime +60 -delete 2>/dev/null || true
  cd "$WORK"; git init --quiet -b data
  gzip -6 -c "$OLDPWD/data/engine.db" > engine.db.gz
  cp -r "$OLDPWD/reports" reports
  git config user.name "halal-engine-bot"; git config user.email "bot@users.noreply.github.com"
  git add -A; git commit --quiet -m "state $(date -u +%Y-%m-%dT%H:%MZ)"
  git push --quiet --force "$REPO_URL" data
  echo "saved: engine.db.gz $(ls -lh engine.db.gz | awk '{print $5}')" ;;
esac
