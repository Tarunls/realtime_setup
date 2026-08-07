# Updating all ScintPi dashboards

The dashboard source lives in `website_code/finazure.py`. After changing and
testing that file, run the updater from the project root.

## Preview the targets

```bash
./deploy_dashboards.sh --dry-run
```

The script automatically finds dashboard apps in the current Azure subscription
whose names begin with `scintpi-dash-` or `scintpidashboard`.

## Deploy

```bash
./deploy_dashboards.sh
```

On the first run for an older app, the script asks for its station name,
location, and IANA timezone (for example, `America/Lima`). Azure saves those
values with the app, so later deployments are automatic.

To update only specific apps, list them explicitly:

```bash
./deploy_dashboards.sh \
  scintpi-rg/scintpi-dash-utd \
  OTHER_RESOURCE_GROUP/OTHER_WEBAPP
```

## Before deploying

1. Sign in with `az login`.
2. Select the intended subscription with `az account set --subscription NAME`
   if you use more than one.
3. Run the dry run and confirm every intended app appears.
4. Deploy, then open each URL printed by the script and check the header,
   plots, controls, and mobile tabs.

The updater packages only `finazure.py` and `requirements.txt`. It also keeps
the supported Python/Gunicorn startup settings consistent. It does not change
storage mounts or Raspberry Pi data collection.

Storage retention is managed separately:

```bash
./setup_storage_retention.sh RESOURCE_GROUP/STORAGE_ACCOUNT
```

New station setup runs this automatically.

## Configure automatic health alerts

After deploying the dashboard code, preview the monitoring targets:

```bash
./setup_monitoring.sh --dry-run YOUR_EMAIL@gmail.com
```

Then enable the alerts:

```bash
./setup_monitoring.sh YOUR_EMAIL@gmail.com
```

No Gmail password or app password is needed. Azure Monitor sends the email.
The app is considered healthy only when it responds and has data in more than
90% of the minute bins over the previous 24 hours. Azure checks continuously
and sends an email when the dashboard fails and when it recovers.

Azure only searches the currently selected subscription. If dashboards are in
different subscriptions, select each subscription and run the deployment and
monitoring commands once for each.

## Existing UTD app

Do not delete the UTD resource group or storage account: the storage account
already contains uploaded observations. Update the app in place:

```bash
./deploy_dashboards.sh scintpi-rg/scintpi-dash-utd
./setup_monitoring.sh YOUR_EMAIL@gmail.com scintpi-rg/scintpi-dash-utd
```
