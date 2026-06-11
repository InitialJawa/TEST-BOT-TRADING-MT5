"""
Backtest Fabio — 4 strategies x berbagai situasi market
Strategi: EMA_CROSS, MOMENTUM, PULLBACK, TREND_RE
Market regime: TREND, RANGE, HIGH_VOL, LOW_VOL, BULL, BEAR
Pair: XAUUSD, US30, JP225 — M5
"""
import json, sys
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np
import MetaTrader5 as mt5

# ============================================================
# 1. PARAMETER
# ============================================================
EMA_FAST = 8
EMA_SLOW = 34
ATR_PERIOD = 14
SL_ATR = 0.3
TP_ATR = 0.6
TRAIL_ACT = 0.2
MOM_THRESH = 0.0005
LOT = 0.01
MAX_SPREAD = 300

SYMBOLS = ["XAUUSDm", "US30m", "JP225m"]
START = "2025-10-01"
END = "2026-02-01"

OUT = Path(__file__).parent / "backtest_scalping" / "output"
OUT.mkdir(parents=True, exist_ok=True)

# ============================================================
# 2. LOAD DATA
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
# 3. INDIKATOR
# ============================================================
def prep_data(df):
    c = df["close"]
    df["ef"] = c.ewm(span=EMA_FAST, adjust=False).mean()
    df["es"] = c.ewm(span=EMA_SLOW, adjust=False).mean()
    h, l = df["high"], df["low"]
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=ATR_PERIOD, adjust=False).mean()

    # ADX
    df["up"] = (h - h.shift()).clip(lower=0)
    df["down"] = (l.shift() - l).clip(lower=0)
    df["dx"] = 100 * (df["up"].ewm(span=14).mean() - df["down"].ewm(span=14).mean()).abs() / \
               (df["up"].ewm(span=14).mean() + df["down"].ewm(span=14).mean() + 1e-10)
    df["adx"] = df["dx"].ewm(span=14).mean()

    # EMA 200 buat trend jangka panjang
    df["ema200"] = c.ewm(span=200, adjust=False).mean()

    df.dropna(inplace=True)
    return df

# ============================================================
# 4. MARKET REGIME DETECTION
# ============================================================
def detect_regime(df):
    conditions = {}
    for idx in range(len(df)):
        bar = df.iloc[idx]
        date = bar.name

        # Trend atau Range?
        adx = bar["adx"]
        trend = "TREND" if adx >= 25 else ("RANGE" if adx < 20 else "MIXED")

        # Volatility
        # Pake rolling quantile biar fair
        vol = "NORMAL"
        if idx >= 100:
            window = df.iloc[idx-100:idx]["atr"]
            if not window.empty and bar["atr"] > window.quantile(0.8):
                vol = "HIGH_VOL"
            elif not window.empty and bar["atr"] < window.quantile(0.2):
                vol = "LOW_VOL"

        # Trend arah (bull/bear)
        dir_ = "BULL" if bar["close"] > bar["ema200"] else "BEAR"

        conditions[date] = {"trend": trend, "vol": vol, "dir": dir_}
    return conditions

# ============================================================
# 5. STRATEGY ENGINES
# ============================================================
def sig_ema_cross(prev, curr):
    bull = prev["ef"] <= prev["es"] and curr["ef"] > curr["es"]
    bear = prev["ef"] >= prev["es"] and curr["ef"] < curr["es"]
    if bull: return 1
    if bear: return -1
    return 0

def sig_momentum(df, idx):
    if idx < 4: return 0
    trend_up = df.iloc[idx]["ef"] > df.iloc[idx]["es"]
    trend_down = df.iloc[idx]["ef"] < df.iloc[idx]["es"]
    if not trend_up and not trend_down: return 0
    mom = df.iloc[idx]["close"] / df.iloc[idx-3]["close"] - 1
    if trend_up and mom > MOM_THRESH: return 1
    if trend_down and mom < -MOM_THRESH: return -1
    return 0

