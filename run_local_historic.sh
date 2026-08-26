#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
HISTORIC_DATA_DIR="${1:-/Users/isaac/rt_JRO/realtime}"
LOCAL_VENV_DIR="${LOCAL_DASHBOARD_VENV:-$SCRIPT_DIR/.venv-local-dashboard}"
LOCAL_PORT="${PORT:-8050}"
REQUIREMENTS_FILE="$SCRIPT_DIR/website_code/requirements.txt"
REQUIREMENTS_MARKER="$LOCAL_VENV_DIR/.requirements.cksum"

if [[ ! -d "$HISTORIC_DATA_DIR" ]]; then
    echo "Historic data directory does not exist: $HISTORIC_DATA_DIR" >&2
    echo "Usage: ./run_local_historic.sh [/path/to/realtime/csv/directory]" >&2
    exit 1
fi

if ! find "$HISTORIC_DATA_DIR" -type f -name '*.csv' -print -quit | grep -q .; then
    echo "No CSV files found under: $HISTORIC_DATA_DIR" >&2
    exit 1
fi

if [[ ! -x "$LOCAL_VENV_DIR/bin/python" ]]; then
    echo "Creating local dashboard environment..."
    python3 -m venv "$LOCAL_VENV_DIR"
fi

REQUIREMENTS_SIGNATURE="$(cksum "$REQUIREMENTS_FILE")"
INSTALLED_SIGNATURE=""
if [[ -f "$REQUIREMENTS_MARKER" ]]; then
    INSTALLED_SIGNATURE="$(<"$REQUIREMENTS_MARKER")"
fi

if ! "$LOCAL_VENV_DIR/bin/python" -c \
    'import dash, dash_bootstrap_components, dash_bootstrap_templates, pandas, plotly, pytz' \
    >/dev/null 2>&1 || [[ "$INSTALLED_SIGNATURE" != "$REQUIREMENTS_SIGNATURE" ]]; then
    echo "Installing dashboard dependencies (first run only)..."
    "$LOCAL_VENV_DIR/bin/python" -m pip install \
        --requirement "$REQUIREMENTS_FILE"
    printf '%s\n' "$REQUIREMENTS_SIGNATURE" > "$REQUIREMENTS_MARKER"
fi

echo "Starting the historic dashboard without deploying anything."
echo "Data: $HISTORIC_DATA_DIR"
echo "Open: http://127.0.0.1:$LOCAL_PORT"
echo "Stop it with Control-C."

cd "$SCRIPT_DIR/website_code"
DATA_MOUNT_PATH="$HISTORIC_DATA_DIR" \
STATION_NAME="Historic Jicamarca Test" \
STATION_LOCATION="Jicamarca historic realtime data" \
STATION_TIMEZONE="America/Lima" \
HOST="127.0.0.1" \
PORT="$LOCAL_PORT" \
exec "$LOCAL_VENV_DIR/bin/python" finazure.py
