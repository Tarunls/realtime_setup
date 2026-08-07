import dash
from dash import dcc, html, Input, Output, State, ctx
import dash_bootstrap_components as dbc
from dash_bootstrap_templates import load_figure_template
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
import os
import logging
from datetime import datetime
import concurrent.futures
import time
import pytz
import threading # Added for background updates
from dash.exceptions import MissingCallbackContextException
from flask import jsonify

# --- CONFIGURATION ---
LOCAL_DATA_DIR = os.getenv("DATA_MOUNT_PATH", "dataforday") 
GLOBAL_STATUS = "Initializing..."
UPDATE_INTERVAL_MS = 300000 # Update graphs every 5 minutes
HEALTH_WINDOW_HOURS = float(os.getenv("HEALTH_WINDOW_HOURS", "24"))
HEALTH_COVERAGE_THRESHOLD = float(
    os.getenv("HEALTH_COVERAGE_THRESHOLD", "0.90")
)

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# Full Name System Mapping for Legend (QZSS Removed)
SYSTEM_MAP = {
    0: "GPS", 1: "SBAS", 2: "Galileo", 3: "BeiDou", 6: "GLONASS"
}
TEC_CONVERSION_FACTOR = 9.5196 
TEC_MINIMUM_VISIBLE_SPAN = 20.0
STATION_NAME = os.getenv("STATION_NAME", "ScintPi Station")
STATION_LOCATION = os.getenv("STATION_LOCATION", "Location not configured")
STATION_TZ = os.getenv("STATION_TIMEZONE", "__STATION_TIMEZONE__")
if STATION_TZ == "__STATION_TIMEZONE__":
    STATION_TZ = "UTC"


def get_triggered_id():
    """Return the Dash trigger when called by a callback, otherwise None."""
    try:
        return ctx.triggered_id
    except MissingCallbackContextException:
        return None


def station_time_label(timestamp=None):
    """Return the station's concise local timezone abbreviation."""
    if timestamp is None:
        timestamp = pd.Timestamp.now(tz=STATION_TZ)
    elif timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC").tz_convert(STATION_TZ)
    else:
        timestamp = timestamp.tz_convert(STATION_TZ)

    special_labels = {"America/Lima": "PET"}
    return special_labels.get(STATION_TZ, timestamp.strftime("%Z") or "LT")


def station_time_name(timestamp=None):
    """Return the full name of the station's local time convention."""
    abbreviation = station_time_label(timestamp)
    time_names = {
        "PET": "Peru Time",
        "CDT": "Central Daylight Time",
        "CST": "Central Standard Time",
    }
    return time_names.get(abbreviation, "Local Time")


def universal_time_title(reference_time):
    """Describe local station time as an offset from Universal Time."""
    if reference_time.tzinfo is None:
        local_reference = reference_time.tz_localize("UTC").tz_convert(STATION_TZ)
    else:
        local_reference = reference_time.tz_convert(STATION_TZ)
    local_offset = local_reference.utcoffset().total_seconds() / 3600
    sign = "+" if local_offset >= 0 else "−"
    amount = abs(local_offset)
    amount_text = str(int(amount)) if amount.is_integer() else f"{amount:g}"
    abbreviation = station_time_label(local_reference)
    return (
        "Universal Time (UT), "
        f"{station_time_name(local_reference)} ({abbreviation}) = "
        f"UT {sign} {amount_text}"
    )


def utc_ticks(start_time, end_time):
    """Return readable UTC labels at local-time positions Plotly can place."""
    start_utc = start_time.tz_convert("UTC")
    end_utc = end_time.tz_convert("UTC")
    duration_hours = (end_utc - start_utc).total_seconds() / 3600
    if duration_hours <= 3:
        frequency = "30min"
    elif duration_hours <= 8:
        frequency = "1h"
    elif duration_hours <= 14:
        frequency = "2h"
    else:
        frequency = "3h"

    tick_start = start_utc.ceil(frequency)
    tick_end = end_utc.floor(frequency)
    ticks_utc = pd.date_range(tick_start, tick_end, freq=frequency)
    if len(ticks_utc) < 2:
        ticks_utc = pd.DatetimeIndex([start_utc, end_utc])

    tick_positions = ticks_utc.tz_convert(STATION_TZ)
    tick_labels = [timestamp.strftime("%H:%M") for timestamp in ticks_utc]
    return tick_positions, tick_labels


def preset_time_limits(hours, reference_time):
    """Return a rolling local-time window ending at the latest data time."""
    return reference_time - pd.Timedelta(hours=float(hours)), reference_time


def manual_ut_time_limits(start_text, end_text, reference_time):
    """Parse a concise HH:MM manual window in universal time."""
    start_text = str(start_text).strip()
    end_text = str(end_text).strip()
    reference_utc = reference_time.tz_convert("UTC")

    end_utc = pd.Timestamp(f"{reference_utc.date()} {end_text}", tz="UTC")
    if end_utc > reference_utc + pd.Timedelta(minutes=1):
        end_utc -= pd.Timedelta(days=1)

    start_utc = pd.Timestamp(f"{end_utc.date()} {start_text}", tz="UTC")
    if start_utc >= end_utc:
        start_utc -= pd.Timedelta(days=1)

    return start_utc.tz_convert(STATION_TZ), end_utc.tz_convert(STATION_TZ)


def default_manual_window(hours):
    """Return start/end HH:MM values based on the newest loaded sample."""
    if GLOBAL_DF.empty:
        end_utc = pd.Timestamp.now(tz="UTC")
    else:
        end_utc = GLOBAL_DF["datetime"].max().tz_localize("UTC")
    start_utc = end_utc - pd.Timedelta(hours=hours)
    return start_utc.strftime("%H:%M"), end_utc.strftime("%H:%M")

# Initialize Dash
app = dash.Dash(__name__, external_stylesheets=[dbc.themes.CYBORG], 
                meta_tags=[{'name': 'viewport', 'content': 'width=device-width, initial-scale=1.0'}])
server = app.server 
load_figure_template("cyborg")

