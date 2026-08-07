#!/usr/bin/env bash
set -Eeuo pipefail

# ==============================================================================
# SCINTPI: ALL-IN-ONE STATION SETUP (STORAGE + PI + WEB APP)
# ==============================================================================

trap 'echo "❌ Setup stopped on line $LINENO while running: $BASH_COMMAND" >&2' ERR

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DATA_LOG_DIR="$SCRIPT_DIR/data_log"
CODE_DIR="$SCRIPT_DIR/website_code"
RETENTION_SCRIPT="$SCRIPT_DIR/setup_storage_retention.sh"

RG_NAME="scintpi-rg"
LOCATION="centralus"
PLAN_NAME="scintpi-app-plan"
CONTAINER_NAME="scintpi"
MOUNT_NAME="ScintPiDataMount"
MOUNT_PATH="/mounts/scintpidata"

usage() {
    cat <<'EOF'
Usage:
  ./setup_everything.sh

Run this from the realtime_setup project on a 64-bit Raspberry Pi. The script
prompts for station details and confirms the active Azure subscription before
making changes. It may be rerun safely after a partial setup.
EOF
}

case "${1:-}" in
    -h|--help)
        usage
        exit 0
        ;;
    "")
        ;;
    *)
        echo "Unknown argument: $1" >&2
        usage >&2
        exit 2
        ;;
esac

require_project_file() {
    if [[ ! -e "$1" ]]; then
        echo "❌ Required project file is missing: $1" >&2
        exit 1
    fi
}

azure_resource_exists() {
    az "$@" --output none >/dev/null 2>&1
}

require_project_file "$DATA_LOG_DIR/realtime_2026"
require_project_file "$DATA_LOG_DIR/automation.py"
require_project_file "$CODE_DIR/finazure.py"
require_project_file "$CODE_DIR/requirements.txt"
require_project_file "$RETENTION_SCRIPT"

machine_arch="$(uname -m)"
case "$machine_arch" in
    aarch64|arm64) ;;
    *)
        echo "❌ realtime_2026 is built for 64-bit ARM Linux, but this machine is $machine_arch." >&2
        echo "   Run this setup on a 64-bit Raspberry Pi." >&2
        exit 1
        ;;
esac

echo "🚀 Starting ScintPi station setup..."

