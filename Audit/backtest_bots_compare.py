"""
Backtest Perbandingan — bot_fabio vs bot_hendro
Semua TREND_RE, beda lot + session filter

Config:
  bot_fabio: lot XAU=0.1, US30=0.1, JP225=47.0, no filter
  bot_hendro: lot XAU=0.06, session 07-22 UTC (XAU only)
"""

import sys
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np
import MetaTrader5 as mt5

# ============================================================
# PARAMS
# ============================================================
EMA_FAST = 8; EMA_SLOW = 34; ATR_PERIOD = 14
SL_ATR = 0.3; TP_ATR = 0.6; TRAIL_ACT = 0.2
MAX_SPREAD = 300

SYMBOLS = ["XAUUSDm", "US30m", "JP225m"]
START = "2025-10-01"
END = "2026-06-10"

# Configs
BOTS = {
    "bot_fabio": {
        "XAUUSDm": {"lot": 0.1,  "magic": 25062026, "session": None},
        "US30m":   {"lot": 0.1,  "magic": 25062027, "session": None},
        "JP225m":  {"lot": 47.0, "magic": 25062028, "session": None},
    },
    "bot_hendro": {
        "XAUUSDm": {"lot": 0.06, "magic": 25062036, "session": ("07:00", "22:00")},
    },
}

OUT = Path(__file__).parent / "output_tuner"
OUT.mkdir(parents=True, exist_ok=True)

# ============================================================
# DATA
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
# TREND_RE SIGNAL
# ============================================================
def sig_trend_re(df, idx):
    if idx < 2: return 0
    tu = df.iloc[idx]["ef"] > df.iloc[idx]["es"]
    td = df.iloc[idx]["ef"] < df.iloc[idx]["es"]
    if not tu and not td: return 0
    return 1 if tu else -1

# ============================================================
# BACKTEST ENGINE (with optional session filter)
# ============================================================
def backtest_trend_re(df, lot, session=None, mult=100):
    results = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0,
               "gross_p": 0, "gross_l": 0}
    active = False
    side = entry = sl = tp = atr_e = 0

    for idx in range(1, len(df)):
        bar = df.iloc[idx]
        bar_time = bar.name

        # Session filter
        if session:
            t = bar_time.time()
            open_t = datetime.strptime(session[0], "%H:%M").time()
            close_t = datetime.strptime(session[1], "%H:%M").time()
            if open_t < close_t:
                if not (open_t <= t < close_t): continue
            else:
                if not (t >= open_t or t < close_t): continue

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
                pnl_usd = pnl * lot * mult
                results["trades"] += 1
                results["pnl"] += pnl_usd
                if pnl_usd > 0:
                    results["wins"] += 1; results["gross_p"] += pnl_usd
                else:
                    results["losses"] += 1; results["gross_l"] += abs(pnl_usd)
                active = False
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

    return results

# ============================================================
# PnL calculator with correct lot handling
# ============================================================
def calc_pnl(pnl_points, lot, is_jp225=False):
    if is_jp225:
        return pnl_points * lot
    return pnl_points * lot * 100

# Correct multiplier per symbol
MULT = {"XAUUSDm": 100, "US30m": 1, "JP225m": 1}

# ============================================================
# RUN
# ============================================================
print("\n" + "=" * 90)
print("  BACKTEST PERBANDINGAN — bot_fabio vs bot_hendro")
print("  Semua TREND_RE | Oct 2025 - Jun 2026 | M5")
print("=" * 90)
print(f"  Mult: XAU=100, US30=1, JP225=1 (corrected)")

# Load data
all_data = {}
for sym in SYMBOLS:
    print(f"\nLoading {sym}...")
    df = load_data(sym)
    df = prep_data(df)
    all_data[sym] = df

# Run per bot
for bot_name, pairs in BOTS.items():
    print(f"\n\n")
    print("=" * 90)
    print(f"  {bot_name.upper()}")
    print("=" * 90)

    total = {"trades": 0, "wins": 0, "pnl": 0.0, "gross_p": 0, "gross_l": 0}
    details = []

    for sym, cfg in pairs.items():
        df = all_data[sym]
        is_jp = sym == "JP225m"
        session = cfg["session"]
        lot = cfg["lot"]

        r = backtest_trend_re(df, lot, session, MULT.get(sym, 100))
        wr = (r["wins"] / r["trades"] * 100) if r["trades"] else 0
        pf = r["gross_p"] / r["gross_l"] if r["gross_l"] > 0 else float("inf")
        pf_s = f"{pf:.2f}" if pf != float("inf") else "inf"

        details.append((sym, lot, session, r, wr, pf_s, pf))
        for k in total: total[k] += r[k]

        # print per pair
        sesh = f" sess={session[0]}-{session[1]}UTC" if session else ""
        print(f"\n  {sym:<10} lot={lot:<6} magic={cfg['magic']}{sesh}")
        print(f"  {'-'*50}")
        print(f"    Trades: {r['trades']}")
        print(f"    WR:     {wr:.0f}%  ({r['wins']}/{r['trades']})")
        print(f"    PF:     {pf_s}")
        print(f"    Net:    ${r['pnl']:.2f}")
        print(f"    Avg:    ${r['pnl']/r['trades']:.2f}" if r['trades'] else "    Avg:   $0")
        if wr and pf != float("inf"):
            print(f"    Score:  {wr * pf:.0f}")

    pt = total["gross_p"] / total["gross_l"] if total["gross_l"] > 0 else float("inf")
    pt_s = f"{pt:.2f}" if pt != float("inf") else "inf"
    wt = (total["wins"] / total["trades"] * 100) if total["trades"] else 0
    print(f"\n  {'='*50}")
    print(f"  BOT TOTAL:")
    print(f"    Trades: {total['trades']}")
    print(f"    WR:     {wt:.0f}%")
    print(f"    PF:     {pt_s}")
    print(f"    Net:    ${total['pnl']:.2f}")