# --- CUSTOM SPLASH SCREEN ---
app.index_string = '''
<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>ScintPi Dashboard</title>
        {%favicon%}
        {%css%}
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');
            html { scroll-behavior: smooth; } /* Smooth auto-scrolling */
            
            #splash-screen {
                position: fixed;
                top: 0;
                left: 0;
                width: 100vw;
                height: 100vh;
                background-color: #0b0f19;
                z-index: 9999;
                display: flex;
                flex-direction: column;
                justify-content: center;
                align-items: center;
                transition: opacity 0.6s ease-in-out;
            }
            
            .radar-spinner {
                width: 80px;
                height: 80px;
                border: 4px solid rgba(0, 204, 150, 0.1);
                border-top-color: #00cc96;
                border-radius: 50%;
                animation: spin 1s cubic-bezier(0.68, -0.55, 0.265, 1.55) infinite;
                box-shadow: 0 0 20px rgba(0, 204, 150, 0.2);
            }
            
            @keyframes spin {
                0% { transform: rotate(0deg); }
                100% { transform: rotate(360deg); }
            }
            
            .splash-text {
                margin-top: 25px;
                color: #00cc96;
                font-family: 'Inter', sans-serif;
                font-weight: 600;
                letter-spacing: 3px;
                text-transform: uppercase;
                font-size: 0.85rem;
                animation: pulse 2s infinite;
            }
            
            @keyframes pulse {
                0%, 100% { opacity: 1; }
                50% { opacity: 0.5; }
            }

            .manuscript-dashboard {
                font-family: 'Inter', sans-serif;
                display: flex;
                flex-direction: column;
            }
            #panel-skyplot { order: 1; }
            #panel-detail { order: 2; }
            #panel-timeline { order: 3; }
            #mobile-tab-bar { order: 4; }
            .dashboard-title-link {
                color: #ffffff;
                text-decoration: none;
            }
            .dashboard-title-link:hover {
                color: #00cc96;
            }
            .about-link {
                color: #00cc96;
                text-decoration: none;
            }
            .panel-subtitle {
                color: #94a3b8;
                font-size: 0.92rem;
                font-weight: 400;
                letter-spacing: 0;
                text-transform: none;
            }
            .card-header {
                font-size: 1.05rem;
            }
            label.small,
            .form-control,
            .Select-value-label,
            .Select-placeholder {
                font-size: 0.98rem !important;
            }

            /* --- MOBILE BOTTOM TABS --- */
            #mobile-tab-bar { display: none; }

            @media (max-width: 768px) {
                #mobile-tab-bar {
                    display: flex !important;
                    position: fixed;
                    bottom: 0; left: 0; right: 0;
                    z-index: 1000;
                    background: linear-gradient(180deg, rgba(11,15,25,0.95), rgba(11,15,25,1));
                    border-top: 1px solid rgba(0, 204, 150, 0.2);
                    backdrop-filter: blur(10px);
                    padding: 6px 0 max(6px, env(safe-area-inset-bottom));
                }
                #mobile-tab-bar button {
                    flex: 1; background: none; border: none;
                    color: #64748b; font-family: 'Inter', sans-serif;
                    font-size: 0.7rem; font-weight: 600;
                    padding: 6px 4px; cursor: pointer;
                    display: flex; flex-direction: column;
                    align-items: center; gap: 3px;
                    transition: color 0.2s;
                }
                #mobile-tab-bar button.active { color: #00cc96; }
                #mobile-tab-bar button .tab-icon { font-size: 1.3rem; }
                #tab-btn-skyplot { order: 1; }
                #tab-btn-detail { order: 2; }
                #tab-btn-timeline { order: 3; }

                .container-fluid {
                    padding-bottom: calc(128px + env(safe-area-inset-bottom)) !important;
                }
                .mobile-panel { display: none; }
                .mobile-panel.mobile-active { display: block !important; }

                .mobile-panel .row > [class*="col-md-9"],
                .mobile-panel .row > [class*="col-md-3"] {
                    flex: 0 0 100%; max-width: 100%;
                }

                .mobile-header h3 { font-size: 1.1rem !important; }
                .mobile-header p { font-size: 0.75rem !important; }
                .panel-subtitle { font-size: 0.75rem; }
                .card-header { font-size: 0.95rem; }
                .manuscript-dashboard {
                    padding: 8px 8px calc(128px + env(safe-area-inset-bottom)) !important;
                }
                .mobile-panel .card-body {
                    padding: 0.65rem;
                }
            }

            @media (min-width: 769px) {
                .mobile-panel { display: block !important; }
                #mobile-tab-bar { display: none !important; }
            }
        </style>
    </head>
    <body>
        <div id="splash-screen">
            <div class="radar-spinner"></div>
            <div class="splash-text">Gathering Data...</div>
        </div>
        
        {%app_entry%}
        
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
        
        <script>
            window.onload = function() {
                setTimeout(function() {
                    var splash = document.getElementById('splash-screen');
                    if(splash) {
                        splash.style.opacity = '0';
                        setTimeout(() => splash.remove(), 600);
                    }
                }, 800); 
            };
        </script>
    </body>
</html>
'''

# --- HELPER FUNCTIONS ---
def read_local_csv(filepath):
    try:
        clean_headers = ['week', 'tow_min', 'prn', 'const', 'elev', 'az', 'n_l1', 's4_f1', 'p_f1', 'l_f1', 'n_l2', 's4_f2', 'p_f2', 'l_f2']
        df_chunk = pd.read_csv(filepath, header=0, names=clean_headers + ['junk'], usecols=clean_headers)

        if 'week' in df_chunk.columns and 'tow_min' in df_chunk.columns:
            df_chunk['week'] = pd.to_numeric(df_chunk['week'], errors='coerce')
            df_chunk['tow_min'] = pd.to_numeric(df_chunk['tow_min'], errors='coerce')
            df_chunk = df_chunk.dropna(subset=['week', 'tow_min'])
            df_chunk = df_chunk[df_chunk['week'] < 3000]
        return df_chunk
    except Exception as e:
        logger.error(f"Error reading file {filepath}: {e}")
        return None

