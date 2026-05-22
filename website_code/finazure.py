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
from datetime import datetime, timedelta
import concurrent.futures
import time
import pytz
import threading # Added for background updates

# --- CONFIGURATION ---
LOCAL_DATA_DIR = os.getenv("DATA_MOUNT_PATH", "dataforday") 
GLOBAL_STATUS = "Initializing..."
UPDATE_INTERVAL_MS = 300000 # Update graphs every 5 minutes

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# Full Name System Mapping for Legend (QZSS Removed)
SYSTEM_MAP = {
    0: "GPS", 1: "SBAS", 2: "Galileo", 3: "BeiDou", 6: "GLONASS"
}
TEC_CONVERSION_FACTOR = 9.5196 
STATION_TZ = 'America/Lima' # PET (UTC-5)

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
        for col in ['s4_f1', 's4_f2', 's4', 'TEC']:
            if col in df.columns:
                df[col] = df.groupby(['system', 'prn'])[col].transform(lambda x: x.interpolate(method='linear', limit=2))

        # Explicitly added the Date formatted string to the header update text
        dt_str = datetime.now(pytz.timezone(STATION_TZ)).strftime('%b %d, %Y • %H:%M:%S')
        GLOBAL_STATUS = f"Live • Updated {dt_str} LT • {len(df):,} samples"
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

# --- LAYOUT ---
app.layout = dbc.Container(fluid=True, style={"padding": "25px", "maxWidth": "1600px"}, children=[
    
    # Auto-scrolling anchor
    dcc.Location(id='url', refresh=False),
    # Interval timer for live refresh
    dcc.Interval(id='live-update', interval=UPDATE_INTERVAL_MS, n_intervals=0),

    # --- HEADER ---
    dbc.Row([
        dbc.Col([
            html.H3("ScintPi Monitoring Dashboard", className="text-white text-center mb-1", style={'fontWeight': 'bold'}),
            html.H5("Real-time GNSS Scintillation and TEC Observations", className="text-secondary text-center mb-2", style={'fontStyle': 'italic'}),
            html.P("Station: __STATION_NAME__ | Location: __STATION_LOCATION__ | Time zone: PET (UTC−5) | Window: Last 24 Hours", 
                   className="text-muted text-center mb-2", style={'fontSize': '0.95rem'}),
            html.A("About ScintPi", href="http://scintpi.utdallas.edu", target="_blank", className="text-info text-center d-block mb-2"),
            html.Div("⚠️ Note: This dashboard is not currently optimized for mobile devices.", className="text-warning text-center small mb-2"),
            html.Div(id='last-update-display', className="text-success text-center fw-bold mb-3")
        ])
    ], className="mb-0"),

    # --- ROW 1: SKYPLOT ---
    dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardHeader(html.Div([
                    html.Span("Satellite Sky Map", style={'fontWeight': 'bold', 'color': '#ffffff'}),
                    html.Br(),
                    html.Span("Color = S4 index", style={'fontSize': '0.8rem', 'color': '#94a3b8', 'textTransform': 'none', 'letterSpacing': '0'}),
                    html.Div("👉 Click any satellite to view its detailed profile below.", className="small mt-1", style={"color": "#00cc96"})
                ]), className="text-center pb-2 pt-3"),
                dbc.CardBody(
                    dcc.Loading(dcc.Graph(id='sky-plot', config={'displayModeBar': False}, style={"height": "450px"}), color="#00cc96")
                )
            ], className="border-0 shadow-sm mb-4")
        ], md=9, className="mb-4"),
        
        dbc.Col([
            dbc.Card([
                dbc.CardHeader("Skyplot Controls", className="text-center"),
                dbc.CardBody([
                    html.Label("Elevation Mask (°):", className="fw-bold text-muted small"),
                    dbc.Input(id='sky-elev-mask', type='number', value=10, min=0, max=90, className="mb-3"),
                    
                    html.Label("Time Window:", className="fw-bold text-muted small"),
                    dcc.Dropdown(id='sky-time-window', options=[
                        {'label': 'Last 15 Mins', 'value': 0.25},
                        {'label': 'Last 1 Hour', 'value': 1},
                        {'label': 'Last 3 Hours', 'value': 3},
                        {'label': 'Last 6 Hours', 'value': 6}
                    ], value=1, clearable=False, className="mb-3 text-dark"),

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
        ], md=3, className="mb-4")
    ]),

    # --- ROW 2: INDIVIDUAL SATELLITE PANEL (5 Plots) ---
    dbc.Collapse(
        dbc.Row([
            dbc.Col([
                dbc.Card([
                    dbc.CardHeader(id='detail-header', className="text-center"),
                    dbc.CardBody(dcc.Graph(id='detail-graph')) 
                ], className="shadow mb-4 border-0")
            ], md=9, className="mb-4"),
            
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
                            {'label': 'Last 24 Hours', 'value': 24}
                        ], value=1, clearable=False, className="mb-3 text-dark"),
                        
                        html.Label("Elevation Filter (°):", className="fw-bold text-muted small"),
                        html.Div("Points below this mask drop to NaN.", className="text-muted mb-1", style={'fontSize': '0.75rem'}),
                        dbc.Input(id='detail-elev-mask', type='number', value=10, min=0, max=90, className="mb-4"),

                        dbc.Row([
                            dbc.Col(dbc.Button("Apply", id='btn-detail-apply', color="primary", className="w-100 fw-bold"), width=7),
                            dbc.Col(dbc.Button("Reset", id='btn-detail-reset', outline=True, color="secondary", className="w-100"), width=5)
                        ])
                    ])
                ], className="border-0 shadow-sm", style={"height": "100%"})
            ], md=3, className="mb-4")
        ]),
        id='detail-collapse',
        is_open=True 
    ),

    # --- ROW 3: MAIN TIMELINE & CONTROLS ---
    dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardHeader(html.Div([
                    html.Span("Scintillation Time Series", style={'fontWeight': 'bold', 'color': '#ffffff'}),
                    html.Br(),
                    html.Span("Amplitude Scintillation Index (S4)", style={'fontSize': '0.8rem', 'color': '#94a3b8', 'textTransform': 'none', 'letterSpacing': '0'}),
                    html.Div("👉 Click any data point to view its detailed profile above.", className="small mt-1", style={"color": "#00cc96"})
                ]), className="text-center pb-2 pt-3"),
                dbc.CardBody([
                    dcc.Loading(
                        dcc.Graph(id='main-graph', config={'displayModeBar': False}, style={"height": "500px"}),
                        color="#00cc96", type="circle"
                    )
                ])
            ], className="border-0 shadow-sm", style={"height": "100%"})
        ], md=9, className="mb-4"),
        
        dbc.Col([
            dbc.Card([
                dbc.CardHeader("Timeline Controls", className="text-center"),
                dbc.CardBody([
                    html.Label("Elevation Mask (°):", className="fw-bold text-muted small"),
                    dbc.Input(id='main-elev-mask', type='number', value=30, min=0, max=90, className="mb-3"),
                    
                    html.Label("Time Window:", className="fw-bold text-muted small"),
                    dcc.Dropdown(id='main-time-window', options=[
                        {'label': 'Last 1 Hour', 'value': 1},
                        {'label': 'Last 6 Hours', 'value': 6},
                        {'label': 'Last 12 Hours', 'value': 12},
                        {'label': 'Last 24 Hours', 'value': 24}
                    ], value=12, clearable=False, className="mb-3 text-dark"),
                    
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
        ], md=3, className="mb-4")
    ])
])

