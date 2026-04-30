import streamlit as st
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
import streamlit.components.v1 as components
from datetime import datetime, UTC
from pathlib import Path
import json

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None
from streamlit_autorefresh import st_autorefresh

# -------- CONFIG --------
st.set_page_config(layout="wide")
st.title("📊 XAUUSD Smart Money Dashboard (PRO LIVE)")

API_KEY = "a5c727723b7d4b669379dcafa53e110a"

# -------- LOCAL STORAGE --------
APP_DIR = Path(__file__).resolve().parent
STORAGE_DIR = APP_DIR / "trdr_storage"
STORAGE_DIR.mkdir(exist_ok=True)

TRADE_LOG_FILE = STORAGE_DIR / "trade_log.json"
ALERTED_EVENTS_FILE = STORAGE_DIR / "alerted_events.json"
SETTINGS_FILE = STORAGE_DIR / "settings.json"
WEEKLY_STATE_FILE = STORAGE_DIR / "weekly_state.json"
WEEKLY_REPORTS_FILE = STORAGE_DIR / "weekly_reports.json"

# -------- MT5 DATA CONFIG --------
MT5_SYMBOL_DEFAULT = "XAUUSD"
MIN_CANDLES_REQUIRED = 50
CANDLE_OUTPUT_SIZE = 150

# -------- MT5 LIVE TRADING CONFIG --------
MT5_TRADING_ENABLED_DEFAULT = False
MT5_MAGIC = 909090
MT5_DEVIATION = 50
MT5_ORDER_COMMENT = "TRDR_PRO_LIVE"

# -------- TELEGRAM CONFIG --------
TELEGRAM_TOKEN = "8669775545:AAHu3dkCSPLNRXXNyIVCqkeTwWhSoLQm99U"
TELEGRAM_CHAT_ID = "8032647182"
# Optional Telegram channel/public group target. Use @channelusername or -100xxxxxxxxxx.
TELEGRAM_CHANNEL_ID = "@TRDRXAUUSD"

def send_telegram(message, include_channel=True):
    """Send alert to the main Telegram chat and optionally to the Telegram channel."""
    try:
        token_ready = TELEGRAM_TOKEN not in ["", "YOUR_BOT_TOKEN"]
        if not token_ready:
            return

        targets = []
        private_target = str(TELEGRAM_CHAT_ID).strip()
        channel_target = str(TELEGRAM_CHANNEL_ID).strip()

        if private_target and private_target != "YOUR_CHAT_ID":
            targets.append(private_target)
        if include_channel and channel_target:
            targets.append(channel_target)

        # remove duplicate targets while keeping order
        seen = set()
        clean_targets = []
        for target in targets:
            if target not in seen:
                clean_targets.append(target)
                seen.add(target)

        for chat_id in clean_targets:
            requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                params={
                    "chat_id": chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=5
            )
    except Exception:
        pass


def play_alert_sound(sound_type="entry"):
    """Browser sound alerts: entry click, TP cashout, SL loss tone.
    Uses WebAudio directly, so no external sound files are required.
    """
    sound_type = str(sound_type).lower().strip()

    if sound_type == "tp":
        # bright cashout style: quick rising coin/cash tones
        js = """
        const tones = [880, 1175, 1568];
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        tones.forEach((freq, i) => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'triangle';
            osc.frequency.setValueAtTime(freq, ctx.currentTime + i * 0.08);
            gain.gain.setValueAtTime(0.0001, ctx.currentTime + i * 0.08);
            gain.gain.exponentialRampToValueAtTime(0.23, ctx.currentTime + i * 0.08 + 0.015);
            gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + i * 0.08 + 0.12);
            osc.connect(gain); gain.connect(ctx.destination);
            osc.start(ctx.currentTime + i * 0.08);
            osc.stop(ctx.currentTime + i * 0.08 + 0.14);
        });
        """
    elif sound_type == "sl":
        # loss tone: falling low beep
        js = """
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sawtooth';
        osc.frequency.setValueAtTime(360, ctx.currentTime);
        osc.frequency.exponentialRampToValueAtTime(120, ctx.currentTime + 0.55);
        gain.gain.setValueAtTime(0.0001, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.22, ctx.currentTime + 0.03);
        gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.60);
        osc.connect(gain); gain.connect(ctx.destination);
        osc.start(); osc.stop(ctx.currentTime + 0.65);
        """
    else:
        # entry click: short clean double-click tone
        js = """
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        [620, 820].forEach((freq, i) => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'square';
            osc.frequency.setValueAtTime(freq, ctx.currentTime + i * 0.07);
            gain.gain.setValueAtTime(0.0001, ctx.currentTime + i * 0.07);
            gain.gain.exponentialRampToValueAtTime(0.18, ctx.currentTime + i * 0.07 + 0.01);
            gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + i * 0.07 + 0.06);
            osc.connect(gain); gain.connect(ctx.destination);
            osc.start(ctx.currentTime + i * 0.07);
            osc.stop(ctx.currentTime + i * 0.07 + 0.08);
        });
        """

    components.html(
        f"""
        <script>
        try {{
            {js}
        }} catch(e) {{ console.log('TRDR sound blocked or unavailable', e); }}
        </script>
        """,
        height=0,
        width=0,
    )


# -------- PERSISTENCE HELPERS --------
def _json_default(obj):
    if isinstance(obj, (datetime, pd.Timestamp)):
        return obj.isoformat()
    if isinstance(obj, set):
        return list(obj)
    try:
        if pd.isna(obj):
            return None
    except Exception:
        pass
    return str(obj)




def safe_json_view(value, max_depth: int = 5, _depth: int = 0, _seen=None):
    """Convert MT5/Streamlit objects into safe JSON without circular references."""
    if _seen is None:
        _seen = set()
    if _depth > max_depth:
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    obj_id = id(value)
    if obj_id in _seen:
        return "<circular reference removed>"
    if isinstance(value, dict):
        _seen.add(obj_id)
        clean = {str(k): safe_json_view(v, max_depth, _depth + 1, _seen) for k, v in value.items()}
        _seen.discard(obj_id)
        return clean
    if isinstance(value, (list, tuple, set)):
        _seen.add(obj_id)
        clean = [safe_json_view(v, max_depth, _depth + 1, _seen) for v in value]
        _seen.discard(obj_id)
        return clean
    if isinstance(value, (datetime, pd.Timestamp)):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    try:
        if isinstance(value, (np.integer,)):
            return int(value)
        if isinstance(value, (np.floating,)):
            return float(value)
        if isinstance(value, (np.bool_,)):
            return bool(value)
    except Exception:
        pass
    try:
        if hasattr(value, "_asdict"):
            return safe_json_view(value._asdict(), max_depth, _depth + 1, _seen)
    except Exception:
        pass
    return str(value)