def fetch_and_process_local_data():
    global GLOBAL_STATUS
    target_dir = os.path.abspath(LOCAL_DATA_DIR)
    
    if not os.path.exists(target_dir):
        GLOBAL_STATUS = f"❌ Error: The directory '{target_dir}' does not exist."
        return pd.DataFrame()

    try:
        all_files = []
        for root, dirs, files in os.walk(target_dir):
            for f in files:
                if f.lower().endswith(".csv"):
                    all_files.append(os.path.join(root, f))

        if not all_files:
            GLOBAL_STATUS = f"📂 Directory found, but ZERO '.csv' files exist inside {target_dir}."
            return pd.DataFrame()

        all_files.sort()
        target_files = all_files[-1500:]

        df_list = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = executor.map(read_local_csv, target_files)
            df_list = [r for r in results if r is not None and not r.empty]

        if not df_list:
            GLOBAL_STATUS = f"⚠️ Found {len(all_files)} CSVs, but failed to parse them."
            return pd.DataFrame()

        df = pd.concat(df_list, ignore_index=True)
        
        if 'const' not in df.columns:
            return pd.DataFrame()

        cols_to_coerce = ['prn', 'elev', 'az', 's4_f1', 's4_f2', 's4', 'p_f1', 'p_f2', 'n_l1', 'n_l2']
        for col in cols_to_coerce:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        df = df[df['prn'] != 255]

        gps_epoch = pd.Timestamp("1980-01-06")
        weeks_td = pd.to_timedelta(df['week'] * 7, unit='D', errors='coerce')
        tow_td = pd.to_timedelta(df['tow_min'], unit='s', errors='coerce')
        df['datetime'] = gps_epoch + weeks_td + tow_td - pd.Timedelta(seconds=18)
        df = df.dropna(subset=['datetime'])

        if df.empty: return df

        current_time_utc = pd.Timestamp.utcnow().tz_localize(None)
        df = df[df['datetime'] <= (current_time_utc + pd.Timedelta(hours=2))]
        
        if df.empty: return df

        latest_time = df['datetime'].max()
        cutoff_time = latest_time - pd.Timedelta(hours=24)
        df = df[df['datetime'] >= cutoff_time]

        SAFE_MAP = {float(k): v for k, v in SYSTEM_MAP.items()}
        df['const'] = pd.to_numeric(df['const'], errors='coerce')
        df['system'] = df['const'].map(SAFE_MAP).fillna("Unknown")
        
        df = df[df['system'] != "Unknown"]
        if df.empty: return df

        df = df.sort_values(['system', 'prn', 'datetime'])
        df = df.drop_duplicates(subset=['system', 'prn', 'datetime'], keep='last')
        
        df['time_diff'] = df.groupby(['system', 'prn'])['datetime'].diff()
        df = df[(df['time_diff'] >= pd.Timedelta(seconds=30)) | (df['time_diff'].isna())]
        df = df.drop(columns=['time_diff'])

        df['TEC'] = None
        if 'p_f1' in df.columns and 'p_f2' in df.columns:
            mask = (df['p_f2'] > 0) & (df['p_f1'] > 0) & (df['p_f2'].notna()) & (df['p_f1'].notna())
            df.loc[mask, 'TEC'] = (df.loc[mask, 'p_f2'] - df.loc[mask, 'p_f1']) * TEC_CONVERSION_FACTOR
            df.loc[(df['TEC'] > 250) | (df['TEC'] < -50), 'TEC'] = None
            
        if 'n_l1' in df.columns:
            if 's4_f1' in df.columns: df.loc[df['n_l1'] < 100, 's4_f1'] = None
            if 's4' in df.columns: df.loc[df['n_l1'] < 100, 's4'] = None
        if 'n_l2' in df.columns and 's4_f2' in df.columns:
            df.loc[df['n_l2'] < 100, 's4_f2'] = None
            
        df = df.sort_values('datetime')

        updated_at = datetime.now(pytz.timezone(STATION_TZ))
        dt_str = updated_at.strftime("%H:%M")
        GLOBAL_STATUS = (
            f"{dt_str} {station_time_label(pd.Timestamp(updated_at))} "
            f"• {len(df):,} samples"
        )
        return df

    except Exception as e:
        GLOBAL_STATUS = f"❌ Fatal Pipeline Error: {str(e)}"
        return pd.DataFrame()

# --- INITIAL DATA LOAD & BACKGROUND WORKER ---
logger.info("Pre-loading local data into memory...")
GLOBAL_DF = fetch_and_process_local_data()

def data_refresh_worker():
    """Background thread that safely refreshes GLOBAL_DF every 60 seconds."""
    global GLOBAL_DF
    while True:
        time.sleep(300) 
        try:
            logger.info("Background thread fetching new data...")
            new_df = fetch_and_process_local_data()
            if not new_df.empty:
                GLOBAL_DF = new_df
        except Exception as e:
            logger.error(f"Background refresh failed: {e}")

# Start the background polling thread for real-time updates
threading.Thread(target=data_refresh_worker, daemon=True).start()


def data_coverage_status():
    """Summarize minute-level data coverage over the previous health window."""
    expected_minutes = max(1, int(round(HEALTH_WINDOW_HOURS * 60)))
    now_utc = pd.Timestamp.now(tz="UTC").tz_localize(None).floor("min")
    window_start = now_utc - pd.Timedelta(hours=HEALTH_WINDOW_HOURS)

    if GLOBAL_DF.empty or "datetime" not in GLOBAL_DF.columns:
        return {
            "coverage": 0.0,
            "covered_minutes": 0,
            "expected_minutes": expected_minutes,
            "latest_data_utc": None,
            "latest_data_age_minutes": None,
        }

    timestamps = pd.to_datetime(GLOBAL_DF["datetime"], errors="coerce").dropna()
    timestamps = timestamps[
        (timestamps >= window_start) & (timestamps <= now_utc)
    ]
    covered_minutes = int(timestamps.dt.floor("min").nunique())
    coverage = min(1.0, covered_minutes / expected_minutes)

    all_timestamps = pd.to_datetime(
        GLOBAL_DF["datetime"],
        errors="coerce",
    ).dropna()
    latest_data = all_timestamps.max() if not all_timestamps.empty else None
    latest_age_minutes = None
    latest_data_text = None
    if latest_data is not None:
        latest_age_minutes = max(
            0.0,
            (now_utc - latest_data).total_seconds() / 60,
        )
        latest_data_text = latest_data.isoformat() + "Z"

    return {
        "coverage": coverage,
        "covered_minutes": covered_minutes,
        "expected_minutes": expected_minutes,
        "latest_data_utc": latest_data_text,
        "latest_data_age_minutes": latest_age_minutes,
    }


@server.route("/health")
def health_check():
    """Return 200 only when the dashboard has adequate recent data coverage."""
    coverage_status = data_coverage_status()
    coverage = coverage_status["coverage"]
    healthy = coverage > HEALTH_COVERAGE_THRESHOLD
    response = {
        "status": "healthy" if healthy else "unhealthy",
        "station": STATION_NAME,
        "coverage_percent": round(coverage * 100, 2),
        "required_coverage_percent": round(
            HEALTH_COVERAGE_THRESHOLD * 100,
            2,
        ),
        "window_hours": HEALTH_WINDOW_HOURS,
        **coverage_status,
    }
    return jsonify(response), 200 if healthy else 503

# Smart default satellite selection
default_sys, default_prn = "GPS", 1
initial_prn_options = []

