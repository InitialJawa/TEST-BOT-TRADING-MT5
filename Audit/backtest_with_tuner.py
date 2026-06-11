"""
Backtest Auto-Tuner — simulasi 3 bot + auto strategy switch
Period: Oct 2025 - Jun 2026 | M5 | Lot 0.01
Compare:
  - Static best strategy per pair
  - Auto-tuner rotating strategy setiap minggu
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import numpy as np
import MetaTrader5 as mt5

# ============================================================
# PARAMS
# ============================================================
EMA_FAST = 8; EMA_SLOW = 34; ATR_PERIOD = 14
SL_ATR = 0.3; TP_ATR = 0.6; TRAIL_ACT = 0.2; MOM_THRESH = 0.0005
LOT = 0.01; MAX_SPREAD = 300
MIN_TRADES = 10; WR_MIN = 40; PNL_MIN = -20

SYMBOLS = ["XAUUSDm", "US30m", "JP225m"]
START = "2025-10-01"
END = "2026-06-10"

# Strategy rotation order
ROTATION = {
    "MOMENTUM": ["PULLBACK", "TREND_RE", "EMA_CROSS", "MOMENTUM"],
    "PULLBACK": ["MOMENTUM", "TREND_RE", "EMA_CROSS", "PULLBACK"],
    "TREND_RE": ["MOMENTUM", "PULLBACK", "EMA_CROSS", "TREND_RE"],
    "EMA_CROSS": ["MOMENTUM", "PULLBACK", "TREND_RE", "EMA_CROSS"],
}

# Current real-world starting strategies
INITIAL_STRATS = {
    "XAUUSDm": "MOMENTUM",
    "US30m": "PULLBACK",
    "JP225m": "MOMENTUM",
}

OUT = Path(__file__).parent / "output_tuner"
OUT.mkdir(parents=True, exist_ok=True)

# ============================================================
# LOAD DATA
# ============================================================
def load_data(sym):
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
    mt5.symbol_select(sym, True)
    print(f"  Downloading {sym} M5 {START} to {END}...", end="")
    rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M5,
                                 datetime.strptime(START, "%Y-%m-%d"),
                                 datetime.strptime(END, "%Y-%m-%d"))
    mt5.shutdown()
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df.columns = [c.lower() for c in df.columns]
    print(f" {len(df):,} bars")
    return df

# ============================================================
# INDICATORS
# ============================================================
def prep_data(df):
    c = df["close"]
    df["ef"] = c.ewm(span=EMA_FAST, adjust=False).mean()
    df["es"] = c.ewm(span=EMA_SLOW, adjust=False).mean()
    h, l = df["high"], df["low"]
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=ATR_PERIOD, adjust=False).mean()
    df.dropna(inplace=True)
    return df

# ============================================================
# STRATEGY SIGNALS
# ============================================================
def sig_ema_cross(prev, curr):
    bull = prev["ef"] <= prev["es"] and curr["ef"] > curr["es"]
    bear = prev["ef"] >= prev["es"] and curr["ef"] < curr["es"]
    if bull: return 1
    if bear: return -1
    return 0

def sig_momentum(df, idx):
    if idx < 4: return 0
    tu = df.iloc[idx]["ef"] > df.iloc[idx]["es"]
    td = df.iloc[idx]["ef"] < df.iloc[idx]["es"]
    if not tu and not td: return 0
    mom = df.iloc[idx]["close"] / df.iloc[idx-3]["close"] - 1
    if tu and mom > MOM_THRESH: return 1
    if td and mom < -MOM_THRESH: return -1
    return 0

def sig_pullback(df, idx):
    if idx < 2: return 0
    tu = df.iloc[idx]["ef"] > df.iloc[idx]["es"]
    td = df.iloc[idx]["ef"] < df.iloc[idx]["es"]
    if not tu and not td: return 0
    curr, prev = df.iloc[idx], df.iloc[idx-1]
    if tu and curr["low"] <= prev["ef"]: return 1
    if td and curr["high"] >= prev["ef"]: return -1
    return 0

def sig_trend_re(df, idx):
    if idx < 2: return 0
    tu = df.iloc[idx]["ef"] > df.iloc[idx]["es"]
    td = df.iloc[idx]["ef"] < df.iloc[idx]["es"]
    if not tu and not td: return 0
    return 1 if tu else -1

STRATEGIES = {
    "EMA_CROSS": lambda df, i: sig_ema_cross(df.iloc[i-1], df.iloc[i]) if i > 0 else 0,
    "MOMENTUM":  lambda df, i: sig_momentum(df, i),
    "PULLBACK":  lambda df, i: sig_pullback(df, i),
    "TREND_RE":  lambda df, i: sig_trend_re(df, i),
}

# ============================================================
# BACKTEST ENGINE (single strategy)
# ============================================================
def backtest_strategy(df, strat_fn, lot=LOT):
    results = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0,
               "gross_p": 0, "gross_l": 0, "bars": []}
    active = False
    side = entry = sl = tp = atr_e = 0
    entry_date = None

    for idx in range(1, len(df)):
        bar = df.iloc[idx]
        if active:
            if TRAIL_ACT and atr_e:
                act = atr_e * TRAIL_ACT
                if side == 1:
                    p = bar["close"] - entry
                    if p > act:
                        ns = bar["close"] - act
                        if ns > sl: sl = ns
                else:
                    p = entry - bar["close"]
                    if p > act:
                        ns = bar["close"] + act
                        if ns < sl: sl = ns

            exit_px = None
            if (side == 1 and bar["high"] >= tp) or (side == -1 and bar["low"] <= tp):
                exit_px = tp
            elif (side == 1 and bar["low"] <= sl) or (side == -1 and bar["high"] >= sl):
                exit_px = sl

            if exit_px:
                pnl = (exit_px - entry) if side == 1 else (entry - exit_px)
                pnl_usd = pnl * lot * 100
                results["trades"] += 1
                results["pnl"] += pnl_usd
                if pnl_usd > 0:
                    results["wins"] += 1; results["gross_p"] += pnl_usd
                else:
                    results["losses"] += 1; results["gross_l"] += abs(pnl_usd)
                if entry_date:
                    results["bars"].append((bar.name - entry_date).total_seconds() / 300)
                active = False
            continue

        if bar["spread"] > MAX_SPREAD: continue
        sig = strat_fn(df, idx)
        if sig == 0: continue

        side = sig; entry = bar["close"]; entry_date = date = bar.name
        atr_e = bar["atr"]
        if side == 1:
            sl = entry - atr_e * SL_ATR; tp = entry + atr_e * TP_ATR
        else:
            sl = entry + atr_e * SL_ATR; tp = entry - atr_e * TP_ATR
        active = True
    return results

# ============================================================
# AUTO-TUNER BACKTEST (walk-forward mingguan)
# ============================================================
def backtest_with_tuner(df, symbol):
    strat_name = INITIAL_STRATS[symbol]
    tried = []

    cum = {"trades": 0, "wins": 0, "pnl": 0.0, "gross_p": 0, "gross_l": 0}
    log_switches = []
    bad_streak = 0
    weekly_split = df.resample("W-MON")

    for week_start, week_df in weekly_split:
        if len(week_df) < 100:
            continue

        r = backtest_strategy(week_df, STRATEGIES[strat_name])
        cum["trades"] += r["trades"]
        cum["wins"] += r["wins"]
        cum["pnl"] += r["pnl"]
        cum["gross_p"] += r["gross_p"]
        cum["gross_l"] += r["gross_l"]

        wr = (r["wins"] / r["trades"] * 100) if r["trades"] >= MIN_TRADES else 999
        pnl = r["pnl"]

        # Auto-tuner decision — require 2 consecutive bad weeks
        if r["trades"] >= MIN_TRADES and (wr < WR_MIN or pnl < PNL_MIN):
            bad_streak += 1
        else:
            bad_streak = 0

        if bad_streak >= 2:
            rotation = ROTATION.get(strat_name, ROTATION["MOMENTUM"])
            next_strat = rotation[0]
            if next_strat in tried and len(rotation) > 1:
                next_strat = rotation[1]
            if next_strat != strat_name:
                log_switches.append(f"  {week_start.date()} {strat_name}->{next_strat} (WR={wr:.0f}% PnL=${pnl:.2f})")
                tried.append(strat_name)
                strat_name = next_strat
            bad_streak = 0

    pf = cum["gross_p"] / cum["gross_l"] if cum["gross_l"] > 0 else float("inf")
    wr_total = (cum["wins"] / cum["trades"] * 100) if cum["trades"] else 0
    return cum, pf, wr_total, log_switches

# ============================================================
# RUN
# ============================================================
print("\n" + "=" * 80)
print("  BACKTEST WITH AUTO-TUNER")
print("  Period: Oct 2025 - Jun 2026 | M5 | Lot 0.01")
print("  Auto-tuner: switch strategy if WR<40% or PnL<-20$")
print("  Adjusted: MIN_TRADES=10, WR_MIN=40, PNL_MIN=-20, require 2 consecutive bad weeks")
print("=" * 80)

all_data = {}
for sym in SYMBOLS:
    print(f"\nLoading {sym}...")
    df = load_data(sym)
    df = prep_data(df)
    all_data[sym] = df

# ---- 1. Static best strategy per pair ----
print("\n\n--- STATIC BEST STRATEGY (full period) ---")
best_overall = {}
for sym in SYMBOLS:
    df = all_data[sym]
    best_pf = -1; best_name = ""; best_r = None
    for sname in STRATEGIES:
        r = backtest_strategy(df, STRATEGIES[sname])
        if r["trades"] < 50: continue
        pf = r["gross_p"] / r["gross_l"] if r["gross_l"] > 0 else 999
        if pf > best_pf:
            best_pf = pf; best_name = sname; best_r = r
    best_overall[sym] = (best_name, best_r, best_pf)
    wr = (best_r["wins"] / best_r["trades"] * 100) if best_r and best_r["trades"] else 0
    print(f"  {sym:<10} -> {best_name:<12} trades={best_r['trades']} WR={wr:.0f}% PF={best_pf:.2f} Net=${best_r['pnl']:.2f}")

# ---- 2. Current static strategies ----
print("\n\n--- CURRENT STATIC STRATEGIES ---")
for sym in SYMBOLS:
    df = all_data[sym]
    sn = INITIAL_STRATS[sym]
    r = backtest_strategy(df, STRATEGIES[sn])
    wr = (r["wins"] / r["trades"] * 100) if r["trades"] else 0
    pf = r["gross_p"] / r["gross_l"] if r["gross_l"] > 0 else float("inf")
    print(f"  {sym:<10} {sn:<12} trades={r['trades']} WR={wr:.0f}% PF={pf:.2f} Net=${r['pnl']:.2f}")

# ---- 3. Auto-tuner ----
print("\n\n--- AUTO-TUNER (weekly evaluation) ---")
tuner_total = {"trades": 0, "wins": 0, "pnl": 0.0, "gross_p": 0, "gross_l": 0}
for sym in SYMBOLS:
    df = all_data[sym]
    cum, pf, wr_total, switches = backtest_with_tuner(df, sym)
    print(f"\n  {sym}:")
    for s in switches: print(s)
    pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
    print(f"  -> Total: trades={cum['trades']} WR={wr_total:.0f}% PF={pf_str} Net=${cum['pnl']:.2f}")
    for k in tuner_total: tuner_total[k] += cum[k]

pt = tuner_total["gross_p"] / tuner_total["gross_l"] if tuner_total["gross_l"] > 0 else float("inf")
wt = (tuner_total["wins"] / tuner_total["trades"] * 100) if tuner_total["trades"] else 0
print(f"\n  {'='*40}")
print(f"  TUNER TOTAL: trades={tuner_total['trades']} WR={wt:.0f}% PF={pt:.2f} Net=${tuner_total['pnl']:.2f}")

# ---- 4. Compare static best vs current vs tuner ----
print("\n\n")
print("=" * 80)
print("  COMPARISON")
print("=" * 80)
header = f"  {'Symbol':<10} {'Method':<15} {'Trades':>7} {'WR%':>5} {'PF':>6} {'Net $':>10}"
print(header)
print(f"  {'-'*60}")

for sym in SYMBOLS:
    sn_static, r_static, pf_static = best_overall[sym]
    wr_static = (r_static["wins"]/r_static["trades"]*100) if r_static["trades"] else 0
    pf_s = f"{pf_static:.2f}" if pf_static != float("inf") else "inf"
    print(f"  {sym:<10} {'BEST-'+sn_static:<15} {r_static['trades']:>7} {wr_static:>4.0f}% {pf_s:>6} ${r_static['pnl']:>7.2f}")

    sn_cur = INITIAL_STRATS[sym]
    df = all_data[sym]
    r_cur = backtest_strategy(df, STRATEGIES[sn_cur])
    wr_cur = (r_cur["wins"]/r_cur["trades"]*100) if r_cur["trades"] else 0
    pf_cur = r_cur["gross_p"] / r_cur["gross_l"] if r_cur["gross_l"] > 0 else float("inf")
    pf_c = f"{pf_cur:.2f}" if pf_cur != float("inf") else "inf"
    print(f"  {'':<10} {'CURRENT-'+sn_cur:<15} {r_cur['trades']:>7} {wr_cur:>4.0f}% {pf_c:>6} ${r_cur['pnl']:>7.2f}")

    cum, pf_t, wr_t, _ = backtest_with_tuner(df, sym)
    pf_t_s = f"{pf_t:.2f}" if pf_t != float("inf") else "inf"
    print(f"  {'':<10} {'TUNER':<15} {cum['trades']:>7} {wr_t:>4.0f}% {pf_t_s:>6} ${cum['pnl']:>7.2f}")
    print()

print("\nDone.")
