"""
Compound backtest: equity $300 start, auto-adjust lot per pair.
Simultaneous trading (equity dibagi 3 untuk tiap pair).
Lot capped di 1.0 untuk membatasi risiko.
"""

import sys, json
from pathlib import Path
from datetime import datetime
import pandas as pd
import MetaTrader5 as mt5

EMA_FAST = 8; EMA_SLOW = 34; ATR_PERIOD = 14
SL_ATR = 0.3; TP_ATR = 0.6; TRAIL_ACT = 0.2
MAX_SPREAD = 300
START = "2025-10-01"; END = "2026-06-10"

PAIR_CFG = {
    "XAUUSDm": {"ratio": 30000, "is_jp": False, "max_lot": 1.0},
    "JP225m":  {"ratio": 3000,  "is_jp": True,  "max_lot": 5.0},
    "US30m":   {"ratio": 30000, "is_jp": False, "max_lot": 1.0},
}

# === BACKTEST ENGINE ===
def load_prep(sym):
    mt5.initialize()
    mt5.symbol_select(sym, True)
    rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M5,
                                  datetime.strptime(START, "%Y-%m-%d"),
                                  datetime.strptime(END, "%Y-%m-%d"))
    mt5.shutdown()
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df.columns = [c.lower() for c in df.columns]
    c = df["close"]
    df["ef"] = c.ewm(span=EMA_FAST, adjust=False).mean()
    df["es"] = c.ewm(span=EMA_SLOW, adjust=False).mean()
    h, l = df["high"], df["low"]
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=ATR_PERIOD, adjust=False).mean()
    df.dropna(inplace=True)
    df["sig"] = 0
    df.loc[df["ef"] > df["es"], "sig"] = 1
    df.loc[df["ef"] < df["es"], "sig"] = -1
    return df


def backtest_bar(row, state, ratio, is_jp, max_lot, equity):
    """Process one bar, return (new_state, trade_result)"""
    if state["active"]:
        s = state
        if TRAIL_ACT and s["atr_e"]:
            act = s["atr_e"] * TRAIL_ACT
            if s["side"] == 1:
                p = row["close"] - s["entry"]
                if p > act:
                    ns = row["close"] - act
                    if ns > s["sl"]: s["sl"] = ns
            else:
                p = s["entry"] - row["close"]
                if p > act:
                    ns = row["close"] + act
                    if ns < s["sl"]: s["sl"] = ns
        exit_px = None
        if (s["side"] == 1 and row["high"] >= s["tp"]) or (s["side"] == -1 and row["low"] <= s["tp"]):
            exit_px = s["tp"]
        elif (s["side"] == 1 and row["low"] <= s["sl"]) or (s["side"] == -1 and row["high"] >= s["sl"]):
            exit_px = s["sl"]
        if exit_px:
            pnl = (exit_px - s["entry"]) if s["side"] == 1 else (s["entry"] - exit_px)
            lot = min(max(round(equity / ratio, 2), 0.01), max_lot)
            pnl_usd = pnl * lot * (100 if not is_jp else 1)
            state = {"active": False, "side": 0, "entry": 0, "sl": 0, "tp": 0, "atr_e": 0}
            return state, {"exit": True, "pnl": pnl_usd, "lot": lot}
        return state, {"exit": False, "pnl": 0, "lot": 0}

    if row["spread"] > MAX_SPREAD:
        return state, {"exit": False, "pnl": 0, "lot": 0}

    sig = row["sig"]
    if sig == 0:
        return state, {"exit": False, "pnl": 0, "lot": 0}

    atr_e = row["atr"]
    if sig == 1:
        sl = row["close"] - atr_e * SL_ATR
        tp = row["close"] + atr_e * TP_ATR
    else:
        sl = row["close"] + atr_e * SL_ATR
        tp = row["close"] - atr_e * TP_ATR
    state = {"active": True, "side": sig, "entry": row["close"],
             "sl": sl, "tp": tp, "atr_e": atr_e}
    return state, {"exit": False, "pnl": 0, "lot": 0}