def load_json_file(path, default):
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def save_json_file(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(safe_json_view(data), f, indent=2, default=_json_default)
    except Exception:
        pass


# -------- MEMORY + SETTINGS HELPERS --------
MAX_SAVED_TRADES = 120
MAX_UI_TRADES = 60
MAX_ALERTED_EVENTS = 500
MAX_MT5_RESULT_ROWS = 20

def prune_runtime_state():
    """Keep Streamlit/browser memory stable during long live sessions."""
    try:
        trades = st.session_state.get("trade_log", [])
        if isinstance(trades, list) and len(trades) > MAX_SAVED_TRADES:
            # Keep most recent trades only. This prevents browser out-of-memory crashes.
            st.session_state.trade_log = trades[-MAX_SAVED_TRADES:]
    except Exception:
        pass
    try:
        events = st.session_state.get("alerted_events", set())
        if isinstance(events, set) and len(events) > MAX_ALERTED_EVENTS:
            st.session_state.alerted_events = set(list(events)[-MAX_ALERTED_EVENTS:])
    except Exception:
        pass
    try:
        results = st.session_state.get("mt5_last_order_result", [])
        if isinstance(results, list) and len(results) > MAX_MT5_RESULT_ROWS:
            st.session_state.mt5_last_order_result = results[-MAX_MT5_RESULT_ROWS:]
    except Exception:
        pass

def load_settings():
    defaults = {
        "interval": "15min",
        "zone_threshold": 2.5,
        "mt5_symbol": MT5_SYMBOL_DEFAULT,
        "use_mt5_primary": True,
        "use_api_backup": True,
        "sl_mode": "Pips",
        "sl_value": 200.0,
        "tp1_mode": "Pips",
        "tp1_value": 300.0,
        "tp2_mode": "Pips",
        "tp2_value": 600.0,
        "tp3_mode": "Pips",
        "tp3_value": 900.0,
        "entry_lot": 0.03,
        "enable_weekly_reports": True,
        "mt5_live_trading": False,
        "mt5_trade_confirm": False,
        "show_mt5_panel": True,
        "show_trade_setup": True,
        "show_open_trade_management": True,
        "show_trade_history": True,
        "show_performance_dashboard": True,
        "refresh_seconds": 15,
    }
    saved = load_json_file(SETTINGS_FILE, {})
    if isinstance(saved, dict):
        defaults.update(saved)
    return defaults

def save_settings(settings):
    save_json_file(SETTINGS_FILE, settings)

def index_for(options, value, default=0):
    try:
        return options.index(value)
    except Exception:
        return default

def safe_toggle(label, value=True, key=None):
    try:
        return st.toggle(label, value=value, key=key)
    except Exception:
        return st.checkbox(label, value=value, key=key)

APP_SETTINGS = load_settings()


def split_entry_lot(total_lot):
    """Split one user-selected entry lot into TP1/TP2/TP3 MT5 legs."""
    try:
        units = int(round(float(total_lot) * 100))
    except Exception:
        units = 3
    units = max(1, min(100, units))
    if units == 1:
        return [0.01, 0.0, 0.0]
    if units == 2:
        return [0.01, 0.01, 0.0]
    base = units // 3
    remainder = units - (base * 3)
    parts = [base, base, base]
    for i in range(remainder):
        parts[2 - i] += 1
    return [round(x / 100, 2) for x in parts]


def trade_tp_lot(trade, level_key):
    try:
        value = float(trade.get(f"{level_key}_lot", 0.0) or 0.0)
        if value > 0:
            return value
    except Exception:
        pass
    try:
        idx = {"tp1": 0, "tp2": 1, "tp3": 2}.get(level_key, 0)
        return split_entry_lot(float(trade.get("lots", 0.03) or 0.03))[idx]
    except Exception:
        return 0.01


def format_lot(value):
    try:
        return f"{float(value):.2f}"
    except Exception:
        return "0.00"


def normalize_entry_lot(value):
    try:
        return round(max(0.01, min(1.0, float(value))), 2)
    except Exception:
        return 0.03


def normalize_loaded_trade(trade):
    if not isinstance(trade, dict):
        return None

    defaults = {
        "id": datetime.now().strftime("%Y%m%d%H%M%S"),
        "time": datetime.now().isoformat(),
        "type": "BUY",
        "entry": 0.0,
        "sl": 0.0,
        "initial_sl": 0.0,
        "tp1": 0.0,
        "tp2": 0.0,
        "tp3": 0.0,
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
        "lots": 0.03,
        "tp1_lot": 0.01,
        "tp2_lot": 0.01,
        "tp3_lot": 0.01,
        "profit": 0.0,
        "locked_sl": False,
        "status": "OPEN",
        "result": None,
        "entry_number": 0,
        "entry_label": None,
        "source": "SIGNAL",
        "open_candle_time": None,
        "signal_key": None,
        "mt5_executed": False,
        "mt5_orders": [],
        "mt5_order_tickets": [],
        "mt5_deals": [],
        "mt5_open_positions": 0,
        "mt5_open_volume": 0.0,
        "mt5_live_profit": 0.0,
        "sl_mode": "Pips",
        "sl_value": 200.0,
        "tp1_mode": "Pips",
        "tp1_value": 300.0,
        "tp2_mode": "Pips",
        "tp2_value": 600.0,
        "tp3_mode": "Pips",
        "tp3_value": 900.0,
        "sl_pips": 200.0,
        "tp1_pips": 300.0,
        "tp2_pips": 600.0,
        "tp3_pips": 900.0,
        "risk_reward": 0.0,
    }

    defaults.update(trade)

    for key in ["entry", "sl", "initial_sl", "tp1", "tp2", "tp3", "lots", "tp1_lot", "tp2_lot", "tp3_lot", "profit", "sl_value", "tp1_value", "tp2_value", "tp3_value", "sl_pips", "tp1_pips", "tp2_pips", "tp3_pips", "risk_reward", "mt5_open_volume", "mt5_live_profit"]:
        try:
            defaults[key] = float(defaults[key])
        except Exception:
            defaults[key] = 0.0

    for key in ["tp1_hit", "tp2_hit", "tp3_hit", "locked_sl", "mt5_executed"]:
        defaults[key] = bool(defaults[key])

    return defaults


def load_saved_trades():
    raw = load_json_file(TRADE_LOG_FILE, [])
    trades = []
    if isinstance(raw, list):
        for item in raw:
            t = normalize_loaded_trade(item)
            if t:
                trades.append(t)
    return trades


def next_entry_number():
    """Return the next human-friendly trade number: Entry 1, Entry 2, etc."""
    max_num = 0
    for t in st.session_state.get("trade_log", []):
        try:
            max_num = max(max_num, int(t.get("entry_number") or 0))
        except Exception:
            pass
    return max_num + 1


def ensure_trade_label(trade):
    """Make sure every trade has a stable Telegram/dashboard label."""
    if not isinstance(trade, dict):
        return "Entry ?"
    try:
        n = int(trade.get("entry_number") or 0)
    except Exception:
        n = 0
    if n <= 0:
        n = next_entry_number()
        trade["entry_number"] = n
    label = trade.get("entry_label") or f"Entry {n}"
    trade["entry_label"] = label
    return label


def trade_label(trade):
    if isinstance(trade, dict):
        return trade.get("entry_label") or (f"Entry {trade.get('entry_number')}" if trade.get("entry_number") else "Entry ?")
    return "Entry ?"


def mt5_trade_prefix(trade):
    """Short MT5-safe prefix so multiple trades in same direction do not close each other's TP orders."""
    try:
        n = int(trade.get("entry_number") or 0)
    except Exception:
        n = 0
    if n <= 0:
        ensure_trade_label(trade)
        try:
            n = int(trade.get("entry_number") or 0)
        except Exception:
            n = 0
    return f"E{n}" if n > 0 else "E0"


def mt5_trade_order_label(trade, tp_label):
    return f"{mt5_trade_prefix(trade)}_{tp_label}"


def build_trade(direction, entry, source="SIGNAL"):
    """Create one dashboard trade using the current Risk / TP sidebar settings."""
    levels = calculate_trade_levels(
        entry, direction,
        sl_mode, sl_value,
        tp1_mode, tp1_value,
        tp2_mode, tp2_value,
        tp3_mode, tp3_value,
    )
    trade_obj = {
        "id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
        "time": datetime.now(),
        "type": direction,
        "entry": float(entry),
        "sl": levels["sl"],
        "initial_sl": levels["sl"],
        "tp1": levels["tp1"],
        "tp2": levels["tp2"],
        "tp3": levels["tp3"],
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
        "lots": TOTAL_LOT,
        "tp1_lot": TP1_LOT,
        "tp2_lot": TP2_LOT,
        "tp3_lot": TP3_LOT,
        "profit": 0.0,
        "locked_sl": False,
        "status": "OPEN",
        "result": None,
        "source": source,
        "sl_mode": sl_mode,
        "sl_value": float(sl_value),
        "tp1_mode": tp1_mode,
        "tp1_value": float(tp1_value),
        "tp2_mode": tp2_mode,
        "tp2_value": float(tp2_value),
        "tp3_mode": tp3_mode,
        "tp3_value": float(tp3_value),
        "sl_pips": levels["sl_pips"],
        "tp1_pips": levels["tp1_pips"],
        "tp2_pips": levels["tp2_pips"],
        "tp3_pips": levels["tp3_pips"],
        "risk_reward": levels["risk_reward"],
        "open_candle_time": pd.Timestamp(data.index[-1]).isoformat() if "data" in globals() and data is not None and not data.empty else None,
        "mt5_executed": False,
        "mt5_orders": [],
        "mt5_order_tickets": [],
        "mt5_deals": [],
        "mt5_open_positions": 0,
        "mt5_open_volume": 0.0,
        "mt5_live_profit": 0.0,
    }
    ensure_trade_label(trade_obj)
    return trade_obj


def send_trade_entry_telegram(trade):
    label = ensure_trade_label(trade)
    send_telegram(
        f"🚨 {label} NEW {trade['type']} XAUUSD\n"
        f"Entry: {trade['entry']:.2f}\n"
        f"SL: {trade['sl']:.2f} ({trade.get('sl_pips', 0):.1f} pips)\n"
        f"TP1: {trade['tp1']:.2f} ({trade.get('tp1_pips', 0):.1f} pips)\n"
        f"TP2: {trade['tp2']:.2f} ({trade.get('tp2_pips', 0):.1f} pips)\n"
        f"TP3: {trade['tp3']:.2f} ({trade.get('tp3_pips', 0):.1f} pips)\n"
        f"RR to TP3: 1:{trade.get('risk_reward', 0):.2f}\n"
        f"Lot: {format_lot(trade.get('lots', TOTAL_LOT))} split TP1 {format_lot(trade_tp_lot(trade, 'tp1'))} | TP2 {format_lot(trade_tp_lot(trade, 'tp2'))} | TP3 {format_lot(trade_tp_lot(trade, 'tp3'))}"
    )


def mt5_trade_tickets_open(symbol, trade):
    """Return open MT5 strategy positions matching this trade's saved tickets."""
    try:
        if mt5 is None or not trade.get("mt5_executed"):
            return []
        tickets = {str(x) for x in (trade.get("mt5_order_tickets") or []) if x is not None}
        if not tickets:
            return []
        ok, _ = safe_mt5_initialize()
        if not ok:
            return []
        symbol = auto_detect_xauusd_symbol(symbol)
        positions = mt5.positions_get(symbol=symbol) or []
        return [pos for pos in positions if str(getattr(pos, "ticket", "")) in tickets]
    except Exception:
        return []


def save_runtime_state():
    prune_runtime_state()
    save_json_file(TRADE_LOG_FILE, st.session_state.get("trade_log", []))
    save_json_file(ALERTED_EVENTS_FILE, list(st.session_state.get("alerted_events", set())))


# -------- WEEKLY PERFORMANCE REPORT + RESET --------
def current_trading_week_info(now=None):
    """Monday to Friday trading-week label using the computer local date."""
    now = now or datetime.now()
    week_start = now.date() - pd.Timedelta(days=now.weekday())
    week_end = week_start + pd.Timedelta(days=4)
    iso_year, iso_week, _ = now.isocalendar()
    return {"week_id": f"{iso_year}-W{iso_week:02d}", "week_start": str(week_start), "week_end": str(week_end)}


def safe_trade_profit(trade):
    try:
        return round(float(trade.get("profit", 0.0) or 0.0), 2)
    except Exception:
        return 0.0


def summarize_weekly_trades(trades):
    clean_trades = [t for t in trades if isinstance(t, dict)]
    closed = [t for t in clean_trades if str(t.get("status", "")).upper() == "CLOSED"]
    open_trades = [t for t in clean_trades if str(t.get("status", "")).upper() == "OPEN"]
    wins = [t for t in closed if safe_trade_profit(t) > 0]
    losses = [t for t in closed if safe_trade_profit(t) < 0]
    breakeven = [t for t in closed if safe_trade_profit(t) == 0]
    counted = len(wins) + len(losses)
    gains = round(sum(max(0.0, safe_trade_profit(t)) for t in closed), 2)
    losses_value = round(sum(min(0.0, safe_trade_profit(t)) for t in closed), 2)
    net = round(gains + losses_value, 2)
    return {
        "total_trades": len(clean_trades),
        "closed": len(closed),
        "open": len(open_trades),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(breakeven),
        "winrate": round((len(wins) / counted) * 100, 1) if counted else 0.0,
        "closed_gains": gains,
        "closed_losses": losses_value,
        "net_profit": net,
    }


def format_weekly_telegram_report(week_info, summary):
    return (
        "📊 <b>XAUUSD WEEKLY PERFORMANCE REPORT</b>\n\n"
        f"Week: <b>{week_info.get('week_start')} to {week_info.get('week_end')}</b>\n"
        f"Total trades: <b>{summary['total_trades']}</b>\n"
        f"Closed: <b>{summary['closed']}</b> | Open carried: <b>{summary['open']}</b>\n\n"
        f"✅ Wins: <b>{summary['wins']}</b>\n"
        f"❌ Losses: <b>{summary['losses']}</b>\n"
        f"➖ Breakeven: <b>{summary['breakeven']}</b>\n"
        f"🏆 Winrate: <b>{summary['winrate']:.1f}%</b>\n\n"
        f"💚 Closed gains: <b>${summary['closed_gains']:.2f}</b>\n"
        f"❤️ Closed losses: <b>${summary['closed_losses']:.2f}</b>\n"
        f"💰 Net result: <b>${summary['net_profit']:.2f}</b>\n\n"
        "🔄 New trading week started. Dashboard has been reset for fresh weekly tracking."
    )


def load_weekly_reports():
    reports = load_json_file(WEEKLY_REPORTS_FILE, [])
    return reports if isinstance(reports, list) else []


def save_weekly_reports(reports):
    save_json_file(WEEKLY_REPORTS_FILE, reports[-60:])


def run_weekly_report_and_reset(enable_reports=True):
    """Archive previous week, send Telegram report once, and clear closed trades when a new Monday week starts."""
    if not enable_reports:
        return

    this_week = current_trading_week_info()
    weekly_state = load_json_file(WEEKLY_STATE_FILE, {})
    if not isinstance(weekly_state, dict):
        weekly_state = {}

    saved_week_id = weekly_state.get("current_week_id")
    if not saved_week_id:
        weekly_state.update(this_week)
        weekly_state["current_week_id"] = this_week["week_id"]
        save_json_file(WEEKLY_STATE_FILE, weekly_state)
        return

    if saved_week_id == this_week["week_id"]:
        return

    trades = [t for t in st.session_state.get("trade_log", []) if isinstance(t, dict)]
    previous_week_info = {
        "week_id": saved_week_id,
        "week_start": weekly_state.get("week_start", "previous week"),
        "week_end": weekly_state.get("week_end", "previous week"),
    }
    summary = summarize_weekly_trades(trades)

    reports = load_weekly_reports()
    already_saved = any(isinstance(r, dict) and r.get("week_id") == saved_week_id for r in reports)
    if trades and not already_saved:
        reports.append({
            "week_id": saved_week_id,
            "week_start": previous_week_info.get("week_start"),
            "week_end": previous_week_info.get("week_end"),
            "saved_at": datetime.now().isoformat(),
            "summary": summary,
            "trades": trades,
        })
        save_weekly_reports(reports)

    sent_key = f"WEEKLY_REPORT_SENT_{saved_week_id}"
    if trades and sent_key not in st.session_state.get("alerted_events", set()):
        send_telegram(format_weekly_telegram_report(previous_week_info, summary))
        st.session_state.alerted_events.add(sent_key)

    st.session_state.trade_log = [t for t in trades if str(t.get("status", "")).upper() == "OPEN"]

    weekly_state.update(this_week)
    weekly_state["current_week_id"] = this_week["week_id"]
    weekly_state["last_archived_week_id"] = saved_week_id
    weekly_state["last_reset_at"] = datetime.now().isoformat()
    save_json_file(WEEKLY_STATE_FILE, weekly_state)
    save_runtime_state()


# -------- MT5 DATA HELPERS --------
def interval_to_mt5_timeframe(selected_interval):
    if mt5 is None:
        return None

    mapping = {
        "5min": mt5.TIMEFRAME_M5,
        "15min": mt5.TIMEFRAME_M15,
        "1h": mt5.TIMEFRAME_H1,
    }
    return mapping.get(selected_interval)


def safe_mt5_initialize():
    if mt5 is None:
        return False, "MetaTrader5 package not installed. Run: pip install MetaTrader5"

    try:
        if mt5.initialize():
            account = mt5.account_info()
            if account is not None:
                return True, f"MT5 connected: {account.login}"
            return False, f"MT5 account not detected: {mt5.last_error()}"

        return False, f"MT5 initialize failed: {mt5.last_error()}"
    except Exception as exc:
        return False, f"MT5 error: {exc}"


def ensure_mt5_symbol(symbol):
    if mt5 is None:
        return False

    info = mt5.symbol_info(symbol)
    if info is None:
        return False

    if not info.visible:
        return bool(mt5.symbol_select(symbol, True))

    return True


def mt5_retcode_name(retcode):
    """Human-readable MT5 retcode name for debugging failed executions."""
    if mt5 is None:
        return "MT5_NOT_INSTALLED"
    mapping = {}
    for name in dir(mt5):
        if name.startswith("TRADE_RETCODE_"):
            try:
                mapping[int(getattr(mt5, name))] = name
            except Exception:
                pass
    try:
        return mapping.get(int(retcode), str(retcode))
    except Exception:
        return str(retcode)


def get_symbol_trade_status(symbol):
    """Return broker symbol details used by the execution safety checks."""
    if mt5 is None:
        return {"ok": False, "error": "MetaTrader5 package not installed"}
    try:
        info = mt5.symbol_info(symbol)
        if info is None:
            return {"ok": False, "error": f"No symbol info for {symbol}"}
        return {
            "ok": True,
            "symbol": info.name,
            "visible": bool(info.visible),
            "trade_mode": int(getattr(info, "trade_mode", -1)),
            "digits": int(getattr(info, "digits", 2)),
            "point": float(getattr(info, "point", 0.01) or 0.01),
            "spread": int(getattr(info, "spread", 0) or 0),
            "stops_level": int(getattr(info, "trade_stops_level", 0) or 0),
            "freeze_level": int(getattr(info, "trade_freeze_level", 0) or 0),
            "volume_min": float(getattr(info, "volume_min", 0.01) or 0.01),
            "volume_max": float(getattr(info, "volume_max", 100.0) or 100.0),
            "volume_step": float(getattr(info, "volume_step", 0.01) or 0.01),
            "filling_mode": int(getattr(info, "filling_mode", 0) or 0),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def auto_detect_xauusd_symbol(preferred_symbol):
    """Bulletproof broker symbol detection.
    Handles names like 'XAUUSD, Gold (Spot)' shown by some MT5 brokers.
    """
    if mt5 is None:
        return preferred_symbol

    preferred_symbol = str(preferred_symbol or "").strip()

    candidates = []
    if preferred_symbol:
        candidates.append(preferred_symbol)

    # Explicit known variants including comma-style broker symbol.
    candidates.extend([
        "XAUUSD, Gold (Spot)", "XAUUSD", "XAUUSDm", "XAUUSD.", "XAUUSD#", "XAUUSDpro",
        "GOLD", "GOLDm", "GOLD.", "GOLD#", "Gold", "GoldSpot",
    ])

    try:
        all_symbols = mt5.symbols_get() or []
        for sym in all_symbols:
            name = getattr(sym, "name", "")
            desc = str(getattr(sym, "description", ""))
            path = str(getattr(sym, "path", ""))
            hay = f"{name} {desc} {path}".upper()
            if ("XAU" in hay and "USD" in hay) or "GOLD" in hay:
                if name and name not in candidates:
                    candidates.append(name)
    except Exception:
        pass

    scored = []
    for name in candidates:
        info = mt5.symbol_info(name)
        if info is None:
            continue
        score = 0
        upper = name.upper()
        desc = str(getattr(info, "description", "")).upper()
        if preferred_symbol and name == preferred_symbol:
            score += 500
        if "XAUUSD" in upper:
            score += 300
        if "GOLD" in upper or "GOLD" in desc:
            score += 150
        if "SPOT" in upper or "SPOT" in desc:
            score += 50
        if any(x in upper for x in ["MICRO", "MINI", "CENT"]):
            score -= 50
        try:
            trade_mode = int(getattr(info, "trade_mode", -1))
            if trade_mode == getattr(mt5, "SYMBOL_TRADE_MODE_FULL", trade_mode):
                score += 100
        except Exception:
            pass
        scored.append((score, name))

    for _, name in sorted(scored, reverse=True):
        if ensure_mt5_symbol(name):
            return name

    return preferred_symbol


def fetch_mt5_data(selected_interval, preferred_symbol):
    ok, msg = safe_mt5_initialize()
    if not ok:
        return pd.DataFrame(), "MT5", preferred_symbol, msg

    timeframe = interval_to_mt5_timeframe(selected_interval)
    if timeframe is None:
        return pd.DataFrame(), "MT5", preferred_symbol, f"Unsupported MT5 interval: {selected_interval}"

    actual_symbol = auto_detect_xauusd_symbol(preferred_symbol)

    if not actual_symbol or not ensure_mt5_symbol(actual_symbol):
        return pd.DataFrame(), "MT5", actual_symbol, f"MT5 symbol not available: {preferred_symbol}"

    try:
        rates = mt5.copy_rates_from_pos(actual_symbol, timeframe, 0, CANDLE_OUTPUT_SIZE)
        if rates is None or len(rates) == 0:
            return pd.DataFrame(), "MT5", actual_symbol, f"No MT5 candle data returned for {actual_symbol}: {mt5.last_error()}"

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df = df.rename(columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
        })
        df = df[["time", "Open", "High", "Low", "Close"]].copy()
        df = df.astype({"Open": float, "High": float, "Low": float, "Close": float})
        df = df.sort_values("time")
        df.set_index("time", inplace=True)

        return df, "MT5 LIVE", actual_symbol, f"Loaded {len(df)} candles from MT5: {actual_symbol}"

    except Exception as exc:
        return pd.DataFrame(), "MT5", actual_symbol, f"MT5 data error: {exc}"


def cache_file_for_interval(selected_interval):
    safe_interval = str(selected_interval).replace("/", "_").replace(" ", "_")
    return STORAGE_DIR / f"xauusd_cache_{safe_interval}.csv"


def save_candle_cache(selected_interval, df):
    try:
        if df is not None and not df.empty:
            df.to_csv(cache_file_for_interval(selected_interval))
    except Exception:
        pass


def load_candle_cache(selected_interval):
    try:
        path = cache_file_for_interval(selected_interval)
        if path.exists():
            df = pd.read_csv(path)
            df["time"] = pd.to_datetime(df["time"])
            df.set_index("time", inplace=True)
            df = df.astype({"Open": float, "High": float, "Low": float, "Close": float})
            return df
    except Exception:
        pass
    return pd.DataFrame()


@st.cache_data(ttl=5, show_spinner=False)
def fetch_twelvedata_data(selected_interval):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": "XAU/USD",
        "interval": selected_interval,
        "apikey": API_KEY,
        "outputsize": CANDLE_OUTPUT_SIZE,
    }

    try:
        r = requests.get(url, params=params, timeout=10)
        api_data = r.json()

        if "values" not in api_data:
            return pd.DataFrame(), api_data.get("message", "TwelveData returned no candle values")

        df = pd.DataFrame(api_data["values"])
        df = df.rename(columns={
            "datetime": "time",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
        })
        df = df.astype({"Open": float, "High": float, "Low": float, "Close": float})
        df["time"] = pd.to_datetime(df["time"])
        df = df.sort_values("time")
        df.set_index("time", inplace=True)

        return df, f"Loaded {len(df)} candles from TwelveData"

    except Exception as exc:
        return pd.DataFrame(), f"TwelveData fetch failed: {exc}"


def load_market_data(selected_interval, preferred_symbol, use_mt5_primary=True, use_api_backup=True):
    messages = []

    if use_mt5_primary:
        df, source, actual_symbol, msg = fetch_mt5_data(selected_interval, preferred_symbol)
        messages.append(msg)

        if not df.empty:
            save_candle_cache(selected_interval, df)
            return df, source, actual_symbol, messages

    if use_api_backup:
        df, msg = fetch_twelvedata_data(selected_interval)
        messages.append(msg)

        if not df.empty:
            save_candle_cache(selected_interval, df)
            return df, "TWELVEDATA LIVE", preferred_symbol, messages

    df = load_candle_cache(selected_interval)
    if not df.empty:
        messages.append("Live feed unavailable — loaded last saved local candle cache")
        return df, "LOCAL CACHE", preferred_symbol, messages

    return pd.DataFrame(), "NO DATA", preferred_symbol, messages


# -------- MT5 LIVE TRADING HELPERS --------
def mt5_normalize_price(symbol, price):
    try:
        if mt5 is None:
            return float(price)
        info = mt5.symbol_info(symbol)
        digits = int(info.digits) if info else 2
        return round(float(price), digits)
    except Exception:
        return round(float(price), 2)


def mt5_normalize_volume(symbol, lot):
    """Auto-adjust requested lot to broker min/max/step."""
    try:
        info = mt5.symbol_info(symbol)
        if info is None:
            return float(lot), f"No symbol info for {symbol}"
        min_lot = float(getattr(info, "volume_min", 0.01) or 0.01)
        max_lot = float(getattr(info, "volume_max", 100.0) or 100.0)
        step = float(getattr(info, "volume_step", 0.01) or 0.01)
        value = max(min_lot, min(float(lot), max_lot))
        steps = round(value / step)
        adjusted = round(steps * step, 8)
        adjusted = max(min_lot, min(adjusted, max_lot))
        return float(adjusted), "OK"
    except Exception as exc:
        return float(lot), f"Volume normalize error: {exc}"