# --- CALLBACKS ---

# 1. Skyplot Update
@app.callback(
    Output('sky-plot', 'figure'),
    [Input('btn-sky-apply', 'n_clicks'), Input('btn-sky-reset', 'n_clicks'), Input('live-update', 'n_intervals')],
    [State('sky-elev-mask', 'value'), State('sky-time-window', 'value'),
     State('sky-band', 'value'), State('sky-s4-min', 'value'), State('sky-s4-max', 'value')]
)
def update_skyplot(apply_clicks, reset_clicks, n_intervals, elev_mask, time_window, band, s4_min, s4_max):
    if ctx.triggered_id == 'btn-sky-reset':
        elev_mask, time_window, band, s4_min, s4_max = 10, 1, 'L1', 0, 0.6
        
    df = GLOBAL_DF.copy()
    if df.empty: return go.Figure()

    df['datetime_loc'] = df['datetime'].dt.tz_localize('UTC').dt.tz_convert(STATION_TZ)
    if elev_mask is not None:
        df['elev'] = pd.to_numeric(df['elev'], errors='coerce')
        df = df[df['elev'] >= elev_mask]

    max_loc = df['datetime_loc'].max()
    skyplot_start = max_loc - pd.Timedelta(hours=time_window)
    recent = df[(df['datetime_loc'] >= skyplot_start) & (df['datetime_loc'] <= max_loc)].copy()
    
    fig_sky = go.Figure()
    if not recent.empty:
        s4_col = 's4_f2' if (band == 'L2' and 's4_f2' in recent.columns) else ('s4_f1' if 's4_f1' in recent.columns else 's4')
        y_axis_title = "S4 (L2)" if band == 'L2' else "S4 (L1)"
        recent[s4_col] = pd.to_numeric(recent[s4_col], errors='coerce')

        recent.loc[:, 'az'] = pd.to_numeric(recent['az'], errors='coerce')
        recent['time_local_disp'] = recent['datetime_loc'].dt.strftime('%H:%M:%S')
        recent['time_utc_disp'] = recent['datetime'].dt.strftime('%H:%M:%S')
        
        fig_sky.add_trace(go.Scatterpolar(
            r=90 - recent['elev'], theta=recent['az'], mode='markers', text=recent['elev'].round(1), 
            marker=dict(color=recent.get(s4_col, 0), colorscale='Turbo', size=7, opacity=0.8, 
                        cmin=s4_min, cmax=s4_max, colorbar=dict(title=y_axis_title, thickness=15, len=0.8)),
            customdata=recent[['prn', 'system', 'time_local_disp', 'time_utc_disp']],
            hovertemplate="<b>%{customdata[1]} PRN %{customdata[0]}</b><br>Local: %{customdata[2]}<br>UTC: %{customdata[3]}<br>Elev: %{text}°<br>Az: %{theta:.1f}°<br>S4: %{marker.color:.3f}<extra></extra>"
        ))
    
    fig_sky.update_layout(
        template="cyborg", font_family="Inter", paper_bgcolor="rgba(0,0,0,0)",
        polar=dict(
            radialaxis=dict(range=[0, 90], showticklabels=True, tickvals=[0, 30, 60, 90], ticktext=['90°', '60°', '30°', '0°'], gridcolor='rgba(255,255,255,0.1)', title="Elevation (°)"),
            angularaxis=dict(direction="clockwise", rotation=90, gridcolor='rgba(255,255,255,0.1)')
        ),
        margin=dict(t=20, b=40, l=40, r=40)
    )
    return fig_sky

