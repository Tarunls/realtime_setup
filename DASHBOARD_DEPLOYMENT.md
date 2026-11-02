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

The updater packages `finazure.py`, `replay_display.py`, and `requirements.txt`. It also keeps
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

## Optional display replay

Replay is disabled by default. Set `DASHBOARD_REPLAY_ENABLED=true` only for a
station that needs a temporary display of its last recorded 24 hours. The status
identifies the archive date. Original measurements and timestamps remain intact,
and `/health` continues to evaluate real observations. Data less than 15 minutes
old automatically bypass replay. Set the option back to `false` after recovery.

An optional `website_code/replay_archive.csv.gz` startup cache can contain
processed observations with their original timestamps. It is not included in the
repository or deployment ZIP. Without it, the app reads the mounted CSV files.

Deployment and monitoring scripts apply their standard 24-hour, 90% health
settings. Reapply any station-specific health settings after running them.

## Local checks

With the dashboard requirements installed, run from the repository root:

```bash
PYTHONPATH=website_code python -m unittest discover -s tests -v
bash -n deploy_dashboards.sh setup_everything.sh setup_monitoring.sh setup_storage_retention.sh run_local_historic.sh
```
