"""
Backtest dengan modal $300 — cari lot size optimal
TREND_RE | Oct 2025 - Jun 2026 | M5
"""

import sys
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np
import MetaTrader5 as mt5

EMA_FAST = 8; EMA_SLOW = 34; ATR_PERIOD = 14
SL_ATR = 0.3; TP_ATR = 0.6; TRAIL_ACT = 0.2
MAX_SPREAD = 300
CAPITAL = 300

SYMBOLS = ["XAUUSDm", "US30m", "JP225m"]
START = "2025-10-01"
END = "2026-06-10"

OUT = Path(__file__).parent / "output_tuner"
OUT.mkdir(parents=True, exist_ok=True)

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

def prep_data(df):
    c = df["close"]
    df["ef"] = c.ewm(span=EMA_FAST, adjust=False).mean()
    df["es"] = c.ewm(span=EMA_SLOW, adjust=False).mean()
    h, l = df["high"], df["low"]
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=ATR_PERIOD, adjust=False).mean()
    df.dropna(inplace=True)
    return df

def sig_trend_re(df, idx):
    if idx < 2: return 0
    tu = df.iloc[idx]["ef"] > df.iloc[idx]["es"]
    td = df.iloc[idx]["ef"] < df.iloc[idx]["es"]
    if not tu and not td: return 0
    return 1 if tu else -1

def backtest(df, lot, is_jp=False):
    res = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0,
           "gross_p": 0, "gross_l": 0, "dd": 0.0, "peak": CAPITAL}
    active = False
    side = entry = sl = tp = atr_e = 0

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
                pnl_usd = pnl * lot * (100 if not is_jp else 1)
                res["trades"] += 1
                res["pnl"] += pnl_usd
                if pnl_usd > 0:
                    res["wins"] += 1; res["gross_p"] += pnl_usd
                else:
                    res["losses"] += 1; res["gross_l"] += abs(pnl_usd)
                active = False
                eq = CAPITAL + res["pnl"]
                res["dd"] = max(res["dd"], res["peak"] - eq)
                if eq > res["peak"]: res["peak"] = eq
            continue
        if bar["spread"] > MAX_SPREAD: continue
        sig = sig_trend_re(df, idx)
        if sig == 0: continue
        side = sig; entry = bar["close"]
        atr_e = bar["atr"]
        if side == 1:
            sl = entry - atr_e * SL_ATR; tp = entry + atr_e * TP_ATR
        else:
            sl = entry + atr_e * SL_ATR; tp = entry - atr_e * TP_ATR
        active = True
    return res

print("\n" + "=" * 80)
print("  BACKTEST — MODAL $300")
print("  TREND_RE | Oct 2025 - Jun 2026 | M5")
print("=" * 80)

all_data = {}
for sym in SYMBOLS:
    print(f"\nLoading {sym}...")
    df = load_data(sym); df = prep_data(df)
    all_data[sym] = df

# Test various lot sizes
test_lots = [0.01, 0.02, 0.03, 0.05, 0.1]

print("\n\n--- LOT SIZE SIMULATION ---")
for sym in SYMBOLS:
    df = all_data[sym]
    is_jp = sym == "JP225m"
    print(f"\n  {sym}:")
    print(f"  {'Lot':>6} {'Trades':>7} {'WR%':>5} {'PF':>5} {'Net $':>10} {'MaxDD $':>8} {'MaxDD%':>7} {'Score':>7}")
    print(f"  {'-'*55}")
    for lot in test_lots:
        r = backtest(df, lot, is_jp)
        wr = (r["wins"]/r["trades"]*100) if r["trades"] else 0
        pf = r["gross_p"]/r["gross_l"] if r["gross_l"] > 0 else float("inf")
        pf_s = f"{pf:.2f}" if pf != float("inf") else "inf"
        dd_pct = r["dd"] / CAPITAL * 100
        score = r["pnl"] / r["dd"] if r["dd"] > 0 else r["pnl"]
        print(f"  {lot:>5.2f} {r['trades']:>7} {wr:>4.0f}% {pf_s:>5} ${r['pnl']:>8.2f} ${r['dd']:>6.2f} {dd_pct:>5.1f}% {score:>7.0f}")

# Best for $300
print("\n\n--- ADA 3 SKENARIO ---")
print("  Berdasarkan lot yang aman untuk $300:\n")

recommendations = []
for sym in SYMBOLS:
    df = all_data[sym]
    is_jp = sym == "JP225m"
    print(f"  {sym}:")
    best_score = -999; best_lot = 0; best_r = None
    for lot in test_lots:
        r = backtest(df, lot, is_jp)
        if r["dd"] == 0: continue
        score = r["pnl"] / r["dd"]
        if score > best_score:
            best_score = score; best_lot = lot; best_r = r
    if best_r:
        wr = (best_r["wins"]/best_r["trades"]*100) if best_r["trades"] else 0
        pf = best_r["gross_p"]/best_r["gross_l"] if best_r["gross_l"] > 0 else float("inf")
        dd_pct = best_r["dd"] / CAPITAL * 100
        monthly = best_r["pnl"] / 8
        print(f"    Lot {best_lot:.2f}: Net=${best_r['pnl']:.2f} | Monthly=${monthly:.2f} | MaxDD={dd_pct:.1f}% | WR={wr:.0f}% | PF={pf:.2f}")
        recommendations.append((sym, best_lot, best_r, monthly))