# 2. Dynamic PRN Options Generator
@app.callback(Output('detail-prn', 'options'), [Input('detail-constellation', 'value'), Input('live-update', 'n_intervals')])
def set_prn_options(selected_sys, n_intervals):
    if GLOBAL_DF.empty or not selected_sys: return []
    prns = GLOBAL_DF[GLOBAL_DF['system'] == selected_sys]['prn'].dropna().unique()
    return [{'label': f"PRN {int(p)}", 'value': int(p)} for p in sorted(prns)]

# 3. Dynamic Dropdown Sync + Auto Scroll
@app.callback(
    [Output('detail-constellation', 'value'), Output('detail-prn', 'value'), Output('detail-time-window', 'value'), Output('url', 'hash')],
    [Input('main-graph', 'clickData'), Input('sky-plot', 'clickData')],
    prevent_initial_call=True
)
def sync_and_scroll(main_click, sky_click):
    triggered_id = ctx.triggered_id
    if triggered_id == 'main-graph' and main_click:
        point = main_click['points'][0]
        prn = int(point['customdata'][0]) 
        sys = point['customdata'][1]
        return sys, prn, 12, '#detail-collapse'
    elif triggered_id == 'sky-plot' and sky_click:
        point = sky_click['points'][0]
        prn = int(point['customdata'][0]) 
        sys = point['customdata'][1]
        return sys, prn, 1, '#detail-collapse'
    return dash.no_update, dash.no_update, dash.no_update, dash.no_update