def mt5_lot_is_valid(symbol, lot):
    try:
        if mt5 is None:
            return False, "MetaTrader5 package not installed"
        info = mt5.symbol_info(symbol)
        if info is None:
            return False, f"Symbol info not found: {symbol}"
        min_lot = float(getattr(info, "volume_min", 0.01) or 0.01)
        max_lot = float(getattr(info, "volume_max", 100.0) or 100.0)
        step = float(getattr(info, "volume_step", 0.01) or 0.01)
        lot = float(lot)
        if lot < min_lot:
            return False, f"Lot {lot} is below broker minimum {min_lot}"
        if lot > max_lot:
            return False, f"Lot {lot} is above broker maximum {max_lot}"
        steps = round(lot / step)
        adjusted = round(steps * step, 8)
        if abs(adjusted - lot) > 1e-8:
            return False, f"Lot {lot} does not match broker step {step}"
        return True, "OK"
    except Exception as exc:
        return False, f"Lot validation error: {exc}"


def mt5_get_filling_mode(symbol):
    """Backward-compatible helper used by partial-close requests."""
    try:
        modes = mt5_allowed_filling_modes(symbol)
        for mode in modes:
            if mode in [mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN]:
                return mode
    except Exception:
        pass
    return mt5.ORDER_FILLING_IOC


def mt5_allowed_filling_modes(symbol):
    """Return a practical sequence of filling modes to try.
    Some brokers reject one mode even when symbol info claims it is supported.
    """
    modes = []
    try:
        info = mt5.symbol_info(symbol)
        filling = int(getattr(info, "filling_mode", 0) or 0) if info else 0
        # Prefer the broker visible policy first when possible.
        for mode in [filling, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN]:
            if mode not in modes:
                modes.append(mode)
    except Exception:
        modes = [mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN]
    return modes


def mt5_adjust_sl_tp(symbol, direction, price, sl, tp):
    """Normalize SL/TP and respect minimum stop level if broker requires it."""
    try:
        info = mt5.symbol_info(symbol)
        if info is None:
            return mt5_normalize_price(symbol, sl), mt5_normalize_price(symbol, tp), "No symbol info"
        point = float(getattr(info, "point", 0.01) or 0.01)
        stops_level = int(getattr(info, "trade_stops_level", 0) or 0)
        min_dist = max(stops_level * point, point)
        direction = str(direction).upper()
        price = float(price)
        sl = float(sl)
        tp = float(tp)

        if direction == "BUY":
            if sl >= price - min_dist:
                sl = price - min_dist
            if tp <= price + min_dist:
                tp = price + min_dist
        else:
            if sl <= price + min_dist:
                sl = price + min_dist
            if tp >= price - min_dist:
                tp = price - min_dist

        return mt5_normalize_price(symbol, sl), mt5_normalize_price(symbol, tp), "OK"
    except Exception as exc:
        return mt5_normalize_price(symbol, sl), mt5_normalize_price(symbol, tp), f"SLTP adjust error: {exc}"


def mt5_trade_check_request(request):
    try:
        check = mt5.order_check(request)
        if check is None:
            return {"ok": False, "error": str(mt5.last_error())}
        retcode = int(getattr(check, "retcode", 0))
        return {
            "ok": retcode in [0, mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED],
            "retcode": retcode,
            "retcode_name": mt5_retcode_name(retcode),
            "comment": getattr(check, "comment", ""),
            "margin": getattr(check, "margin", None),
            "profit": getattr(check, "profit", None),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def place_mt5_market_order(symbol, direction, sl, tp, lot, label):
    """Bulletproof MT5 order sender with symbol auto-detect, volume auto-fix,
    SL/TP normalization, fill-mode retries, order_check and detailed errors.
    """
    if mt5 is None:
        return {"ok": False, "label": label, "error": "MetaTrader5 package not installed"}

    ok, msg = safe_mt5_initialize()
    if not ok:
        return {"ok": False, "label": label, "error": msg, "last_error": str(mt5.last_error())}

    account = mt5.account_info()
    if account is None:
        return {"ok": False, "label": label, "error": "No MT5 account info", "last_error": str(mt5.last_error())}

    symbol = auto_detect_xauusd_symbol(symbol)
    if not symbol or not ensure_mt5_symbol(symbol):
        return {"ok": False, "label": label, "error": f"Symbol not available: {symbol}", "symbol_requested": symbol}

    symbol_status = get_symbol_trade_status(symbol)
    if not symbol_status.get("ok"):
        return {"ok": False, "label": label, "symbol": symbol, "error": symbol_status.get("error")}

    # Common MT5 constants: SYMBOL_TRADE_MODE_FULL = 4. Keep non-blocking for brokers that return custom values.
    trade_mode = int(symbol_status.get("trade_mode", -1))
    if hasattr(mt5, "SYMBOL_TRADE_MODE_DISABLED") and trade_mode == mt5.SYMBOL_TRADE_MODE_DISABLED:
        return {"ok": False, "label": label, "symbol": symbol, "error": "Trading disabled for this symbol", "symbol_status": symbol_status}

    lot, volume_msg = mt5_normalize_volume(symbol, lot)
    lot_ok, lot_msg = mt5_lot_is_valid(symbol, lot)
    if not lot_ok:
        return {"ok": False, "label": label, "symbol": symbol, "error": lot_msg, "volume_msg": volume_msg, "symbol_status": symbol_status}

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return {"ok": False, "label": label, "symbol": symbol, "error": f"No live tick data for {symbol}", "last_error": str(mt5.last_error())}

    direction = str(direction).upper()
    if direction not in ["BUY", "SELL"]:
        return {"ok": False, "label": label, "symbol": symbol, "error": f"Invalid direction: {direction}"}

    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    price = float(tick.ask) if direction == "BUY" else float(tick.bid)
    sl, tp, sltp_msg = mt5_adjust_sl_tp(symbol, direction, price, sl, tp)

    attempts = []
    for filling_mode in mt5_allowed_filling_modes(symbol):
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(lot),
            "type": order_type,
            "price": mt5_normalize_price(symbol, price),
            "sl": sl,
            "tp": tp,
            "deviation": MT5_DEVIATION,
            "magic": MT5_MAGIC,
            "comment": f"{MT5_ORDER_COMMENT}_{label}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_mode,
        }

        check = mt5_trade_check_request(request)
        result = mt5.order_send(request)
        if result is None:
            attempt = {
                "ok": False,
                "label": label,
                "symbol": symbol,
                "direction": direction,
                "lot": float(lot),
                "price": request["price"],
                "sl": request["sl"],
                "tp": request["tp"],
                "filling_mode": filling_mode,
                "check": check,
                "error": str(mt5.last_error()),
            }
            attempts.append(attempt)
            continue

        retcode = int(getattr(result, "retcode", 0))
        ok_send = retcode in [mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED]
        attempt = {
            "ok": ok_send,
            "label": label,
            "symbol": symbol,
            "direction": direction,
            "lot": float(lot),
            "price": request["price"],
            "sl": request["sl"],
            "tp": request["tp"],
            "ticket": getattr(result, "order", None),
            "deal": getattr(result, "deal", None),
            "retcode": retcode,
            "retcode_name": mt5_retcode_name(retcode),
            "comment": getattr(result, "comment", ""),
            "request_id": getattr(result, "request_id", None),
            "filling_mode": filling_mode,
            "check": check,
            "symbol_status": symbol_status,
            "sltp_msg": sltp_msg,
            "last_error": str(mt5.last_error()),
        }
        attempts.append(attempt)
        if ok_send:
            return attempt

    # Return the most informative failed attempt.
    final = attempts[-1] if attempts else {"ok": False, "label": label, "symbol": symbol, "error": "No execution attempts made"}
    final["attempts"] = attempts
    return final


def place_three_mt5_orders_for_trade(symbol, trade):
    if not trade:
        return []
    ensure_trade_label(trade)
    direction = trade.get("type")
    order_plan = [
        ("TP1", trade.get("tp1"), trade_tp_lot(trade, "tp1")),
        ("TP2", trade.get("tp2"), trade_tp_lot(trade, "tp2")),
        ("TP3", trade.get("tp3"), trade_tp_lot(trade, "tp3")),
    ]
    results = []
    for label, tp_price, lot in order_plan:
        if float(lot or 0.0) <= 0:
            continue
        results.append(place_mt5_market_order(symbol, direction, trade.get("sl"), tp_price, lot, mt5_trade_order_label(trade, label)))
    return results


def execute_mt5_for_trade(symbol, trade, signal_name=None, allow_retry_failed=False):
    """Send one dashboard SIGNAL trade to MT5 once, with a safe catch-up path."""
    if not trade or str(trade.get("status", "")).upper() != "OPEN":
        return []
    if str(trade.get("source", "SIGNAL")).upper() != "SIGNAL":
        return []
    if trade.get("mt5_executed") and trade.get("mt5_order_tickets"):
        return []

    signal_key_value = trade.get("signal_key") or trade.get("id") or datetime.now().strftime("%Y%m%d%H%M%S%f")
    if signal_key_value == "WAIT":
        return []

    mt5_execution_key = f"MT5_ENTRY_{signal_key_value}"
    already_attempted = mt5_execution_key in st.session_state.get("alerted_events", set())
    if already_attempted and not allow_retry_failed:
        trade["mt5_execution_key"] = mt5_execution_key
        return []

    results = place_three_mt5_orders_for_trade(symbol, trade)
    st.session_state.mt5_last_order_result = results
    trade["mt5_orders"] = results
    trade["mt5_executed"] = any(r.get("ok") for r in results)
    trade["mt5_order_tickets"] = [r.get("ticket") for r in results if r.get("ok") and r.get("ticket") is not None]
    trade["mt5_deals"] = [r.get("deal") for r in results if r.get("ok") and r.get("deal") is not None]
    trade["mt5_execution_key"] = mt5_execution_key
    st.session_state.alerted_events.add(mt5_execution_key)

    ok_count = sum(1 for r in results if r.get("ok"))
    fail_count = len(results) - ok_count
    send_telegram(
        f"🤖 {trade_label(trade)} MT5 EXECUTION RESULT\n"
        f"Signal: {signal_name or trade.get('type', 'SIGNAL')} XAUUSD\n"
        f"Placed: {ok_count}/{len(results)}\n"
        f"Failed: {fail_count}/{len(results)}"
    )

    for r in results:
        if r.get("ok"):
            send_telegram(
                f"✅ {trade_label(trade)} MT5 ORDER EXECUTED\n"
                f"{r.get('direction')} {r.get('symbol')}\n"
                f"Lot: {r.get('lot')}\n"
                f"Price: {r.get('price')}\n"
                f"SL: {r.get('sl')}\n"
                f"TP: {r.get('tp')}\n"
                f"Ticket: {r.get('ticket')}"
            )
        else:
            send_telegram(
                f"❌ {trade_label(trade)} MT5 ORDER FAILED\n"
                f"Label: {r.get('label')}\n"
                f"Error: {r.get('error', r.get('comment', 'Unknown error'))}\n"
                f"Retcode: {r.get('retcode', 'N/A')}"
            )

    save_runtime_state()
    return results


def execute_pending_mt5_signal_trades(symbol, max_pending=3):
    """When MT5 is armed, catch any open dashboard signal that has not reached MT5."""
    if not (mt5_live_trading and mt5_trade_confirm):
        return []
    sent = []
    open_signal_trades = [
        t for t in st.session_state.get("trade_log", [])
        if isinstance(t, dict)
        and str(t.get("status", "")).upper() == "OPEN"
        and str(t.get("source", "SIGNAL")).upper() == "SIGNAL"
        and not (t.get("mt5_executed") and t.get("mt5_order_tickets"))
    ]
    for pending_trade in open_signal_trades[-max_pending:]:
        res = execute_mt5_for_trade(symbol, pending_trade, pending_trade.get("type"), allow_retry_failed=False)
        if res:
            sent.extend(res)
    return sent

def modify_mt5_sl_for_strategy_positions(symbol, direction, new_sl, event_key):
    """Move SL for positions opened by this script only."""
    if mt5 is None:
        return 0
    already_key = f"MT5_MOD_{event_key}"
    if already_key in st.session_state.alerted_events:
        return 0

    ok, _ = safe_mt5_initialize()
    if not ok:
        return 0

    symbol = auto_detect_xauusd_symbol(symbol)
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return 0

    target_type = mt5.POSITION_TYPE_BUY if str(direction).upper() == "BUY" else mt5.POSITION_TYPE_SELL
    modified = 0

    for pos in positions:
        if int(getattr(pos, "magic", 0)) != MT5_MAGIC:
            continue
        if int(getattr(pos, "type", -1)) != target_type:
            continue
        if MT5_ORDER_COMMENT not in str(getattr(pos, "comment", "")):
            continue

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": pos.ticket,
            "symbol": symbol,
            "sl": mt5_normalize_price(symbol, new_sl),
            "tp": mt5_normalize_price(symbol, pos.tp),
            "magic": MT5_MAGIC,
            "comment": f"{MT5_ORDER_COMMENT}_SL_UPDATE",
        }
        result = mt5.order_send(request)
        if result and int(getattr(result, "retcode", 0)) == mt5.TRADE_RETCODE_DONE:
            modified += 1

    st.session_state.alerted_events.add(already_key)
    return modified


def get_mt5_strategy_positions(symbol):
    """Return open MT5 positions created by this script."""
    if mt5 is None:
        return []
    ok, _ = safe_mt5_initialize()
    if not ok:
        return []
    symbol = auto_detect_xauusd_symbol(symbol)
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return []
    rows = []
    for pos in positions:
        if int(getattr(pos, "magic", 0)) != MT5_MAGIC:
            continue
        if MT5_ORDER_COMMENT not in str(getattr(pos, "comment", "")):
            continue
        rows.append({
            "ticket": getattr(pos, "ticket", None),
            "symbol": getattr(pos, "symbol", symbol),
            "type": "BUY" if getattr(pos, "type", None) == mt5.POSITION_TYPE_BUY else "SELL",
            "volume": getattr(pos, "volume", None),
            "price_open": getattr(pos, "price_open", None),
            "sl": getattr(pos, "sl", None),
            "tp": getattr(pos, "tp", None),
            "profit": getattr(pos, "profit", None),
            "comment": getattr(pos, "comment", ""),
        })
    return rows


def mt5_position_matches_label(pos, label):
    try:
        comment = str(getattr(pos, "comment", "")).upper()
        return str(label).upper() in comment
    except Exception:
        return False


def close_mt5_positions_by_label(symbol, direction, label, event_key):
    """Close strategy positions that match TP1/TP2/TP3 labels. Safe to call repeatedly."""
    if mt5 is None:
        return {"closed": 0, "results": [{"ok": False, "label": label, "error": "MetaTrader5 package not installed"}]}

    already_key = f"MT5_CLOSE_{event_key}_{label}"
    if already_key in st.session_state.alerted_events:
        return {"closed": 0, "results": [], "skipped": "already processed"}

    ok, msg = safe_mt5_initialize()
    if not ok:
        return {"closed": 0, "results": [{"ok": False, "label": label, "error": msg}]}

    symbol = auto_detect_xauusd_symbol(symbol)
    if not symbol or not ensure_mt5_symbol(symbol):
        return {"closed": 0, "results": [{"ok": False, "label": label, "error": f"Symbol not available: {symbol}"}]}

    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        st.session_state.alerted_events.add(already_key)
        return {"closed": 0, "results": [], "skipped": "no open strategy positions"}

    target_type = mt5.POSITION_TYPE_BUY if str(direction).upper() == "BUY" else mt5.POSITION_TYPE_SELL
    results = []
    closed = 0

    for pos in positions:
        if int(getattr(pos, "magic", 0)) != MT5_MAGIC:
            continue
        if int(getattr(pos, "type", -1)) != target_type:
            continue
        if MT5_ORDER_COMMENT not in str(getattr(pos, "comment", "")):
            continue
        if not mt5_position_matches_label(pos, label):
            continue

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            results.append({"ok": False, "ticket": getattr(pos, "ticket", None), "label": label, "error": "No live tick data"})
            continue

        close_type = mt5.ORDER_TYPE_SELL if target_type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        close_price = float(tick.bid) if target_type == mt5.POSITION_TYPE_BUY else float(tick.ask)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": pos.ticket,
            "symbol": symbol,
            "volume": float(pos.volume),
            "type": close_type,
            "price": mt5_normalize_price(symbol, close_price),
            "deviation": MT5_DEVIATION,
            "magic": MT5_MAGIC,
            "comment": f"{MT5_ORDER_COMMENT}_CLOSE_{label}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5_get_filling_mode(symbol),
        }
        result = mt5.order_send(request)
        if result is None:
            results.append({"ok": False, "ticket": getattr(pos, "ticket", None), "label": label, "error": str(mt5.last_error())})
            continue

        retcode = int(getattr(result, "retcode", 0))
        ok_close = retcode in [mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED]
        if ok_close:
            closed += 1
        results.append({
            "ok": ok_close,
            "ticket": getattr(pos, "ticket", None),
            "label": label,
            "symbol": symbol,
            "volume": float(pos.volume),
            "price": mt5_normalize_price(symbol, close_price),
            "retcode": retcode,
            "comment": getattr(result, "comment", ""),
        })

    st.session_state.alerted_events.add(already_key)
    return {"closed": closed, "results": results}


