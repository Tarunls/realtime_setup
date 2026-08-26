# ScintPi station setup

All stations can share the `scintpi-rg` resource group and
`scintpi-app-plan`. Each station receives its own web app and storage account.
This keeps management simple while isolating each station's data and settings.

## How the system fits together

```text
GNSS receiver
    ↓
Raspberry Pi: realtime_2026 writes CSV files into RT/
    ↓ every 5 minutes
automation.py uploads the files and removes the successful local copies
    ↓
Azure Blob Storage: rolling 72-hour buffer in the scintpi container
    ↓
Azure App Service mounts that container at /mounts/scintpidata
    ↓
finazure.py reads the CSV files and serves the dashboard
    ↓
/health reports website and 24-hour data-coverage health to Azure Monitor
```

The storage limit uses an Azure lifecycle rule rather than an application
database or cleanup tracker. Blobs become eligible for permanent deletion after
three days since their last modification. Azure runs lifecycle cleanup in the
background, so deletion can begin up to 24 hours after a blob becomes eligible.

## What each shell script does

### `setup_everything.sh`

Run this on a new 64-bit Raspberry Pi. It:

- collects the station ID, name, coordinates, and timezone;
- installs the Pi and Azure command-line dependencies;
- confirms the active Azure subscription;
- creates or reuses the resource group, storage account, shared App Service
  plan, and station web app;
- calls `setup_storage_retention.sh` for the 72-hour buffer;
- configures the uploader and the Pi's scheduled data jobs;
- mounts the station storage in the web app; and
- deploys and checks the dashboard.

It is safe to rerun after a partial installation.

### `deploy_dashboards.sh`

Run this from the development computer after changing
`website_code/finazure.py` or `requirements.txt`. It packages the dashboard,
keeps the App Service startup settings consistent, and deploys the same current
dashboard code to selected stations. It does not reinstall the Raspberry Pi or
change the station's storage mount.

### `setup_storage_retention.sh`

Run this once for existing storage accounts. It creates or updates the
three-day lifecycle rule for the `scintpi` container while preserving unrelated
lifecycle rules. It also disables blob soft delete and versioning so expired
data is actually removed instead of accumulating as hidden recoverable copies.

New station installation calls this script automatically.

### `setup_monitoring.sh`

Run this after the dashboard containing `/health` has been deployed. It enables
App Service Health Check and creates an Azure Monitor alert that emails when
the website is offline or the previous 24 hours have 90% or less minute
coverage. It does not need Gmail credentials—only the destination address.

## New station

Copy this project to a 64-bit Raspberry Pi, open a terminal in the project
folder, and run:

```bash
chmod +x setup_everything.sh
./setup_everything.sh
```

The script validates the station information and Azure subscription, then
creates missing resources or safely reuses matching resources. It can be rerun
after a partial failure.

The included `realtime_2026` program is a Linux ARM64 executable, so this setup
script intentionally stops on incompatible computers.

## Local test with historic data

To run the current dashboard against the archived Jicamarca realtime CSVs
without changing Azure, run:

```bash
./run_local_historic.sh
```

Then open <http://127.0.0.1:8050>. The first run creates an ignored local
virtual environment and installs the dashboard dependencies. Stop the server
with Control-C.

To use a different directory of realtime CSV files, pass it as the first
argument:

```bash
./run_local_historic.sh /path/to/realtime/csv/files
```

## Update all dashboards

Preview which apps will change:

```bash
./deploy_dashboards.sh --dry-run
```

Deploy the current dashboard to those apps:

```bash
./deploy_dashboards.sh
```

## Update one existing station

Dashboard-only changes do not require touching the Pi:

```bash
./deploy_dashboards.sh RESOURCE_GROUP/WEBAPP
```

If storage retention has not been configured for that station, run once:

```bash
./setup_storage_retention.sh RESOURCE_GROUP/STORAGE_ACCOUNT
```

If monitoring has not been configured, run once:

```bash
./setup_monitoring.sh YOUR_EMAIL@gmail.com RESOURCE_GROUP/WEBAPP
```

For changes to `data_log/automation.py` or `realtime_2026`, copy the updated
file to the station Raspberry Pi and rerun `./setup_everything.sh`. The script
reuses the station's existing Azure resources and refreshes the local jobs.

## Add email monitoring

```bash
./setup_monitoring.sh --dry-run YOUR_EMAIL@gmail.com
./setup_monitoring.sh YOUR_EMAIL@gmail.com
```

The email is registered with Azure Monitor; Gmail credentials are not needed.
The monitor covers both availability and recent data coverage.

The scripts operate on the current Azure subscription. If stations are split
across subscriptions, use `az account set --subscription NAME` and run the
relevant command once in each subscription.

## Existing UTD station

Keep the existing `scintpi-rg` resources. Do not delete and recreate the UTD
station. Update its web app and configure its rolling storage buffer in place:

```bash
./deploy_dashboards.sh scintpi-rg/scintpi-dash-utd
./setup_storage_retention.sh scintpi-rg/scintpistorgutd
```
