#!/usr/bin/env bash
# Re-render docs/mockups/*.html into docs/assets/*.png (2x scale, headless Chrome).
set -euo pipefail
cd "$(dirname "$0")/.."
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
[ -x "$CHROME" ] || { echo "Google Chrome not found at $CHROME" >&2; exit 1; }

render() { # html png width height
  "$CHROME" --headless=new --disable-gpu --force-device-scale-factor=2 \
    --window-size="$3,$4" --screenshot="$2" "file://$PWD/$1" 2>/dev/null
  echo "rendered $2"
}

render docs/mockups/tui-rail-card.html docs/assets/tui-rail-card.png 1180 760
render docs/mockups/tui-pane.html       docs/assets/tui-pane.png       1180 840