def get_mt5_open_position_summary(symbol):
    """Summarize open MT5 strategy positions by direction for dashboard sync."""
    rows = get_mt5_strategy_positions(symbol)
    summary = {"BUY": {"count": 0, "volume": 0.0, "profit": 0.0}, "SELL": {"count": 0, "volume": 0.0, "profit": 0.0}}
    for row in rows:
        d = str(row.get("type", "")).upper()
        if d not in summary:
            continue
        summary[d]["count"] += 1
        summary[d]["volume"] += float(row.get("volume") or 0.0)
        summary[d]["profit"] += float(row.get("profit") or 0.0)
    return summary, rows


def sync_dashboard_trades_with_mt5(symbol):
    """Keep dashboard open trades aware of real MT5 position state/profit.
    Also creates dashboard rows for MT5 positions that exist in terminal but are missing
    from the saved dashboard journal, so MT5 trades remain visible after restart.
    """
    summary, rows = get_mt5_open_position_summary(symbol)

    known_tickets = set()
    for trade in st.session_state.get("trade_log", []):
        for ticket in trade.get("mt5_order_tickets", []) or []:
            if ticket is not None:
                known_tickets.add(str(ticket))
        for order in trade.get("mt5_orders", []) or []:
            if isinstance(order, dict) and order.get("ticket") is not None:
                known_tickets.add(str(order.get("ticket")))

    for row in rows:
        ticket = row.get("ticket")
        if ticket is None or str(ticket) in known_tickets:
            continue
        direction = str(row.get("type", "BUY")).upper()
        entry = float(row.get("price_open") or 0.0)
        sl = float(row.get("sl") or 0.0)
        tp = float(row.get("tp") or 0.0)
        comment = str(row.get("comment", "")).upper()
        label = "TP1" if "TP1" in comment else "TP2" if "TP2" in comment else "TP3" if "TP3" in comment else "MT5"

        synced_trade = {
            "id": f"MT5_{ticket}",
            "time": datetime.now(),
            "type": direction,
            "entry": entry,
            "sl": sl,
            "initial_sl": sl,
            "tp1": tp if label == "TP1" else entry,
            "tp2": tp if label == "TP2" else entry,
            "tp3": tp if label == "TP3" else tp,
            "tp1_hit": False,
            "tp2_hit": False,
            "tp3_hit": False,
            "lots": float(row.get("volume") or 0.0),
            "profit": 0.0,
            "locked_sl": False,
            "status": "OPEN",
            "result": None,
            "source": "MT5_SYNC",
            "open_candle_time": pd.Timestamp(data.index[-1]).isoformat() if "data" in globals() and data is not None and not data.empty else None,
            "signal_key": f"MT5_SYNC_{ticket}",
            "mt5_executed": True,
            "mt5_orders": [{"ok": True, "ticket": ticket, "label": label, "symbol": row.get("symbol"), "price": entry, "sl": sl, "tp": tp, "lot": row.get("volume")}],
            "mt5_order_tickets": [ticket],
            "mt5_open_positions": 1,
            "mt5_open_volume": float(row.get("volume") or 0.0),
            "mt5_live_profit": float(row.get("profit") or 0.0),
            "sl_mode": "Pips",
            "sl_value": abs(sl - entry) / PIP if sl else 0.0,
            "tp1_mode": "Pips",
            "tp1_value": abs(tp - entry) / PIP if tp else 0.0,
            "tp2_mode": "Pips",
            "tp2_value": abs(tp - entry) / PIP if tp else 0.0,
            "tp3_mode": "Pips",
            "tp3_value": abs(tp - entry) / PIP if tp else 0.0,
            "sl_pips": abs(sl - entry) / PIP if sl else 0.0,
            "tp1_pips": abs(tp - entry) / PIP if label == "TP1" and tp else 0.0,
            "tp2_pips": abs(tp - entry) / PIP if label == "TP2" and tp else 0.0,
            "tp3_pips": abs(tp - entry) / PIP if label == "TP3" and tp else 0.0,
            "risk_reward": 0.0,
        }
        ensure_trade_label(synced_trade)
        st.session_state.trade_log.append(synced_trade)
        known_tickets.add(str(ticket))

    for trade in st.session_state.get("trade_log", []):
        if str(trade.get("status", "")).upper() != "OPEN":
            continue
        direction = str(trade.get("type", "")).upper()
        if direction not in summary:
            continue
        trade["mt5_open_positions"] = summary[direction]["count"]
        trade["mt5_open_volume"] = round(summary[direction]["volume"], 2)
        trade["mt5_live_profit"] = round(summary[direction]["profit"], 2)
        if trade.get("mt5_executed") and summary[direction]["count"] == 0 and trade.get("tp3_hit"):
            trade["status"] = "CLOSED"
            trade["result"] = trade.get("result") or "MT5 CLOSED"
    return rows


# -------- AUTO REFRESH --------
refresh_seconds = int(APP_SETTINGS.get("refresh_seconds", 15) or 15)
st_autorefresh(interval=max(5000, refresh_seconds * 1000), key="refresh")

# -------- STATE --------
if "last_signal" not in st.session_state:
    st.session_state.last_signal = None

if "trade_log" not in st.session_state:
    st.session_state.trade_log = load_saved_trades()

if "alerted_events" not in st.session_state:
    st.session_state.alerted_events = set(load_json_file(ALERTED_EVENTS_FILE, []))

if "mt5_last_order_result" not in st.session_state:
    st.session_state.mt5_last_order_result = []

# -------- SETTINGS --------
PIP = 0.10
INITIAL_SL_PIPS = 200
TP1_PIPS = 300
TP2_PIPS = 600
TP3_PIPS = 900
LOCK_PROFIT_PIPS = 400

DEFAULT_TOTAL_LOT = 0.03
TOTAL_LOT = normalize_entry_lot(APP_SETTINGS.get("entry_lot", DEFAULT_TOTAL_LOT))
TP1_LOT, TP2_LOT, TP3_LOT = split_entry_lot(TOTAL_LOT)
LOT_PER_TP = TP1_LOT
PIP_VALUE_PER_001 = 0.10


# -------- LIVE PROFIT / LOSS HELPERS --------
def calc_trade_live_pnl(trade, current_price):
    """Return live floating P/L in USD for the full trade lot.
    Uses the dashboard pip model: PIP = 0.10 and $0.10 per pip per 0.01 lot.
    """
    try:
        entry = float(trade.get("entry", 0))
        lots = float(trade.get("lots", 0.03))
        direction = str(trade.get("type", "")).upper()
        price = float(current_price)

        if entry <= 0 or lots <= 0 or direction not in ["BUY", "SELL"]:
            return 0.0

        price_diff = price - entry if direction == "BUY" else entry - price
        pips = price_diff / PIP
        lot_multiplier = lots / 0.01
        return round(pips * PIP_VALUE_PER_001 * lot_multiplier, 2)
    except Exception:
        return 0.0


def calc_trade_live_pips(trade, current_price):
    try:
        entry = float(trade.get("entry", 0))
        direction = str(trade.get("type", "")).upper()
        price = float(current_price)
        if entry <= 0 or direction not in ["BUY", "SELL"]:
            return 0.0
        price_diff = price - entry if direction == "BUY" else entry - price
        return round(price_diff / PIP, 1)
    except Exception:
        return 0.0


def calc_trade_total_pnl(trade, current_price):
    realized = float(trade.get("profit", 0.0) or 0.0)
    if str(trade.get("status", "")).upper() == "OPEN":
        return round(realized + calc_trade_live_pnl(trade, current_price), 2)
    return round(realized, 2)


# -------- CUSTOM RISK / TARGET HELPERS --------
def distance_from_entry(entry, mode, value):
    """Convert a user risk/target value to a price distance.
    Pips uses the dashboard XAUUSD pip model. Percent uses % of entry price.
    """
    try:
        value = float(value)
        entry = float(entry)
    except Exception:
        return 0.0

    if str(mode).lower().startswith("percent") or str(mode).strip() == "%":
        return abs(entry * (value / 100.0))
    return abs(value * PIP)


def pips_from_distance(distance):
    try:
        return round(abs(float(distance)) / PIP, 1)
    except Exception:
        return 0.0


def calculate_trade_levels(entry, direction, sl_mode, sl_value, tp1_mode, tp1_value, tp2_mode, tp2_value, tp3_mode, tp3_value):
    entry = float(entry)
    direction = str(direction).upper()
    sl_distance = distance_from_entry(entry, sl_mode, sl_value)
    tp1_distance = distance_from_entry(entry, tp1_mode, tp1_value)
    tp2_distance = distance_from_entry(entry, tp2_mode, tp2_value)
    tp3_distance = distance_from_entry(entry, tp3_mode, tp3_value)

    if direction == "BUY":
        sl = entry - sl_distance
        tp1 = entry + tp1_distance
        tp2 = entry + tp2_distance
        tp3 = entry + tp3_distance
    else:
        sl = entry + sl_distance
        tp1 = entry - tp1_distance
        tp2 = entry - tp2_distance
        tp3 = entry - tp3_distance

    sl_pips = pips_from_distance(sl_distance)
    tp1_pips = pips_from_distance(tp1_distance)
    tp2_pips = pips_from_distance(tp2_distance)
    tp3_pips = pips_from_distance(tp3_distance)
    rr = round(tp3_pips / sl_pips, 2) if sl_pips > 0 else 0.0

    return {
        "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "sl_pips": sl_pips, "tp1_pips": tp1_pips, "tp2_pips": tp2_pips, "tp3_pips": tp3_pips,
        "risk_reward": rr,
    }


def trade_level_pips(trade, level_key):
    stored_key = f"{level_key}_pips"
    if stored_key in trade:
        try:
            return float(trade.get(stored_key) or 0.0)
        except Exception:
            pass
    try:
        return abs(float(trade.get(level_key, 0.0)) - float(trade.get("entry", 0.0))) / PIP
    except Exception:
        return 0.0


def realized_profit_for_level(trade, level_key):
    lot = trade_tp_lot(trade, level_key)
    return round(trade_level_pips(trade, level_key) * PIP_VALUE_PER_001 * (lot / 0.01), 2)


def max_trade_profit_value(trade):
    return round(
        realized_profit_for_level(trade, "tp1")
        + realized_profit_for_level(trade, "tp2")
        + realized_profit_for_level(trade, "tp3"),
        2,
    )


def remaining_lots_for_trade(trade):
    try:
        lots = float(trade.get("lots", 0.03) or 0.03)
    except Exception:
        lots = 0.03
    closed = 0.0
    if trade.get("tp1_hit"):
        closed += trade_tp_lot(trade, "tp1")
    if trade.get("tp2_hit"):
        closed += trade_tp_lot(trade, "tp2")
    if trade.get("tp3_hit"):
        closed += trade_tp_lot(trade, "tp3")
    return max(0.0, round(lots - closed, 2))


def stop_loss_total_pnl(trade):
    try:
        realized = float(trade.get("profit", 0.0) or 0.0)
        entry = float(trade.get("entry", 0.0) or 0.0)
        sl = float(trade.get("sl", 0.0) or 0.0)
        remaining_lots = remaining_lots_for_trade(trade)
        direction = str(trade.get("type", "")).upper()
        if remaining_lots <= 0 or entry <= 0 or sl <= 0:
            return round(realized, 2)
        if direction == "BUY":
            pips = (sl - entry) / PIP
        elif direction == "SELL":
            pips = (entry - sl) / PIP
        else:
            pips = 0.0
        floating_at_sl = pips * PIP_VALUE_PER_001 * (remaining_lots / 0.01)
        return round(realized + floating_at_sl, 2)
    except Exception:
        return round(float(trade.get("profit", 0.0) or 0.0), 2)


def stop_loss_total_pips(trade):
    try:
        entry = float(trade.get("entry", 0.0) or 0.0)
        sl = float(trade.get("sl", 0.0) or 0.0)
        direction = str(trade.get("type", "")).upper()
        if direction == "BUY":
            return round((sl - entry) / PIP, 1)
        if direction == "SELL":
            return round((entry - sl) / PIP, 1)
    except Exception:
        pass
    return 0.0


def trade_closed_result_by_profit(trade):
    """Classify closed trades by realized P/L, not only by the exit reason.

    This fixes TP1/TP2 partial winners that later close at SL.
    A trade that banked profit before SL should count as a win/partial win.
    """
    try:
        profit = float(trade.get("profit", 0.0) or 0.0)
    except Exception:
        profit = 0.0

    if profit > 0:
        if trade.get("tp3_hit"):
            return "FULL WIN"
        if trade.get("tp1_hit") or trade.get("tp2_hit"):
            return "PARTIAL WIN"
        return "WIN"
    if profit < 0:
        return "LOSS"
    return "BREAKEVEN"


def trade_realized_pips_total(trade):
    """Estimate total realized pips across the three 0.01-lot legs.

    TP1/TP2/TP3 add positive pips. If the remainder closes at SL,
    the SL pips are multiplied only by the remaining open legs.
    """
    try:
        pips = 0.0
        if trade.get("tp1_hit"):
            pips += trade_level_pips(trade, "tp1")
        if trade.get("tp2_hit"):
            pips += trade_level_pips(trade, "tp2")
        if trade.get("tp3_hit"):
            pips += trade_level_pips(trade, "tp3")

        if str(trade.get("exit_reason", trade.get("result", ""))).upper() in ["SL HIT", "STOP LOSS HIT"]:
            remaining_units = max(0, 3 - int(bool(trade.get("tp1_hit"))) - int(bool(trade.get("tp2_hit"))) - int(bool(trade.get("tp3_hit"))))
            if remaining_units > 0:
                pips += stop_loss_total_pips(trade) * remaining_units
        return round(float(pips), 1)
    except Exception:
        return round(float(trade.get("final_pips", 0.0) or 0.0), 1)


def finalize_sl_closed_trade(trade):
    """Finalize a trade that touched SL while preserving win/loss accuracy."""
    sl_total_pnl = stop_loss_total_pnl(trade)
    trade["profit"] = sl_total_pnl
    trade["final_pnl"] = sl_total_pnl
    trade["exit_reason"] = "SL HIT"
    trade["final_pips"] = trade_realized_pips_total(trade)
    trade["result"] = trade_closed_result_by_profit(trade)
    return sl_total_pnl, trade["final_pips"]

# -------- SIDEBAR --------
interval = st.sidebar.selectbox("Interval", ["5min", "15min", "1h"], index=index_for(["5min", "15min", "1h"], APP_SETTINGS.get("interval", "15min"), 1), key="setting_interval")
zone_threshold = st.sidebar.slider("Zone Sensitivity ($)", 1.0, 10.0, float(APP_SETTINGS.get("zone_threshold", 2.5)), key="setting_zone_threshold")
refresh_seconds = st.sidebar.selectbox("Refresh Rate", [5, 10, 15, 30, 60], index=index_for([5, 10, 15, 30, 60], int(APP_SETTINGS.get("refresh_seconds", 15) or 15), 2), key="setting_refresh_seconds")

# Sidebar sections are now toggle/accordion panels for cleaner control.
with st.sidebar.expander("📡 Live Data Feed", expanded=True):
    mt5_symbol = st.text_input("MT5 Symbol", value=str(APP_SETTINGS.get("mt5_symbol", MT5_SYMBOL_DEFAULT)), key="setting_mt5_symbol")
    use_mt5_primary = st.checkbox("Use MT5 live candles first", value=bool(APP_SETTINGS.get("use_mt5_primary", True)), key="setting_use_mt5_primary")
    use_api_backup = st.checkbox("Use TwelveData backup", value=bool(APP_SETTINGS.get("use_api_backup", True)), key="setting_use_api_backup")
    st.caption("If both live feeds fail, the dashboard loads the last saved local candle cache.")

