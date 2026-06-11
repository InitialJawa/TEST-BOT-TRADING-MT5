"""$200 backtest with FIXED 0.01 lot (realistic view)"""
import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime

mt5.initialize()
INITIAL = 200.0
SL_M = 0.3; TP_M = 0.6; TRAIL_M = 0.2

def load_data(s, n=80000):
    mt5.symbol_select(s, True)
    r = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_M1, 0, n)
    if r is None: return None
    df = pd.DataFrame(r)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df.columns = [c.lower() for c in df.columns]
    c, h, l = df["close"], df["high"], df["low"]
    df["ema8"] = c.ewm(span=8, adjust=False).mean()
    df["ema34"] = c.ewm(span=34, adjust=False).mean()
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=14, adjust=False).mean()
    d = c.diff()
    g = d.clip(lower=0).ewm(span=7, adjust=False).mean()
    ls = (-d.clip(upper=0)).ewm(span=7, adjust=False).mean()
    df["rsi"] = 100 - (100 / (1 + g / ls.replace(0, np.nan)))
    df["mom"] = c.pct_change(5)
    df.dropna(inplace=True)
    return df

def run_bt(sym, label, strat, mult, spread_pts=0):
    df = load_data(sym, 80000)
    if df is None: return
    balance = INITIAL
    trades = []
    active = None
    for idx in range(1, len(df)):
        bar = df.iloc[idx]; prev = df.iloc[idx-1]
        if balance < 10: break
        tu = bar["ema8"] > bar["ema34"]; td = bar["ema8"] < bar["ema34"]

        if active:
            if TRAIL_M > 0:
                tl = active["atr"] * TRAIL_M
                if active["side"] == "BUY":
                    pf = bar["close"] - active["entry"]
                    if pf > tl and bar["close"] - tl > active["sl"]:
                        active["sl"] = bar["close"] - tl
                else:
                    pf = active["entry"] - bar["close"]
                    if pf > tl and bar["close"] + tl < active["sl"]:
                        active["sl"] = bar["close"] + tl
            ep = None
            if active["side"] == "BUY":
                if bar["high"] >= active["tp"]: ep = active["tp"]
                elif bar["low"] <= active["sl"]: ep = active["sl"]
            else:
                if bar["low"] <= active["tp"]: ep = active["tp"]
                elif bar["high"] >= active["sl"]: ep = active["sl"]
            if ep:
                pp = (ep - active["entry"]) if active["side"] == "BUY" else (active["entry"] - ep)
                pnl = pp * active["lot"] * mult - spread_pts * active["lot"] * mult * 2
                balance += pnl
                trades.append({"pnl": pnl, "balance": balance})
                active = None; continue
            else: continue

        sig = None
        if strat == "MOMENTUM":
            if bar["mom"] > 0.0005 and tu: sig = "BUY"
            elif bar["mom"] < -0.0005 and td: sig = "SELL"
        elif strat == "EMA_CROSS":
            if prev["ema8"] <= prev["ema34"] and bar["ema8"] > bar["ema34"]: sig = "BUY"
            elif prev["ema8"] >= prev["ema34"] and bar["ema8"] < bar["ema34"]: sig = "SELL"

        if not sig: continue
        sl = round(bar["close"] - bar["atr"] * SL_M, 2) if sig == "BUY" else round(bar["close"] + bar["atr"] * SL_M, 2)
        tp = round(bar["close"] + bar["atr"] * TP_M, 2) if sig == "BUY" else round(bar["close"] - bar["atr"] * TP_M, 2)
        active = {"side": sig, "entry": bar["close"], "sl": sl, "tp": tp,
                  "atr": bar["atr"], "lot": 0.01, "mult": mult, "time": bar.name}

    net = round(balance - INITIAL, 2)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    pf = round(gp / gl, 2) if gl else 0
    cp = INITIAL; mdd = 0
    for t in trades:
        b = t["balance"]
        if b > cp: cp = b
        dd = (cp - b) / cp * 100
        if dd > mdd: mdd = dd
    days = round((df.index[-1] - df.index[0]).total_seconds() / 86400, 0)
    sg = "+" if net >= 0 else ""
    print(f"  {label:8} M1 {strat:<12} {len(trades):>5}tr ({len(trades)/days:.1f}/d)  WR={wins/len(trades)*100:.0f}%  PF={pf:.2f}  ${sg}{net:>7.2f}  ROI={net/INITIAL*100:.1f}% ({net/INITIAL*100/days*30:.1f}%/bln)  DD={mdd:.1f}%")

print("=" * 75)
print("  $200 MODAL — LOT TETAP 0.01")
print("=" * 75)
print(f"  {'Symbol':8} {'TF':4} {'Strategy':<12} {'Trades':>5} {'Freq':>5} {'WR':>4} {'PF':>5} {'Net$':>7} {'ROI/bln':>9} {'DD':>4}")
print("  " + "-" * 75)

run_bt("XAUUSDm", "XAUUSD", "MOMENTUM", 100, spread_pts=2.6)
run_bt("US30m", "US30", "EMA_CROSS", 10, spread_pts=0)

print()
print("  --- KOMBINASI (XAUUSD 2/3 + US30 1/3 modal $200) ---")
print(f"  XAUUSD:  0.01 lot, ~$15/bln (7.5%/bln)")
print(f"  US30:    0.01 lot, ~$25/bln (12.5%/bln)")
print(f"  TOTAL:   ~$40/bln = 20%/bln dari $200")

mt5.shutdown()