if not GLOBAL_DF.empty:
    dual_freq_df = GLOBAL_DF.dropna(subset=['s4_f2', 'TEC'])
    if not dual_freq_df.empty:
        last_good = dual_freq_df.iloc[-1]
        default_sys, default_prn = last_good['system'], int(last_good['prn'])
    else:
        last_row = GLOBAL_DF.iloc[-1]
        default_sys, default_prn = last_row['system'], int(last_row['prn'])
        
    prns_for_default_sys = GLOBAL_DF[GLOBAL_DF['system'] == default_sys]['prn'].dropna().unique()
    initial_prn_options = [{'label': f"PRN {int(p)}", 'value': int(p)} for p in sorted(prns_for_default_sys)]

logger.info("Data loaded! App is ready.")

sky_manual_start, sky_manual_end = default_manual_window(1)
detail_manual_start, detail_manual_end = default_manual_window(3)
main_manual_start, main_manual_end = default_manual_window(5)

# --- LAYOUT ---
app.layout = dbc.Container(
    fluid=True,
    className="manuscript-dashboard",
    style={"padding": "12px", "maxWidth": "1800px"},
    children=[
    
    # Auto-scrolling anchor
    dcc.Location(id='url', refresh=False),
    # Interval timer for live refresh
    dcc.Interval(id='live-update', interval=UPDATE_INTERVAL_MS, n_intervals=0),
    dcc.Interval(id='clock-update', interval=60000, n_intervals=0),
    # Hidden store for active mobile tab
    dcc.Store(id='mobile-active-tab', data='skyplot'),

    # --- HEADER ---
    dbc.Row([
        dbc.Col([
            html.H3(
                html.A(
                    "ScintPi Monitoring Dashboard",
                    href="http://scintpi.utdallas.edu",
                    target="_blank",
                    className="dashboard-title-link",
                ),
                className="text-white text-center mb-1",
                style={'fontWeight': 'bold', 'fontSize': '2rem'},
            ),
            html.P([
                f"{STATION_NAME} • {STATION_LOCATION} • Local time: ",
                html.Span(id='current-local-time'),
                " • Last updated: ",
                html.Span(id='last-update-display'),
                " • ",
                html.A(
                    "About ScintPi",
                    href="http://scintpi.utdallas.edu",
                    target="_blank",
                    className="about-link",
                ),
            ], className="text-muted text-center mb-2", style={'fontSize': '1.05rem'}),
        ])
    ], className="dashboard-header mobile-header"),

    # --- PANEL 1: SKYPLOT ---
    html.Div(id='panel-skyplot', className='mobile-panel mobile-active', children=[
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader(html.Div([
                        html.Span("Satellite Sky Map", style={'fontWeight': 'bold', 'color': '#ffffff', 'fontSize': '1.3rem'}),
                        html.Span(id="sky-header-period", className="panel-subtitle"),
                        html.Span(" • Click a satellite for details", className="panel-subtitle"),
                    ]), className="text-center"),
                    dbc.CardBody(
                        dcc.Loading(
                            dcc.Graph(
                                id='sky-plot',
                                config={'displayModeBar': False, 'responsive': True},
                                style={"height": "430px"},
                            ),
                            color="#00cc96",
                        ),
                        className="p-2",
                    )
                ], className="border-0 shadow-sm")
            ], md=9, className="mb-3"),
            
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader("Skyplot Controls", className="text-center"),
                    dbc.CardBody([
                        html.Label("Elevation Mask (°):", className="fw-bold text-muted small"),
                        dbc.Input(id='sky-elev-mask', type='number', value=10, min=0, max=90, className="mb-3"),
                        
                        html.Label("Time Window:", className="fw-bold text-muted small"),
                        dcc.Dropdown(id='sky-time-window', options=[
                            {'label': 'Last 1 Hour', 'value': 1},
                            {'label': 'Last 3 Hours', 'value': 3},
                            {'label': 'Last 6 Hours', 'value': 6},
                            {'label': 'Full Day (24 Hours)', 'value': 24},
                            {'label': 'Manual', 'value': 'manual'},
                        ], value=1, clearable=False, className="mb-2 text-dark"),

                        dbc.Collapse(
                            html.Div([
                                html.Label("Manual Window (UT):", className="fw-bold text-muted small"),
                                dbc.InputGroup([
                                    dbc.InputGroupText("Start"),
                                    dbc.Input(id='sky-manual-start', type='text', value=sky_manual_start),
                                    dbc.InputGroupText("End"),
                                    dbc.Input(id='sky-manual-end', type='text', value=sky_manual_end),
                                ], className="mb-3"),
                            ]),
                            id='sky-manual-controls',
                            is_open=False,
                        ),

                        html.Label("Signal Band:", className="fw-bold text-muted small"),
                        dcc.Dropdown(id='sky-band', options=[
                            {'label': 'L1 Frequency', 'value': 'L1'},
                            {'label': 'L2 Frequency', 'value': 'L2'}
                        ], value='L1', clearable=False, className="mb-3 text-dark"),

                        html.Label("Color Bar Min/Max (S4):", className="fw-bold text-muted small"),
                        dbc.Row([
                            dbc.Col(dbc.Input(id='sky-s4-min', type='number', value=0, step=0.1, className="mb-3"), width=6),
                            dbc.Col(dbc.Input(id='sky-s4-max', type='number', value=0.6, step=0.1, className="mb-3"), width=6)
                        ]),

                        dbc.Row([
                            dbc.Col(dbc.Button("Apply", id='btn-sky-apply', color="primary", className="w-100 fw-bold"), width=7),
                            dbc.Col(dbc.Button("Reset", id='btn-sky-reset', outline=True, color="secondary", className="w-100"), width=5)
                        ])
                    ])
                ], className="border-0 shadow-sm", style={"height": "100%"})
            ], md=3, className="mb-3")
        ])
    ]),

    # --- PANEL 2: S4 TIMELINE ---
    html.Div(id='panel-timeline', className='mobile-panel', children=[
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader(html.Div([
                        html.Span("Scintillation Time Series", style={'fontWeight': 'bold', 'color': '#ffffff', 'fontSize': '1.3rem'}),
                        html.Span(" • Click a point for details", className="panel-subtitle"),
                    ]), className="text-center"),
                    dbc.CardBody([
                        dcc.Loading(
                            dcc.Graph(
                                id='main-graph',
                                config={'displayModeBar': False, 'responsive': True},
                                style={"height": "420px"},
                            ),
                            color="#00cc96", type="circle"
                        )
                    ], className="p-2")
                ], className="border-0 shadow-sm", style={"height": "100%"})
            ], md=9, className="mb-3"),
            
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader("Timeline Controls", className="text-center"),
                    dbc.CardBody([
                        html.Label("Elevation Mask (°):", className="fw-bold text-muted small"),
                        dbc.Input(id='main-elev-mask', type='number', value=30, min=0, max=90, className="mb-3"),
                        
                        html.Label("Time Window:", className="fw-bold text-muted small"),
                        dcc.Dropdown(id='main-time-window', options=[
                            {'label': 'Last 1 Hour', 'value': 1},
                            {'label': 'Last 3 Hours', 'value': 3},
                            {'label': 'Last 6 Hours', 'value': 6},
                            {'label': 'Last 12 Hours', 'value': 12},
                            {'label': 'Last 24 Hours', 'value': 24},
                            {'label': 'Manual', 'value': 'manual'},
                        ], value=6, clearable=False, className="mb-2 text-dark"),

                        dbc.Collapse(
                            html.Div([
                                html.Label("Manual Window (UT):", className="fw-bold text-muted small"),
                                dbc.InputGroup([
                                    dbc.InputGroupText("Start"),
                                    dbc.Input(id='main-manual-start', type='text', value=main_manual_start),
                                    dbc.InputGroupText("End"),
                                    dbc.Input(id='main-manual-end', type='text', value=main_manual_end),
                                ], className="mb-3"),
                            ]),
                            id='main-manual-controls',
                            is_open=False,
                        ),
                        
                        html.Label("Constellations:", className="fw-bold text-muted small"),
                        dcc.Dropdown(id='main-constellations', options=[{'label': v, 'value': v} for v in SYSTEM_MAP.values()], 
                                     value=list(SYSTEM_MAP.values()), multi=True, className="mb-3 text-dark"),

                        html.Label("Signal Band:", className="fw-bold text-muted small"),
                        dcc.Dropdown(id='main-band', options=[
                            {'label': 'L1 Frequency', 'value': 'L1'},
                            {'label': 'L2 Frequency', 'value': 'L2'}
                        ], value='L1', clearable=False, className="mb-4 text-dark"),

                        dbc.Row([
                            dbc.Col(dbc.Button("Apply", id='btn-main-apply', color="primary", className="w-100 fw-bold"), width=7),
                            dbc.Col(dbc.Button("Reset", id='btn-main-reset', outline=True, color="secondary", className="w-100"), width=5)
                        ])
                    ])
                ], className="border-0 shadow-sm", style={"height": "100%"})
            ], md=3, className="mb-3")
        ])
    ]),

    # --- PANEL 3: INDIVIDUAL SATELLITE DETAIL ---
    html.Div(id='panel-detail', className='mobile-panel', children=[
        dbc.Collapse(
            dbc.Row([
                dbc.Col([
                    dbc.Card([
                        dbc.CardHeader(
                            html.Div(id='detail-header', style={"fontSize": "1.3rem"}),
                            className="text-center",
                        ),
                        dbc.CardBody(
                            dcc.Graph(
                                id='detail-graph',
                                config={'displayModeBar': False, 'responsive': True},
                                style={"height": "700px", "minHeight": "700px"},
                            ),
                            className="p-2",
                        )
                    ], className="shadow border-0")
                ], md=9, className="mb-3"),
                
                dbc.Col([
                    dbc.Card([
                        dbc.CardHeader("Detail Controls", className="text-center"),
                        dbc.CardBody([
                            html.Label("Constellation:", className="fw-bold text-muted small"),
                            dcc.Dropdown(id='detail-constellation', options=[{'label': v, 'value': v} for v in SYSTEM_MAP.values()], 
                                         value=default_sys, clearable=False, className="mb-3 text-dark"),
                            
                            html.Label("Satellite PRN:", className="fw-bold text-muted small"),
                            dcc.Dropdown(id='detail-prn', options=initial_prn_options, value=default_prn, clearable=False, className="mb-3 text-dark"),

                            html.Label("Time Window:", className="fw-bold text-muted small"),
                            dcc.Dropdown(id='detail-time-window', options=[
                                {'label': 'Last 1 Hour', 'value': 1},
                                {'label': 'Last 3 Hours', 'value': 3},
                                {'label': 'Last 6 Hours', 'value': 6},
                                {'label': 'Last 12 Hours', 'value': 12},
                                {'label': 'Last 24 Hours', 'value': 24},
                                {'label': 'Manual', 'value': 'manual'},
                            ], value=3, clearable=False, className="mb-2 text-dark"),

                            dbc.Collapse(
                                html.Div([
                                    html.Label("Manual Window (UT):", className="fw-bold text-muted small"),
                                    dbc.InputGroup([
                                        dbc.InputGroupText("Start"),
                                        dbc.Input(id='detail-manual-start', type='text', value=detail_manual_start),
                                        dbc.InputGroupText("End"),
                                        dbc.Input(id='detail-manual-end', type='text', value=detail_manual_end),
                                    ], className="mb-3"),
                                ]),
                                id='detail-manual-controls',
                                is_open=False,
                            ),
                            
                            html.Label("Elevation Filter (°):", className="fw-bold text-muted small"),
                            html.Div("Points below this mask drop to NaN.", className="text-muted mb-1", style={'fontSize': '0.75rem'}),
                            dbc.Input(id='detail-elev-mask', type='number', value=10, min=0, max=90, className="mb-3"),

                            dbc.Row([
                                dbc.Col(dbc.Button("Apply", id='btn-detail-apply', color="primary", className="w-100 fw-bold"), width=7),
                                dbc.Col(dbc.Button("Reset", id='btn-detail-reset', outline=True, color="secondary", className="w-100"), width=5)
                            ])
                        ])
                    ], className="border-0 shadow-sm", style={"height": "100%"})
                ], md=3, className="mb-3")
            ]),
            id='detail-collapse',
            is_open=True 
        )
    ]),

    # --- MOBILE BOTTOM TAB BAR ---
    html.Div(id='mobile-tab-bar', children=[
        html.Button([
            html.Span("\U0001f4e1", className="tab-icon"),
            html.Span("Sky Plot")
        ], id='tab-btn-skyplot', className='active', n_clicks=0),
        html.Button([
            html.Span("\U0001f6f0", className="tab-icon"),
            html.Span("Satellite")
        ], id='tab-btn-detail', n_clicks=0),
        html.Button([
            html.Span("\U0001f4c8", className="tab-icon"),
            html.Span("S4 Timeline")
        ], id='tab-btn-timeline', n_clicks=0),
    ])
])