with st.sidebar.expander("⚙️ Risk / TP Settings", expanded=False):
    st.caption("Choose pip distance or percent distance from entry. Defaults match the original system.")
    risk_col1, risk_col2 = st.columns([1, 1])
    with risk_col1:
        sl_mode = st.selectbox("SL Mode", ["Pips", "Percent"], index=index_for(["Pips", "Percent"], APP_SETTINGS.get("sl_mode", "Pips")), key="sl_mode")
        tp1_mode = st.selectbox("TP1 Mode", ["Pips", "Percent"], index=index_for(["Pips", "Percent"], APP_SETTINGS.get("tp1_mode", "Pips")), key="tp1_mode")
        tp2_mode = st.selectbox("TP2 Mode", ["Pips", "Percent"], index=index_for(["Pips", "Percent"], APP_SETTINGS.get("tp2_mode", "Pips")), key="tp2_mode")
        tp3_mode = st.selectbox("TP3 Mode", ["Pips", "Percent"], index=index_for(["Pips", "Percent"], APP_SETTINGS.get("tp3_mode", "Pips")), key="tp3_mode")
    with risk_col2:
        sl_value = st.number_input("SL Value", min_value=0.01, value=float(APP_SETTINGS.get("sl_value", INITIAL_SL_PIPS)), step=10.0, key="sl_value")
        tp1_value = st.number_input("TP1 Value", min_value=0.01, value=float(APP_SETTINGS.get("tp1_value", TP1_PIPS)), step=10.0, key="tp1_value")
        tp2_value = st.number_input("TP2 Value", min_value=0.01, value=float(APP_SETTINGS.get("tp2_value", TP2_PIPS)), step=10.0, key="tp2_value")
        tp3_value = st.number_input("TP3 Value", min_value=0.01, value=float(APP_SETTINGS.get("tp3_value", TP3_PIPS)), step=10.0, key="tp3_value")

    entry_lot = st.number_input(
        "Total Entry Lot",
        min_value=0.01,
        max_value=1.00,
        value=normalize_entry_lot(APP_SETTINGS.get("entry_lot", DEFAULT_TOTAL_LOT)),
        step=0.01,
        format="%.2f",
        key="entry_lot",
    )
    TOTAL_LOT = normalize_entry_lot(entry_lot)
    TP1_LOT, TP2_LOT, TP3_LOT = split_entry_lot(TOTAL_LOT)
    LOT_PER_TP = TP1_LOT
    st.caption(f"MT5 split: TP1 {format_lot(TP1_LOT)} lot | TP2 {format_lot(TP2_LOT)} lot | TP3 {format_lot(TP3_LOT)} lot")
    if TOTAL_LOT < 0.03:
        st.warning("Most MT5 brokers cannot split below 0.01 lot. 0.01 opens TP1 only; 0.02 opens TP1 and TP2 only.")

with st.sidebar.expander("🤖 MT5 Live Trading", expanded=False):
    mt5_live_trading = st.checkbox("Enable MT5 live/demo order execution", value=bool(APP_SETTINGS.get("mt5_live_trading", MT5_TRADING_ENABLED_DEFAULT)), key="setting_mt5_live_trading")
    mt5_trade_confirm = st.checkbox("I understand this can place real trades", value=bool(APP_SETTINGS.get("mt5_trade_confirm", False)), key="setting_mt5_trade_confirm")
    st.caption("Keep OFF until tested on demo. When enabled, each signal opens using your Total Entry Lot split across TP1/TP2/TP3.")

    if mt5_live_trading:
        _ok, _msg = safe_mt5_initialize()
        if _ok:
            st.success(_msg)
        else:
            st.error(_msg)

        acc = mt5.account_info() if mt5 is not None else None
        with st.expander("MT5 Bulletproof Diagnostics", expanded=False):
            st.write("Account:", acc)
            try:
                detected_symbol = auto_detect_xauusd_symbol(mt5_symbol)
                st.write("Detected Symbol:", detected_symbol)
                st.write("Symbol Status:", get_symbol_trade_status(detected_symbol))
            except Exception as exc:
                st.write("Diagnostics error:", exc)

with st.sidebar.expander("📲 Telegram Weekly Reports", expanded=False):
    enable_weekly_reports = st.checkbox(
        "Send weekly performance report + reset dashboard",
        value=bool(APP_SETTINGS.get("enable_weekly_reports", True)),
        key="setting_enable_weekly_reports",
    )
    st.caption("At the first app run of a new Monday trading week, last week's report is sent to Telegram, saved, and the visible dashboard resets. Open trades are carried forward.")

# Run weekly report/reset before rendering the new week dashboard.
run_weekly_report_and_reset(enable_weekly_reports)

# -------- LOAD DATA --------
data, data_source, active_symbol, data_messages = load_market_data(
    interval,
    mt5_symbol,
    use_mt5_primary=use_mt5_primary,
    use_api_backup=use_api_backup,
)

try:
    strategy_positions = sync_dashboard_trades_with_mt5(active_symbol or mt5_symbol) if mt5_live_trading else []
except Exception:
    strategy_positions = []
prune_runtime_state()

with st.sidebar.expander("Data Feed Status", expanded=False):
    st.write(f"Source: {data_source}")
    st.write(f"Symbol: {active_symbol}")
    for m in data_messages[-5:]:
        st.write(f"• {m}")
# -------- VALIDATION --------
if data.empty:
    st.error("❌ No data available yet. Open MT5, login, select the correct XAUUSD/GOLD symbol, then refresh.")
    with st.expander("Data feed details"):
        for m in data_messages:
            st.write(f"• {m}")
    st.stop()

if len(data) < MIN_CANDLES_REQUIRED:
    st.warning(f"⚠️ Only {len(data)} candles loaded. Chart will still show, but signals may be weaker until {MIN_CANDLES_REQUIRED}+ candles are available.")

# -------- PRICE --------
latest_price = float(data["Close"].iloc[-1])

# -------- MT5 BULLETPROOF EXECUTION TEST --------
if mt5_live_trading and mt5_trade_confirm:
    with st.expander("🧪 MT5 Execution Test / Diagnostics", expanded=False):
        st.write("Detected active symbol:", active_symbol or mt5_symbol)
        st.write("Symbol status:", get_symbol_trade_status(auto_detect_xauusd_symbol(active_symbol or mt5_symbol)))
        st.caption("Demo test opens the same structure as a real app signal using your Total Entry Lot and Risk / TP Settings.")
        test_col1, test_col2 = st.columns(2)

        def run_mt5_full_test(direction):
            test_trade = build_trade(direction, latest_price, source="MT5_TEST")
            test_trade["signal_key"] = f"MT5_TEST_{direction}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
            results = place_three_mt5_orders_for_trade(active_symbol or mt5_symbol, test_trade)
            test_trade["mt5_orders"] = results
            test_trade["mt5_executed"] = any(r.get("ok") for r in results)
            test_trade["mt5_order_tickets"] = [r.get("ticket") for r in results if r.get("ok") and r.get("ticket") is not None]
            test_trade["mt5_deals"] = [r.get("deal") for r in results if r.get("ok") and r.get("deal") is not None]
            test_trade["mt5_open_positions"] = len(test_trade["mt5_order_tickets"])
            test_trade["mt5_open_volume"] = round(sum(trade_tp_lot(test_trade, k) for k in ["tp1", "tp2", "tp3"] if trade_tp_lot(test_trade, k) > 0), 2)
            st.session_state.trade_log.append(test_trade)
            st.session_state.mt5_last_order_result = results
            save_runtime_state()

            ok_count = sum(1 for r in results if r.get("ok"))
            fail_count = len(results) - ok_count
            label = trade_label(test_trade)
            send_telegram(
                f"🧪 {label} MT5 TEST {direction} XAUUSD\n"
                f"Entry: {test_trade['entry']:.2f}\n"
                f"SL: {test_trade['sl']:.2f} ({test_trade.get('sl_pips', 0):.1f} pips)\n"
                f"TP1: {test_trade['tp1']:.2f} ({test_trade.get('tp1_pips', 0):.1f} pips)\n"
                f"TP2: {test_trade['tp2']:.2f} ({test_trade.get('tp2_pips', 0):.1f} pips)\n"
                f"TP3: {test_trade['tp3']:.2f} ({test_trade.get('tp3_pips', 0):.1f} pips)\n"
                f"Placed: {ok_count}/3 | Failed: {fail_count}/3"
            )
            return test_trade, results

        if test_col1.button(f"TEST BUY MT5 {format_lot(TOTAL_LOT)}", key="test_buy_mt5"):
            test_trade, results = run_mt5_full_test("BUY")
            st.success(f"{trade_label(test_trade)} test BUY sent as 3 orders using current Risk / TP settings.")
            st.write(results)

        if test_col2.button(f"TEST SELL MT5 {format_lot(TOTAL_LOT)}", key="test_sell_mt5"):
            test_trade, results = run_mt5_full_test("SELL")
            st.success(f"{trade_label(test_trade)} test SELL sent as 3 orders using current Risk / TP settings.")
            st.write(results)

# -------- SESSION --------
def get_session():
    hour = datetime.now(UTC).hour

    if 21 <= hour or hour < 1:
        return "SYDNEY"
    elif 1 <= hour < 7:
        return "ASIA"
    elif 7 <= hour < 9:
        return "LONDON OPEN"
    elif 9 <= hour < 13:
        return "LONDON"
    elif 13 <= hour < 16:
        return "NY OPEN"
    else:
        return "NEW YORK"

session = get_session()

# -------- SWINGS --------
def detect_swings(df, lookback=3):
    highs, lows = [], []
    h = df["High"].values
    l = df["Low"].values

    for i in range(lookback, len(df) - lookback):
        if h[i] == np.max(h[i-lookback:i+lookback+1]):
            highs.append((i, float(h[i])))
        if l[i] == np.min(l[i-lookback:i+lookback+1]):
            lows.append((i, float(l[i])))

    return highs, lows

swing_highs, swing_lows = detect_swings(data)

# -------- EMA TREND --------
data["EMA9"] = data["Close"].ewm(span=9).mean()
data["EMA21"] = data["Close"].ewm(span=21).mean()
data["EMA20"] = data["Close"].ewm(span=20).mean()
data["EMA50"] = data["Close"].ewm(span=50).mean()

ema9 = float(data["EMA9"].iloc[-1])
ema21 = float(data["EMA21"].iloc[-1])
ema20 = float(data["EMA20"].iloc[-1])
ema50 = float(data["EMA50"].iloc[-1])

trend = "bullish" if ema20 > ema50 else "bearish" if ema20 < ema50 else "range"
ema_signal = "BUY" if ema9 > ema21 else "SELL" if ema9 < ema21 else "WAIT"

# -------- EMA CROSS DETECTION --------
prev_ema9 = float(data["EMA9"].iloc[-2])
prev_ema21 = float(data["EMA21"].iloc[-2])

ema_cross = "NONE"

if prev_ema9 <= prev_ema21 and ema9 > ema21:
    ema_cross = "BULLISH CROSS"
elif prev_ema9 >= prev_ema21 and ema9 < ema21:
    ema_cross = "BEARISH CROSS"

# -------- LEVELS --------
resistance = max([h[1] for h in swing_highs[-5:]]) if swing_highs else latest_price
support = min([l[1] for l in swing_lows[-5:]]) if swing_lows else latest_price

# -------- SWEEP --------
def detect_sweep(df, highs, lows):
    last = df.iloc[-1]

    if highs and last["High"] > highs[-1][1]:
        return "sell"

    if lows and last["Low"] < lows[-1][1]:
        return "buy"

    return "none"

sweep_signal = detect_sweep(data, swing_highs, swing_lows)

# -------- ASIA SCALPING --------
def asia_scalp(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]

    if last["Close"] > prev["High"]:
        return "BUY"

    if last["Close"] < prev["Low"]:
        return "SELL"

    return "WAIT"

# -------- SNIPER ENTRY --------
def sniper_entry(df, sweep):
    last = df.iloc[-1]
    prev = df.iloc[-2]

    if sweep == "buy" and last["Close"] > prev["High"]:
        return "BUY"

    if sweep == "sell" and last["Close"] < prev["Low"]:
        return "SELL"

    return "WAIT"

# -------- ENTRY LOGIC --------
entry_signal = sniper_entry(data, sweep_signal)

if session in ["SYDNEY", "ASIA"]:
    entry_signal = asia_scalp(data)
elif "LONDON" in session or "NY" in session:
    entry_signal = sniper_entry(data, sweep_signal)

# EMA 9/21 FILTER
if entry_signal == "BUY" and ema_signal != "BUY":
    entry_signal = "WAIT"

if entry_signal == "SELL" and ema_signal != "SELL":
    entry_signal = "WAIT"

# EMA CROSS BOOST
if ema_cross == "BULLISH CROSS":
    entry_signal = "BUY"

if ema_cross == "BEARISH CROSS":
    entry_signal = "SELL"

# -------- VOLATILITY FILTER --------
range_size = float(data["High"].iloc[-1] - data["Low"].iloc[-1])

if session in ["SYDNEY", "ASIA"] and range_size < 0.6:
    entry_signal = "WAIT"

if session not in ["SYDNEY", "ASIA"] and range_size < 1.2:
    entry_signal = "WAIT"

# -------- CREATE TRADE --------
trade = None

if entry_signal in ["BUY", "SELL"]:
    entry = latest_price
    trade = build_trade(entry_signal, entry, source="SIGNAL")

# -------- LOG NEW TRADE --------
current_candle_time = data.index[-1]
try:
    candle_key = pd.Timestamp(current_candle_time).strftime("%Y%m%d%H%M")
except Exception:
    candle_key = datetime.now().strftime("%Y%m%d%H%M")

signal_key = f"{entry_signal}_{interval}_{candle_key}" if entry_signal != "WAIT" else "WAIT"
already_logged_signal = any(t.get("signal_key") == signal_key for t in st.session_state.trade_log)
same_direction_open = False  # memory-optimized final: allow multiple same-direction trades; duplicate candle key still protects refresh repeats

if trade:
    ensure_trade_label(trade)
    trade["signal_key"] = signal_key
    trade["mt5_executed"] = False
    trade["mt5_orders"] = []
    trade["mt5_open_positions"] = 0
    trade["mt5_open_volume"] = 0.0
    trade["mt5_live_profit"] = 0.0

if entry_signal != "WAIT" and not already_logged_signal:

    st.toast(f"🚨 NEW {entry_signal} SIGNAL")
    play_alert_sound("entry")

    if trade:
        st.session_state.trade_log.append(trade)

        send_trade_entry_telegram(trade)

        # MT5 EXECUTION: duplicate-safe. One signal candle = one execution attempt.
        if mt5_live_trading and mt5_trade_confirm:
            execute_mt5_for_trade(active_symbol or mt5_symbol, trade, entry_signal, allow_retry_failed=False)

    st.session_state.last_signal = entry_signal
    save_runtime_state()
elif entry_signal == "WAIT":
    st.session_state.last_signal = None

# MT5 catch-up: if a signal was already logged but MT5 was armed after the log,
# or Streamlit reran before the original execution block completed, execute it now.
try:
    execute_pending_mt5_signal_trades(active_symbol or mt5_symbol)
except Exception as exc:
    st.session_state.mt5_last_order_result = [{"ok": False, "error": f"Pending MT5 execution error: {exc}"}]

