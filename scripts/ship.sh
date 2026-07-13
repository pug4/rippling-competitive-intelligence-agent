#!/usr/bin/env bash
# Ship helper: build a clean distributable zip and (optionally) push to GitHub.
# The assignment accepts a GitHub repo OR a zip. This produces the zip
# unconditionally, and prints the exact GitHub push commands for when auth is
# available (the zip is the guaranteed deliverable; GitHub is the nice-to-have).
#
# Usage:
#   bash scripts/ship.sh zip        # build outputs/dist/competitive-agent.zip
#   bash scripts/ship.sh github     # create the GitHub repo and push (needs gh auth)
set -euo pipefail
cd "$(dirname "$0")/.."

case "${1:-zip}" in
  zip)
    mkdir -p outputs/dist
    OUT="outputs/dist/competitive-agent.zip"
    rm -f "$OUT"
    # git archive = exactly the tracked tree (never .env, .venv, node_modules,
    # outputs, or any gitignored secret). HEAD must be committed first.
    git archive --format=zip --output="$OUT" HEAD
    echo "Wrote $OUT ($(du -h "$OUT" | cut -f1)) — tracked files only, no secrets."
    echo "Reviewer runs: unzip, then 'make install && cp .env.example .env' and"
    echo "'uv run competitive-agent demo-check --mode fixture' (no keys needed)."
    ;;
  github)
    # One-time auth (do this first if gh is not logged in):
    #   gh auth login
    # Then create the repo from this directory and push main:
    REPO_NAME="${2:-rippling-competitive-intelligence-agent}"
    gh repo create "$REPO_NAME" --private --source=. --remote=origin --push
    echo "Pushed to GitHub as $REPO_NAME. Add a collaborator or make public to share."
    ;;
  *)
    echo "usage: bash scripts/ship.sh [zip|github]"; exit 1 ;;
esac
