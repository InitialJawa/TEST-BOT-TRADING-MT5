"""
Unified backtest: Original (EMA_CROSS) vs Fabio (MOMENTUM/PULLBACK/TREND_RE) vs Hendro (ADAPTIVE)
0.01 lot, M5, Oct 2025 - Jun 2026
"""
import sys, json, time
from datetime import datetime
from pathlib import Path

import pandas as pd
import MetaTrader5 as mt5

sys.path.insert(0, str(Path(__file__).parent / "bot_fabio"))
from strategy import ScalpingStrategy as FabioStrategy
sys.path.pop(0)

sys.path.insert(0, str(Path(__file__).parent / "bot_hendro"))
from strategy import ScalpingStrategy as HendroStrategy
sys.path.pop(0)

TF_MAP = {"M5": mt5.TIMEFRAME_M5}
EMA_FAST, EMA_SLOW = 8, 34
ATR_PERIOD = 14
SL_ATR, TP_ATR = 0.3, 0.6
TRAIL = 0.2
MOM_THRESH = 0.0005
REGIME_TH = 0.7
MAX_SPREAD = 300

SYMBOLS = ["XAUUSDm", "US30m", "JP225m"]
LOT = 0.01
END = datetime(2026, 6, 11)
START = datetime(2025, 10, 1)

if not mt5.initialize():
    print("MT5 init fail"); sys.exit(1)


def fetch_all(sym, tf, start, end):
    rates = mt5.copy_rates_range(sym, tf, start, end)
    if rates is None or len(rates) < 200:
        return None
    return rates


def make_config(sym, strategy_type="EMA_CROSS"):
    return {
        "symbol": sym, "lot_size": LOT, "magic_number": 1, "comment": "BT",
        "max_spread_points": MAX_SPREAD, "timeframe": "M5",
        "strategy_type": strategy_type, "session_filter": {},
        "strategy": {
            "ema_fast": EMA_FAST, "ema_slow": EMA_SLOW, "atr_period": ATR_PERIOD,
            "sl_atr_mult": SL_ATR, "tp_atr_mult": TP_ATR, "trailing_activation": TRAIL,
            "momentum_threshold": MOM_THRESH, "regime_threshold": REGIME_TH,
        }
    }


def run_original(df, sym):
    """Original: EMA_CROSS only (single symbol) - it only trades XAUUSD"""
    cfg = make_config(sym, "EMA_CROSS")
    strat = FabioStrategy(cfg)
    trades = []
    for i in range(len(df)):
        chunk = df.iloc[:i+1]
        rates = chunk.reset_index().to_dict('records')
        # Convert back to list of tuples
        rates_list = []
        for _, r in chunk.iterrows():
            rates_list.append((
                int(r.get('time', pd.Timestamp.now()).timestamp()),
                r.get('open', 0), r.get('high', 0), r.get('low', 0), r.get('close', 0),
                0, r.get('spread', 0), 0, 0, 0
            ))
        # Can't easily reuse strategy without MT5...
    return []


def run_backtest_via_mt5():
    """Use copy_rates_range + iterate like strategy does"""
    results = {}
    
    for sym in SYMBOLS:
        print(f"Fetching {sym}...")
        rates = fetch_all(sym, mt5.TIMEFRAME_M5, START, END)
        if rates is None:
            print(f"  No data for {sym}")
            continue
        
        # Build dataframe for strategy
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df.set_index("time", inplace=True)
        
        # For testing, we need to simulate incremental data feeding
        # Strategy.get_signal() calls copy_rates_from_pos internally
        # So we can't easily intercept it...

    print("See individual backtest files for detailed results")
    mt5.shutdown()


# Since strategies use MT5 internally, we need to compare existing files
# Load existing JSON results
results_dir = Path(__file__).parent / "backtest_results"

# From multi_strategy_comparison.json (same methodology, but buggy 1-bar momentum)
with open(results_dir / "multi_strategy_comparison.json") as f:
    comp1 = json.load(f)

# From v2_comparison.json (corrected ADAPTIVE)
with open(results_dir / "v2_comparison.json") as f:
    comp2 = json.load(f)

print("=" * 80)
print("BACKTEST COMPARISON — All strategies on SAME data (Oct 2025-Jun 2026, M5)")
print("=" * 80)
print()

print(f"{'Strategy':<20} {'Trades':>8} {'WR%':>6} {'PF':>6} {'AdjNet$':>10} {'Note'}")
print("-" * 80)