# -------- TRADE MANAGEMENT --------
for t in st.session_state.trade_log:
    if t["status"] == "OPEN":
        # MT5_SYNC rows are mirror rows from terminal positions. Do not close them using candle logic.
        # They stay aligned by sync_dashboard_trades_with_mt5().
        if t.get("source") == "MT5_SYNC":
            continue
        # Prevent false instant TP/SL closes from the same candle that created the trade.
        # The current candle high/low may include price action from before the entry/test order existed.
        current_candle_iso = pd.Timestamp(data.index[-1]).isoformat()
        if t.get("open_candle_time") == current_candle_iso:
            continue
        label = ensure_trade_label(t)

        high = float(data["High"].iloc[-1])
        low = float(data["Low"].iloc[-1])

        trade_id = t.get("id", "trade")

        if t["type"] == "BUY":

            if low <= t["sl"]:
                t["status"] = "CLOSED"
                sl_total_pnl, sl_total_pips = finalize_sl_closed_trade(t)
                event_key = f"{trade_id}_SL"
                if event_key not in st.session_state.alerted_events:
                    play_alert_sound("sl")
                    send_telegram(
                        f"❌ {label} BUY STOP LOSS HIT\n"
                        f"SL: {t['sl']:.2f}\n"
                        f"Final P/L: ${sl_total_pnl:.2f}\n"
                        f"Final Pips: {sl_total_pips:.1f}\n"
                        f"Profit/Loss locked: ${sl_total_pnl:.2f}"
                    )
                    st.session_state.alerted_events.add(event_key)

            else:
                if not t["tp1_hit"] and high >= t["tp1"]:
                    t["tp1_hit"] = True
                    t["profit"] += realized_profit_for_level(t, "tp1")

                    event_key = f"{trade_id}_TP1"
                    if mt5_live_trading and mt5_trade_confirm:
                        close_result = close_mt5_positions_by_label(active_symbol or mt5_symbol, t["type"], mt5_trade_order_label(t, "TP1"), event_key)
                        t["mt5_tp1_close"] = close_result
                        if close_result.get("closed", 0):
                            send_telegram(f"✅ {label} MT5 TP1 partial close completed on {close_result.get('closed')} position(s)")
                    if event_key not in st.session_state.alerted_events:
                        play_alert_sound("tp")
                        send_telegram(
                            f"✅ {label} BUY TP1 HIT\n"
                            f"TP1: {t['tp1']:.2f}\n"
                            f"Closed: {format_lot(trade_tp_lot(t, 'tp1'))} lot\n"
                            f"Profit: ${realized_profit_for_level(t, 'tp1'):.2f}"
                        )
                        st.session_state.alerted_events.add(event_key)

                if not t["tp2_hit"] and high >= t["tp2"]:
                    t["tp2_hit"] = True
                    t["sl"] = t["entry"]
                    if mt5_live_trading and mt5_trade_confirm:
                        modified = modify_mt5_sl_for_strategy_positions(active_symbol or mt5_symbol, t["type"], t["sl"], f"{trade_id}_BE")
                        if modified:
                            send_telegram(f"🔐 {label} MT5 SL moved to Break Even on {modified} {t['type']} position(s)")
                    t["profit"] += realized_profit_for_level(t, "tp2")

                    event_key = f"{trade_id}_TP2"
                    if mt5_live_trading and mt5_trade_confirm:
                        close_result = close_mt5_positions_by_label(active_symbol or mt5_symbol, t["type"], mt5_trade_order_label(t, "TP2"), event_key)
                        t["mt5_tp2_close"] = close_result
                        if close_result.get("closed", 0):
                            send_telegram(f"✅ {label} MT5 TP2 partial close completed on {close_result.get('closed')} position(s)")
                    if event_key not in st.session_state.alerted_events:
                        play_alert_sound("tp")
                        send_telegram(
                            f"🔥 {label} BUY TP2 HIT\n"
                            f"TP2: {t['tp2']:.2f}\n"
                            f"Closed: {format_lot(trade_tp_lot(t, 'tp2'))} lot\n"
                            f"Profit: ${realized_profit_for_level(t, 'tp2'):.2f}\n"
                            f"SL moved to Break Even"
                        )
                        st.session_state.alerted_events.add(event_key)

                if t["tp2_hit"]:
                    new_sl = t["entry"] + (LOCK_PROFIT_PIPS * PIP)
                    if t["sl"] < new_sl:
                        t["sl"] = new_sl
                        t["locked_sl"] = True
                        if mt5_live_trading and mt5_trade_confirm:
                            modified = modify_mt5_sl_for_strategy_positions(active_symbol or mt5_symbol, t["type"], t["sl"], f"{trade_id}_LOCK400")
                            if modified:
                                send_telegram(f"🔐 {label} MT5 SL locked on {modified} {t['type']} position(s) at {t['sl']:.2f}")

                if not t["tp3_hit"] and high >= t["tp3"]:
                    t["tp3_hit"] = True
                    t["profit"] += realized_profit_for_level(t, "tp3")
                    t["final_pnl"] = round(float(t.get("profit", 0.0) or 0.0), 2)
                    t["final_pips"] = round(float(t.get("tp1_pips", trade_level_pips(t, "tp1"))) + float(t.get("tp2_pips", trade_level_pips(t, "tp2"))) + float(t.get("tp3_pips", trade_level_pips(t, "tp3"))), 1)
                    t["status"] = "CLOSED"
                    t["result"] = "FULL WIN"

                    event_key = f"{trade_id}_TP3"
                    if mt5_live_trading and mt5_trade_confirm:
                        close_result = close_mt5_positions_by_label(active_symbol or mt5_symbol, t["type"], mt5_trade_order_label(t, "TP3"), event_key)
                        t["mt5_tp3_close"] = close_result
                    if event_key not in st.session_state.alerted_events:
                        play_alert_sound("tp")
                        send_telegram(
                            f"🏁 {label} BUY TP3 HIT — TRADE CLOSED\n"
                            f"TP3: {t['tp3']:.2f}\n"
                            f"Closed final: {format_lot(trade_tp_lot(t, 'tp3'))} lot\n"
                            f"Total Profit: ${t['profit']:.2f}"
                        )
                        st.session_state.alerted_events.add(event_key)

        if t["type"] == "SELL":

            if high >= t["sl"]:
                t["status"] = "CLOSED"
                sl_total_pnl, sl_total_pips = finalize_sl_closed_trade(t)
                event_key = f"{trade_id}_SL"
                if event_key not in st.session_state.alerted_events:
                    play_alert_sound("sl")
                    send_telegram(
                        f"❌ {label} SELL STOP LOSS HIT\n"
                        f"SL: {t['sl']:.2f}\n"
                        f"Final P/L: ${sl_total_pnl:.2f}\n"
                        f"Final Pips: {sl_total_pips:.1f}\n"
                        f"Profit/Loss locked: ${sl_total_pnl:.2f}"
                    )
                    st.session_state.alerted_events.add(event_key)

            else:
                if not t["tp1_hit"] and low <= t["tp1"]:
                    t["tp1_hit"] = True
                    t["profit"] += realized_profit_for_level(t, "tp1")

                    event_key = f"{trade_id}_TP1"
                    if mt5_live_trading and mt5_trade_confirm:
                        close_result = close_mt5_positions_by_label(active_symbol or mt5_symbol, t["type"], mt5_trade_order_label(t, "TP1"), event_key)
                        t["mt5_tp1_close"] = close_result
                        if close_result.get("closed", 0):
                            send_telegram(f"✅ {label} MT5 TP1 partial close completed on {close_result.get('closed')} position(s)")
                    if event_key not in st.session_state.alerted_events:
                        play_alert_sound("tp")
                        send_telegram(
                            f"✅ {label} SELL TP1 HIT\n"
                            f"TP1: {t['tp1']:.2f}\n"
                            f"Closed: {format_lot(trade_tp_lot(t, 'tp1'))} lot\n"
                            f"Profit: ${realized_profit_for_level(t, 'tp1'):.2f}"
                        )
                        st.session_state.alerted_events.add(event_key)

                if not t["tp2_hit"] and low <= t["tp2"]:
                    t["tp2_hit"] = True
                    t["sl"] = t["entry"]
                    if mt5_live_trading and mt5_trade_confirm:
                        modified = modify_mt5_sl_for_strategy_positions(active_symbol or mt5_symbol, t["type"], t["sl"], f"{trade_id}_BE")
                        if modified:
                            send_telegram(f"🔐 {label} MT5 SL moved to Break Even on {modified} {t['type']} position(s)")
                    t["profit"] += realized_profit_for_level(t, "tp2")

                    event_key = f"{trade_id}_TP2"
                    if mt5_live_trading and mt5_trade_confirm:
                        close_result = close_mt5_positions_by_label(active_symbol or mt5_symbol, t["type"], mt5_trade_order_label(t, "TP2"), event_key)
                        t["mt5_tp2_close"] = close_result
                        if close_result.get("closed", 0):
                            send_telegram(f"✅ {label} MT5 TP2 partial close completed on {close_result.get('closed')} position(s)")
                    if event_key not in st.session_state.alerted_events:
                        play_alert_sound("tp")
                        send_telegram(
                            f"🔥 {label} SELL TP2 HIT\n"
                            f"TP2: {t['tp2']:.2f}\n"
                            f"Closed: {format_lot(trade_tp_lot(t, 'tp2'))} lot\n"
                            f"Profit: ${realized_profit_for_level(t, 'tp2'):.2f}\n"
                            f"SL moved to Break Even"
                        )
                        st.session_state.alerted_events.add(event_key)

                if t["tp2_hit"]:
                    new_sl = t["entry"] - (LOCK_PROFIT_PIPS * PIP)
                    if t["sl"] > new_sl:
                        t["sl"] = new_sl
                        t["locked_sl"] = True
                        if mt5_live_trading and mt5_trade_confirm:
                            modified = modify_mt5_sl_for_strategy_positions(active_symbol or mt5_symbol, t["type"], t["sl"], f"{trade_id}_LOCK400")
                            if modified:
                                send_telegram(f"🔐 {label} MT5 SL locked on {modified} {t['type']} position(s) at {t['sl']:.2f}")

                if not t["tp3_hit"] and low <= t["tp3"]:
                    t["tp3_hit"] = True
                    t["profit"] += realized_profit_for_level(t, "tp3")
                    t["final_pnl"] = round(float(t.get("profit", 0.0) or 0.0), 2)
                    t["final_pips"] = round(float(t.get("tp1_pips", trade_level_pips(t, "tp1"))) + float(t.get("tp2_pips", trade_level_pips(t, "tp2"))) + float(t.get("tp3_pips", trade_level_pips(t, "tp3"))), 1)
                    t["status"] = "CLOSED"
                    t["result"] = "FULL WIN"

                    event_key = f"{trade_id}_TP3"
                    if mt5_live_trading and mt5_trade_confirm:
                        close_result = close_mt5_positions_by_label(active_symbol or mt5_symbol, t["type"], mt5_trade_order_label(t, "TP3"), event_key)
                        t["mt5_tp3_close"] = close_result
                    if event_key not in st.session_state.alerted_events:
                        play_alert_sound("tp")
                        send_telegram(
                            f"🏁 {label} SELL TP3 HIT — TRADE CLOSED\n"
                            f"TP3: {t['tp3']:.2f}\n"
                            f"Closed final: {format_lot(trade_tp_lot(t, 'tp3'))} lot\n"
                            f"Total Profit: ${t['profit']:.2f}"
                        )
                        st.session_state.alerted_events.add(event_key)

# Sync dashboard trades with live MT5 positions so the app and terminal stay aligned.
mt5_synced_positions = []
if mt5_live_trading:
    mt5_synced_positions = sync_dashboard_trades_with_mt5(active_symbol or mt5_symbol)

# Refresh live P/L fields on every rerun so saved trades and tables show current floating values.
for _trade in st.session_state.trade_log:
    if _trade.get("status") == "OPEN":
        _trade["live_pips"] = calc_trade_live_pips(_trade, latest_price)
        _trade["live_pnl"] = calc_trade_live_pnl(_trade, latest_price)
        _trade["total_pnl"] = calc_trade_total_pnl(_trade, latest_price)
        _trade["current_price"] = round(float(latest_price), 2)
    else:
        _trade["live_pips"] = 0.0
        _trade["live_pnl"] = 0.0
        _trade["total_pnl"] = round(float(_trade.get("profit", 0.0) or 0.0), 2)

save_runtime_state()