# --- CALLBACKS ---

# Header clock and data freshness
@app.callback(
    [
        Output('current-local-time', 'children'),
        Output('last-update-display', 'children'),
    ],
    [
        Input('clock-update', 'n_intervals'),
        Input('live-update', 'n_intervals'),
    ],
)
def update_header_status(clock_intervals, live_intervals):
    now_local = pd.Timestamp.now(tz=STATION_TZ)
    current_text = (
        f"{now_local.strftime('%b %d, %Y %H:%M')} "
        f"{station_time_label(now_local)}"
    )
    return current_text, GLOBAL_STATUS


# Manual time controls
@app.callback(
    [
        Output('sky-manual-controls', 'is_open'),
        Output('detail-manual-controls', 'is_open'),
        Output('main-manual-controls', 'is_open'),
    ],
    [
        Input('sky-time-window', 'value'),
        Input('detail-time-window', 'value'),
        Input('main-time-window', 'value'),
    ],
)
def toggle_manual_time_controls(sky_window, detail_window, main_window):
    return (
        sky_window == 'manual',
        detail_window == 'manual',
        main_window == 'manual',
    )


# 1. Skyplot Update
@app.callback(
    [Output('sky-plot', 'figure'), Output('sky-header-period', 'children')],
    [Input('btn-sky-apply', 'n_clicks'), Input('btn-sky-reset', 'n_clicks'), Input('live-update', 'n_intervals')],
    [State('sky-elev-mask', 'value'), State('sky-time-window', 'value'),
     State('sky-manual-start', 'value'), State('sky-manual-end', 'value'),
     State('sky-band', 'value'), State('sky-s4-min', 'value'), State('sky-s4-max', 'value')]
)
def update_skyplot(
    apply_clicks, reset_clicks, n_intervals, elev_mask, time_window,
    manual_start, manual_end, band, s4_min, s4_max
):
    if get_triggered_id() == 'btn-sky-reset':
        elev_mask, time_window, band, s4_min, s4_max = 10, 1, 'L1', 0, 0.6
        
    df = GLOBAL_DF.copy()
    if df.empty:
        return go.Figure(), ""

    df['datetime_loc'] = df['datetime'].dt.tz_localize('UTC').dt.tz_convert(STATION_TZ)
    if elev_mask is not None:
        df['elev'] = pd.to_numeric(df['elev'], errors='coerce')
        df = df[df['elev'] >= elev_mask]

    max_loc = df['datetime_loc'].max()
    if time_window == 'manual':
        try:
            skyplot_start, skyplot_end = manual_ut_time_limits(
                manual_start, manual_end, max_loc
            )
        except (TypeError, ValueError):
            skyplot_start, skyplot_end = preset_time_limits(1, max_loc)
    else:
        skyplot_start, skyplot_end = preset_time_limits(
            float(time_window or 1), max_loc
        )

    start_ut = skyplot_start.tz_convert("UTC").strftime("%H:%M")
    end_ut = skyplot_end.tz_convert("UTC").strftime("%H:%M")
    period_label = f" • {start_ut}–{end_ut} UT"
    recent = df[
        (df['datetime_loc'] >= skyplot_start)
        & (df['datetime_loc'] <= skyplot_end)
    ].copy()
    
    fig_sky = go.Figure()
    if not recent.empty:
        s4_col = 's4_f2' if (band == 'L2' and 's4_f2' in recent.columns) else ('s4_f1' if 's4_f1' in recent.columns else 's4')
        y_axis_title = "S4 (L2)" if band == 'L2' else "S4 (L1)"
        recent[s4_col] = pd.to_numeric(recent[s4_col], errors='coerce')

        recent.loc[:, 'az'] = pd.to_numeric(recent['az'], errors='coerce')
        recent['time_utc_disp'] = recent['datetime'].dt.strftime(
            '%Y-%m-%d %H:%M:%S'
        )
        
        fig_sky.add_trace(go.Scatterpolar(
            r=90 - recent['elev'], theta=recent['az'], mode='markers', text=recent['elev'].round(1), 
            marker=dict(color=recent.get(s4_col, 0), colorscale='Turbo', size=11, opacity=0.8,
                        cmin=s4_min, cmax=s4_max, colorbar=dict(
                            title=dict(text=y_axis_title, font=dict(size=20)),
                            tickfont=dict(size=17), thickness=20, len=0.8)),
            customdata=recent[['prn', 'system', 'time_utc_disp']],
            hovertemplate="<b>%{customdata[1]} PRN %{customdata[0]}</b><br>UTC: %{customdata[2]}<br>Elev: %{text}°<br>Az: %{theta:.1f}°<br>S4: %{marker.color:.3f}<extra></extra>"
        ))
    
    fig_sky.update_layout(
        template="cyborg", height=430, font=dict(family="Inter", size=17),
        paper_bgcolor="rgba(0,0,0,0)",
        polar=dict(
            radialaxis=dict(
                range=[0, 90], showticklabels=True, tickfont=dict(size=17),
                tickvals=[0, 30, 60, 90],
                ticktext=['90°', '60°', '30°', '0°'],
                gridcolor='rgba(255,255,255,0.1)'),
            angularaxis=dict(
                direction="clockwise", rotation=90, tickfont=dict(size=17),
                gridcolor='rgba(255,255,255,0.1)')
        ),
        margin=dict(t=18, b=35, l=45, r=70)
    )
    return fig_sky, period_label

