#!/bin/bash
# Install LaunchAgents for this clone. Templates keep __REPO_ROOT__; the
# copies in ~/Library/LaunchAgents get the real path. Re-run after moving the repo.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="${HOME}/Library/LaunchAgents"
mkdir -p "$DEST" "$ROOT/logs"

shopt -s nullglob
installed=0
for src in "$ROOT"/deploy/launchd/com.tradey.*.plist; do
  name="$(basename "$src")"
  dst="$DEST/$name"
  sed "s|__REPO_ROOT__|$ROOT|g" "$src" > "$dst"
  launchctl unload "$dst" 2>/dev/null || true
  launchctl load "$dst"
  echo "installed $dst"
  installed=$((installed + 1))
done

if [[ "$installed" -eq 0 ]]; then
  echo "error: no com.tradey.*.plist templates found" >&2
  exit 1
fi

echo "loaded $installed agent(s) for $ROOT"