print(f"\n\n--- PROYEKSI 3 PAIR (gabungan) ---")
total = {"trades": 0, "pnl": 0.0, "dd": 0.0}
for sym, lot, r, monthly in recommendations:
    total["trades"] += r["trades"]
    total["pnl"] += r["pnl"]
    total["dd"] += r["dd"]
    print(f"  {sym:<10} lot={lot:.2f}  ${r['pnl']:.2f} (${monthly:.2f}/bln)  DD=${r['dd']:.2f}")

print(f"  {'='*50}")
print(f"  TOTAL:    ${total['pnl']:.2f} (${total['pnl']/8:.2f}/bln)")
print(f"  MAX DD:   ${total['dd']:.2f} ({total['dd']/CAPITAL*100:.1f}%)")
print(f"  ROE:      {total['pnl']/CAPITAL*100:.0f}% (over 8 bulan)")
print(f"  ROE/bln:  {total['pnl']/CAPITAL/8*100:.1f}%")

# Actual current config with $300
print(f"\n\n--- SKENARIO REALISTIS UNTUK $300 ---")
print(f"  Modal $300, risk 2% per trade = $6/trade")
print(f"  TREND_RE avg loss ~$2-5 per trade di lot 0.01")

total_r = {"trades": 0, "pnl": 0.0, "dd": 0.0}
for sym in SYMBOLS:
    df = all_data[sym]
    is_jp = sym == "JP225m"
    lot = 0.01
    r = backtest(df, lot, is_jp)
    wr = (r["wins"]/r["trades"]*100) if r["trades"] else 0
    dd_pct = r["dd"] / CAPITAL * 100
    monthly = r["pnl"] / 8
    total_r["trades"] += r["trades"]
    total_r["pnl"] += r["pnl"]
    total_r["dd"] += r["dd"]
    print(f"\n  {sym:<10} lot={lot:.2f}")
    print(f"    Trades: {r['trades']}  WR: {wr:.0f}%")
    print(f"    Net:    ${r['pnl']:.2f}  (${monthly:.2f}/bln)")
    print(f"    MaxDD:  ${r['dd']:.2f}  ({dd_pct:.1f}%)")

print(f"\n  {'='*50}")
print(f"  TOTAL 3 PAIR lot 0.01:")
print(f"    Net:    ${total_r['pnl']:.2f}")
print(f"    Per bln: ${total_r['pnl']/8:.2f}")
print(f"    ROE:    {total_r['pnl']/CAPITAL*100:.0f}% (8 bln)")
print(f"    ROE/bln: {total_r['pnl']/CAPITAL/8:.1f}%")

# Try higher lots scaled
print(f"\n\n--- SCALING: lot 0.02 ---")
total_r2 = {"trades": 0, "pnl": 0.0, "dd": 0.0}
for sym in SYMBOLS:
    df = all_data[sym]
    is_jp = sym == "JP225m"
    r = backtest(df, 0.02, is_jp)
    dd_pct = r["dd"] / CAPITAL * 100
    monthly = r["pnl"] / 8
    total_r2["trades"] += r["trades"]; total_r2["pnl"] += r["pnl"]; total_r2["dd"] += r["dd"]
    print(f"  {sym:<10} Net=${r['pnl']:.2f}  DD=${r['dd']:.2f} ({dd_pct:.1f}%)")
print(f"  TOTAL: ${total_r2['pnl']:.2f}  ROE={total_r2['pnl']/CAPITAL*100:.0f}%  MaxDD={total_r2['dd']/CAPITAL*100:.1f}%")

print(f"\n\n--- SCALING: lot 0.05 ---")
total_r3 = {"trades": 0, "pnl": 0.0, "dd": 0.0}
for sym in SYMBOLS:
    df = all_data[sym]
    is_jp = sym == "JP225m"
    r = backtest(df, 0.05, is_jp)
    dd_pct = r["dd"] / CAPITAL * 100
    monthly = r["pnl"] / 8
    total_r3["trades"] += r["trades"]; total_r3["pnl"] += r["pnl"]; total_r3["dd"] += r["dd"]
    print(f"  {sym:<10} Net=${r['pnl']:.2f}  DD=${r['dd']:.2f} ({dd_pct:.1f}%)")
print(f"  TOTAL: ${total_r3['pnl']:.2f}  ROE={total_r3['pnl']/CAPITAL*100:.0f}%  MaxDD={total_r3['dd']/CAPITAL*100:.1f}%")

print(f"\n\n--- SCALING: lot 0.1 ---")
total_r4 = {"trades": 0, "pnl": 0.0, "dd": 0.0}
for sym in SYMBOLS:
    df = all_data[sym]
    is_jp = sym == "JP225m"
    r = backtest(df, 0.1, is_jp)
    dd_pct = r["dd"] / CAPITAL * 100
    monthly = r["pnl"] / 8
    total_r4["trades"] += r["trades"]; total_r4["pnl"] += r["pnl"]; total_r4["dd"] += r["dd"]
    print(f"  {sym:<10} Net=${r['pnl']:.2f}  DD=${r['dd']:.2f} ({dd_pct:.1f}%)")
print(f"  TOTAL: ${total_r4['pnl']:.2f}  ROE={total_r4['pnl']/CAPITAL*100:.0f}%  MaxDD={total_r4['dd']/CAPITAL*100:.1f}%")

print("\nDone.")
