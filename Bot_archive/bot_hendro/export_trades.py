"""Export trade history to CSV"""
import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

out = Path(__file__).parent / "trades.csv"
magic = 25062026

mt5.initialize()

# Get all history
deals = mt5.history_deals_get(datetime(2025, 1, 1), datetime.now())
if deals is None:
    print("No deals found")
    mt5.shutdown()
    exit()

rows = []
for d in deals:
    if d.magic != magic:
        continue
    rows.append({
        "ticket": d.ticket,
        "time": datetime.fromtimestamp(d.time),
        "type": "BUY" if d.type in (0,2) else "SELL",
        "entry": "IN" if d.entry == 0 else "OUT",
        "price": d.price,
        "volume": d.volume,
        "profit": d.profit if d.entry == 1 else "",
        "commission": d.commission,
        "swap": d.swap,
        "symbol": d.symbol,
        "position_id": d.position_id,
        "comment": d.comment,
    })

if not rows:
    print("No trades found for bot")
    mt5.shutdown()
    exit()

df = pd.DataFrame(rows)

# Calculate per-position summary
positions = []
for pos_id in df[df["entry"] == "IN"]["position_id"].unique():
    deals_in = df[(df["position_id"] == pos_id) & (df["entry"] == "IN")]
    deals_out = df[(df["position_id"] == pos_id) & (df["entry"] == "OUT")]
    if deals_in.empty or deals_out.empty:
        continue
    entry = deals_in.iloc[0]
    exit_d = deals_out.iloc[0]
    pnl = exit_d["profit"]
    if pnl == "":
        pnl = 0
    positions.append({
        "position_id": pos_id,
        "symbol": entry["symbol"],
        "side": entry["type"],
        "entry_time": entry["time"],
        "entry_price": entry["price"],
        "exit_time": exit_d["time"],
        "exit_price": exit_d["price"],
        "volume": entry["volume"],
        "profit": float(pnl),
        "commission": float(exit_d["commission"]) if exit_d["commission"] else 0,
        "swap": float(exit_d["swap"]) if exit_d["swap"] else 0,
        "net_pnl": float(pnl) - float(exit_d.get("commission", 0)) - float(exit_d.get("swap", 0)),
    })

pdf = pd.DataFrame(positions)
pdf.to_csv(out, index=False)

print(f"Exported {len(positions)} positions to {out}")
print()
print("=== SUMMARY ===")
print(f"Total trades: {len(pdf)}")
print(f"Wins: {len(pdf[pdf['profit'] > 0])}")
print(f"Losses: {len(pdf[pdf['profit'] <= 0])}")
win_rate = len(pdf[pdf['profit'] > 0]) / len(pdf) * 100 if len(pdf) > 0 else 0
print(f"Win rate: {win_rate:.1f}%")
print(f"Gross profit: ${pdf[pdf['profit'] > 0]['profit'].sum():.2f}")
print(f"Gross loss: ${pdf[pdf['profit'] <= 0]['profit'].sum():.2f}")
gp = pdf[pdf['profit'] > 0]['profit'].sum()
gl = abs(pdf[pdf['profit'] <= 0]['profit'].sum())
pf = gp / gl if gl > 0 else 0
print(f"Profit factor: {pf:.2f}")
print(f"Net P&L: ${pdf['profit'].sum():.2f}")

mt5.shutdown()
