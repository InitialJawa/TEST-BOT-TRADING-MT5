"""
Grid Search — Scalping Bot
Pass 1: Cari kombinasi EMA terbaik
Pass 2: Optimasi SL/TP + trailing
"""
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np
import MetaTrader5 as mt5


def load_data(symbol, start, end):
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5,
                                 datetime.strptime(start, "%Y-%m-%d"),
                                 datetime.strptime(end, "%Y-%m-%d"))
    mt5.shutdown()
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def run_bt(df, ef, es, sl_atr, tp_atr, trail, lot=0.01):
    c = df["close"]
    df["ef"] = c.ewm(span=ef, adjust=False).mean()
    df["es"] = c.ewm(span=es, adjust=False).mean()
    h, l = df["high"], df["low"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=14, adjust=False).mean()
    df.dropna(inplace=True)

    pnls = []
    active = False
    side = entry = sl = tp = atr_e = 0

    for idx in range(1, len(df)):
        bar = df.iloc[idx]
        prev = df.iloc[idx - 1]

        if active:
            if trail and atr_e:
                act = atr_e * trail
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

            if (side == 1 and bar["high"] >= tp) or (side == -1 and bar["low"] <= tp):
                pnls.append(tp - entry if side == 1 else entry - tp)
                active = False
            elif (side == 1 and bar["low"] <= sl) or (side == -1 and bar["high"] >= sl):
                pnls.append(sl - entry if side == 1 else entry - sl)
                active = False
            continue

        if bar["spread"] > 300: continue

        bull = prev["ef"] <= prev["es"] and bar["ef"] > bar["es"]
        bear = prev["ef"] >= prev["es"] and bar["ef"] < bar["es"]
        if not (bull or bear): continue

        side = 1 if bull else -1
        entry = bar["close"]
        atr_e = bar["atr"]
        if side == 1:
            sl = entry - atr_e * sl_atr
            tp = entry + atr_e * tp_atr
        else:
            sl = entry + atr_e * sl_atr
            tp = entry - atr_e * tp_atr
        active = True

    n = len(pnls)
    if n < 3: return None

    pnls = np.array(pnls) * lot * 100
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    wr = len(wins) / n * 100
    gp = float(wins.sum()) if len(wins) > 0 else 0
    gl = float(abs(losses.sum())) if len(losses) > 0 else 0
    net = float(pnls.sum())
    pf = gp / gl if gl > 0 else float("inf")
    exp_val = float(pnls.mean())

    eq = 10000 + np.cumsum(pnls)
    dd = (eq - np.maximum.accumulate(eq)) / np.maximum.accumulate(eq) * 100
    mdd = float(dd.min())

    if n > 1 and pnls.std() > 0:
        sh = np.sqrt(252 * 24 * 12) * (pnls / 10000).mean() / (pnls / 10000).std()
    else:
        sh = 0

    return {"n": n, "wr": round(wr, 1), "pf": pf, "net": net,
            "exp": round(exp_val, 3), "mdd": round(mdd, 2), "sharpe": round(sh, 2)}


def main():
    symbol = "XAUUSDm"
    print(f"\n  Grid Search — {symbol} M5")
    print(f"  {'='*45}")

    print("  Loading 2 months data...", end=" ")
    df = load_data(symbol, "2026-03-01", "2026-05-01")
    print(f"{len(df):,} bars\n")

    # ========================
    # PASS 1: EMA search
    # ========================
    print("  PASS 1 — EMA Optimization (fixed SL/TP=0.6/1.0, trail=0.3)")
    print(f"  {'='*45}")
    ema_results = []
    for ef in [2, 3, 5, 8]:
        for es in [7, 10, 13, 21, 34]:
            if ef >= es: continue
            r = run_bt(df.copy(), ef, es, 0.6, 1.0, 0.3)
            if r:
                r.update({"ef": ef, "es": es})
                ema_results.append(r)
                print(f"    EMA {ef}/{es}: {r['n']} trades, WR={r['wr']}%, PF={r['pf']:.2f}, Net=${r['net']:+.2f}")

    ema_results.sort(key=lambda x: x.get("pf", 0) if x.get("pf", 0) != float("inf") else 9999, reverse=True)
    if not ema_results:
        print("  No valid results!")
        return

    print(f"\n  Top 5 EMA:")
    print(f"  {'Rank':<5} {'EMA':<8} {'Trades':<7} {'Win%':<7} {'PF':<7} {'Net$':<10} {'Sharpe':<7}")
    print(f"  {'-'*45}")
    for i, r in enumerate(ema_results[:5]):
        print(f"  {i+1:<5} {r['ef']}/{r['es']:<6} {r['n']:<7} {r['wr']:<7} {r['pf']:<7.2f} {r['net']:<+10.2f} {r['sharpe']:<7}")

    best_ema = ema_results[0]
    best_ef, best_es = best_ema["ef"], best_ema["es"]

    # ========================
    # PASS 2: SL/TP + trail
    # ========================
    print(f"\n  PASS 2 — SL/TP/Trailing Optimization (EMA {best_ef}/{best_es} fixed)")
    print(f"  {'='*45}")
    risk_results = []
    for sl_atr in [0.3, 0.4, 0.6, 0.8, 1.0, 1.5]:
        for tp_atr in [0.6, 0.8, 1.0, 1.2, 1.5, 2.0, 3.0]:
            for trail in [0, 0.2, 0.3, 0.5]:
                r = run_bt(df.copy(), best_ef, best_es, sl_atr, tp_atr, trail)
                if r:
                    r.update({"sl_atr": sl_atr, "tp_atr": tp_atr, "trail": trail, "ef": best_ef, "es": best_es})
                    risk_results.append(r)

    for metric in ["pf", "sharpe", "net", "n"]:
        valid = [r for r in risk_results if r.get(metric) not in (float("inf"), float("-inf"))]
        valid.sort(key=lambda x: x.get(metric, 0) if isinstance(x.get(metric), (int, float)) else 0, reverse=True)
        print(f"\n  Top 5 by {metric}:")
        print(f"  {'Rank':<5} {'SL/TP':<10} {'Trail':<6} {'Trades':<7} {'Win%':<7} {'PF':<7} {'Net$':<10} {'Sharpe':<7}")
        print(f"  {'-'*55}")
        for i, r in enumerate(valid[:5]):
            tr = f"{r['trail']}" if r['trail'] > 0 else "none"
            print(f"  {i+1:<5} {r['sl_atr']}/{r['tp_atr']:<7} {tr:<6} {r['n']:<7} {r['wr']:<7} {r['pf']:<7.2f} {r['net']:<+10.2f} {r['sharpe']:<7}")

    # Save
    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(exist_ok=True)
    results = {
        "ema_search": ema_results,
        "risk_search": risk_results,
        "best_ema": best_ema,
        "best_params": valid[0] if risk_results else None
    }
    with open(output_dir / "optimization_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Saved: output/optimization_results.json")


if __name__ == "__main__":
    main()
