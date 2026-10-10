#!/usr/bin/env sh
# Starts Kestrel on macOS / Linux: ./start.sh   (Ctrl+C stops everything)
set -e
cd "$(dirname "$0")"

command -v uv >/dev/null 2>&1 || { echo "Kestrel needs uv: https://docs.astral.sh/uv/"; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "Kestrel needs Node.js 20 or newer: https://nodejs.org"; exit 1; }

echo "Preparing the backend..."
(cd backend && uv sync)
if [ ! -d frontend/node_modules ]; then
  echo "Installing the dashboard (first run only)..."
  (cd frontend && npm install)
fi

# The dashboard runs as an optimised production build (no developer tools or overlays).
echo "Building the dashboard..."
export NEXT_TELEMETRY_DISABLED=1
(cd frontend && npm run build)

trap 'trap - INT TERM EXIT; kill 0' INT TERM EXIT
(cd backend && uv run kestrel) &
(cd frontend && npm start) &

sleep 6
url="http://localhost:3000"
if command -v open >/dev/null 2>&1; then open "$url"
elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$url" >/dev/null 2>&1 || true
fi
echo "Kestrel is running at $url. Press Ctrl+C to stop."
wait
