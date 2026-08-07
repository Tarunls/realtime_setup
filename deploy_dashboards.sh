#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CODE_DIR="$SCRIPT_DIR/website_code"
DRY_RUN=false
TARGETS=()

usage() {
    cat <<'EOF'
Usage:
  ./deploy_dashboards.sh [--dry-run] [RESOURCE_GROUP/WEBAPP ...]

With no targets, the script updates every web app in the current Azure
subscription whose name starts with "scintpi-dash-" or "scintpidashboard".
EOF
}

for arg in "$@"; do
    case "$arg" in
        --dry-run)
            DRY_RUN=true
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        */*)
            TARGETS+=("$arg")
            ;;
        *)
            echo "Invalid target: $arg (expected RESOURCE_GROUP/WEBAPP)" >&2
            usage >&2
            exit 2
            ;;
    esac
done

for command_name in az zip; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "Missing required command: $command_name" >&2
        exit 1
    fi
done

if [[ ! -f "$CODE_DIR/finazure.py" || ! -f "$CODE_DIR/requirements.txt" ]]; then
    echo "Run this script from the realtime_setup project; website_code is missing." >&2
    exit 1
fi

if ! az account show >/dev/null 2>&1; then
    echo "Azure login required. Run: az login" >&2
    exit 1
fi

if [[ ${#TARGETS[@]} -eq 0 ]]; then
    while IFS=$'\t' read -r resource_group app_name; do
        case "$app_name" in
            scintpi-dash-*|scintpidashboard*)
                TARGETS+=("$resource_group/$app_name")
                ;;
        esac
    done < <(az webapp list --query '[].[resourceGroup,name]' --output tsv)
fi

if [[ ${#TARGETS[@]} -eq 0 ]]; then
    echo "No ScintPi dashboard web apps were found in the current subscription." >&2
    echo "Pass targets explicitly as RESOURCE_GROUP/WEBAPP." >&2
    exit 1
fi

subscription_id="$(az account show --query id --output tsv)"

echo "Dashboard targets:"
printf '  - %s\n' "${TARGETS[@]}"

if [[ "$DRY_RUN" == true ]]; then
    echo "Dry run complete; nothing was deployed."
    exit 0
fi

temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/scintpi-dashboard-deploy.XXXXXX")"
trap 'rm -rf "$temp_dir"' EXIT
archive="$temp_dir/scintpi-dashboard.zip"

(
    cd "$CODE_DIR"
    zip -q -j "$archive" finazure.py requirements.txt
)

get_setting() {
    local resource_group="$1"
    local app_name="$2"
    local setting_name="$3"
    az webapp config appsettings list \
        --resource-group "$resource_group" \
        --name "$app_name" \
        --query "[?name=='$setting_name'].value | [0]" \
        --output tsv
}

prompt_for_setting() {
    local resource_group="$1"
    local app_name="$2"
    local setting_name="$3"
    local prompt_text="$4"
    local value

    value="$(get_setting "$resource_group" "$app_name" "$setting_name")"
    if [[ -z "$value" ]]; then
        if [[ ! -t 0 ]]; then
            echo "$setting_name is missing for $app_name; rerun interactively." >&2
            exit 1
        fi
        read -r -p "$prompt_text for $app_name: " value
        if [[ -z "$value" ]]; then
            echo "$setting_name cannot be blank." >&2
            exit 1
        fi
    fi
    printf '%s' "$value"
}

for target in "${TARGETS[@]}"; do
    resource_group="${target%%/*}"
    app_name="${target#*/}"
    app_resource_id="/subscriptions/$subscription_id/resourceGroups/$resource_group/providers/Microsoft.Web/sites/$app_name"

    az resource show \
        --ids "$app_resource_id" \
        --output none

    station_name="$(prompt_for_setting \
        "$resource_group" "$app_name" STATION_NAME "Station name")"
    station_location="$(prompt_for_setting \
        "$resource_group" "$app_name" STATION_LOCATION "Station location")"
    station_timezone="$(prompt_for_setting \
        "$resource_group" "$app_name" STATION_TIMEZONE \
        "IANA timezone (for example America/Lima)")"

    echo "Configuring $app_name..."
    az webapp config appsettings set \
        --resource-group "$resource_group" \
        --name "$app_name" \
        --settings \
            STATION_NAME="$station_name" \
            STATION_LOCATION="$station_location" \
            STATION_TIMEZONE="$station_timezone" \
            HEALTH_WINDOW_HOURS=24 \
            HEALTH_COVERAGE_THRESHOLD=0.90 \
            WEBSITE_HEALTHCHECK_MAXPINGFAILURES=10 \
            SCM_DO_BUILD_DURING_DEPLOYMENT=true \
            WEBSITES_CONTAINER_START_TIME_LIMIT=1800 \
            WEBSITES_PORT=8000 \
        --output none

    az webapp config set \
        --resource-group "$resource_group" \
        --name "$app_name" \
        --linux-fx-version "PYTHON|3.10" \
        --startup-file "gunicorn --bind=0.0.0.0:8000 --timeout 1800 --access-logfile - --error-logfile - finazure:server" \
        --always-on true \
        --http20-enabled true \
        --min-tls-version 1.2 \
        --output none

    echo "Deploying $app_name..."
    az webapp deploy \
        --resource-group "$resource_group" \
        --name "$app_name" \
        --src-path "$archive" \
        --type zip \
        --track-status true \
        --timeout 1800 \
        --output none

    host_name="$(az resource show \
        --ids "$app_resource_id" \
        --query properties.defaultHostName \
        --output tsv)"
    echo "Updated: https://$host_name"
done

echo "All dashboard deployments completed."