def sig_pullback(df, idx):
    if idx < 2: return 0
    trend_up = df.iloc[idx]["ef"] > df.iloc[idx]["es"]
    trend_down = df.iloc[idx]["ef"] < df.iloc[idx]["es"]
    if not trend_up and not trend_down: return 0
    curr = df.iloc[idx]
    prev = df.iloc[idx-1]
    if trend_up and curr["low"] <= prev["ef"]: return 1
    if trend_down and curr["high"] >= prev["ef"]: return -1
    return 0

def sig_trend_re(df, idx):
    if idx < 2: return 0
    trend_up = df.iloc[idx]["ef"] > df.iloc[idx]["es"]
    trend_down = df.iloc[idx]["ef"] < df.iloc[idx]["es"]
    if not trend_up and not trend_down: return 0
    return 1 if trend_up else -1

STRATEGIES = {
    "EMA_CROSS": lambda df, i: sig_ema_cross(df.iloc[i-1], df.iloc[i]) if i > 0 else 0,
    "MOMENTUM":  lambda df, i: sig_momentum(df, i),
    "PULLBACK":  lambda df, i: sig_pullback(df, i),
    "TREND_RE":  lambda df, i: sig_trend_re(df, i),
}

# ============================================================
# 6. BACKTEST PER (SYMBOL, STRATEGY) — split by regime
# ============================================================
def backtest_per_regime(df, strat_fn, cur_regimes, lot=LOT):
    results = {r: {"trades": 0, "wins": 0, "losses": 0, "pnl": 0,
                   "gross_p": 0, "gross_l": 0, "bars": []}
               for r in ["TREND", "RANGE", "MIXED", "HIGH_VOL", "LOW_VOL", "BULL", "BEAR", "ALL"]}

    active = False
    side = entry = sl = tp = atr_e = 0
    entry_date = None
    regime_at_entry = None

    for idx in range(1, len(df)):
        bar = df.iloc[idx]
        prev = df.iloc[idx-1]
        date = bar.name

        # Regime for this bar
        r = cur_regimes[date]

        if active:
            # trailing
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

            exit_px = None; reason = ""
            if (side == 1 and bar["high"] >= tp) or (side == -1 and bar["low"] <= tp):
                exit_px = tp
                reason = "TP"
            elif (side == 1 and bar["low"] <= sl) or (side == -1 and bar["high"] >= sl):
                exit_px = sl
                reason = "SL"

            if exit_px:
                pnl = (exit_px - entry) if side == 1 else (entry - exit_px)
                pnl_usd = pnl * lot * 100
                for reg_key in [regime_at_entry, "ALL"]:
                    r2 = results[reg_key]
                    r2["trades"] += 1
                    r2["pnl"] += pnl_usd
                    r2["bars"].append((bar.name - entry_date).total_seconds() / 300)
                    if pnl_usd > 0:
                        r2["wins"] += 1; r2["gross_p"] += pnl_usd
                    else:
                        r2["losses"] += 1; r2["gross_l"] += abs(pnl_usd)
                active = False
            continue

        # Filter spread
        if bar["spread"] > MAX_SPREAD: continue

        # Signal
        sig = strat_fn(df, idx)
        if sig == 0: continue

        side = sig
        entry = bar["close"]
        entry_date = date
        atr_e = bar["atr"]
        regime_at_entry = r["trend"]
        if side == 1:
            sl = entry - atr_e * SL_ATR
            tp = entry + atr_e * TP_ATR
        else:
            sl = entry + atr_e * SL_ATR
            tp = entry - atr_e * TP_ATR
        active = True

    return results

# ============================================================
# 7. RUN
# ============================================================
REGIMES = {}  # akan diisi per symbol

all_results = {}

for sym in SYMBOLS:
    print(f"\nDownloading {sym}...")
    df = load_data(sym)
    df = prep_data(df)
    regimes = detect_regime(df)
    REGIMES[sym] = regimes
    print(f"  {len(df)} bars | "
          f"TREND={sum(1 for v in regimes.values() if v['trend']=='TREND')} "
          f"RANGE={sum(1 for v in regimes.values() if v['trend']=='RANGE')} "
          f"HIGH_VOL={sum(1 for v in regimes.values() if v['vol']=='HIGH_VOL')} "
          f"LOW_VOL={sum(1 for v in regimes.values() if v['vol']=='LOW_VOL')}")

    sym_results = {}
    for sname, sfn in STRATEGIES.items():
        res = backtest_per_regime(df, sfn, regimes)
        sym_results[sname] = res
    all_results[sym] = sym_results