# ============================================================
# COMPARE
# ============================================================
print("\n\n")
print("=" * 110)
print("  COMPARISON — bot_fabio vs bot_hendro (all TREND_RE)")
print("=" * 110)

header = f"  {'Bot':<14} {'Pair':<10} {'Lot':>6} {'Session':<14} {'Trades':>7} {'WR%':>5} {'PF':>5} {'Net $':>10}"
print(header)
print(f"  {'-'*85}")

all_totals = {}
for bot_name, pairs in BOTS.items():
    total = {"trades": 0, "wins": 0, "pnl": 0.0, "gross_p": 0, "gross_l": 0}
    for sym, cfg in pairs.items():
        df = all_data[sym]
        session = cfg["session"]
        lot = cfg["lot"]
        r = backtest_trend_re(df, lot, session, MULT.get(sym, 100))
        wr = (r["wins"] / r["trades"] * 100) if r["trades"] else 0
        pf = r["gross_p"] / r["gross_l"] if r["gross_l"] > 0 else float("inf")
        pf_s = f"{pf:.2f}" if pf != float("inf") else "inf"
        sesh = f"{session[0]}-{session[1]}UTC" if session else "none"
        print(f"  {bot_name:<14} {sym:<10} {lot:>6.2f} {sesh:<14} {r['trades']:>7} {wr:>4.0f}% {pf_s:>5} ${r['pnl']:>8.2f}")
        for k in total: total[k] += r[k]
    all_totals[bot_name] = total

print(f"  {'-'*85}")
for bot_name, total in all_totals.items():
    wt = (total["wins"] / total["trades"] * 100) if total["trades"] else 0
    pt = total["gross_p"] / total["gross_l"] if total["gross_l"] > 0 else float("inf")
    pt_s = f"{pt:.2f}" if pt != float("inf") else "inf"
    print(f"  {bot_name:<14} {'TOTAL':<10} {'':>6} {'':<14} {total['trades']:>7} {wt:>4.0f}% {pt_s:>5} ${total['pnl']:>8.2f}")

# Per-pair comparison
print("\n\n")
print("=" * 80)
print("  PER-PAIR COMPARISON (XAUUSDm only for hendro)")
print("=" * 80)

for sym in SYMBOLS:
    df = all_data[sym]
    print(f"\n  {sym}:")
    for bot_name, pairs in BOTS.items():
        if sym not in pairs: continue
        cfg = pairs[sym]
        session = cfg["session"]
        lot = cfg["lot"]
        r = backtest_trend_re(df, lot, session, MULT.get(sym, 100))
        wr = (r["wins"] / r["trades"] * 100) if r["trades"] else 0
        pf = r["gross_p"] / r["gross_l"] if r["gross_l"] > 0 else float("inf")
        sesh = f" sess={session[0]}-{session[1]}UTC" if session else ""
        print(f"    {bot_name:<14} lot={lot:.2f}{sesh}")
        print(f"      trades={r['trades']} WR={wr:.0f}% PF={pf:.2f} Net=${r['pnl']:.2f}")

# What-if: fabio with session filter
print("\n\n")
print("=" * 80)
print("  WHAT-IF: bot_fabio + session filter 07-22 UTC (XAU)")
print("=" * 80)
for bot_name, pairs in BOTS.items():
    for sym in pairs:
        if sym != "XAUUSDm": continue
        df = all_data[sym]
        cfg = pairs[sym]
        lot = cfg["lot"]
        session = cfg["session"]

        # with filter
        r_filter = backtest_trend_re(df, lot, session, MULT.get(sym, 100))
        wr_f = (r_filter["wins"] / r_filter["trades"] * 100) if r_filter["trades"] else 0
        pf_f = r_filter["gross_p"] / r_filter["gross_l"] if r_filter["gross_l"] > 0 else float("inf")

        # without filter
        r_no = backtest_trend_re(df, lot, None, MULT.get(sym, 100))
        wr_n = (r_no["wins"] / r_no["trades"] * 100) if r_no["trades"] else 0
        pf_n = r_no["gross_p"] / r_no["gross_l"] if r_no["gross_l"] > 0 else float("inf")

        print(f"\n  XAUUSDm lot={lot}:")
        print(f"    No filter:    trades={r_no['trades']} WR={wr_n:.0f}% PF={pf_n:.2f} Net=${r_no['pnl']:.2f}")
        print(f"    Sess 07-22UTC: trades={r_filter['trades']} WR={wr_f:.0f}% PF={pf_f:.2f} Net=${r_filter['pnl']:.2f}")
        print(f"    Difference:    -{r_no['trades']-r_filter['trades']} trades  ${r_no['pnl']-r_filter['pnl']:.2f} PnL lost")

print("\nDone.")