# === SIMULATION ===
def simulate(initial_equity, pair_cfgs, all_data, mode="simultaneous"):
    """
    mode = "simultaneous": equity dibagi ke tiap pair, semua jalan bareng
    mode = "sequential": equity pindah dari pair1->pair2->pair3
    """
    equity = initial_equity
    total_trades = 0; total_profit = 0.0
    dd = 0.0; peak = equity

    if mode == "sequential":
        for sym, cfg in pair_cfgs.items():
            if sym not in all_data: continue
            df = all_data[sym]
            state = {"active": False, "side": 0, "entry": 0, "sl": 0, "tp": 0, "atr_e": 0}
            trades = 0; profit = 0.0; pair_dd = 0.0; pair_peak = equity

            for idx in range(len(df)):
                row = df.iloc[idx]
                state, res = backtest_bar(row, state, cfg["ratio"], cfg["is_jp"], cfg["max_lot"], equity)
                if res["exit"]:
                    trades += 1; profit += res["pnl"]
                    equity += res["pnl"]
                    pair_peak = max(pair_peak, equity)
                    pair_dd = max(pair_dd, pair_peak - equity)

            total_trades += trades; total_profit += profit
            peak = max(peak, equity)
            dd = max(dd, pair_dd)
            print(f"  {sym:<10} {trades:>6} trades  ${profit:>12,.2f}  eq=${equity:>10,.2f}  DD=${pair_dd:>10,.2f}")

    elif mode == "simultaneous":
        # Each pair gets equity / N, trade independently
        n = len([s for s in pair_cfgs if s in all_data])
        eq_per_pair = equity / n
        states = {sym: {"active": False, "side": 0, "entry": 0, "sl": 0, "tp": 0, "atr_e": 0}
                  for sym in pair_cfgs if sym in all_data}
        eqs = {sym: eq_per_pair for sym in pair_cfgs if sym in all_data}
        trades = {sym: 0 for sym in pair_cfgs if sym in all_data}
        profits = {sym: 0.0 for sym in pair_cfgs if sym in all_data}
        dds = {sym: 0.0 for sym in pair_cfgs if sym in all_data}
        peaks = {sym: eq_per_pair for sym in pair_cfgs if sym in all_data}

        # Find longest df
        max_len = max(len(all_data[sym]) for sym in pair_cfgs if sym in all_data)
        for idx in range(max_len):
            for sym in pair_cfgs:
                if sym not in all_data: continue
                if idx >= len(all_data[sym]): continue
                row = all_data[sym].iloc[idx]
                pc = pair_cfgs[sym]
                equity_this = sum(eqs.values())
                states[sym], res = backtest_bar(row, states[sym], pc["ratio"],
                                                 pc["is_jp"], pc["max_lot"], eqs[sym])
                if res["exit"]:
                    trades[sym] += 1
                    profits[sym] += res["pnl"]
                    eqs[sym] += res["pnl"]
                    peaks[sym] = max(peaks[sym], eqs[sym])
                    dds[sym] = max(dds[sym], peaks[sym] - eqs[sym])

        total_trades = sum(trades.values())
        total_profit = sum(profits.values())
        dd = max(dds.values())
        equity = sum(eqs.values())

        for sym in pair_cfgs:
            if sym not in all_data: continue
            pct = profits[sym] / total_profit * 100 if total_profit else 0
            print(f"  {sym:<10} {trades[sym]:>6} trades  ${profits[sym]:>12,.2f} ({pct:>5.1f}%)  eq=${eqs[sym]:>10,.2f}  DD=${dds[sym]:>10,.2f}")

    else:
        # equal mode: all pairs share same equity bar-by-bar
        n = len([s for s in pair_cfgs if s in all_data])
        eq_share = equity
        states = {}
        for sym in pair_cfgs:
            if sym not in all_data: continue
            states[sym] = {"active": False, "side": 0, "entry": 0, "sl": 0, "tp": 0, "atr_e": 0,
                           "last_pnl": 0}

        max_len = max(len(all_data[sym]) for sym in pair_cfgs if sym in all_data)
        trades = {sym: 0 for sym in pair_cfgs if sym in all_data}
        profits = {sym: 0.0 for sym in pair_cfgs if sym in all_data}
        dds = {sym: 0.0 for sym in pair_cfgs if sym in all_data}
        peaks = {sym: equity for sym in pair_cfgs if sym in all_data}

        for idx in range(max_len):
            for sym in pair_cfgs:
                if sym not in all_data: continue
                if idx >= len(all_data[sym]): continue
                row = all_data[sym].iloc[idx]
                pc = pair_cfgs[sym]
                states[sym], res = backtest_bar(row, states[sym], pc["ratio"],
                                                 pc["is_jp"], pc["max_lot"], eq_share)
                if res["exit"]:
                    trades[sym] += 1
                    profits[sym] += res["pnl"]
                    eq_share += res["pnl"]
                    peaks[sym] = max(peaks[sym], eq_share)
                    dds[sym] = max(dds[sym], peaks[sym] - eq_share)

        total_trades = sum(trades.values())
        total_profit = sum(profits.values())
        dd = max(dds.values())
        equity = eq_share

        for sym in pair_cfgs:
            if sym not in all_data: continue
            pct = profits[sym] / total_profit * 100 if total_profit else 0
            print(f"  {sym:<10} {trades[sym]:>6} trades  ${profits[sym]:>12,.2f} ({pct:>5.1f}%)  DD=${dds[sym]:>10,.2f}")

    return equity, total_trades, total_profit, dd