# -------- UI --------
# -------- PRO TRADING TERMINAL UI STYLE --------
st.markdown("""
<style>
:root{
  --trdr-bg:#0b1220;
  --trdr-bg2:#111827;
  --trdr-white:#ffffff;
  --trdr-soft:#f8fafc;
  --trdr-card:#f1f5f9;
  --trdr-line:#e2e8f0;
  --trdr-text:#0f172a;
  --trdr-muted:#64748b;
  --trdr-green:#16a34a;
  --trdr-green-bg:#dcfce7;
  --trdr-red:#dc2626;
  --trdr-red-bg:#fee2e2;
  --trdr-blue:#0284c7;
  --trdr-blue-bg:#e0f2fe;
  --trdr-amber:#f59e0b;
  --trdr-shadow:0 20px 50px rgba(2,6,23,.28);
}
html, body, [data-testid="stAppViewContainer"], [data-testid="stApp"]{
  background:#ebebeb!important;
  color:#0f172a!important;
}
[data-testid="stHeader"]{background:rgba(235,235,235,.86)!important;backdrop-filter:blur(12px)}
[data-testid="stSidebar"]{background:#747679!important;color:#ffffff!important;border-right:1px solid rgba(15,23,42,.12)}
[data-testid="stSidebar"] *{color:#ffffff!important}
.block-container{padding-top:1.2rem;max-width:1640px;color:#0f172a!important}
h1{font-weight:950!important;letter-spacing:-.035em;color:#0f172a!important}
hr{border-color:rgba(255,255,255,.12)!important}
/* Toggle labels on the dark background */
[data-testid="stToggle"] label,[data-testid="stToggle"] p,[data-testid="stCheckbox"] label,[data-testid="stCheckbox"] p{
  color:#313030!important;font-weight:900!important;font-size:16px!important;
}
[data-testid="stToggle"] [role="switch"]{filter:drop-shadow(0 4px 12px rgba(0,0,0,.3))}
/* Main white section panels */
.trdr-terminal-wrap{
  background:#ffffff!important;
  color:var(--trdr-text)!important;
  border:1px solid rgba(226,232,240,.95);
  border-radius:24px;
  padding:22px 22px 24px 22px;
  margin:16px 0 22px 0;
  box-shadow:var(--trdr-shadow);
  overflow:hidden;
}
.trdr-terminal-wrap *{color:var(--trdr-text)!important}
.trdr-terminal-title{
  font-size:26px!important;
  font-weight:950!important;
  letter-spacing:-.02em;
  color:var(--trdr-text)!important;
  margin:0 0 18px 0!important;
  display:flex;align-items:center;gap:9px;
}
.trdr-terminal-wrap [data-testid="stMetric"]{
  background:var(--trdr-soft)!important;
  border:1px solid var(--trdr-line)!important;
  border-radius:18px!important;
  padding:14px 16px!important;
  min-height:104px!important;
  box-shadow:0 8px 18px rgba(15,23,42,.06)!important;
}
.trdr-terminal-wrap [data-testid="stMetricLabel"] p,
.trdr-terminal-wrap [data-testid="stMetricLabel"]{
  color:var(--trdr-muted)!important;
  font-weight:900!important;
  font-size:13px!important;
  text-transform:uppercase!important;
  letter-spacing:.04em!important;
}
.trdr-terminal-wrap [data-testid="stMetricValue"],
.trdr-terminal-wrap [data-testid="stMetricValue"] div{
  color:var(--trdr-text)!important;
  font-size:34px!important;
  font-weight:950!important;
  letter-spacing:-.03em!important;
}
.trdr-terminal-wrap [data-testid="stMetricDelta"],
.trdr-terminal-wrap [data-testid="stMetricDelta"] div{
  font-weight:900!important;
}
.trdr-terminal-wrap .stAlert{
  background:#ecfdf5!important;
  color:#047857!important;
  border:1px solid #bbf7d0!important;
  border-radius:14px!important;
}
.trdr-terminal-wrap .stAlert *{color:inherit!important}
.trdr-terminal-wrap [data-testid="stDataFrame"], .trdr-terminal-wrap .stDataFrame{
  background:#ffffff!important;border-radius:16px!important;overflow:hidden!important;border:1px solid var(--trdr-line)!important;
}
.trdr-terminal-wrap .element-container:has([data-testid="stDataFrame"]){background:#ffffff!important;border-radius:16px!important}
/* Trade setup cards */
.trdr-setup-grid{display:grid;grid-template-columns:repeat(5,minmax(130px,1fr));gap:12px;margin:6px 0 14px 0}
.trdr-card{
  border-radius:18px;padding:14px 16px;min-height:86px;
  border:1px solid var(--trdr-line);background:var(--trdr-soft);
  box-shadow:0 10px 24px rgba(15,23,42,.07);
}
.trdr-card .label{font-size:12px;text-transform:uppercase;letter-spacing:.09em;color:var(--trdr-muted)!important;margin-bottom:8px;font-weight:950}
.trdr-card .value{font-size:30px;font-weight:950;line-height:1;color:var(--trdr-text)!important;letter-spacing:-.03em}
.trdr-green{border-left:6px solid var(--trdr-green);background:linear-gradient(180deg,#f8fafc,#ecfdf5)}.trdr-green .value{color:var(--trdr-green)!important}
.trdr-red{border-left:6px solid var(--trdr-red);background:linear-gradient(180deg,#f8fafc,#fef2f2)}.trdr-red .value{color:var(--trdr-red)!important}
.trdr-blue{border-left:6px solid var(--trdr-blue);background:linear-gradient(180deg,#f8fafc,#eff6ff)}.trdr-blue .value{color:var(--trdr-blue)!important}
.trdr-neutral{border-left:6px solid #64748b}
.trdr-small-grid{display:grid;grid-template-columns:repeat(4,minmax(140px,1fr));gap:12px;margin:10px 0 12px 0}
/* Open trade cards as white premium cards */
.trdr-open-card{
  border:1px solid var(--trdr-line);border-radius:24px;padding:22px 22px 20px 22px;margin:16px 0 22px 0;
  background:#ffffff!important;color:var(--trdr-text)!important;box-shadow:var(--trdr-shadow);
}
.trdr-open-card *{color:var(--trdr-text)!important}
.trdr-open-title{font-size:27px;font-weight:950;margin-bottom:18px;color:var(--trdr-text)!important;letter-spacing:-.025em}
.trdr-side-badge{font-size:12px;letter-spacing:.08em;text-transform:uppercase;padding:5px 10px;border-radius:999px;margin-left:10px;border:1px solid rgba(15,23,42,.10);font-weight:950;vertical-align:middle}
.trdr-buy{background:var(--trdr-green-bg);color:#15803d!important}.trdr-sell{background:var(--trdr-red-bg);color:#b91c1c!important}
.trdr-open-grid{display:grid;grid-template-columns:repeat(6,minmax(140px,1fr));gap:14px}
.trdr-mini-metric{border:1px solid var(--trdr-line);background:var(--trdr-soft);border-radius:18px;padding:15px 15px;min-height:98px;box-shadow:0 9px 20px rgba(15,23,42,.06)}
.trdr-mini-metric .label{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--trdr-muted)!important;font-weight:950;margin-bottom:9px}
.trdr-mini-metric .value{font-size:31px;font-weight:950;line-height:1.05;color:var(--trdr-text)!important;letter-spacing:-.035em}
.trdr-profit-pos{color:var(--trdr-green)!important}.trdr-profit-neg{color:var(--trdr-red)!important}
.trdr-caption{font-size:13px;color:#475569!important;margin-top:10px;line-height:1.45;font-weight:650}
.trdr-chip{display:inline-block;font-size:12px;margin-right:8px;margin-top:9px;padding:6px 9px;border-radius:999px;border:1px solid var(--trdr-line);background:#f1f5f9;color:#334155!important;font-weight:800}
.trdr-locked{font-size:13px;margin-top:8px;padding:9px 12px;border-radius:14px;background:#dcfce7;color:#15803d!important;border:1px solid #86efac;display:inline-block;font-weight:900}
.trdr-hidden-note{border:1px dashed rgba(148,163,184,.45);border-radius:16px;padding:14px;margin:10px 0;background:#ffffff;color:var(--trdr-text)!important;box-shadow:0 10px 24px rgba(2,6,23,.14);font-weight:800}
.trdr-hidden-note *{color:var(--trdr-text)!important}
/* Make normal markdown inside white panels readable */
.trdr-terminal-wrap p,.trdr-terminal-wrap span,.trdr-terminal-wrap div{color:var(--trdr-text)!important}
.trdr-terminal-wrap small,.trdr-terminal-wrap .caption{color:var(--trdr-muted)!important}
/* Expander polish */
[data-testid="stExpander"]{border-radius:16px!important;border:1px solid rgba(148,163,184,.18)!important;overflow:hidden!important;background:rgba(15,23,42,.35)!important}
[data-testid="stExpander"] summary p{color:#ffffff!important;font-weight:900!important}
/* Dataframe readability globally */
.stDataFrame, [data-testid="stDataFrame"]{border-radius:16px!important;overflow:hidden!important}
@media(max-width:1250px){.trdr-open-grid{grid-template-columns:repeat(3,minmax(150px,1fr))}.trdr-setup-grid{grid-template-columns:repeat(3,minmax(140px,1fr))}.trdr-small-grid{grid-template-columns:repeat(2,minmax(140px,1fr))}}
@media(max-width:760px){.trdr-open-grid,.trdr-setup-grid,.trdr-small-grid{grid-template-columns:1fr}.trdr-card .value,.trdr-mini-metric .value{font-size:25px}.trdr-terminal-title,.trdr-open-title{font-size:22px!important}}


/* -------- FINAL VISIBILITY + SIDEBAR TOGGLE POLISH -------- */
[data-testid="stSidebar"]{background:#747679!important;color:#ffffff!important;border-right:1px solid rgba(148,163,184,.25)!important;}
[data-testid="stSidebar"] *{color:#ffffff!important;opacity:1!important;}
[data-testid="stSidebar"] label,[data-testid="stSidebar"] p,[data-testid="stSidebar"] span,[data-testid="stSidebar"] div{color:#ffffff!important;}
[data-testid="stSidebar"] input,[data-testid="stSidebar"] textarea,[data-testid="stSidebar"] select{background:#ffffff!important;color:#111827!important;border:1px solid rgba(255,255,255,.55)!important;border-radius:10px!important;}
[data-testid="stSidebar"] [data-baseweb="select"] *{color:#111827!important;background:#ffffff!important;}
[data-testid="stSidebar"] [data-testid="stExpander"]{background:rgba(255,255,255,.12)!important;border:1px solid rgba(255,255,255,.35)!important;border-radius:16px!important;margin-bottom:12px!important;box-shadow:0 8px 22px rgba(0,0,0,.18)!important;}
[data-testid="stSidebar"] [data-testid="stExpander"] summary p{color:#ffffff!important;font-weight:950!important;font-size:15px!important;}
[data-testid="stSidebar"] .stAlert{background:rgba(22,163,74,.16)!important;border:1px solid rgba(34,197,94,.35)!important;border-radius:12px!important;}
[data-testid="stSidebar"] .stAlert *{color:#ffffff!important;}

.trdr-market-wrap{background:linear-gradient(135deg,#0b1220 0%,#111827 55%,#17233a 100%)!important;color:#ffffff!important;border:1px solid rgba(148,163,184,.22);border-radius:24px;padding:22px 22px 24px 22px;margin:16px 0 22px 0;box-shadow:0 20px 50px rgba(2,6,23,.35);overflow:hidden;}
.trdr-market-wrap *{color:#ffffff!important;opacity:1!important;}
.trdr-market-wrap [data-testid="stMetric"]{background:rgba(15,23,42,.74)!important;border:1px solid rgba(148,163,184,.22)!important;border-radius:18px!important;padding:14px 16px!important;min-height:104px!important;box-shadow:0 8px 18px rgba(0,0,0,.18)!important;}
.trdr-market-wrap [data-testid="stMetricLabel"] p,.trdr-market-wrap [data-testid="stMetricLabel"]{color:#cbd5e1!important;font-weight:900!important;font-size:13px!important;text-transform:uppercase!important;letter-spacing:.04em!important;}
.trdr-market-wrap [data-testid="stMetricValue"],.trdr-market-wrap [data-testid="stMetricValue"] div{color:#ffffff!important;font-size:34px!important;font-weight:950!important;letter-spacing:-.03em!important;}
.trdr-market-wrap .stAlert{background:rgba(16,185,129,.17)!important;border:1px solid rgba(16,185,129,.28)!important;border-radius:14px!important;}
.trdr-market-wrap .stAlert *{color:#86efac!important;font-weight:800!important;}
.trdr-market-wrap [data-testid="stCaptionContainer"],.trdr-market-wrap [data-testid="stCaptionContainer"] *{color:#e5e7eb!important;font-weight:750!important;}


/* Custom Market Info grid, always readable on dark market panel */
.trdr-market-source{color:#e5e7eb!important;font-weight:800;margin-bottom:14px;font-size:14px;}
.trdr-market-status{border-radius:14px;padding:13px 16px;margin:10px 0 18px 0;font-weight:900;}
.trdr-market-status.live{background:rgba(16,185,129,.17);border:1px solid rgba(16,185,129,.28);color:#86efac!important;}
.trdr-market-status.cache{background:rgba(245,158,11,.16);border:1px solid rgba(245,158,11,.30);color:#fde68a!important;}
.trdr-market-grid{display:grid;grid-template-columns:repeat(7,minmax(120px,1fr));gap:14px;margin-top:12px;}
.trdr-market-tile{background:rgba(15,23,42,.74);border:1px solid rgba(148,163,184,.22);border-radius:18px;padding:15px 16px;min-height:96px;box-shadow:0 8px 18px rgba(0,0,0,.18);}
.trdr-market-label{color:#cbd5e1!important;font-size:12px;font-weight:950;text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px;}
.trdr-market-value{color:#ffffff!important;font-size:32px;font-weight:950;line-height:1;letter-spacing:-.03em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
@media(max-width:1250px){.trdr-market-grid{grid-template-columns:repeat(3,minmax(120px,1fr));}}
@media(max-width:760px){.trdr-market-grid{grid-template-columns:1fr}.trdr-market-value{font-size:26px}}


/* Performance dashboard pure HTML panel - keeps all details inside the white area */
.trdr-performance-panel{padding:24px 26px 28px 26px!important;}
.trdr-performance-panel .trdr-terminal-title{margin-bottom:22px!important;}
.trdr-perf-grid{display:grid;gap:16px;margin:16px 0;}
.trdr-perf-grid-five{grid-template-columns:repeat(5,minmax(120px,1fr));}
.trdr-perf-grid-three{grid-template-columns:repeat(3,minmax(170px,1fr));}
.trdr-perf-tile{background:#f8fafc;border:1px solid #e2e8f0;border-radius:18px;padding:16px 18px;min-height:104px;box-shadow:0 8px 18px rgba(15,23,42,.06);}
.trdr-perf-label{font-size:13px;font-weight:900;text-transform:uppercase;letter-spacing:.04em;color:#64748b!important;margin-bottom:10px;}
.trdr-perf-value{font-size:36px;font-weight:950;letter-spacing:-.035em;line-height:1.05;color:#0f172a!important;}
.trdr-perf-delta{display:inline-block;margin-top:10px;font-size:13px;font-weight:900;padding:5px 9px;border-radius:999px;}
.trdr-perf-delta-pos{background:#dcfce7;color:#16a34a!important;}
.trdr-perf-delta-neg{background:#fee2e2;color:#dc2626!important;}
.trdr-performance-panel .trdr-profit-pos{color:#16a34a!important;}
.trdr-performance-panel .trdr-profit-neg{color:#dc2626!important;}
@media(max-width:1200px){.trdr-perf-grid-five{grid-template-columns:repeat(3,minmax(120px,1fr));}.trdr-perf-grid-three{grid-template-columns:repeat(2,minmax(160px,1fr));}}
@media(max-width:760px){.trdr-perf-grid-five,.trdr-perf-grid-three{grid-template-columns:1fr}.trdr-perf-value{font-size:30px}}

</style>
""", unsafe_allow_html=True)

market_status_class = "live" if data_source in ["MT5 LIVE", "TWELVEDATA LIVE"] else "cache"
market_status_text = "✅ Live chart data loaded from " + str(data_source) if data_source in ["MT5 LIVE", "TWELVEDATA LIVE"] else "⚠️ Live data unavailable — showing last saved chart data."

st.markdown(f"""
<div class="trdr-market-wrap">
  <div class="trdr-terminal-title">📊 Market Info</div>
  <div class="trdr-market-source"><b>Data Source:</b> {data_source} &nbsp;|&nbsp; <b>Symbol:</b> {active_symbol} &nbsp;|&nbsp; <b>Candles:</b> {len(data)}</div>
  <div class="trdr-market-status {market_status_class}">{market_status_text}</div>
  <div class="trdr-market-grid">
    <div class="trdr-market-tile"><div class="trdr-market-label">Price</div><div class="trdr-market-value">{latest_price:.2f}</div></div>
    <div class="trdr-market-tile"><div class="trdr-market-label">Trend</div><div class="trdr-market-value">{trend.upper()}</div></div>
    <div class="trdr-market-tile"><div class="trdr-market-label">EMA 9/21</div><div class="trdr-market-value">{ema_signal}</div></div>
    <div class="trdr-market-tile"><div class="trdr-market-label">EMA Cross</div><div class="trdr-market-value">{ema_cross}</div></div>
    <div class="trdr-market-tile"><div class="trdr-market-label">Sweep</div><div class="trdr-market-value">{sweep_signal}</div></div>
    <div class="trdr-market-tile"><div class="trdr-market-label">Signal</div><div class="trdr-market-value">{entry_signal}</div></div>
    <div class="trdr-market-tile"><div class="trdr-market-label">Session</div><div class="trdr-market-value">{session}</div></div>
  </div>
</div>
""", unsafe_allow_html=True)

# -------- MT5 LIVE EXECUTION STATUS --------
show_mt5_status_panel = st.toggle("🤖 MT5 Live Trading", value=True, key="show_mt5_live_trading_panel")
strategy_positions = mt5_synced_positions if mt5_live_trading else []
if show_mt5_status_panel:
    st.markdown('<div class="trdr-terminal-wrap"><div class="trdr-terminal-title">🤖 MT5 Live Trading Status</div>', unsafe_allow_html=True)
    if mt5_live_trading:
        if mt5_trade_confirm:
            st.success(f"MT5 order execution is ARMED for {active_symbol or mt5_symbol}. New signals can open live/demo trades.")
        else:
            st.warning("MT5 order execution toggle is ON, but confirmation is not ticked. No live orders will be sent.")
    else:
        st.info("MT5 order execution is OFF. Signals are saved and managed by the dashboard only.")

    if st.session_state.get("mt5_last_order_result"):
        st.write("Last MT5 order attempt:")
        safe_last_results = safe_json_view(st.session_state.get("mt5_last_order_result", []))
        st.json(safe_last_results)

    if strategy_positions:
        st.write("Open MT5 positions created by this script:")
        st.dataframe(pd.DataFrame(strategy_positions).tail(MAX_MT5_RESULT_ROWS), width="stretch")
    st.markdown('</div>', unsafe_allow_html=True)
else:
    st.markdown('<div class="trdr-hidden-note">🤖 MT5 Live Trading Status hidden.</div>', unsafe_allow_html=True)

# -------- TRADE PANEL --------
show_trade_setup_panel = st.toggle("📈 Trade Setup", value=True, key="show_trade_setup_panel")
if show_trade_setup_panel:
    st.markdown('<div class="trdr-terminal-wrap">', unsafe_allow_html=True)
    setup_direction_title = f" {entry_signal}" if entry_signal in ["BUY", "SELL"] else ""
    st.markdown(f'<div class="trdr-terminal-title">📈 Trade Setup{setup_direction_title}</div>', unsafe_allow_html=True)

if show_trade_setup_panel and trade:
    st.markdown(
        f"""
        <div class="trdr-setup-grid">
            <div class="trdr-card trdr-green"><div class="label">Entry</div><div class="value">{trade['entry']:.2f}</div></div>
            <div class="trdr-card trdr-red"><div class="label">SL</div><div class="value">{trade['sl']:.2f}</div></div>
            <div class="trdr-card trdr-blue"><div class="label">TP1</div><div class="value">{trade['tp1']:.2f}</div></div>
            <div class="trdr-card trdr-blue"><div class="label">TP2</div><div class="value">{trade['tp2']:.2f}</div></div>
            <div class="trdr-card trdr-blue"><div class="label">TP3</div><div class="value">{trade['tp3']:.2f}</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <div class="trdr-small-grid">
            <div class="trdr-card trdr-neutral"><div class="label">Lot</div><div class="value">{format_lot(trade.get('lots', TOTAL_LOT))}</div></div>
            <div class="trdr-card trdr-neutral"><div class="label">TP Split</div><div class="value">{format_lot(trade_tp_lot(trade, 'tp1'))}/{format_lot(trade_tp_lot(trade, 'tp2'))}/{format_lot(trade_tp_lot(trade, 'tp3'))}</div></div>
            <div class="trdr-card trdr-neutral"><div class="label">Max Profit</div><div class="value">${max_trade_profit_value(trade):.2f}</div></div>
            <div class="trdr-card trdr-neutral"><div class="label">RR</div><div class="value">1:{trade.get('risk_reward', 0):.2f}</div></div>
        </div>
        <div class="trdr-caption">SL: {trade.get('sl_pips', 0):.1f} pips | TP1: {trade.get('tp1_pips', 0):.1f} pips | TP2: {trade.get('tp2_pips', 0):.1f} pips | TP3: {trade.get('tp3_pips', 0):.1f} pips</div>
        """,
        unsafe_allow_html=True,
    )
elif show_trade_setup_panel:
    st.info("No new active setup")
if show_trade_setup_panel:
    st.markdown("</div>", unsafe_allow_html=True)
else:
    st.markdown('<div class="trdr-hidden-note">📈 Trade Setup hidden.</div>', unsafe_allow_html=True)

# -------- OPEN TRADES --------
show_open_trades = st.toggle("🔥 Open Trade Management", value=True, key="show_open_trade_management")

open_trades = [t for t in st.session_state.trade_log if t["status"] == "OPEN"]

