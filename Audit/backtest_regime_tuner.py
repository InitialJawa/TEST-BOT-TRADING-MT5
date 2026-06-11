"""
Backtest Regime-Based Auto-Tuner
Period: Oct 2025 - Jun 2026 | M5 | Lot 0.01

Approach:
  1. Full-period backtest -> cari strategi terbaik PER REGIME per pair
  2. Walk-forward mingguan: deteksi regime -> pakai strategi terbaik untuk regime itu
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

SYMBOLS = ["XAUUSDm", "US30m", "JP225m"]
START = "2025-10-01"
END = "2026-06-10"

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
    # ADX
    df["up"] = (h - h.shift()).clip(lower=0)
    df["down"] = (l.shift() - l).clip(lower=0)
    df["dx"] = 100 * (df["up"].ewm(span=14).mean() - df["down"].ewm(span=14).mean()).abs() / \
               (df["up"].ewm(span=14).mean() + df["down"].ewm(span=14).mean() + 1e-10)
    df["adx"] = df["dx"].ewm(span=14).mean()
    df["ema200"] = c.ewm(span=200, adjust=False).mean()
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
# BACKTEST ENGINE
# ============================================================
def backtest_strategy(df, strat_fn, lot=LOT):
    results = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0,
               "gross_p": 0, "gross_l": 0}
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
                pnl_usd = pnl * lot * 100
                results["trades"] += 1; results["pnl"] += pnl_usd
                if pnl_usd > 0:
                    results["wins"] += 1; results["gross_p"] += pnl_usd
                else:
                    results["losses"] += 1; results["gross_l"] += abs(pnl_usd)
                active = False
            continue
        if bar["spread"] > MAX_SPREAD: continue
        sig = strat_fn(df, idx)
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
# REGIME DETECTION
# ============================================================
def detect_regime(df_window):
    """Detect dominant regime from a window of bars."""
    avg_adx = df_window["adx"].mean()
    avg_atr = df_window["atr"].mean()
    avg_close = df_window["close"].mean()
    avg_ema200 = df_window["ema200"].mean()

    # ADX-based: trend or range
    if avg_adx >= 25:
        regime = "TREND"
    elif avg_adx <= 20:
        regime = "RANGE"
    else:
        regime = "MIXED"

    # Volatility
    vol = "NORMAL"
    if len(df_window) >= 100:
        p80 = df_window["atr"].quantile(0.8)
        p20 = df_window["atr"].quantile(0.2)
        if avg_atr > p80: vol = "HIGH_VOL"
        elif avg_atr < p20: vol = "LOW_VOL"

    # Direction
    direction = "BULL" if avg_close > avg_ema200 else "BEAR"

    return {"trend": regime, "vol": vol, "dir": direction}

# ============================================================
# PHASE 1: Full-period backtest per regime
# ============================================================
def calc_regime_performance(df):
    """Backtest all strategies split by regime."""
    regimes = {}
    for idx in range(len(df)):
        bar = df.iloc[idx]
        if idx < 100: continue
        window = df.iloc[idx-100:idx]
        r = detect_regime(window)
        regimes[bar.name] = r

    results = {s: {"TREND": [], "RANGE": [], "MIXED": [],
                   "HIGH_VOL": [], "LOW_VOL": [], "NORMAL": [],
                   "BULL": [], "BEAR": []}
               for s in STRATEGIES}

    # Track per-bar PnL per strategy
    active = {s: False for s in STRATEGIES}
    side = {s: 0 for s in STRATEGIES}
    entry = {s: 0 for s in STRATEGIES}
    sl = {s: 0 for s in STRATEGIES}
    tp = {s: 0 for s in STRATEGIES}
    atr_e = {s: 0 for s in STRATEGIES}

    for idx in range(1, len(df)):
        bar = df.iloc[idx]
        date = bar.name
        if date not in regimes: continue

        for sname in STRATEGIES:
            act = active[sname]
            if act:
                if TRAIL_ACT and atr_e[sname]:
                    act_val = atr_e[sname] * TRAIL_ACT
                    if side[sname] == 1:
                        p = bar["close"] - entry[sname]
                        if p > act_val:
                            ns = bar["close"] - act_val
                            if ns > sl[sname]: sl[sname] = ns
                    else:
                        p = entry[sname] - bar["close"]
                        if p > act_val:
                            ns = bar["close"] + act_val
                            if ns < sl[sname]: sl[sname] = ns

                exit_px = None
                if (side[sname] == 1 and bar["high"] >= tp[sname]) or \
                   (side[sname] == -1 and bar["low"] <= tp[sname]):
                    exit_px = tp[sname]
                elif (side[sname] == 1 and bar["low"] <= sl[sname]) or \
                     (side[sname] == -1 and bar["high"] >= sl[sname]):
                    exit_px = sl[sname]

                if exit_px:
                    pnl = (exit_px - entry[sname]) if side[sname] == 1 else (entry[sname] - exit_px)
                    pnl_usd = pnl * LOT * 100
                    results[sname][regimes[date]["trend"]].append(pnl_usd)
                    results[sname][regimes[date]["vol"]].append(pnl_usd)
                    results[sname][regimes[date]["dir"]].append(pnl_usd)
                    active[sname] = False
                continue

            if bar["spread"] > MAX_SPREAD: continue
            sig = STRATEGIES[sname](df, idx)
            if sig == 0: continue
            side[sname] = sig; entry[sname] = bar["close"]
            atr_e[sname] = bar["atr"]
            if side[sname] == 1:
                sl[sname] = entry[sname] - atr_e[sname] * SL_ATR
                tp[sname] = entry[sname] + atr_e[sname] * TP_ATR
            else:
                sl[sname] = entry[sname] + atr_e[sname] * SL_ATR
                tp[sname] = entry[sname] - atr_e[sname] * TP_ATR
            active[sname] = True

    # Aggregate
    agg = {}
    for s in STRATEGIES:
        agg[s] = {}
        for regime, pnls in results[s].items():
            if len(pnls) < 5:
                agg[s][regime] = None
                continue
            gross_p = sum(p for p in pnls if p > 0)
            gross_l = sum(abs(p) for p in pnls if p < 0)
            wins = sum(1 for p in pnls if p > 0)
            trades = len(pnls)
            pf = gross_p / gross_l if gross_l > 0 else float("inf")
            agg[s][regime] = {
                "pnl": sum(pnls), "trades": trades, "wins": wins,
                "wr": wins / trades * 100, "pf": pf,
                "gross_p": gross_p, "gross_l": gross_l,
            }
    return agg, regimes

# ============================================================
# PHASE 2: Walk-forward regime switching
# ============================================================
def backtest_regime_tuner(df, regime_map, sym):
    """Walk forward weekly, detect regime, use best strategy for that regime."""
    weekly_split = df.resample("W-MON")
    cum = {"trades": 0, "wins": 0, "pnl": 0.0, "gross_p": 0, "gross_l": 0}
    log_switches = []
    current_strat = None

    for week_start, week_df in weekly_split:
        if len(week_df) < 100:
            continue

        # Detect regime for this week
        r = detect_regime(week_df)
        regime_label = r["trend"]  # TREND, RANGE, MIXED
        # Also consider vol + dir as tiebreakers

        # Pick best strategy for this regime
        candidates = []
        for sname in STRATEGIES:
            perf = regime_map[sname][regime_label]
            if perf is None: continue
            candidates.append((perf["pf"], perf["pnl"], sname))

        if not candidates:
            continue

        # Sort by profit factor then total PnL
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        best = candidates[0][2]

        if best != current_strat and current_strat is not None:
            log_switches.append(f"  {week_start.date()} {current_strat}->{best} (regime={regime_label})")
        current_strat = best

        # Trade this week with chosen strategy
        r2 = backtest_strategy(week_df, STRATEGIES[current_strat])
        cum["trades"] += r2["trades"]; cum["wins"] += r2["wins"]
        cum["pnl"] += r2["pnl"]; cum["gross_p"] += r2["gross_p"]
        cum["gross_l"] += r2["gross_l"]

    pf = cum["gross_p"] / cum["gross_l"] if cum["gross_l"] > 0 else float("inf")
    wr = (cum["wins"] / cum["trades"] * 100) if cum["trades"] else 0
    return cum, pf, wr, log_switches, current_strat

# ============================================================
# RUN
# ============================================================
print("\n" + "=" * 90)
print("  BACKTEST — REGIME-BASED AUTO-TUNER")
print("  Period: Oct 2025 - Jun 2026 | M5 | Lot 0.01")
print("=" * 90)

all_data = {}
for sym in SYMBOLS:
    print(f"\nLoading {sym}...")
    df = load_data(sym)
    df = prep_data(df)
    all_data[sym] = df

# ---- Phase 1: Full backtest per regime ----
print("\n\n--- PHASE 1: REGIME PERFORMANCE TABLE ---")
regime_maps = {}
for sym in SYMBOLS:
    df = all_data[sym]
    agg, _ = calc_regime_performance(df)
    regime_maps[sym] = agg

    print(f"\n  {sym}:")
    h = f"  {'Strategi':<12} {'Kondisi':<10} {'Trades':>6} {'WR%':>5} {'PF':>5} {'Net $':>8}"
    print(h)
    print(f"  {'-'*54}")
    for sname in STRATEGIES:
            for regime in ["TREND", "RANGE", "MIXED", "HIGH_VOL", "LOW_VOL", "NORMAL", "BULL", "BEAR"]:
                r = agg[sname][regime]
                if r is None: continue
                pf_s = f"{r['pf']:.2f}" if r['pf'] != float("inf") else "inf"
                print(f"  {sname:<12} {regime:<10} {r['trades']:>6} {r['wr']:>4.0f}% {pf_s:>5} ${r['pnl']:>6.2f}")

# Print best per regime
print("\n\n  REKOMENDASI PER REGIME:")
for sym in SYMBOLS:
    print(f"\n  {sym}:")
    for regime in ["TREND", "RANGE", "MIXED", "HIGH_VOL", "LOW_VOL", "NORMAL", "BULL", "BEAR"]:
        best = None; best_score = -999
        for sname in STRATEGIES:
            r = regime_maps[sym][sname][regime]
            if r is None: continue
            score = r["pf"] * r["pnl"]  # composite score
            if score > best_score:
                best_score = score
                best = (sname, r["trades"], r["wr"], r["pf"], r["pnl"])
        if best:
            print(f"    {regime:<10} -> {best[0]:<12} ({best[1]} tr, WR={best[2]:.0f}%, PF={best[3]:.2f}, ${best[4]:+.2f})")

# ---- Static baseline ----
print("\n\n--- STATIC BASELINE ---")
for sym in SYMBOLS:
    df = all_data[sym]
    best_s = ""; best_net = -999
    for sname in STRATEGIES:
        r = backtest_strategy(df, STRATEGIES[sname])
        if r["pnl"] > best_net:
            best_net = r["pnl"]; best_s = sname
    for sname in STRATEGIES:
        r = backtest_strategy(df, STRATEGIES[sname])
        wr = (r["wins"]/r["trades"]*100) if r["trades"] else 0
        pf = r["gross_p"]/r["gross_l"] if r["gross_l"] > 0 else float("inf")
        pf_s = f"{pf:.2f}" if pf != float("inf") else "inf"
        tag = " << BEST" if sname == best_s else ""
        print(f"  {sym:<10} {sname:<12} trades={r['trades']:>5} WR={wr:>4.0f}% PF={pf_s:>6} Net=${r['pnl']:>8.2f}{tag}")

# ---- Phase 2: Walk-forward regime tuner ----
print("\n\n--- PHASE 2: REGIME TUNER (walk forward weekly) ---")
tuner_total = {"trades": 0, "wins": 0, "pnl": 0.0, "gross_p": 0, "gross_l": 0}
for sym in SYMBOLS:
    df = all_data[sym]
    cum, pf, wr_total, switches, final_strat = backtest_regime_tuner(df, regime_maps[sym], sym)
    pf_s = f"{pf:.2f}" if pf != float("inf") else "inf"
    print(f"\n  {sym}:")
    for s in switches: print(s)
    print(f"  -> Final strategy: {final_strat}")
    print(f"  -> Total: trades={cum['trades']} WR={wr_total:.0f}% PF={pf_s} Net=${cum['pnl']:.2f}")
    for k in tuner_total: tuner_total[k] += cum[k]

pt = tuner_total["gross_p"] / tuner_total["gross_l"] if tuner_total["gross_l"] > 0 else float("inf")
wt = (tuner_total["wins"] / tuner_total["trades"] * 100) if tuner_total["trades"] else 0
print(f"\n  {'='*40}")
print(f"  TUNER TOTAL: trades={tuner_total['trades']} WR={wt:.0f}% PF={pt:.2f} Net=${tuner_total['pnl']:.2f}")

# ---- Static best strategy ----
print("\n\n--- STATIC BEST STRATEGY (max net profit) ---")
static_total = {"trades": 0, "wins": 0, "pnl": 0.0, "gross_p": 0, "gross_l": 0}
for sym in SYMBOLS:
    df = all_data[sym]
    best_s = ""; best_r = None
    for sname in STRATEGIES:
        r = backtest_strategy(df, STRATEGIES[sname])
        if r["pnl"] > (best_r["pnl"] if best_r else -99999):
            best_r = r; best_s = sname
    for k in static_total: static_total[k] += best_r[k]
    wr = (best_r["wins"]/best_r["trades"]*100) if best_r["trades"] else 0
    pf = best_r["gross_p"]/best_r["gross_l"] if best_r["gross_l"] > 0 else float("inf")
    pf_s = f"{pf:.2f}" if pf != float("inf") else "inf"
    print(f"  {sym:<10} {best_s:<12} trades={best_r['trades']:>5} WR={wr:>4.0f}% PF={pf_s:>6} Net=${best_r['pnl']:>8.2f}")
ps = static_total["gross_p"] / static_total["gross_l"] if static_total["gross_l"] > 0 else float("inf")
ws = (static_total["wins"] / static_total["trades"] * 100) if static_total["trades"] else 0
print(f"  {'':20} {'-'*44}")
print(f"  STATIC TOTAL: trades={static_total['trades']} WR={ws:.0f}% PF={ps:.2f} Net=${static_total['pnl']:.2f}")

# ---- Current static ----
print("\n\n--- CURRENT STATIC STRATEGIES ---")
CURRENT = {"XAUUSDm": "MOMENTUM", "US30m": "PULLBACK", "JP225m": "MOMENTUM"}
cur_total = {"trades": 0, "wins": 0, "pnl": 0.0, "gross_p": 0, "gross_l": 0}
for sym in SYMBOLS:
    df = all_data[sym]
    sn = CURRENT[sym]
    r = backtest_strategy(df, STRATEGIES[sn])
    for k in cur_total: cur_total[k] += r[k]
    wr = (r["wins"]/r["trades"]*100) if r["trades"] else 0
    pf = r["gross_p"]/r["gross_l"] if r["gross_l"] > 0 else float("inf")
    pf_s = f"{pf:.2f}" if pf != float("inf") else "inf"
    print(f"  {sym:<10} {sn:<12} trades={r['trades']:>5} WR={wr:>4.0f}% PF={pf_s:>6} Net=${r['pnl']:>8.2f}")
pc = cur_total["gross_p"] / cur_total["gross_l"] if cur_total["gross_l"] > 0 else float("inf")
wc = (cur_total["wins"] / cur_total["trades"] * 100) if cur_total["trades"] else 0
print(f"  {'':20} {'-'*44}")
print(f"  CURRENT TOTAL: trades={cur_total['trades']} WR={wc:.0f}% PF={pc:.2f} Net=${cur_total['pnl']:.2f}")

# ---- FINAL COMPARISON ----
print("\n\n")
print("=" * 90)
print("  FINAL COMPARISON")
print("=" * 90)
print(f"  {'Method':<25} {'Trades':>7} {'WR%':>5} {'PF':>6} {'Net $':>10}")
print(f"  {'-'*60}")
print(f"  {'Static Best':<25} {static_total['trades']:>7} {ws:>4.0f}% {ps:>5.2f}  ${static_total['pnl']:>8.2f}")
print(f"  {'Current (MOM/PULL)':<25} {cur_total['trades']:>7} {wc:>4.0f}% {pc:>5.2f}  ${cur_total['pnl']:>8.2f}")
print(f"  {'Regime Tuner':<25} {tuner_total['trades']:>7} {wt:>4.0f}% {pt:>5.2f}  ${tuner_total['pnl']:>8.2f}")

print("\nDone.")
