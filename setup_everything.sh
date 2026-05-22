#!/bin/bash

# ==============================================================================
# SCINTPI: THE TRUE ALL-IN-ONE SCRIPT (STORAGE + PI + WEB APP)
# ==============================================================================

echo "🚀 Initiating Full ScintPi Deployment Pipeline..."

# --- 1. PROMPT FOR USER INPUTS ---
read -p "🌐 Enter a unique ID for this station (e.g., node2, alpha, dallas1): " STATION_ID
# Storage accounts must be lowercase alphanumeric only, max 24 chars
CLEAN_ID=$(echo "$STATION_ID" | tr -dc 'a-z0-9' | cut -c 1-10)

# Fixed Cloud Variables
RG_NAME="scintpi-rg"
LOCATION="southcentralus"
PLAN_NAME="scintpi-app-plan"
WEBAPP_NAME="scintpi-dash-${CLEAN_ID}"
STORAGE_ACC_NAME="scintpistorg${CLEAN_ID}"
CONTAINER_NAME="scintpi"

# --- 2. INSTALL LOCAL RASPBERRY PI DEPENDENCIES ---
echo "📦 Installing required Python libraries for the Raspberry Pi..."
sudo apt-get update -y
sudo apt-get install -y python3-pip curl
pip3 install azure-storage-blob --break-system-packages 2>/dev/null || pip3 install azure-storage-blob

# --- 2.5 INSTALL AZURE CLI ---
if ! command -v az &> /dev/null; then
    echo "☁️  Azure CLI not found. Installing..."
    curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
else
    echo "✅ Azure CLI already installed ($(az version --query '\"azure-cli\"' -o tsv))."
fi

# --- 2.6 AZURE LOGIN ---
echo "-------------------------------------------------------------------"
echo "🔐 You need to log in to your Azure account."
echo "   A browser window will open (or you'll get a device code)."
echo "   Complete the sign-in and then return here."
echo "-------------------------------------------------------------------"
az login
if [ $? -ne 0 ]; then
    echo "❌ Azure login failed. Cannot continue without authentication."
    exit 1
fi
echo "✅ Azure login successful!"

# --- 3. CREATE AZURE STORAGE AND GET KEYS ---
echo "☁️  Creating Azure Resource Group..."
az group create --name $RG_NAME --location $LOCATION --output none

echo "📦 Provisioning Storage Account ($STORAGE_ACC_NAME)..."
az storage account create \
    --name $STORAGE_ACC_NAME \
    --resource-group $RG_NAME \
    --location $LOCATION \
    --sku Standard_LRS \
    --output none

echo "🔑 Retrieving Connection Strings automatically..."
AZURE_CONN_STR=$(az storage account show-connection-string \
    --name $STORAGE_ACC_NAME \
    --resource-group $RG_NAME \
    --query connectionString \
    --output tsv)

echo "🪣 Initializing Blob Container ($CONTAINER_NAME)..."
az storage container create \
    --name $CONTAINER_NAME \
    --connection-string "$AZURE_CONN_STR" \
    --output none

# Extract Account Name and Key from Connection String (Needed for the storage mount later)
ACCT_NAME=$(echo "$AZURE_CONN_STR" | sed -n 's/.*AccountName=\([^;]*\).*/\1/p')
ACCT_KEY=$(echo "$AZURE_CONN_STR" | sed -n 's/.*AccountKey=\([^;]*\).*/\1/p')

# --- 4. INJECT CONNECTION STRING INTO AUTOMATION.PY ---
echo "⚙️  Injecting credentials into data_log/automation.py..."
sed -i "s|AZURE_CONNECTION_STRING=\"\"|AZURE_CONNECTION_STRING=\"$AZURE_CONN_STR\"|g" data_log/automation.py

# --- 5. CONFIGURE LOCAL RASPBERRY PI CRONTAB & START REALTIME ---
echo "⏰ Setting up Raspberry Pi automated Cron jobs..."
CURRENT_DIR="$(pwd)"

