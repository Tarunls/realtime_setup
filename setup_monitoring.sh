#!/usr/bin/env bash
set -Eeuo pipefail

ACTION_GROUP_NAME="scintpi-dashboard-alerts"
ACTION_GROUP_SHORT_NAME="ScintPi"
DRY_RUN=false
TARGETS=()
ALERT_EMAIL=""

usage() {
    cat <<'EOF'
Usage:
  ./setup_monitoring.sh [--dry-run] EMAIL [RESOURCE_GROUP/WEBAPP ...]

Examples:
  ./setup_monitoring.sh --dry-run isaac@example.com
  ./setup_monitoring.sh isaac@example.com
  ./setup_monitoring.sh isaac@example.com scintpi-rg/scintpi-dash-utd

With no explicit targets, the script configures every web app in the current
Azure subscription whose name starts with "scintpi-dash-" or "scintpidashboard".

The email address is used by Azure Monitor. No Gmail password is required.
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
        *@*)
            if [[ -n "$ALERT_EMAIL" ]]; then
                echo "Only one alert email address may be supplied." >&2
                exit 2
            fi
            ALERT_EMAIL="$arg"
            ;;
        *)
            echo "Invalid argument: $arg" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ -z "$ALERT_EMAIL" ]]; then
    echo "An alert email address is required." >&2
    usage >&2
    exit 2
fi

if ! command -v az >/dev/null 2>&1; then
    echo "Azure CLI is required." >&2
    exit 1
fi

if ! az account show >/dev/null 2>&1; then
    echo "Azure login required. Run: az login" >&2
    exit 1
fi

subscription_id="$(az account show --query id --output tsv)"
subscription_name="$(az account show --query name --output tsv)"

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

monitor_resource_group="${TARGETS[0]%%/*}"
action_group_id="/subscriptions/$subscription_id/resourceGroups/$monitor_resource_group/providers/Microsoft.Insights/actionGroups/$ACTION_GROUP_NAME"

echo "Azure subscription: $subscription_name"
echo "Alert email: $ALERT_EMAIL"
echo "Monitoring targets:"
printf '  - %s\n' "${TARGETS[@]}"
echo
echo "A dashboard is healthy only when /health is online and data covers"
echo "more than 90% of the previous 24 hours."

if [[ "$DRY_RUN" == true ]]; then
    echo "Dry run complete; Azure was not changed."
    exit 0
fi

echo "Creating or updating the shared email action group..."
az monitor action-group create \
    --resource-group "$monitor_resource_group" \
    --name "$ACTION_GROUP_NAME" \
    --short-name "$ACTION_GROUP_SHORT_NAME" \
    --action email ScintPiEmail "$ALERT_EMAIL" usecommonalertschema \
    --output none

for target in "${TARGETS[@]}"; do
    resource_group="${target%%/*}"
    app_name="${target#*/}"
    app_resource_id="/subscriptions/$subscription_id/resourceGroups/$resource_group/providers/Microsoft.Web/sites/$app_name"
    alert_name="scintpi-health-$app_name"

    az resource show --ids "$app_resource_id" --output none

    echo "Configuring health monitoring for $app_name..."
    az webapp config appsettings set \
        --resource-group "$resource_group" \
        --name "$app_name" \
        --settings \
            HEALTH_WINDOW_HOURS=24 \
            HEALTH_COVERAGE_THRESHOLD=0.90 \
            WEBSITE_HEALTHCHECK_MAXPINGFAILURES=10 \
        --output none

    az webapp config set \
        --resource-group "$resource_group" \
        --name "$app_name" \
        --generic-configurations '{"healthCheckPath":"/health"}' \
        --output none

    az monitor metrics alert create \
        --resource-group "$resource_group" \
        --name "$alert_name" \
        --description "ScintPi dashboard is offline or has 90% or less data coverage over the previous 24 hours." \
        --scopes "$app_resource_id" \
        --condition "avg HealthCheckStatus < 100" \
        --window-size 15m \
        --evaluation-frequency 5m \
        --severity 1 \
        --action "$action_group_id" \
        --auto-mitigate true \
        --output none
done

echo "Monitoring is configured."
echo "Azure may send a one-time confirmation message to $ALERT_EMAIL."
echo "Alerts will be sent when a dashboard fails and when it recovers."