# 2. Dynamic PRN Options Generator
@app.callback(Output('detail-prn', 'options'), [Input('detail-constellation', 'value'), Input('live-update', 'n_intervals')])
def set_prn_options(selected_sys, n_intervals):
    if GLOBAL_DF.empty or not selected_sys: return []
    prns = GLOBAL_DF[GLOBAL_DF['system'] == selected_sys]['prn'].dropna().unique()
    return [{'label': f"PRN {int(p)}", 'value': int(p)} for p in sorted(prns)]

# 3. Dynamic Dropdown Sync + Auto Scroll
@app.callback(
    [Output('detail-constellation', 'value'), Output('detail-prn', 'value'), Output('url', 'hash')],
    [Input('main-graph', 'clickData'), Input('sky-plot', 'clickData')],
    prevent_initial_call=True
)
def sync_and_scroll(main_click, sky_click):
    triggered_id = ctx.triggered_id
    if triggered_id == 'main-graph' and main_click:
        point = main_click['points'][0]
        prn = int(point['customdata'][0]) 
        sys = point['customdata'][1]
        return sys, prn, '#detail-collapse'
    elif triggered_id == 'sky-plot' and sky_click:
        point = sky_click['points'][0]
        prn = int(point['customdata'][0]) 
        sys = point['customdata'][1]
        return sys, prn, '#detail-collapse'
    return dash.no_update, dash.no_update, dash.no_update


