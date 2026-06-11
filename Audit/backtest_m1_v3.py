"""M1 backtest with copy_rates_from_pos (batch approach)"""
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime

mt5.initialize()
LOT = 0.01; SL_M = 0.3; TP_M = 0.6; TRAIL_M = 0.2

# Load M1 data in chunks (max ~40K bars per call)
def load_m1(symbol, n_bars=60000):
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, n_bars)
    if rates is None: return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df.columns = [c.lower() for c in df.columns]
    return df

for symbol, label in [("XAUUSDm", "XAUUSD"), ("US30m", "US30")]:
    mt5.symbol_select(symbol, True)
    df = load_m1(symbol, 60000)
    if df is None or len(df) < 1000:
        print(f"{label} M1: no data"); continue

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
    days = round((df.index[-1] - df.index[0]).total_seconds() / 86400, 1)

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
        freq = len(trades) / days if days > 0 else 0
        print(f"{label:8} M1 {strat:<12} {len(trades):>4} trades in {days:.0f}d ({freq:.1f}/day)  WR={wins/len(trades)*100 if trades else 0:.0f}% PF={pf:.2f} Net=${sum(trades):+.2f}")

    # Same period M5 comparison
    bars5 = int(60000 / 5)
    rates5 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, bars5)
    if rates5 is not None:
        df5 = pd.DataFrame(rates5)
        df5["time"] = pd.to_datetime(df5["time"], unit="s")
        df5.set_index("time", inplace=True)
        df5.columns = [c.lower() for c in df5.columns]
        c5 = df5["close"]; h5 = df5["high"]; l5 = df5["low"]
        df5["ema8"] = c5.ewm(span=8, adjust=False).mean()
        df5["ema34"] = c5.ewm(span=34, adjust=False).mean()
        tr5 = pd.concat([h5-l5, (h5-c5.shift()).abs(), (l5-c5.shift()).abs()], axis=1).max(axis=1)
        df5["ema20"] = c5.ewm(span=20, adjust=False).mean()
        df5["atr"] = tr5.ewm(span=14, adjust=False).mean()
        df5.dropna(inplace=True)
        days5 = round((df5.index[-1] - df5.index[0]).total_seconds() / 86400, 1)

        # EMA cross trades
        trades5 = []; active5 = None; cl5 = 0
        for idx in range(1, len(df5)):
            bar = df5.iloc[idx]; prev = df5.iloc[idx-1]
            if cl5 >= 5: continue
            sig = None
            if prev["ema8"] <= prev["ema34"] and bar["ema8"] > bar["ema34"]: sig = "BUY"
            elif prev["ema8"] >= prev["ema34"] and bar["ema8"] < bar["ema34"]: sig = "SELL"
            if sig:
                sl = round(bar["close"] - bar["atr"] * SL_M, 2) if sig == "BUY" else round(bar["close"] + bar["atr"] * SL_M, 2)
                tp = round(bar["close"] + bar["atr"] * TP_M, 2) if sig == "BUY" else round(bar["close"] - bar["atr"] * TP_M, 2)
                active5 = {"side": sig, "entry": bar["close"], "sl": sl, "tp": tp, "atr": bar["atr"]}
            if active5:
                if TRAIL_M > 0:
                    tp = active5["atr"] * TRAIL_M
                    if active5["side"] == "BUY":
                        pf = bar["close"] - active5["entry"]
                        if pf > tp and bar["close"] - tp > active5["sl"]: active5["sl"] = bar["close"] - tp
                    else:
                        pf = active5["entry"] - bar["close"]
                        if pf > tp and bar["close"] + tp < active5["sl"]: active5["sl"] = bar["close"] + tp
                ep = None
                if active5["side"] == "BUY":
                    if bar["high"] >= active5["tp"]: ep = active5["tp"]
                    elif bar["low"] <= active5["sl"]: ep = active5["sl"]
                else:
                    if bar["low"] <= active5["tp"]: ep = active5["tp"]
                    elif bar["high"] >= active5["sl"]: ep = active5["sl"]
                if ep:
                    pnl = (ep - active5["entry"]) * LOT * 100 if active5["side"] == "BUY" else (active5["entry"] - ep) * LOT * 100
                    trades5.append(pnl)
                    if pnl > 0: cl5 = 0
                    else: cl5 += 1
                    active5 = None

        wins5 = sum(1 for p in trades5 if p > 0)
        gp5 = sum(p for p in trades5 if p > 0)
        gl5 = abs(sum(p for p in trades5 if p < 0))
        pf5 = gp5 / gl5 if gl5 else 0
        freq5 = len(trades5) / days5 if days5 > 0 else 0
        print(f"{label:8} M5 EMA_CROSS  {len(trades5):>4} trades in {days5:.0f}d ({freq5:.1f}/day)  WR={wins5/len(trades5)*100 if trades5 else 0:.0f}% PF={pf5:.2f} Net=${sum(trades5):+.2f}")

    print()

mt5.shutdown()