# === MAIN ===
print("=" * 70)
print("  COMPOUND BACKTEST — $300 START, AUTO-ADJUST LOT")
print("  TREND_RE | Oct 2025 - Jun 2026 | M5")
print("=" * 70)

all_data = {}
for sym in PAIR_CFG:
    print(f"\nLoading {sym}...", end="")
    df = load_prep(sym)
    if df is not None and not df.empty:
        all_data[sym] = df
        print(f" {len(df):,} bars")
    else:
        print(" SKIP")

print(f"\n{'='*70}")
print("  MODE 1: EQUAL SHARE (all pairs share same equity)")
print(f"{'='*70}")
eq1, tr1, pnl1, dd1 = simulate(300, PAIR_CFG, all_data, "equal")
print(f"  {'-'*55}")
print(f"  Final:  ${eq1:,.2f}")
print(f"  Profit: ${pnl1:,.2f}  (${pnl1/8:,.0f}/mo)")
print(f"  Trades: {tr1}")
print(f"  MaxDD:  ${dd1:,.2f}  ({dd1/300*100:.1f}%)")
print(f"  ROE:    {pnl1/300*100:.0f}%  ({pnl1/300/8:.1f}%/mo)")

print(f"\n{'='*70}")
print("  MODE 2: SEQUENTIAL (equity rotates through pairs)")
print(f"{'='*70}")
eq2, tr2, pnl2, dd2 = simulate(300, PAIR_CFG, all_data, "sequential")
print(f"  {'-'*55}")
print(f"  Final:  ${eq2:,.2f}")
print(f"  Profit: ${pnl2:,.2f}  (${pnl2/8:,.0f}/mo)")
print(f"  Trades: {tr2}")
print(f"  MaxDD:  ${dd2:,.2f}  ({dd2/300*100:.1f}%)")
print(f"  ROE:    {pnl2/300*100:.0f}%  ({pnl2/300/8:.1f}%/mo)")

print(f"\n{'='*70}")
print("  MODE 3: SIMULTANEOUS (equity split equally, each trades independently)")
print(f"{'='*70}")
eq3, tr3, pnl3, dd3 = simulate(300, PAIR_CFG, all_data, "simultaneous")
print(f"  {'-'*55}")
print(f"  Final:  ${eq3:,.2f}")
print(f"  Profit: ${pnl3:,.2f}  (${pnl3/8:,.0f}/mo)")
print(f"  Trades: {tr3}")
print(f"  MaxDD:  ${dd3:,.2f}  ({dd3/300*100:.1f}%)")
print(f"  ROE:    {pnl3/300*100:.0f}%  ({pnl3/300/8:.1f}%/mo)")

# Quick ref: fixed 0.01
print(f"\n{'='*70}")
print("  REFERENCE: Fixed lot 0.01 (from backtest_300.py)")
print(f"    XAU 0.01: +$7,805, DD $117 (39%)")
print(f"    JP225 0.01: +$992, DD $9 (3%)")
print(f"    US30 0.01: +$42,904, DD $694 (231%)")
print(f"    TOTAL: +$51,701, DD $694 (231%)")
print(f"    Per bulan: ~$6,463/mo")
print("=" * 70)