# 4. Detail Graph Render
@app.callback(
    [Output('detail-graph', 'figure'), Output('detail-header', 'children')],
    [Input('detail-constellation', 'value'), Input('detail-prn', 'value'),
     Input('btn-detail-apply', 'n_clicks'), Input('btn-detail-reset', 'n_clicks'), Input('live-update', 'n_intervals')],
    [State('detail-time-window', 'value'),
     State('detail-manual-start', 'value'), State('detail-manual-end', 'value'),
     State('detail-elev-mask', 'value')]
)
def display_details(
    sys, prn, apply_clicks, reset_clicks, n_intervals, time_window,
    manual_start, manual_end, elev_mask
):
    if get_triggered_id() == 'btn-detail-reset':
        time_window, elev_mask = 3, 10
        
    df = GLOBAL_DF.copy()
    if df.empty or not sys or not prn: return go.Figure(), ""

    sat_df = df[(df['prn'] == prn) & (df['system'] == sys)].copy()
    if sat_df.empty: return go.Figure(), ""
    
    sat_df['datetime_loc'] = sat_df['datetime'].dt.tz_localize('UTC').dt.tz_convert(STATION_TZ)
    
    global_max_loc = df['datetime'].max().tz_localize('UTC').tz_convert(STATION_TZ)
    if time_window == 'manual':
        try:
            start_time, end_time = manual_ut_time_limits(
                manual_start, manual_end, global_max_loc
            )
        except (TypeError, ValueError):
            start_time, end_time = preset_time_limits(3, global_max_loc)
    else:
        start_time, end_time = preset_time_limits(
            float(time_window or 3), global_max_loc
        )
    
    sat_df = sat_df[
        (sat_df['datetime_loc'] >= start_time)
        & (sat_df['datetime_loc'] <= end_time)
    ]
    sat_df = sat_df.sort_values('datetime_loc')

    # 1. Apply Elevation Mask
    low_elev_mask = pd.Series(False, index=sat_df.index)
    if 'elev' in sat_df.columns and elev_mask is not None:
        low_elev_mask = sat_df['elev'] < elev_mask
        cols_to_nan = ['s4_f1', 's4_f2', 's4', 'TEC']
        for col in cols_to_nan:
            if col in sat_df.columns:
                sat_df.loc[low_elev_mask, col] = np.nan

    # 2. TEC Slip Correction & Rolling Mean
    if 'TEC' in sat_df.columns:
        sat_df['TEC'] = pd.to_numeric(sat_df['TEC'], errors='coerce')
        if not sat_df['TEC'].dropna().empty:
            diffs = sat_df['TEC'].diff().fillna(0)
            slips = diffs.copy()
            slips[slips.abs() <= 5.0] = 0
            sat_df['TEC'] = sat_df['TEC'] - slips.cumsum()
            sat_df.loc[low_elev_mask, 'TEC'] = np.nan

    # 3. S4 cleanup
    s4_zero_mask = sat_df['datetime_loc'].dt.hour >= 21
    for s4_metric in ['s4_f1', 's4_f2', 's4']:
        if s4_metric in sat_df.columns:
            sat_df.loc[s4_zero_mask & (sat_df[s4_metric] <= 0), s4_metric] = np.nan

    if 'az' in sat_df.columns:
        sat_df['az'] = pd.to_numeric(sat_df['az'], errors='coerce')

    # 4. GAP INJECTION
    gap_mask = sat_df['datetime_loc'].diff() > pd.Timedelta(minutes=3)
    if gap_mask.any():
        gap_rows = sat_df[gap_mask].copy()
        gap_rows['datetime_loc'] -= pd.Timedelta(seconds=1) 
        cols_to_nan = ['s4_f1', 's4_f2', 's4', 'TEC', 'elev', 'az']
        for col in cols_to_nan:
            if col in gap_rows.columns: gap_rows[col] = np.nan
        sat_df = pd.concat([sat_df, gap_rows]).sort_values('datetime_loc')

    # 5. Final Display Prep
    sat_df['time_utc_disp'] = (
        sat_df['datetime_loc']
        .dt.tz_convert('UTC')
        .dt.strftime('%Y-%m-%d %H:%M:%S')
    )

    plots_config = []
    if 's4_f1' in sat_df.columns: plots_config.append({'title': 'S4 (L1)', 'col': 's4_f1', 'color': '#00cc96', 'range': [0, 1.5]})
    elif 's4' in sat_df.columns: plots_config.append({'title': 'S4', 'col': 's4', 'color': '#00cc96', 'range': [0, 1.5]})
    if 's4_f2' in sat_df.columns and sat_df['s4_f2'].count() > 0: plots_config.append({'title': 'S4 (L2)', 'col': 's4_f2', 'color': '#119dff', 'range': [0, 1.5]})
    if 'TEC' in sat_df.columns:
        tec_values = sat_df['TEC'].dropna()
        tec_range = None
        if not tec_values.empty:
            tec_min = float(tec_values.min())
            tec_max = float(tec_values.max())
            if tec_max - tec_min < TEC_MINIMUM_VISIBLE_SPAN:
                tec_center = (tec_min + tec_max) / 2
                tec_half_span = TEC_MINIMUM_VISIBLE_SPAN / 2
                tec_range = [
                    tec_center - tec_half_span,
                    tec_center + tec_half_span,
                ]
        plots_config.append({
            'title': 'TEC (TECU)',
            'col': 'TEC',
            'color': '#ff7f0e',
            'range': tec_range,
        })
    if 'elev' in sat_df.columns: plots_config.append({'title': 'Elev. (°)', 'col': 'elev', 'color': '#ab63fa', 'range': [0, 90]})
    if 'az' in sat_df.columns: plots_config.append({'title': 'Azim. (°)', 'col': 'az', 'color': '#ffb300', 'range': [0, 360]})

    num_rows = len(plots_config)
    if num_rows == 0: return go.Figure(), ""

    fig = make_subplots(rows=num_rows, cols=1, shared_xaxes=True, vertical_spacing=0.035)

    for i, plot in enumerate(plots_config, start=1):
        plot_mode = 'markers' if plot['col'] == 'az' else 'lines'
        line_dict = dict(color=plot['color'], width=2.5) if plot_mode == 'lines' else None
        marker_dict = dict(color=plot['color'], size=5) if plot_mode == 'markers' else None
        
        fig.add_trace(go.Scatter(
            x=sat_df['datetime_loc'], y=sat_df[plot['col']], mode=plot_mode, name=plot['title'], 
            line=line_dict, marker=marker_dict, connectgaps=False,
            customdata=sat_df[['time_utc_disp']],
            hovertemplate="UTC: %{customdata[0]}<br>Value: %{y:.3f}<extra></extra>"
        ), row=i, col=1)

        fig.update_yaxes(
            title_text=plot['title'], title_font=dict(size=20),
            tickfont=dict(size=17), row=i, col=1, showgrid=True,
            gridcolor='rgba(255,255,255,0.05)', zeroline=False)
        if plot['range']: fig.update_yaxes(range=plot['range'], row=i, col=1)

    header = html.Div(
        f"Detailed Satellite Profile: {sys} PRN {prn}",
        style={"color": "#94a3b8", "fontWeight": "bold"},
    )

    fig.update_xaxes(range=[start_time, end_time])
    tick_values, tick_labels = utc_ticks(start_time, end_time)
    fig.update_xaxes(
        title_text=universal_time_title(end_time),
        title_font=dict(size=21), title_standoff=14,
        tickfont=dict(size=18), tickmode='array', tickvals=tick_values,
        ticktext=tick_labels, showgrid=True,
        gridcolor='rgba(255,255,255,0.05)', row=num_rows, col=1)
    fig.update_layout(
        template="cyborg", height=700, font=dict(family="Inter", size=18),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False, margin=dict(l=105, r=30, t=18, b=78))
    
    return fig, header

