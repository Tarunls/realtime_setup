# ScintPi realtime dashboard

Station-configurable Dash dashboard for ScintPi GNSS observations, with satellite
sky maps, S4 time series, and satellite profiles containing S4, TEC, elevation,
and azimuth.

- [Station setup](SETUP.md)
- [Dashboard updates and health alerts](DASHBOARD_DEPLOYMENT.md)
- [Dashboard source](website_code/finazure.py)
- [Python dependencies](website_code/requirements.txt)

## Preview local observations

```bash
./run_local_historic.sh /path/to/realtime/csv/directory
```

The script creates a local Python environment and opens the dashboard at
`http://127.0.0.1:8050`. Stop it with Control-C.

## Check before deploying

```bash
PYTHONPATH=website_code python -m unittest discover -s tests -v
./deploy_dashboards.sh --dry-run
```

Install `website_code/requirements.txt` before running the Python checks.
See the deployment guide for station settings, optional replay, and health
configuration. Publishing source to GitHub does not deploy the Azure apps.
