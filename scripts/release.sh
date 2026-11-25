#!/usr/bin/env bash
set -euo pipefail

if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard)" ]; then
  echo "Error: working tree has uncommitted or untracked changes." >&2
  exit 1
fi

if [ "$(git branch --show-current)" != "main" ]; then
  echo "Error: release must run from main." >&2
  exit 1
fi

git fetch origin main --quiet
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
  echo "Error: local main is not synced with origin/main." >&2
  exit 1
fi

uv run pytest -v
uv run python scripts/demo_lifecycle.py -v --fast

rm -rf dist
uv build

VER=$(uv version --short)
TAG="v${VER}"

git tag -a "$TAG" -m "Release $TAG"
git push origin "$TAG"
git tag -f latest
git push -f origin latest

git-cliff --current --strip header | gh release create "$TAG" dist/* --title "$TAG" --notes-file -