# 5. Main Timeline Update
@app.callback(
    Output('main-graph', 'figure'),
    [Input('btn-main-apply', 'n_clicks'), Input('btn-main-reset', 'n_clicks'), Input('live-update', 'n_intervals')], 
    [State('main-elev-mask', 'value'), State('main-time-window', 'value'),
     State('main-manual-start', 'value'), State('main-manual-end', 'value'),
     State('main-constellations', 'value'), State('main-band', 'value')]
)
def update_main_timeline(
    apply_clicks, reset_clicks, n_intervals, elev_mask, time_window,
    manual_start, manual_end, constellations, band
):
    if get_triggered_id() == 'btn-main-reset':
        elev_mask = 30
        time_window = 6
        constellations, band = list(SYSTEM_MAP.values()), 'L1'

    df = GLOBAL_DF.copy()
    if df.empty:
        return go.Figure()

    df['datetime_loc'] = df['datetime'].dt.tz_localize('UTC').dt.tz_convert(STATION_TZ)
    df['time_utc_disp'] = df['datetime'].dt.strftime(
        '%Y-%m-%d %H:%M:%S'
    )

    if elev_mask is not None:
        df['elev'] = pd.to_numeric(df['elev'], errors='coerce')
        df = df[df['elev'] >= elev_mask]
        
    if constellations:
        df = df[df['system'].isin(constellations)]

    max_loc = df['datetime_loc'].max()
    if time_window == 'manual':
        try:
            timeline_start, timeline_end = manual_ut_time_limits(
                manual_start, manual_end, max_loc
            )
        except (TypeError, ValueError):
            timeline_start, timeline_end = preset_time_limits(5, max_loc)
    else:
        timeline_start, timeline_end = preset_time_limits(
            float(time_window or 6), max_loc
        )
    df_main = df[
        (df['datetime_loc'] >= timeline_start)
        & (df['datetime_loc'] <= timeline_end)
    ].copy()

    s4_col = 's4_f2' if (band == 'L2' and 's4_f2' in df_main.columns) else ('s4_f1' if 's4_f1' in df_main.columns else 's4')
    y_axis_title = "S4 (L2)" if band == 'L2' else "S4 (L1)"
    df_main[s4_col] = pd.to_numeric(df_main[s4_col], errors='coerce')

    fig_main = go.Figure()
    for sys in sorted(df_main['system'].unique()):
        sys_df = df_main[df_main['system'] == sys]
        fig_main.add_trace(go.Scattergl(
            x=sys_df['datetime_loc'], y=sys_df[s4_col], mode='markers',
            name=sys, marker=dict(size=6, opacity=0.75),
            customdata=sys_df[['prn', 'system', 'time_utc_disp']],
            hovertemplate="<b>%{customdata[1]} PRN %{customdata[0]}</b><br>UTC: %{customdata[2]}<br>S4: %{y:.3f}<extra></extra>"
        ))
    
    tick_values, tick_labels = utc_ticks(timeline_start, timeline_end)

    fig_main.update_layout(
        template="cyborg", height=420, font=dict(family="Inter", size=18),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        yaxis_title=dict(text=y_axis_title, font=dict(size=22)),
        xaxis_title=dict(
            text=universal_time_title(timeline_end),
            font=dict(size=21),
        ),
        yaxis=dict(
            range=[0, 1.5], showgrid=True,
            gridcolor='rgba(255,255,255,0.05)', zeroline=False,
            tickfont=dict(size=18)),
        xaxis=dict(
            range=[timeline_start, timeline_end], showgrid=True,
            gridcolor='rgba(255,255,255,0.05)', tickmode='array',
            tickvals=tick_values, ticktext=tick_labels,
            tickfont=dict(size=18)),
        margin=dict(l=90, r=30, t=22, b=78),
        legend=dict(
            orientation="h", yanchor="top", y=0.98, xanchor="center", x=0.5,
            font=dict(size=17), bordercolor="rgba(255,255,255,0.1)",
            bgcolor="rgba(0,0,0,0.4)")
    )
    return fig_main

# 6. Mobile Tab Switching (clientside - no server round-trip)
app.clientside_callback(
    """
    function(sky_clicks, timeline_clicks, detail_clicks) {
        // Determine which tab was clicked
        var triggered = dash_clientside.callback_context.triggered;
        if (!triggered || triggered.length === 0) return dash_clientside.no_update;

        var tab_id = triggered[0].prop_id.split('.')[0];
        var tab_map = {
            'tab-btn-skyplot': 'panel-skyplot',
            'tab-btn-timeline': 'panel-timeline',
            'tab-btn-detail': 'panel-detail'
        };

        // Toggle panels
        var panels = ['panel-skyplot', 'panel-timeline', 'panel-detail'];
        panels.forEach(function(pid) {
            var el = document.getElementById(pid);
            if (el) {
                if (pid === tab_map[tab_id]) {
                    el.classList.add('mobile-active');
                } else {
                    el.classList.remove('mobile-active');
                }
            }
        });

        // Toggle button active state
        var btns = ['tab-btn-skyplot', 'tab-btn-timeline', 'tab-btn-detail'];
        btns.forEach(function(bid) {
            var el = document.getElementById(bid);
            if (el) {
                if (bid === tab_id) {
                    el.classList.add('active');
                } else {
                    el.classList.remove('active');
                }
            }
        });

        return tab_map[tab_id] || dash_clientside.no_update;
    }
    """,
    Output('mobile-active-tab', 'data'),
    [Input('tab-btn-skyplot', 'n_clicks'),
     Input('tab-btn-timeline', 'n_clicks'),
     Input('tab-btn-detail', 'n_clicks')],
    prevent_initial_call=True
)

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8050))
    app.run_server(host='0.0.0.0', port=port, debug=False)