# ============================================================
# 8. CETAK
# ============================================================
print("\n")
print("=" * 90)
print("  BACKTEST FABIO — 4 STRATEGI x MARKET CONDITION")
print("  Periode: Oct 2025 - Jun 2026 | M5 | Lot 0.01")
print("=" * 90)

for sym in SYMBOLS:
    header = f"  {'Strategi':<12} {'Kondisi':<10} {'Trades':>7} {'WR%':>5} {'PF':>5} {'Net $':>8} {'Avg$':>7} {'W/R':>5}"
    print(f"  --- {sym} ---")
    print(header)
    print(f"  {'-'*70}")

    sr = all_results[sym]

    # Aggregate by strategy
    for sname in ["EMA_CROSS", "MOMENTUM", "PULLBACK", "TREND_RE"]:
        base = sr[sname]["ALL"]
        if base["trades"] == 0: continue

        for regime_key, label in [("TREND", "TREND"), ("RANGE", "RANGE"),
                                   ("HIGH_VOL", "HIGH_VOL"), ("LOW_VOL", "LOW_VOL"),
                                   ("BULL", "BULL"), ("BEAR", "BEAR")]:
            r = sr[sname][regime_key]
            if r["trades"] == 0: continue
            wr = r["wins"] / r["trades"] * 100
            pf = r["gross_p"] / r["gross_l"] if r["gross_l"] > 0 else float("inf")
            avg = r["pnl"] / r["trades"]
            wrl = f"{r['wins']}/{r['losses']}"
            pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
            print(f"  {sname:<12} {label:<10} {r['trades']:>7} {wr:>4.0f}% {pf_str:>5} ${r['pnl']:>6.2f} ${avg:>5.2f} {wrl:>5}")

    # Total for this symbol
    for sname in ["EMA_CROSS", "MOMENTUM", "PULLBACK", "TREND_RE"]:
        r = sr[sname]["ALL"]
        if r["trades"] == 0: continue
        wr = r["wins"] / r["trades"] * 100
        pf = r["gross_p"] / r["gross_l"] if r["gross_l"] > 0 else float("inf")
        avg = r["pnl"] / r["trades"]
        pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"
        print(f"  {'-'*70}")
        print(f"  {sname:<12} {'ALL':<10} {r['trades']:>7} {wr:>4.0f}% {pf_str:>5} ${r['pnl']:>6.2f} ${avg:>5.2f}")

# ============================================================
# 9. REKOMENDASI
# ============================================================
print("\n")
print("=" * 90)
print("  REKOMENDASI: strategi terbaik per kondisi market")
print("=" * 90)

for sym in SYMBOLS:
    print(f"\n  {sym}:")
    sr = all_results[sym]
    for regime_key, label in [("TREND", "TREND"), ("RANGE", "RANGE"),
                               ("HIGH_VOL", "HIGH_VOL"), ("LOW_VOL", "LOW_VOL"),
                               ("BULL", "BULL"), ("BEAR", "BEAR")]:
        best = None; best_pf = -999
        for sname in ["EMA_CROSS", "MOMENTUM", "PULLBACK", "TREND_RE"]:
            r = sr[sname][regime_key]
            if r["trades"] < 5: continue
            pf = r["gross_p"] / r["gross_l"] if r["gross_l"] > 0 else 999
            if pf > best_pf:
                best_pf = pf
                best = (sname, r["trades"], r["wins"]/r["trades"]*100 if r["trades"] else 0, pf, r["pnl"])
        if best:
            print(f"    {label:<10} -> {best[0]:<12} ({best[1]} trades, WR={best[2]:.0f}%, PF={best[3]:.2f}, Net=${best[4]:+.2f})")
        else:
            print(f"    {label:<10} -> (no trades)")

mt5.shutdown()
print("\nDone.")
