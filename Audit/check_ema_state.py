"""Check current EMA state"""
import MetaTrader5 as mt5
import pandas as pd

mt5.initialize()
for sym in ["XAUUSDm", "US30m"]:
    mt5.symbol_select(sym, True)
    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, 100)
    df = pd.DataFrame(rates)
    c = df["close"]
    ef = c.ewm(span=8, adjust=False).mean()
    es = c.ewm(span=34, adjust=False).mean()
    trend = "UP" if ef.iloc[-1] > es.iloc[-1] else "DN"
    x_up = (ef.shift(1) <= es.shift(1)) & (ef > es)
    x_dn = (ef.shift(1) >= es.shift(1)) & (ef < es)
    last_x = "BUY" if x_up.iloc[-1] else ("SELL" if x_dn.iloc[-1] else "none")
    print(f"{sym:10} close={c.iloc[-1]:.2f} EMA8={ef.iloc[-1]:.2f} EMA34={es.iloc[-1]:.2f} trend={trend}")
    print(f"           last cross: {last_x}")

    # Count bars since last cross
    cross_bars = []
    for i in range(len(df)-1, 0, -1):
        if (ef.iloc[i] > es.iloc[i]) != (ef.iloc[i-1] > es.iloc[i-1]):
            cross_bars.append(i)
            break

    if cross_bars:
        bars_ago = len(df) - 1 - cross_bars[0]
        print(f"           bars since last cross: {bars_ago} (~{bars_ago*5} min)")

    # How many crosses in last 500 bars?
    total = 0
    for i in range(len(df)-1, max(len(df)-501, 0), -1):
        if (ef.iloc[i] > es.iloc[i]) != (ef.iloc[i-1] > es.iloc[i-1]):
            total += 1
    print(f"           crosses in last 500 bars: {total}")
    print()
mt5.shutdown()