# 4. Detail Graph Render
@app.callback(
    [Output('detail-graph', 'figure'), Output('detail-header', 'children')],
    [Input('detail-constellation', 'value'), Input('detail-prn', 'value'),
     Input('btn-detail-apply', 'n_clicks'), Input('btn-detail-reset', 'n_clicks'), Input('live-update', 'n_intervals')],
    [State('detail-time-window', 'value'), State('detail-elev-mask', 'value')]
)
def display_details(sys, prn, apply_clicks, reset_clicks, n_intervals, time_window, elev_mask):
    if ctx.triggered_id == 'btn-detail-reset':
        time_window, elev_mask = 1, 10
        
    df = GLOBAL_DF.copy()
    if df.empty or not sys or not prn: return go.Figure(), ""

    sat_df = df[(df['prn'] == prn) & (df['system'] == sys)].copy()
    if sat_df.empty: return go.Figure(), ""
    
    sat_df['datetime_loc'] = sat_df['datetime'].dt.tz_localize('UTC').dt.tz_convert(STATION_TZ)
    
    # Tight Zoom logic: lock window exactly to [Global Max - Window, Global Max]
    global_max_loc = df['datetime'].max().tz_localize('UTC').tz_convert(STATION_TZ)
    start_time = global_max_loc - pd.Timedelta(hours=time_window)
    
    sat_df = sat_df[(sat_df['datetime_loc'] >= start_time) & (sat_df['datetime_loc'] <= global_max_loc)]
    sat_df = sat_df.sort_values('datetime_loc')

    # 1. Apply Elevation Mask
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
            sat_df['TEC'] = sat_df['TEC'].rolling(window=20, min_periods=1, center=True).mean()

    # 3. S4 Cleanups and Interpolation
    s4_zero_mask = sat_df['datetime_loc'].dt.hour >= 21
    for s4_metric in ['s4_f1', 's4_f2', 's4']:
        if s4_metric in sat_df.columns:
            sat_df.loc[s4_zero_mask & (sat_df[s4_metric] <= 0), s4_metric] = np.nan

    for s4_metric in ['s4_f1', 's4_f2', 's4']:
        if s4_metric in sat_df.columns:
            is_nan = sat_df[s4_metric].isna()
            if is_nan.any():
                blocks = (~is_nan).cumsum()
                block_sizes = blocks[is_nan].map(blocks[is_nan].value_counts())
                valid_blocks = is_nan & (block_sizes <= 3)
                
                high_surroundings = (sat_df[s4_metric].ffill() > 0.2) & (sat_df[s4_metric].bfill() > 0.2)
                mask_to_fill = valid_blocks & high_surroundings
                if mask_to_fill.any():
                    interpolated = sat_df[s4_metric].interpolate(method='linear')
                    sat_df.loc[mask_to_fill, s4_metric] = interpolated.loc[mask_to_fill]

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
    sat_df['time_utc_disp'] = sat_df['datetime_loc'].dt.tz_convert('UTC').dt.strftime('%H:%M:%S')

    plots_config = []
    if 's4_f1' in sat_df.columns: plots_config.append({'title': 'S4 Index (L1)', 'col': 's4_f1', 'color': '#00cc96', 'range': [0, 1.5]})
    elif 's4' in sat_df.columns: plots_config.append({'title': 'S4 Index', 'col': 's4', 'color': '#00cc96', 'range': [0, 1.5]})
    if 's4_f2' in sat_df.columns and sat_df['s4_f2'].count() > 0: plots_config.append({'title': 'S4 Index (L2)', 'col': 's4_f2', 'color': '#119dff', 'range': [0, 1.5]})
    if 'TEC' in sat_df.columns: plots_config.append({'title': 'TEC (TECU)', 'col': 'TEC', 'color': '#ff7f0e', 'range': None})
    if 'elev' in sat_df.columns: plots_config.append({'title': 'Elevation (°)', 'col': 'elev', 'color': '#ab63fa', 'range': [0, 90]})
    if 'az' in sat_df.columns: plots_config.append({'title': 'Azimuth (°)', 'col': 'az', 'color': '#ffb300', 'range': [0, 360]})

    num_rows = len(plots_config)
    if num_rows == 0: return go.Figure(), ""

    fig = make_subplots(rows=num_rows, cols=1, shared_xaxes=True, vertical_spacing=0.04)

    for i, plot in enumerate(plots_config, start=1):
        plot_mode = 'markers' if plot['col'] == 'az' else 'lines'
        line_dict = dict(color=plot['color'], width=2) if plot_mode == 'lines' else None
        marker_dict = dict(color=plot['color'], size=3) if plot_mode == 'markers' else None
        
        fig.add_trace(go.Scatter(
            x=sat_df['datetime_loc'], y=sat_df[plot['col']], mode=plot_mode, name=plot['title'], 
            line=line_dict, marker=marker_dict, connectgaps=False,
            customdata=sat_df[['time_utc_disp']],
            hovertemplate="Local: %{x|%H:%M:%S}<br>UTC: %{customdata[0]}<br>Value: %{y:.3f}<extra></extra>"
        ), row=i, col=1)

        fig.update_yaxes(title_text=plot['title'], title_font=dict(size=12), row=i, col=1, showgrid=True, gridcolor='rgba(255,255,255,0.05)', zeroline=False)
        if plot['range']: fig.update_yaxes(range=plot['range'], row=i, col=1)

    header = html.Div(f"Detailed Satellite Profile: {sys} PRN {prn} (Last {time_window} Hours)", style={"color": "#94a3b8", "fontWeight": "bold"})

    # EXACT bounds explicitly enforced globally to eliminate empty padding
    fig.update_xaxes(range=[start_time, global_max_loc])

    date_str = global_max_loc.strftime('%b %d, %Y')
    if start_time.date() != global_max_loc.date():
        date_str = f"{start_time.strftime('%b %d')} - {global_max_loc.strftime('%b %d, %Y')}"
    xaxis_title = f"Local Time (PET, UTC-5) | Date: {date_str}"

    fig.update_xaxes(title_text=xaxis_title, title_font=dict(size=14), showgrid=True, gridcolor='rgba(255,255,255,0.05)', tickformat='%H:%M', row=num_rows, col=1)
    fig.update_layout(template="cyborg", height=160 * num_rows, font_family="Inter", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", showlegend=False, margin=dict(l=60, r=20, t=20, b=65))
    
    return fig, header

# 5. Main Timeline Update
@app.callback(
    [Output('main-graph', 'figure'), Output('last-update-display', 'children')],
    [Input('btn-main-apply', 'n_clicks'), Input('btn-main-reset', 'n_clicks'), Input('live-update', 'n_intervals')], 
    [State('main-elev-mask', 'value'), State('main-time-window', 'value'),
     State('main-constellations', 'value'), State('main-band', 'value')]
)
def update_main_timeline(apply_clicks, reset_clicks, n_intervals, elev_mask, time_window, constellations, band):
    if ctx.triggered_id == 'btn-main-reset':
        elev_mask, time_window, constellations, band = 30, 12, list(SYSTEM_MAP.values()), 'L1'

    df = GLOBAL_DF.copy()
    if df.empty: return go.Figure(), html.Span(f"Status: {GLOBAL_STATUS}", style={"color": "#ff4d4d"})

    df['datetime_loc'] = df['datetime'].dt.tz_localize('UTC').dt.tz_convert(STATION_TZ)
    df['time_utc_disp'] = df['datetime'].dt.strftime('%H:%M:%S')

    if elev_mask is not None:
        df['elev'] = pd.to_numeric(df['elev'], errors='coerce')
        df = df[df['elev'] >= elev_mask]
        
    if constellations:
        df = df[df['system'].isin(constellations)]

    max_loc = df['datetime_loc'].max()
    timeline_start = max_loc - pd.Timedelta(hours=time_window)
    df_main = df[(df['datetime_loc'] >= timeline_start) & (df['datetime_loc'] <= max_loc)].copy()

    s4_col = 's4_f2' if (band == 'L2' and 's4_f2' in df_main.columns) else ('s4_f1' if 's4_f1' in df_main.columns else 's4')
    y_axis_title = "S4 (L2)" if band == 'L2' else "S4 (L1)"
    df_main[s4_col] = pd.to_numeric(df_main[s4_col], errors='coerce')

    fig_main = go.Figure()
    for sys in sorted(df_main['system'].unique()):
        sys_df = df_main[df_main['system'] == sys]
        fig_main.add_trace(go.Scattergl(
            x=sys_df['datetime_loc'], y=sys_df[s4_col], mode='markers', name=sys, marker=dict(size=3, opacity=0.7),
            customdata=sys_df[['prn', 'system', 'time_utc_disp']],
            hovertemplate="<b>%{customdata[1]} PRN %{customdata[0]}</b><br>Local: %{x|%H:%M:%S}<br>UTC: %{customdata[2]}<br>S4: %{y:.3f}<extra></extra>"
        ))
    
    date_str = max_loc.strftime('%b %d, %Y')
    if timeline_start.date() != max_loc.date():
        date_str = f"{timeline_start.strftime('%b %d')} - {max_loc.strftime('%b %d, %Y')}"
    xaxis_title = f"Local Time (PET, UTC-5) | Date: {date_str}"

    fig_main.update_layout(
        template="cyborg", height=500, font_family="Inter", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        yaxis_title=y_axis_title, 
        xaxis_title=xaxis_title,
        yaxis=dict(range=[0, 1.5], showgrid=True, gridcolor='rgba(255,255,255,0.05)', zeroline=False),
        xaxis=dict(range=[timeline_start, max_loc], showgrid=True, gridcolor='rgba(255,255,255,0.05)', tickformat='%H:%M'),
        margin=dict(l=60, r=20, t=20, b=65), 
        legend=dict(orientation="h", yanchor="top", y=0.98, xanchor="center", x=0.5, bordercolor="rgba(255,255,255,0.1)", bgcolor="rgba(0,0,0,0.4)")
    )
    return fig_main, html.Span(GLOBAL_STATUS)

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8050))
    app.run_server(host='0.0.0.0', port=port, debug=False)