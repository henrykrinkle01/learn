#!/usr/bin/env bash
# One-time setup for the visualize skill's render helper (viz.mjs).
#
#   bash setup.sh            install node deps + a headless Chrome for Mermaid
#   bash setup.sh --check    only report what's available
#
# Node deps land in this scripts/ directory (node_modules/, git-ignored), so
# when the repo lives on a shared mount they're installed once for every
# machine. Headless Chrome goes to this machine's local puppeteer cache
# (~/.cache/puppeteer): Chrome can't mmap its data files from virtiofs/network
# mounts, so run this once per machine that should render Mermaid. Mermaid also
# needs the usual Chrome system libraries (libnss3, libgbm, ...); SVG rendering
# has no system dependencies.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if [[ "${1:-}" == "--check" ]]; then
  exec node viz.mjs check
fi

command -v node >/dev/null || { echo "node (>=18) is required" >&2; exit 1; }

if [[ ! -x node_modules/.bin/mmdc ]] || ! node -e "require.resolve('@resvg/resvg-js')" 2>/dev/null; then
  echo "Installing node dependencies…"
  PUPPETEER_SKIP_DOWNLOAD=1 npm install --no-audit --no-fund
fi

CHROME_CACHE="${PUPPETEER_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/puppeteer}"
if ! node viz.mjs check | grep -q '^mermaid: ok'; then
  echo "Installing headless Chrome into $CHROME_CACHE …"
  ./node_modules/.bin/puppeteer browsers install chrome-headless-shell --path "$CHROME_CACHE"
fi

node viz.mjs check
