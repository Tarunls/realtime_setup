#!/bin/bash

# ==============================================================================
# SCINTPI: THE GOD SCRIPT (LOCAL PI + AZURE CLOUD PIPELINE)
# ==============================================================================

echo "🚀 Initiating Full ScintPi Deployment Pipeline..."

# --- 1. PROMPT FOR USER INPUTS ---
read -p "🔑 Enter your Azure Connection String: " AZURE_CONN_STR
read -p "🌐 Enter a unique name for your new Azure Web App (e.g., scintpi-dash-xyz): " WEBAPP_NAME

# Fixed Cloud Variables
RG_NAME="scintpi-rg"
LOCATION="southcentralus"
PLAN_NAME="scintpi-app-plan"

# Extract Account Name and Key from Connection String (Needed for the storage mount)
ACCT_NAME=$(echo "$AZURE_CONN_STR" | sed -n 's/.*AccountName=\([^;]*\).*/\1/p')
ACCT_KEY=$(echo "$AZURE_CONN_STR" | sed -n 's/.*AccountKey=\([^;]*\).*/\1/p')

# --- 2. INSTALL LOCAL RASPBERRY PI DEPENDENCIES ---
echo "📦 Installing required Python libraries for the Raspberry Pi..."
sudo apt-get update -y
sudo apt-get install -y python3-pip
# Installs Azure Storage Blob. Handles both older Pi OS and newer Bookworm OS requirements.
pip3 install azure-storage-blob --break-system-packages 2>/dev/null || pip3 install azure-storage-blob

# --- 3. INJECT CONNECTION STRING INTO AUTOMATION.PY ---
echo "⚙️  Injecting credentials into data_log/automation.py..."
sed -i "s|AZURE_CONNECTION_STRING=\"\"|AZURE_CONNECTION_STRING=\"$AZURE_CONN_STR\"|g" data_log/automation.py

# --- 4. CONFIGURE LOCAL RASPBERRY PI CRONTAB & START REALTIME ---
echo "⏰ Setting up Raspberry Pi automated Cron jobs..."
CURRENT_DIR="$(pwd)"

# Ensure scripts are executable
chmod +x data_log/realtime_2026
chmod +x data_log/automation.py

# Remove existing ScintPi cron jobs to prevent duplicates, then add fresh ones
crontab -l 2>/dev/null | grep -v 'realtime_2026' | grep -v 'automation.py' | crontab -

# Add realtime_2026 to run on boot, and automation.py to run every 5 minutes targeting the data_log folder
(crontab -l 2>/dev/null; echo "@reboot cd $CURRENT_DIR/data_log && ./realtime_2026") | crontab -
(crontab -l 2>/dev/null; echo "*/5 * * * * cd $CURRENT_DIR/data_log && /usr/bin/python3 automation.py") | crontab -

# --- 5. MANUAL CRONTAB REVIEW ---
echo "-------------------------------------------------------------------"
echo "⚠️  MANUAL REVIEW REQUIRED"
echo "Old data logging scripts or conflicting cron jobs might still be active."
echo "You may need to manually delete them to prevent duplicate data collection."
read -p "Would you like to open your crontab now to review/edit? (y/n): " EDIT_CRON

if [[ "$EDIT_CRON" =~ ^[Yy]$ ]]; then
    crontab -e
    echo "✅ Crontab review complete. Resuming deployment..."
else
    echo "⏭️  Skipping manual crontab review..."
fi
echo "-------------------------------------------------------------------"

# Start realtime_2026 right now
echo "▶️  Starting realtime_2026 data collection in the background..."
pkill realtime_2026 2>/dev/null || true  # Kills any existing instances
cd data_log && nohup ./realtime_2026 > /dev/null 2>&1 &
cd ..

# --- 6. CLOUD INFRASTRUCTURE: APP SERVICE PLAN & WEB APP ---
echo "☁️  Provisioning Azure App Service in $LOCATION..."
# Create the server farm
az appservice plan create --name $PLAN_NAME --resource-group $RG_NAME --sku B1 --is-linux --location $LOCATION

# Create the Web App using Python 3.10
az webapp create --resource-group $RG_NAME --plan $PLAN_NAME --name $WEBAPP_NAME --runtime "PYTHON:3.10"

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
    --mount-path /mounts/scintpidata

# --- 8. INJECT ENVIRONMENT VARIABLES ---
echo "🔧 Setting Web App Environment Variables..."
az webapp config appsettings set --resource-group $RG_NAME --name $WEBAPP_NAME --settings \
    DATA_MOUNT_PATH=/mounts/scintpidata \
    SCM_DO_BUILD_DURING_DEPLOYMENT=true \
    WEBSITES_CONTAINER_START_TIME_LIMIT=1800 \
    WEBSITES_PORT=8050

# --- 9. PACKAGE AND DEPLOY THE DASHBOARD ---
echo "📦 Packaging finazure.py and requirements.txt..."
cd website_code
zip ../deployment.zip finazure.py requirements.txt
cd ..

echo "🚀 Deploying code to Azure Web App (This may take a few minutes)..."
az webapp deployment source config-zip --resource-group $RG_NAME --name $WEBAPP_NAME --src deployment.zip

# --- 10. SET CUSTOM STARTUP COMMAND ---
echo "🏁 Setting the Gunicorn startup command..."
az webapp config set --resource-group $RG_NAME --name $WEBAPP_NAME \
    --startup-file 'sh -c "pip install -r requirements.txt && gunicorn --bind=0.0.0.0:8050 --timeout 1800 finazure:server"'

echo "==================================================================="
echo "✅ PIPELINE COMPLETE!"
echo "📡 Raspberry Pi is capturing data and syncing every 5 minutes."
echo "🌐 Your dashboard is live at: https://$WEBAPP_NAME.azurewebsites.net"
echo "==================================================================="