# The multi_strategy_comparison has all strategies but with buggy 1-bar momentum
# It used lots: XAU=0.03, US30=0.20, JP225=5.0
# Let's normalize to 0.01 lot comparable

data = {}
for strat_name, strat_data in comp1["strategies"].items():
    lots_map = {"XAUUSD": 3, "US30": 20, "JP225": 500}  # divisor to get 0.01 lot value
    syms = {}
    total_adj = 0
    total_trades = 0
    for sym in SYMBOLS:
        short = sym.replace("m", "")
        if short in strat_data:
            s = strat_data[short]
            net01 = s["adj_net"] / lots_map.get(short, 1)
            syms[short] = {"trades": s["trades"], "wr": s["win_pct"], "pf": s["pf"], "net01": net01}
            total_adj += s["adj_net"]
            total_trades += s["trades"]
    
    # Find display name
    if strat_name == "EMA_CROSS":
        display_name = "Original (EMA_CROSS)"
    elif strat_name == "MOMENTUM":
        display_name = "Fabio (MOMENTUM)"
    elif strat_name == "PULLBACK":
        display_name = "PULLBACK*"
    elif strat_name == "TREND_RE":
        display_name = "TREND_RE"
    elif strat_name == "ADAPTIVE":
        display_name = "Hendro (ADAPTIVE)"
    else:
        display_name = strat_name
        
    data[strat_name] = {"display": display_name, "syms": syms, "total_adj": total_adj, "total_trades": total_trades}

# Print per-strategy total
for strat_name in ["ADAPTIVE", "MOMENTUM", "PULLBACK", "TREND_RE", "EMA_CROSS"]:
    if strat_name not in data:
        continue
    d = data[strat_name]
    sym_detail = " | ".join(f"{s}: {d['syms'][s]['trades']}t WR{d['syms'][s]['wr']}% PF{d['syms'][s]['pf']}" for s in d['syms'])
    note = ""
    if strat_name == "PULLBACK":
        note = "*WARNING: overfit (94% from 1 period)"
    elif strat_name in ("MOMENTUM", "TREND_RE", "EMA_CROSS"):
        note = ""
    elif strat_name == "ADAPTIVE":
        note = "(buggy 1-bar mom)"
    print(f"{d['display']:<20} {d['total_trades']:>8} {'-':>6} {'-':>6} ${d['total_adj']:>7.2f} {note}")
    print(f"{'':20} {sym_detail}")

print()
print("-" * 80)
print("CORRECTED ADAPTIVE (v2.1 = Hendro with regime=0.7, session, cooldown)")
print("-" * 80)
print(f"v2.1 ADAPTIVE XAU(0.03) USD(0.20) JP225(5.0): total_adj_net ${comp2['summary']['v2_1_total_adj']:.2f}")
total_adj_21 = comp2['summary']['v2_1_total_adj']
print(f"  XAUUSD: {comp2['comparison']['XAUUSD']['periods']['FULL']['v2_1']['trades']}t WR{comp2['comparison']['XAUUSD']['periods']['FULL']['v2_1']['wr']}% PF{comp2['comparison']['XAUUSD']['periods']['FULL']['v2_1']['pf']}")
print(f"  US30:   {comp2['comparison']['US30']['periods']['FULL']['v2_1']['trades']}t WR{comp2['comparison']['US30']['periods']['FULL']['v2_1']['wr']}% PF{comp2['comparison']['US30']['periods']['FULL']['v2_1']['pf']}")
print(f"  JP225:  {comp2['comparison']['JP225']['periods']['FULL']['v2_1']['trades']}t WR{comp2['comparison']['JP225']['periods']['FULL']['v2_1']['wr']}% PF{comp2['comparison']['JP225']['periods']['FULL']['v2_1']['pf']}")

print()
print("=" * 80)
print("CONCLUSION: Data tidak bisa dibandingkan langsung karena metodenza beda:")
print("  1. Original (EMA_CROSS)  : BACKTEST_RESULTS.txt — single symbol, simpel")
print("  2. Fabio (MOM/PULL/TREND) : backtest_highfreq.py — per-symbol optimasi, 0.01 lot clean")
print("  3. Hendro (ADAPTIVE)     : v2_comparison.json — regime-switching, session filter")
print()
print("Untuk perbandingan akurat, perlu backtest seragam pakai 1 framework.")
print("Test live sekarang jalan — hasil real > backtest.")
print("=" * 80)

mt5.shutdown()
