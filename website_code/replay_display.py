"""Temporary display-only replay; source observations and health stay unchanged."""
import pandas as pd

REPLAY_PERIOD = pd.Timedelta(hours=24)
FRESH_FOR = pd.Timedelta(minutes=15)


def replay_display_data(source, now=None, enabled=False):
    """Loop the last recorded day into the present day when real data is stale.

    Retain original timestamps in a separate column. Rotating within a fixed
    24-hour period advances at real time and repeats indefinitely. Fresh real
    observations bypass replay automatically.
    """
    display = source.copy()
    display.attrs['replay_active'] = False
    if not enabled or display.empty or 'datetime' not in display:
        return display
    now = pd.Timestamp.now(tz='UTC') if now is None else pd.Timestamp(now)
    now = now.tz_localize('UTC') if now.tzinfo is None else now.tz_convert('UTC')
    now = now.tz_localize(None)
    original = pd.to_datetime(display['datetime'], errors='coerce')
    latest = original.max()
    if pd.isna(latest) or now - latest <= FRESH_FOR:
        return display
    # Use a half-open day to avoid showing the same phase twice at the seam.
    display = display.loc[original.gt(latest - REPLAY_PERIOD) & original.le(latest)].copy()
    display['datetime_original_utc'] = display['datetime'].copy()
    # Modulo advances the archive one second per second and wraps every day.
    display['datetime'] = now - ((now - display['datetime_original_utc']) % REPLAY_PERIOD)
    display = display.sort_values('datetime', kind='stable')
    display.attrs['replay_active'] = True
    display.attrs['source_latest_utc'] = latest
    return display


def replay_status(display, original_status):
    if not display.attrs.get('replay_active'):
        return original_status
    latest = display.attrs['source_latest_utc']
    return f"Replay • 24h archive ending {latest:%b %d, %Y %H:%M} UT"