read -r -p "🌐 Unique station ID (for example utd or jicamarca): " station_id_input
CLEAN_ID="$(printf '%s' "$station_id_input" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9')"
if [[ ${#CLEAN_ID} -lt 3 || ${#CLEAN_ID} -gt 12 ]]; then
    echo "❌ Station ID must become 3–12 lowercase letters/numbers after cleaning." >&2
    exit 1
fi

read -r -p "📍 Station name (for example Jicamarca Radio Observatory): " STATION_NAME
read -r -p "🌎 Station latitude (for example 11.95°S): " STATION_LAT
read -r -p "🌎 Station longitude (for example 76.87°W): " STATION_LON
read -r -p "🕐 IANA timezone (for example America/Lima or America/Chicago): " STATION_TIMEZONE

for required_value in "$STATION_NAME" "$STATION_LAT" "$STATION_LON" "$STATION_TIMEZONE"; do
    if [[ -z "$required_value" ]]; then
        echo "❌ Station name, coordinates, and timezone cannot be blank." >&2
        exit 1
    fi
done

STATION_LOCATION="${STATION_LAT}, ${STATION_LON}"
WEBAPP_NAME="scintpi-dash-${CLEAN_ID}"
STORAGE_ACC_NAME="scintpistorg${CLEAN_ID}"

echo "📦 Installing Raspberry Pi dependencies..."
sudo apt-get update -y
sudo apt-get install -y ca-certificates cron curl python3-pip tzdata zip
pip3 install azure-storage-blob --break-system-packages 2>/dev/null \
    || pip3 install azure-storage-blob

if ! python3 - "$STATION_TIMEZONE" <<'PY'
import sys
from zoneinfo import ZoneInfo

try:
    ZoneInfo(sys.argv[1])
except Exception:
    raise SystemExit(1)
PY
then
    echo "❌ '$STATION_TIMEZONE' is not a valid IANA timezone." >&2
    exit 1
fi

if ! command -v az >/dev/null 2>&1; then
    echo "☁️  Installing Azure CLI..."
    curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
fi

if ! az account show >/dev/null 2>&1; then
    echo "🔐 Azure login is required. Follow the displayed device-login instructions."
    az login --use-device-code --output none
fi

if [[ -n "${AZURE_SUBSCRIPTION:-}" ]]; then
    az account set --subscription "$AZURE_SUBSCRIPTION"
fi

subscription_name="$(az account show --query name --output tsv)"
subscription_id="$(az account show --query id --output tsv)"
echo "Azure subscription: $subscription_name ($subscription_id)"
read -r -p "Continue in this subscription? [y/N]: " confirm_subscription
if [[ ! "$confirm_subscription" =~ ^[Yy]$ ]]; then
    echo "Setup cancelled. Select the intended subscription and run this script again."
    exit 0
fi

echo "☁️  Creating or reusing resource group $RG_NAME..."
az group create \
    --name "$RG_NAME" \
    --location "$LOCATION" \
    --output none

if azure_resource_exists storage account show \
    --name "$STORAGE_ACC_NAME" \
    --resource-group "$RG_NAME"; then
    echo "📦 Reusing storage account $STORAGE_ACC_NAME."
else
    name_available="$(az storage account check-name \
        --name "$STORAGE_ACC_NAME" \
        --query nameAvailable \
        --output tsv)"
    if [[ "$name_available" != "true" ]]; then
        echo "❌ Storage account name $STORAGE_ACC_NAME is already used outside this setup." >&2
        echo "   Choose a different station ID." >&2
        exit 1
    fi
    echo "📦 Creating storage account $STORAGE_ACC_NAME..."
    az storage account create \
        --name "$STORAGE_ACC_NAME" \
        --resource-group "$RG_NAME" \
        --location "$LOCATION" \
        --sku Standard_LRS \
        --output none
fi

AZURE_CONN_STR="$(az storage account show-connection-string \
    --name "$STORAGE_ACC_NAME" \
    --resource-group "$RG_NAME" \
    --query connectionString \
    --output tsv)"
ACCT_NAME="$(printf '%s' "$AZURE_CONN_STR" | sed -n 's/.*AccountName=\([^;]*\).*/\1/p')"
ACCT_KEY="$(printf '%s' "$AZURE_CONN_STR" | sed -n 's/.*AccountKey=\([^;]*\).*/\1/p')"

echo "🪣 Creating or reusing blob container $CONTAINER_NAME..."
az storage container create \
    --name "$CONTAINER_NAME" \
    --connection-string "$AZURE_CONN_STR" \
    --output none

echo "🧹 Limiting Azure data storage to a 72-hour rolling buffer..."
"$RETENTION_SCRIPT" "$RG_NAME/$STORAGE_ACC_NAME"

echo "⚙️  Updating the uploader connection..."
python3 - "$DATA_LOG_DIR/automation.py" "$AZURE_CONN_STR" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
connection_string = sys.argv[2]
source = path.read_text()
updated, count = re.subn(
    r'''(?m)^\s*AZURE_CONNECTION_STRING\s*=\s*(?:".*"|'.*')\s*$''',
    f'    AZURE_CONNECTION_STRING={connection_string!r}',
    source,
)
if count != 1:
    raise SystemExit(
        f"Expected one AZURE_CONNECTION_STRING assignment in {path}; found {count}."
    )
path.write_text(updated)
PY

echo "⏰ Installing idempotent Raspberry Pi cron entries..."
chmod +x "$DATA_LOG_DIR/realtime_2026" "$DATA_LOG_DIR/automation.py"
quoted_project_dir="$(printf '%q' "$SCRIPT_DIR")"
existing_cron="$(crontab -l 2>/dev/null || true)"
filtered_cron="$(printf '%s\n' "$existing_cron" \
    | awk '!/realtime_2026/ && !/data_log\/automation\.py/')"
{
    if [[ -n "$filtered_cron" ]]; then
        printf '%s\n' "$filtered_cron"
    fi
    printf '@reboot cd %s && ./data_log/realtime_2026 >> data_log/realtime_2026.log 2>&1\n' "$quoted_project_dir"
    printf '*/5 * * * * cd %s && /usr/bin/python3 data_log/automation.py >> data_log/upload.log 2>&1\n' "$quoted_project_dir"
} | crontab -

echo "⚠️  Review the crontab if this Pi previously ran other data-logging scripts."
read -r -p "Open the crontab editor now? [y/N]: " edit_cron
if [[ "$edit_cron" =~ ^[Yy]$ ]]; then
    crontab -e
fi

echo "▶️  Starting data collection..."
pkill -x realtime_2026 2>/dev/null || true
(
    cd "$SCRIPT_DIR"
    nohup ./data_log/realtime_2026 >> data_log/realtime_2026.log 2>&1 &
)

if azure_resource_exists appservice plan show \
    --name "$PLAN_NAME" \
    --resource-group "$RG_NAME"; then
    echo "☁️  Reusing App Service plan $PLAN_NAME."
else
    echo "☁️  Creating App Service plan $PLAN_NAME..."
    az appservice plan create \
        --name "$PLAN_NAME" \
        --resource-group "$RG_NAME" \
        --sku B1 \
        --is-linux \
        --location "$LOCATION" \
        --output none
fi

if azure_resource_exists webapp show \
    --resource-group "$RG_NAME" \
    --name "$WEBAPP_NAME"; then
    echo "🌐 Reusing web app $WEBAPP_NAME."
else
    echo "🌐 Creating web app $WEBAPP_NAME..."
    az webapp create \
        --resource-group "$RG_NAME" \
        --plan "$PLAN_NAME" \
        --name "$WEBAPP_NAME" \
        --runtime "PYTHON:3.10" \
        --output none
fi

mount_exists="$(az webapp config storage-account list \
    --resource-group "$RG_NAME" \
    --name "$WEBAPP_NAME" \
    --query "[?name=='$MOUNT_NAME'] | length(@)" \
    --output tsv)"

echo "🪣 Configuring the dashboard data mount..."
if [[ "$mount_exists" == "0" ]]; then
    az webapp config storage-account add \
        --resource-group "$RG_NAME" \
        --name "$WEBAPP_NAME" \
        --custom-id "$MOUNT_NAME" \
        --storage-type AzureBlob \
        --share-name "$CONTAINER_NAME" \
        --account-name "$ACCT_NAME" \
        --access-key "$ACCT_KEY" \
        --mount-path "$MOUNT_PATH" \
        --output none
else
    az webapp config storage-account update \
        --resource-group "$RG_NAME" \
        --name "$WEBAPP_NAME" \
        --custom-id "$MOUNT_NAME" \
        --storage-type AzureBlob \
        --share-name "$CONTAINER_NAME" \
        --account-name "$ACCT_NAME" \
        --access-key "$ACCT_KEY" \
        --mount-path "$MOUNT_PATH" \
        --output none
fi

echo "🔧 Configuring the web app..."
az webapp config appsettings set \
    --resource-group "$RG_NAME" \
    --name "$WEBAPP_NAME" \
    --settings \
        DATA_MOUNT_PATH="$MOUNT_PATH" \
        STATION_NAME="$STATION_NAME" \
        STATION_LOCATION="$STATION_LOCATION" \
        STATION_TIMEZONE="$STATION_TIMEZONE" \
        HEALTH_WINDOW_HOURS=24 \
        HEALTH_COVERAGE_THRESHOLD=0.90 \
        WEBSITE_HEALTHCHECK_MAXPINGFAILURES=10 \
        SCM_DO_BUILD_DURING_DEPLOYMENT=true \
        WEBSITES_CONTAINER_START_TIME_LIMIT=1800 \
        WEBSITES_PORT=8000 \
    --output none

az webapp config set \
    --resource-group "$RG_NAME" \
    --name "$WEBAPP_NAME" \
    --linux-fx-version "PYTHON|3.10" \
    --startup-file "gunicorn --bind=0.0.0.0:8000 --timeout 1800 --access-logfile - --error-logfile - finazure:server" \
    --always-on true \
    --http20-enabled true \
    --min-tls-version 1.2 \
    --output none

temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/scintpi-setup.XXXXXX")"
trap 'rm -rf "$temp_dir"' EXIT
archive="$temp_dir/scintpi-dashboard.zip"
(
    cd "$CODE_DIR"
    zip -q -j "$archive" finazure.py requirements.txt
)

echo "🚀 Deploying the dashboard..."
az webapp deploy \
    --resource-group "$RG_NAME" \
    --name "$WEBAPP_NAME" \
    --src-path "$archive" \
    --type zip \
    --track-status true \
    --timeout 1800 \
    --output none

dashboard_url="https://${WEBAPP_NAME}.azurewebsites.net"
echo "🔎 Checking the dashboard response..."
if curl --fail --silent --show-error \
    --retry 6 \
    --retry-all-errors \
    --retry-delay 10 \
    --max-time 30 \
    "$dashboard_url" >/dev/null; then
    echo "✅ Dashboard responded successfully."
else
    echo "⚠️  Deployment finished, but the dashboard did not respond yet." >&2
    echo "   Check its App Service logs if it remains unavailable." >&2
fi

echo "==================================================================="
echo "✅ SETUP COMPLETE"
echo "📡 Data collection is running and uploads are scheduled every 5 minutes."
echo "🌐 Dashboard: $dashboard_url"
echo "==================================================================="
