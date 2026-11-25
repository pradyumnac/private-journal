#!/usr/bin/env bash
set -euo pipefail

PART="${1:-patch}"

if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard)" ]; then
  echo "Error: working tree is not clean." >&2
  exit 1
fi

uv version --bump "$PART"
VER=$(uv version --short)
sed -i -E "s/\"private-journal [0-9]+\.[0-9]+\.[0-9]+\"/\"private-journal ${VER}\"/" man/man1/pj.1
uv lock

git add pyproject.toml uv.lock man/man1/pj.1
git commit -m "Bump version to ${VER}"
git push origin main
