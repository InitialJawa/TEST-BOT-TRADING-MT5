"""Check M1 data availability"""
import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime

mt5.initialize()
for sym in ["XAUUSDm", "US30m", "XAUUSD", "US30"]:
    mt5.symbol_select(sym, True)
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, 5)
    if r is not None:
        print(f"{sym:12} M1: {len(r)} bars, latest={datetime.fromtimestamp(r[-1]['time'])}")
    else:
        print(f"{sym:12} M1: no data ({mt5.last_error()})")

    r5 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, 5)
    if r5 is not None:
        print(f"{sym:12} M5: {len(r5)} bars, latest={datetime.fromtimestamp(r5[-1]['time'])}")
    else:
        print(f"{sym:12} M5: no data ({mt5.last_error()})")
    print()
mt5.shutdown()
