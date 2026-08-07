#!/usr/bin/env bash
set -Eeuo pipefail

RETENTION_DAYS=3
RULE_NAME="delete-scintpi-data-after-72-hours"
CONTAINER_PREFIX="scintpi/"
DRY_RUN=false
TARGETS=()

usage() {
    cat <<'EOF'
Usage:
  ./setup_storage_retention.sh [--dry-run] [RESOURCE_GROUP/STORAGE_ACCOUNT ...]

Examples:
  ./setup_storage_retention.sh --dry-run
  ./setup_storage_retention.sh
  ./setup_storage_retention.sh scintpi-rg/scintpistorgutd

With no explicit targets, the script configures storage accounts in the current
Azure subscription whose names begin with "scintpistorg".

The rule permanently deletes block blobs in the "scintpi" container after
three days since their last modification. Blob soft delete and versioning are
disabled so expired data does not continue accumulating invisibly.
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
            echo "Invalid target: $arg (expected RESOURCE_GROUP/STORAGE_ACCOUNT)" >&2
            usage >&2
            exit 2
            ;;
    esac
done

for command_name in az python3; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "Missing required command: $command_name" >&2
        exit 1
    fi
done

if ! az account show >/dev/null 2>&1; then
    echo "Azure login required. Run: az login" >&2
    exit 1
fi

subscription_name="$(az account show --query name --output tsv)"

if [[ ${#TARGETS[@]} -eq 0 ]]; then
    while IFS=$'\t' read -r resource_group account_name; do
        case "$account_name" in
            scintpistorg*)
                TARGETS+=("$resource_group/$account_name")
                ;;
        esac
    done < <(az storage account list \
        --query '[].[resourceGroup,name]' \
        --output tsv)
fi

if [[ ${#TARGETS[@]} -eq 0 ]]; then
    echo "No ScintPi storage accounts were found in the current subscription." >&2
    echo "Pass targets explicitly as RESOURCE_GROUP/STORAGE_ACCOUNT." >&2
    exit 1
fi

echo "Azure subscription: $subscription_name"
echo "Storage retention targets:"
printf '  - %s\n' "${TARGETS[@]}"
echo
echo "Rule: permanently delete scintpi container blobs after 72 hours."

if [[ "$DRY_RUN" == true ]]; then
    echo "Dry run complete; Azure was not changed."
    exit 0
fi

temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/scintpi-retention.XXXXXX")"
trap 'rm -rf "$temp_dir"' EXIT

for target in "${TARGETS[@]}"; do
    resource_group="${target%%/*}"
    account_name="${target#*/}"
    current_policy="$temp_dir/${account_name}-current.json"
    updated_policy="$temp_dir/${account_name}-updated.json"

    az storage account show \
        --resource-group "$resource_group" \
        --name "$account_name" \
        --output none

    if ! az storage account management-policy show \
        --resource-group "$resource_group" \
        --account-name "$account_name" \
        --query policy \
        --output json >"$current_policy" 2>/dev/null; then
        printf '{"rules":[]}\n' >"$current_policy"
    fi

    python3 - \
        "$current_policy" \
        "$updated_policy" \
        "$RULE_NAME" \
        "$RETENTION_DAYS" \
        "$CONTAINER_PREFIX" <<'PY'
import json
import pathlib
import sys

source_path = pathlib.Path(sys.argv[1])
output_path = pathlib.Path(sys.argv[2])
rule_name = sys.argv[3]
retention_days = int(sys.argv[4])
container_prefix = sys.argv[5]

policy = json.loads(source_path.read_text())
rules = [
    rule
    for rule in policy.get("rules", [])
    if rule.get("name") != rule_name
]
rules.append(
    {
        "enabled": True,
        "name": rule_name,
        "type": "Lifecycle",
        "definition": {
            "actions": {
                "baseBlob": {
                    "delete": {
                        "daysAfterModificationGreaterThan": retention_days
                    }
                }
            },
            "filters": {
                "blobTypes": ["blockBlob"],
                "prefixMatch": [container_prefix],
            },
        },
    }
)
output_path.write_text(json.dumps({"rules": rules}, indent=2) + "\n")
PY

    echo "Configuring 72-hour retention for $account_name..."
    az storage account blob-service-properties update \
        --resource-group "$resource_group" \
        --account-name "$account_name" \
        --enable-delete-retention false \
        --enable-versioning false \
        --output none

    az storage account management-policy create \
        --resource-group "$resource_group" \
        --account-name "$account_name" \
        --policy "@$updated_policy" \
        --output none
done

echo "Storage retention is configured."
echo "Azure applies lifecycle rules asynchronously; the first cleanup may take up to 24 hours to begin."
