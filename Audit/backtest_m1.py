"""Backtest on M1 to compare trade frequency vs M5"""
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime

LOT = 0.01; SL_M = 0.3; TP_M = 0.6; TRAIL_M = 0.2


def load_m1(symbol):
    mt5.initialize()
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1,
                                 datetime(2025, 10, 1), datetime(2026, 6, 10))
    mt5.shutdown()
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df.columns = [c.lower() for c in df.columns]
    c, h, l = df["close"], df["high"], df["low"]
    df["ema8"] = c.ewm(span=8, adjust=False).mean()
    df["ema20"] = c.ewm(span=20, adjust=False).mean()
    df["ema34"] = c.ewm(span=34, adjust=False).mean()
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=14, adjust=False).mean()
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(span=7, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(span=7, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    df["mom"] = c.pct_change(5)
    df.dropna(inplace=True)
    return df


def run(sym, label, df, strat_name):
    trades = []; active = None; cons_loss = 0
    for idx in range(1, len(df)):
        bar = df.iloc[idx]; prev = df.iloc[idx-1]
        if cons_loss >= 5: continue
        trend_up = bar["ema8"] > bar["ema34"]
        trend_dn = bar["ema8"] < bar["ema34"]

        # Manage active
        if active:
            if TRAIL_M > 0:
                trail_pts = active["atr"] * TRAIL_M
                if active["side"] == "BUY":
                    pf = bar["close"] - active["entry"]
                    if pf > trail_pts:
                        ns = bar["close"] - trail_pts
                        if ns > active["sl"]: active["sl"] = ns
                else:
                    pf = active["entry"] - bar["close"]
                    if pf > trail_pts:
                        ns = bar["close"] + trail_pts
                        if ns < active["sl"]: active["sl"] = ns
            exit_p, exit_r = None, None
            if active["side"] == "BUY":
                if bar["high"] >= active["tp"]: exit_p, exit_r = active["tp"], "TP"
                elif bar["low"] <= active["sl"]: exit_p, exit_r = active["sl"], "SL"
            else:
                if bar["low"] <= active["tp"]: exit_p, exit_r = active["tp"], "TP"
                elif bar["high"] >= active["sl"]: exit_p, exit_r = active["sl"], "SL"
            if exit_p:
                if active["side"] == "BUY": pnl = (exit_p - active["entry"]) * LOT * 100
                else: pnl = (active["entry"] - exit_p) * LOT * 100
                trades.append({**active, "exit": exit_p, "reason": exit_r, "pnl": pnl})
                if pnl > 0: cons_loss = 0
                else: cons_loss += 1
                active = None; continue
            else: continue

        # Signal
        signal = None
        if strat_name == "EMA_CROSS":
            if prev["ema8"] <= prev["ema34"] and bar["ema8"] > bar["ema34"]: signal = ("BUY", "EMA")
            elif prev["ema8"] >= prev["ema34"] and bar["ema8"] < bar["ema34"]: signal = ("SELL", "EMA")
        elif strat_name == "RSI_TREND":
            if trend_up and bar["rsi"] < 30: signal = ("BUY", "RSI")
            elif trend_dn and bar["rsi"] > 70: signal = ("SELL", "RSI")
        elif strat_name == "MOMENTUM":
            if bar["mom"] > 0.0005 and trend_up: signal = ("BUY", "MOM")
            elif bar["mom"] < -0.0005 and trend_dn: signal = ("SELL", "MOM")

        if not signal: continue
        side, reason = signal
        if side == "BUY":
            sl = round(bar["close"] - bar["atr"] * SL_M, 2)
            tp = round(bar["close"] + bar["atr"] * TP_M, 2)
        else:
            sl = round(bar["close"] + bar["atr"] * SL_M, 2)
            tp = round(bar["close"] - bar["atr"] * TP_M, 2)
        active = {"side": side, "reason": reason, "entry": bar["close"],
                  "sl": sl, "tp": tp, "atr": bar["atr"], "time": bar.name}

    wins = sum(1 for t in trades if t["pnl"] > 0)
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    peak = 10000.0; dd_max = 0.0; cur = 10000.0
    for t in trades:
        cur += t["pnl"]
        dd = (peak - cur) / peak * 100
        if dd > dd_max: dd_max = dd
        if cur > peak: peak = cur
    return f"{label:8} {strat_name:<12} {len(trades):>5} ({len(trades)/240:.1f}/day) {wins/len(trades)*100 if trades else 0:>4.1f}% {gp/gl if gl else 0:>4.2f}  ${sum(t['pnl'] for t in trades):>+7.2f}  {dd_max:>4.2f}%"


symbols = [("XAUUSDm", "XAUUSD"), ("US30m", "US30")]
strats = ["EMA_CROSS", "RSI_TREND", "MOMENTUM"]

print(f"\n{'~'*65}")
print("  M1 BACKTEST — perbandingan vs M5")
print(f"{'~'*65}")
print(f"  {'Symbol':8} {'Strategy':<12} {'Trades':>5} {'Freq':>11} {'WR':>5} {'PF':>5} {'Net$':>7} {'DD%':>5}")
print(f"  {'-'*60}")

for mt5sym, label in symbols:
    df = load_m1(mt5sym)
    print(f"\n  {label} — {len(df):,} bars M1 (target ~240 hari)")
    for s in strats:
        print(f"  {run(mt5sym, label, df, s)}")