if show_open_trades:
    if open_trades:
        for t in open_trades:
            live_pnl = calc_trade_live_pnl(t, latest_price)
            live_pips = calc_trade_live_pips(t, latest_price)
            total_pnl = calc_trade_total_pnl(t, latest_price)
            pnl_class = "trdr-profit-pos" if live_pnl >= 0 else "trdr-profit-neg"
            pips_arrow = "↑" if live_pips >= 0 else "↓"
            tp1_display = "✅" if t["tp1_hit"] else f"{t['tp1']:.2f}"
            tp2_display = "✅" if t["tp2_hit"] else f"{t['tp2']:.2f}"
            tp3_display = "✅" if t["tp3_hit"] else f"{t['tp3']:.2f}"
            mt5_sync_html = ""
            if t.get("mt5_executed"):
                mt5_sync_html = (
                    f"<div class='trdr-caption'>MT5 Sync: {int(t.get('mt5_open_positions', 0) or 0)} open position(s) | "
                    f"Volume: {float(t.get('mt5_open_volume', 0.0) or 0.0):.2f} | "
                    f"MT5 Live Profit: ${float(t.get('mt5_live_profit', 0.0) or 0.0):.2f}</div>"
                )
            side_class = "trdr-buy" if str(t.get("type", "")).upper() == "BUY" else "trdr-sell"
            st.markdown(
                f"""
                <div class="trdr-open-card">
                    <div class="trdr-open-title">{t['type']} XAUUSD @ {t['entry']:.2f}<span class="trdr-side-badge {side_class}">{t['type']}</span></div>
                    <div class="trdr-open-grid">
                        <div class="trdr-mini-metric"><div class="label">Current SL</div><div class="value">{t['sl']:.2f}</div></div>
                        <div class="trdr-mini-metric"><div class="label">TP1 Target</div><div class="value">{tp1_display}</div></div>
                        <div class="trdr-mini-metric"><div class="label">TP2 Target</div><div class="value">{tp2_display}</div></div>
                        <div class="trdr-mini-metric"><div class="label">TP3 Target</div><div class="value">{tp3_display}</div></div>
                        <div class="trdr-mini-metric"><div class="label">Live P/L</div><div class="value {pnl_class}">${live_pnl:.2f}</div><div class="trdr-caption">{pips_arrow} {live_pips:.1f} pips</div></div>
                        <div class="trdr-mini-metric"><div class="label">Total P/L</div><div class="value {pnl_class}">${total_pnl:.2f}</div></div>
                    </div>
                    <div class="trdr-caption">
                        <span class="trdr-chip">Realized: ${float(t.get('profit', 0.0)):.2f}</span>
                        <span class="trdr-chip">Floating: ${live_pnl:.2f}</span>
                        <span class="trdr-chip">Current Price: {latest_price:.2f}</span>
                    </div>
                    <div class="trdr-caption">
                        <span class="trdr-chip">SL {float(t.get('sl_pips', trade_level_pips(t, 'sl'))):.1f} pips</span>
                        <span class="trdr-chip">TP1 {float(t.get('tp1_pips', trade_level_pips(t, 'tp1'))):.1f} pips</span>
                        <span class="trdr-chip">TP2 {float(t.get('tp2_pips', trade_level_pips(t, 'tp2'))):.1f} pips</span>
                        <span class="trdr-chip">TP3 {float(t.get('tp3_pips', trade_level_pips(t, 'tp3'))):.1f} pips</span>
                        <span class="trdr-chip">RR 1:{float(t.get('risk_reward', 0.0)):.2f}</span>
                    </div>
                    {mt5_sync_html}
                </div>
                """,
                unsafe_allow_html=True,
            )
            if t.get("locked_sl"):
                locked_amount = stop_loss_total_pnl(t)
                locked_pips = stop_loss_total_pips(t)
                st.markdown(f"<div class='trdr-locked'>🔐 SL locked | Locked P/L: ${locked_amount:.2f} | Locked pips: {locked_pips:.1f}</div>", unsafe_allow_html=True)
    else:
        st.info("No open trades")
else:
    st.markdown(f'<div class="trdr-hidden-note">🔥 Open Trade Management hidden. Open trades: <b>{len(open_trades)}</b></div>', unsafe_allow_html=True)

# -------- TRADE HISTORY --------
show_trade_history = st.toggle("📜 Trade History", value=True, key="show_trade_history")

if show_trade_history:
    st.markdown('<div class="trdr-terminal-wrap"><div class="trdr-terminal-title">📜 Trade History</div>', unsafe_allow_html=True)
    if st.session_state.trade_log:
        df_trades = pd.DataFrame(st.session_state.trade_log[-MAX_UI_TRADES:])
        st.dataframe(df_trades, width="stretch")
    else:
        st.info("No trades yet")
    st.markdown('</div>', unsafe_allow_html=True)
else:
    st.markdown(f'<div class="trdr-hidden-note">📜 Trade History hidden. Saved trades: <b>{len(st.session_state.trade_log)}</b></div>', unsafe_allow_html=True)

# -------- PERFORMANCE --------
show_performance_dashboard = st.toggle("📊 Performance Dashboard", value=True, key="show_performance_dashboard")

if show_performance_dashboard:
    if st.session_state.trade_log:
        df_perf = pd.DataFrame(st.session_state.trade_log)

        total = len(df_perf)
        closed_trades_for_stats = [t for t in st.session_state.trade_log if str(t.get("status", "")).upper() == "CLOSED"]
        closed = len(closed_trades_for_stats)

        # Count results by actual realized P/L.
        # TP1/TP2 followed by SL remains a win if the trade banked profit.
        wins = sum(1 for t in closed_trades_for_stats if float(t.get("profit", 0.0) or 0.0) > 0)
        losses = sum(1 for t in closed_trades_for_stats if float(t.get("profit", 0.0) or 0.0) < 0)
        breakeven = sum(1 for t in closed_trades_for_stats if float(t.get("profit", 0.0) or 0.0) == 0)
        winrate = (wins / max(1, wins + losses)) * 100 if (wins + losses) > 0 else 0
        total_profit = float(df_perf["profit"].sum())
        open_live_pnl = sum(calc_trade_live_pnl(t, latest_price) for t in st.session_state.trade_log if t.get("status") == "OPEN")
        total_equity_pnl = total_profit + open_live_pnl

        closed_trades_list = closed_trades_for_stats
        closed_profit_usd = sum(max(float(t.get("profit", 0.0) or 0.0), 0.0) for t in closed_trades_list)
        closed_loss_usd = sum(min(float(t.get("profit", 0.0) or 0.0), 0.0) for t in closed_trades_list)
        closed_net_usd = closed_profit_usd + closed_loss_usd
        closed_profit_pips = sum(max(float(t.get("final_pips", 0.0) or 0.0), 0.0) for t in closed_trades_list)
        closed_loss_pips = sum(min(float(t.get("final_pips", 0.0) or 0.0), 0.0) for t in closed_trades_list)
        closed_net_pips = closed_profit_pips + closed_loss_pips

        def _perf_cls(value):
            try:
                value = float(value)
                if value > 0:
                    return "trdr-profit-pos"
                if value < 0:
                    return "trdr-profit-neg"
            except Exception:
                pass
            return ""

        def _pip_delta(value):
            try:
                value = float(value)
                arrow = "↑" if value >= 0 else "↓"
                cls = "trdr-perf-delta-pos" if value >= 0 else "trdr-perf-delta-neg"
                return f'<span class="trdr-perf-delta {cls}">{arrow} {value:.1f} pips</span>'
            except Exception:
                return ""

        # Render this section with components.html so Streamlit can never escape the HTML as raw text.
        # Trading logic is unchanged; this only fixes the Performance Dashboard display.
        perf_html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<style>
    html, body {{
        margin: 0;
        padding: 0;
        background: transparent;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
        color: #0f172a;
    }}
    .trdr-performance-panel {{
        background: #ffffff;
        border-radius: 22px;
        padding: 28px;
        box-shadow: 0 14px 34px rgba(15, 23, 42, 0.16);
        border: 1px solid rgba(15, 23, 42, 0.06);
        box-sizing: border-box;
        width: 100%;
    }}
    .trdr-terminal-title {{
        font-size: 24px;
        line-height: 1.2;
        font-weight: 900;
        color: #07142d;
        margin: 0 0 22px 0;
        letter-spacing: -0.02em;
    }}
    .trdr-perf-grid {{ display: grid; gap: 16px; margin: 16px 0; }}
    .trdr-perf-grid-five {{ grid-template-columns: repeat(5, minmax(120px, 1fr)); }}
    .trdr-perf-grid-three {{ grid-template-columns: repeat(3, minmax(170px, 1fr)); }}
    .trdr-perf-tile {{
        background: #f8fafc;
        border: 1px solid #e5e7eb;
        border-radius: 16px;
        padding: 16px 18px;
        min-height: 82px;
        box-sizing: border-box;
    }}
    .trdr-perf-label {{ font-size: 14px; font-weight: 650; color: #334155; margin-bottom: 8px; }}
    .trdr-perf-value {{ font-size: 34px; line-height: 1; font-weight: 800; color: #0f172a; letter-spacing: -0.03em; }}
    .trdr-profit-pos {{ color: #16a34a !important; }}
    .trdr-profit-neg {{ color: #dc2626 !important; }}
    .trdr-perf-delta {{ display: inline-block; margin-top: 10px; padding: 3px 8px; border-radius: 999px; font-size: 13px; font-weight: 700; }}
    .trdr-perf-delta-pos {{ background: #dcfce7; color: #16a34a; }}
    .trdr-perf-delta-neg {{ background: #fee2e2; color: #dc2626; }}
</style>
</head>
<body>
    <div class="trdr-performance-panel">
        <div class="trdr-terminal-title">📊 Performance Dashboard</div>
        <div class="trdr-perf-grid trdr-perf-grid-five">
            <div class="trdr-perf-tile"><div class="trdr-perf-label">Total Trades</div><div class="trdr-perf-value">{total}</div></div>
            <div class="trdr-perf-tile"><div class="trdr-perf-label">Closed</div><div class="trdr-perf-value">{closed}</div></div>
            <div class="trdr-perf-tile"><div class="trdr-perf-label">Wins</div><div class="trdr-perf-value">{wins}</div></div>
            <div class="trdr-perf-tile"><div class="trdr-perf-label">Losses</div><div class="trdr-perf-value">{losses}</div></div>
            <div class="trdr-perf-tile"><div class="trdr-perf-label">Winrate %</div><div class="trdr-perf-value">{winrate:.1f}%</div></div>
        </div>
        <div class="trdr-perf-grid trdr-perf-grid-three">
            <div class="trdr-perf-tile"><div class="trdr-perf-label">Realized Profit</div><div class="trdr-perf-value {_perf_cls(total_profit)}">${total_profit:.2f}</div></div>
            <div class="trdr-perf-tile"><div class="trdr-perf-label">Open Live P/L</div><div class="trdr-perf-value {_perf_cls(open_live_pnl)}">${open_live_pnl:.2f}</div></div>
            <div class="trdr-perf-tile"><div class="trdr-perf-label">Total Equity P/L</div><div class="trdr-perf-value {_perf_cls(total_equity_pnl)}">${total_equity_pnl:.2f}</div></div>
        </div>
        <div class="trdr-perf-grid trdr-perf-grid-three">
            <div class="trdr-perf-tile"><div class="trdr-perf-label">Closed Gains</div><div class="trdr-perf-value {_perf_cls(closed_profit_usd)}">${closed_profit_usd:.2f}</div>{_pip_delta(closed_profit_pips)}</div>
            <div class="trdr-perf-tile"><div class="trdr-perf-label">Closed Losses</div><div class="trdr-perf-value {_perf_cls(closed_loss_usd)}">${closed_loss_usd:.2f}</div>{_pip_delta(closed_loss_pips)}</div>
            <div class="trdr-perf-tile"><div class="trdr-perf-label">Closed Net</div><div class="trdr-perf-value {_perf_cls(closed_net_usd)}">${closed_net_usd:.2f}</div>{_pip_delta(closed_net_pips)}</div>
        </div>
    </div>
</body>
</html>"""
        components.html(perf_html, height=470, scrolling=False)
    else:
        st.markdown('<div class="trdr-terminal-wrap"><div class="trdr-terminal-title">📊 Performance Dashboard</div><div class="trdr-hidden-note">No performance data yet.</div></div>', unsafe_allow_html=True)
else:
    st.markdown('<div class="trdr-hidden-note">📊 Performance Dashboard hidden.</div>', unsafe_allow_html=True)


# -------- SAVED WEEKLY PERFORMANCE HISTORY --------
with st.expander("📁 Saved Weekly Performance History", expanded=False):
    weekly_reports = load_weekly_reports()
    if weekly_reports:
        weekly_rows = []
        for r in reversed(weekly_reports[-12:]):
            sm = r.get("summary", {}) if isinstance(r, dict) else {}
            weekly_rows.append({
                "Week": r.get("week_id"),
                "Range": f"{r.get('week_start')} to {r.get('week_end')}",
                "Trades": sm.get("total_trades", 0),
                "Closed": sm.get("closed", 0),
                "Wins": sm.get("wins", 0),
                "Losses": sm.get("losses", 0),
                "Breakeven": sm.get("breakeven", 0),
                "Winrate %": sm.get("winrate", 0),
                "Net $": sm.get("net_profit", 0),
                "Saved At": r.get("saved_at"),
            })
        st.dataframe(pd.DataFrame(weekly_rows), width="stretch")
    else:
        st.info("No saved weekly reports yet. The first report is created when a new trading week starts.")

# -------- CHART --------
fig = go.Figure()

fig.add_trace(go.Candlestick(
    x=data.index,
    open=data.tail(CANDLE_OUTPUT_SIZE)["Open"],
    high=data.tail(CANDLE_OUTPUT_SIZE)["High"],
    low=data.tail(CANDLE_OUTPUT_SIZE)["Low"],
    close=data.tail(CANDLE_OUTPUT_SIZE)["Close"],
    name=str(active_symbol or "XAUUSD")
))

fig.add_trace(go.Scatter(
    x=data.index,
    y=data["EMA9"],
    mode="lines",
    name="EMA 9"
))

fig.add_trace(go.Scatter(
    x=data.index,
    y=data["EMA21"],
    mode="lines",
    name="EMA 21"
))

fig.add_hline(y=resistance, line_color="red", line_dash="dash", annotation_text="Resistance")
fig.add_hline(y=support, line_color="blue", line_dash="dash", annotation_text="Support")

# Draw latest setup levels
if trade:
    fig.add_hline(y=trade["entry"], line_color="green", annotation_text="Entry")
    fig.add_hline(y=trade["sl"], line_color="red", annotation_text="SL")
    fig.add_hline(y=trade["tp1"], line_color="blue", line_dash="dot", annotation_text="TP1")
    fig.add_hline(y=trade["tp2"], line_color="blue", line_dash="dash", annotation_text="TP2")
    fig.add_hline(y=trade["tp3"], line_color="blue", annotation_text="TP3")

# Draw active open trade levels
for t in open_trades:
    fig.add_hline(y=t["entry"], line_color="green", annotation_text="Open Entry")
    fig.add_hline(y=t["sl"], line_color="red", annotation_text="Managed SL")
    fig.add_hline(y=t["tp1"], line_color="blue", line_dash="dot", annotation_text="Open TP1")
    fig.add_hline(y=t["tp2"], line_color="blue", line_dash="dash", annotation_text="Open TP2")
    fig.add_hline(y=t["tp3"], line_color="blue", annotation_text="Open TP3")

fig.update_layout(
    height=600,
    xaxis_rangeslider_visible=False
)

st.plotly_chart(fig, width="stretch")

# -------- SAVE SIDEBAR SETTINGS + FINAL MEMORY CLEANUP --------
try:
    save_settings({
        "interval": interval,
        "zone_threshold": float(zone_threshold),
        "refresh_seconds": int(refresh_seconds),
        "mt5_symbol": mt5_symbol,
        "use_mt5_primary": bool(use_mt5_primary),
        "use_api_backup": bool(use_api_backup),
        "sl_mode": sl_mode,
        "sl_value": float(sl_value),
        "tp1_mode": tp1_mode,
        "tp1_value": float(tp1_value),
        "tp2_mode": tp2_mode,
        "tp2_value": float(tp2_value),
        "tp3_mode": tp3_mode,
        "tp3_value": float(tp3_value),
        "entry_lot": float(TOTAL_LOT),
        "enable_weekly_reports": bool(enable_weekly_reports),
        "mt5_live_trading": bool(mt5_live_trading),
        "mt5_trade_confirm": bool(mt5_trade_confirm),
        "show_trade_history": bool(st.session_state.get("show_trade_history", True)),
        "show_performance_dashboard": bool(st.session_state.get("show_performance_dashboard", True)),
    })
    prune_runtime_state()
    save_runtime_state()
except Exception:
    pass
