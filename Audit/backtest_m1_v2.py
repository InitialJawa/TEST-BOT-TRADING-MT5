"""Quick M1 backtest - shorter period to compare"""
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime

mt5.initialize()
LOT = 0.01; SL_M = 0.3; TP_M = 0.6; TRAIL_M = 0.2

for symbol, label in [("XAUUSDm", "XAUUSD"), ("US30m", "US30")]:
    mt5.symbol_select(symbol, True)

    # Use 2 months for quick comparison
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1,
                                 datetime(2026, 4, 1), datetime(2026, 6, 10))
    if rates is None or len(rates) == 0:
        print(f"{label} M1: no data")
        continue
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df.columns = [c.lower() for c in df.columns]
    c, h, l = df["close"], df["high"], df["low"]

    df["ema8"] = c.ewm(span=8, adjust=False).mean()
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
    days = (df.index[-1] - df.index[0]).days

    for strat in ["EMA_CROSS", "RSI_TREND", "MOMENTUM"]:
        trades = []; active = None; cons_loss = 0
        for idx in range(1, len(df)):
            bar = df.iloc[idx]; prev = df.iloc[idx-1]
            if cons_loss >= 5: continue
            tu = bar["ema8"] > bar["ema34"]; td = bar["ema8"] < bar["ema34"]
            if active:
                if TRAIL_M > 0:
                    tp = active["atr"] * TRAIL_M
                    if active["side"] == "BUY":
                        pf = bar["close"] - active["entry"]
                        if pf > tp and bar["close"] - tp > active["sl"]: active["sl"] = bar["close"] - tp
                    else:
                        pf = active["entry"] - bar["close"]
                        if pf > tp and bar["close"] + tp < active["sl"]: active["sl"] = bar["close"] + tp
                ep, er = None, None
                if active["side"] == "BUY":
                    if bar["high"] >= active["tp"]: ep, er = active["tp"], "TP"
                    elif bar["low"] <= active["sl"]: ep, er = active["sl"], "SL"
                else:
                    if bar["low"] <= active["tp"]: ep, er = active["tp"], "TP"
                    elif bar["high"] >= active["sl"]: ep, er = active["sl"], "SL"
                if ep:
                    pnl = (ep - active["entry"]) * LOT * 100 if active["side"] == "BUY" else (active["entry"] - ep) * LOT * 100
                    trades.append(pnl)
                    if pnl > 0: cons_loss = 0
                    else: cons_loss += 1
                    active = None; continue
                else: continue
            sig = None
            if strat == "EMA_CROSS":
                if prev["ema8"] <= prev["ema34"] and bar["ema8"] > bar["ema34"]: sig = "BUY"
                elif prev["ema8"] >= prev["ema34"] and bar["ema8"] < bar["ema34"]: sig = "SELL"
            elif strat == "RSI_TREND":
                if tu and bar["rsi"] < 30: sig = "BUY"
                elif td and bar["rsi"] > 70: sig = "SELL"
            elif strat == "MOMENTUM":
                if bar["mom"] > 0.0005 and tu: sig = "BUY"
                elif bar["mom"] < -0.0005 and td: sig = "SELL"
            if not sig: continue
            sl = round(bar["close"] - bar["atr"] * SL_M, 2) if sig == "BUY" else round(bar["close"] + bar["atr"] * SL_M, 2)
            tp = round(bar["close"] + bar["atr"] * TP_M, 2) if sig == "BUY" else round(bar["close"] - bar["atr"] * TP_M, 2)
            active = {"side": sig, "entry": bar["close"], "sl": sl, "tp": tp, "atr": bar["atr"]}

        wins = sum(1 for p in trades if p > 0)
        gp = sum(p for p in trades if p > 0)
        gl = abs(sum(p for p in trades if p < 0))
        pf = gp / gl if gl else 0
        print(f"{label:8} M1 {strat:<12} {len(trades):>4} trades ({len(trades)/days:.1f}/day)  WR={wins/len(trades)*100 if trades else 0:.0f}% PF={pf:.2f} Net=${sum(trades):+.2f}")

    # Compare M5 same period
    rates5 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5,
                                  datetime(2026, 4, 1), datetime(2026, 6, 10))
    if rates5 is not None and len(rates5) > 0:
        df5 = pd.DataFrame(rates5)
        df5["time"] = pd.to_datetime(df5["time"], unit="s")
        df5.set_index("time", inplace=True)
        df5.columns = [c.lower() for c in df5.columns]
        c5 = df5["close"]; h5 = df5["high"]; l5 = df5["low"]
        df5["ema8"] = c5.ewm(span=8, adjust=False).mean()
        df5["ema34"] = c5.ewm(span=34, adjust=False).mean()
        tr5 = pd.concat([h5-l5, (h5-c5.shift()).abs(), (l5-c5.shift()).abs()], axis=1).max(axis=1)
        df5["atr"] = tr5.ewm(span=14, adjust=False).mean()
        df5.dropna(inplace=True)

        # EMA cross count
        cross = 0
        for i in range(1, len(df5)):
            if (df5.iloc[i-1]["ema8"] <= df5.iloc[i-1]["ema34"] and df5.iloc[i]["ema8"] > df5.iloc[i]["ema34"]) or \
               (df5.iloc[i-1]["ema8"] >= df5.iloc[i-1]["ema34"] and df5.iloc[i]["ema8"] < df5.iloc[i]["ema34"]):
                cross += 1
        print(f"{label:8} M5 EMA_X  {cross:>4} crosses ({cross/days:.1f}/day)")

    print()

mt5.shutdown()
