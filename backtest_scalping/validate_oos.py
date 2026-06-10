"""
Validate best params on out-of-sample data (Oct 2025 - Jan 2026)
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


def run_bt(df, ef, es, sl_atr, tp_atr, trail, lot=0.01, label=""):
    c = df["close"]
    df["ef"] = c.ewm(span=ef, adjust=False).mean()
    df["es"] = c.ewm(span=es, adjust=False).mean()
    h, l = df["high"], df["low"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=14, adjust=False).mean()
    df.dropna(inplace=True)

    trades = []
    active = False
    side = entry = sl = tp = atr_e = 0

    for idx in range(1, len(df)):
        bar = df.iloc[idx]
        prev = df.iloc[idx - 1]
        date = bar.name

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
                pnl = (tp - entry) if side == 1 else (entry - tp)
                trades.append({"entry": str(entry_date), "exit": str(date), "side": "BUY" if side==1 else "SELL",
                               "entry_px": round(entry,2), "exit_px": round(tp if side==1 else tp,2),
                               "pnl": round(pnl,2), "reason": "TP"})
                active = False
            elif (side == 1 and bar["low"] <= sl) or (side == -1 and bar["high"] >= sl):
                pnl = (sl - entry) if side == 1 else (entry - sl)
                trades.append({"entry": str(entry_date), "exit": str(date), "side": "BUY" if side==1 else "SELL",
                               "entry_px": round(entry,2), "exit_px": round(sl if side==1 else sl,2),
                               "pnl": round(pnl,2), "reason": "SL"})
                active = False
            continue

        if bar["spread"] > 300: continue

        bull = prev["ef"] <= prev["es"] and bar["ef"] > bar["es"]
        bear = prev["ef"] >= prev["es"] and bar["ef"] < bar["es"]
        if not (bull or bear): continue

        side = 1 if bull else -1
        entry = bar["close"]
        entry_date = date
        atr_e = bar["atr"]
        if side == 1:
            sl = entry - atr_e * sl_atr
            tp = entry + atr_e * tp_atr
        else:
            sl = entry + atr_e * sl_atr
            tp = entry - atr_e * tp_atr
        active = True

    pnls = np.array([t["pnl"] for t in trades]) * lot * 100
    n = len(trades)
    if n == 0:
        print(f"  {label}: No trades")
        return

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

    print(f"  {label}: {n} trades, WR={wr:.1f}%, PF={pf:.2f}, Net=${net:+.2f}, "
          f"MaxDD={mdd:.2f}%, Sharpe={sh:.2f}, Avg=${exp_val:+.3f}/trade")


def main():
    symbol = "XAUUSDm"
    best_params = [
        {"ef": 8, "es": 34, "sl": 0.3, "tp": 0.6, "trail": 0.3, "label": "EMA 8/34 SL0.3 TP0.6 T0.3"},
        {"ef": 8, "es": 34, "sl": 0.3, "tp": 0.6, "trail": 0.2, "label": "EMA 8/34 SL0.3 TP0.6 T0.2"},
        {"ef": 8, "es": 34, "sl": 0.4, "tp": 0.6, "trail": 0.3, "label": "EMA 8/34 SL0.4 TP0.6 T0.3"},
    ]

    print("\n  ===============================================")
    print("  OUT-OF-SAMPLE VALIDATION")
    print("  In-sample:  Mar-May 2026")
    print("  Out-of-sample: Oct 2025 - Jan 2026")
    print("  ===============================================")

    print("\n  Loading out-of-sample data (Oct 2025 - Jan 2026)...")
    df_oos = load_data(symbol, "2025-10-01", "2026-02-01")
    print(f"  {len(df_oos):,} bars\n")

    for bp in best_params:
        run_bt(df_oos.copy(), bp["ef"], bp["es"], bp["sl"], bp["tp"], bp["trail"], label=bp["label"])
        print()

    print("  ===============================================")
    print("  IN-SAMPLE (Mar-May 2026) - from optimizer")
    print("  Best: EMA 8/34 SL0.3 TP0.6 T0.3 = 309 trades, WR=49.5%, PF=1.84, Net=$+266")
    print("  ===============================================")

    # Load optimizer results and print the best
    output_dir = Path(__file__).parent / "output"
    with open(output_dir / "optimization_results.json") as f:
        data = json.load(f)
    best = data["best_params"]
    print(f"\n  Best combo overall: EMA {best['ef']}/{best['es']}, SL/TP={best['sl_atr']}/{best['tp_atr']}, trail={best['trail']}")
    print(f"  In-sample: {best['n']} trades, WR={best['wr']}%, PF={best['pf']:.2f}, Net=${best['net']:+.2f}")


if __name__ == "__main__":
    main()