chmod +x data_log/realtime_2026
chmod +x data_log/automation.py

crontab -l 2>/dev/null | grep -v 'realtime_2026' | grep -v 'automation.py' | crontab -
(crontab -l 2>/dev/null; echo "@reboot cd $CURRENT_DIR && ./data_log/realtime_2026") | crontab -
(crontab -l 2>/dev/null; echo "*/5 * * * * cd $CURRENT_DIR && /usr/bin/python3 data_log/automation.py") | crontab -

echo "-------------------------------------------------------------------"
echo "⚠️  MANUAL REVIEW REQUIRED"
echo "Old data logging scripts or conflicting cron jobs might still be active."
read -p "Would you like to open your crontab now to review/edit? (y/n): " EDIT_CRON

if [[ "$EDIT_CRON" =~ ^[Yy]$ ]]; then
    crontab -e
    echo "✅ Crontab review complete. Resuming deployment..."
else
    echo "⏭️  Skipping manual crontab review..."
fi
echo "-------------------------------------------------------------------"

echo "▶️  Starting realtime_2026 data collection in the background..."
pkill realtime_2026 2>/dev/null || true
cd "$CURRENT_DIR" && nohup ./data_log/realtime_2026 > /dev/null 2>&1 &

# --- 6. CLOUD INFRASTRUCTURE: APP SERVICE PLAN & WEB APP ---
echo "☁️  Provisioning Azure App Service Web App ($WEBAPP_NAME)..."
az appservice plan create --name $PLAN_NAME --resource-group $RG_NAME --sku B1 --is-linux --location $LOCATION --output none
az webapp create --resource-group $RG_NAME --plan $PLAN_NAME --name $WEBAPP_NAME --runtime "PYTHON:3.10" --output none

# --- 7. MOUNT AZURE BLOB STORAGE TO THE WEB APP ---
echo "🪣 Mounting Blob Storage directly to Web App (/mounts/scintpidata)..."
az webapp config storage-account add \
    --resource-group $RG_NAME \
    --name $WEBAPP_NAME \
    --custom-id ScintPiDataMount \
    --storage-type AzureBlob \
    --share-name scintpi \
    --account-name "$ACCT_NAME" \
    --access-key "$ACCT_KEY" \
    --mount-path /mounts/scintpidata \
    --output none

# --- 8. INJECT ENVIRONMENT VARIABLES ---
echo "🔧 Setting Web App Environment Variables..."
az webapp config appsettings set --resource-group $RG_NAME --name $WEBAPP_NAME --settings \
    DATA_MOUNT_PATH=/mounts/scintpidata \
    SCM_DO_BUILD_DURING_DEPLOYMENT=true \
    WEBSITES_CONTAINER_START_TIME_LIMIT=1800 \
    WEBSITES_PORT=8050 \
    --output none

# --- 9. PACKAGE AND DEPLOY THE DASHBOARD ---
echo "📦 Packaging finazure.py and requirements.txt..."
cd "$CURRENT_DIR/website_code"
zip "$CURRENT_DIR/deployment.zip" finazure.py requirements.txt
cd "$CURRENT_DIR"

echo "🚀 Deploying code to Azure Web App (This may take a few minutes)..."
az webapp deployment source config-zip --resource-group $RG_NAME --name $WEBAPP_NAME --src deployment.zip

# --- 10. SET CUSTOM STARTUP COMMAND ---
echo "🏁 Setting the Gunicorn startup command..."
az webapp config set --resource-group $RG_NAME --name $WEBAPP_NAME \
    --startup-file 'sh -c "pip install -r requirements.txt && gunicorn --bind=0.0.0.0:8050 --timeout 1800 finazure:server"' \
    --output none

echo "==================================================================="
echo "✅ PIPELINE COMPLETE!"
echo "📡 Raspberry Pi is capturing data and syncing every 5 minutes."
echo "🌐 Your dashboard is live at: https://$WEBAPP_NAME.azurewebsites.net"
echo "==================================================================="