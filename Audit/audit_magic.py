"""Audit semua magic number yang aktif & history"""
import MetaTrader5 as mt5
from datetime import datetime, timedelta

mt5.initialize()

# 1. Current positions
positions = mt5.positions_get()
print("== OPEN POSITIONS ==")
if positions:
    for p in positions:
        print(f"  Magic={p.magic} {p.symbol} {p.volume} lot {p.comment} Profit=${p.profit:+.2f}")
else:
    print("  (none)")

# 2. Today's deals grouped by magic
today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
deals = mt5.history_deals_get(today_start, datetime.now())
print(f"\n== TODAY'S DEALS ({len(deals) if deals else 0} total) ==")

if deals:
    magic_groups = {}
    for d in deals:
        mg = d.magic
        if mg not in magic_groups:
            magic_groups[mg] = {"trades": 0, "profit": 0.0}
        magic_groups[mg]["trades"] += 1
        magic_groups[mg]["profit"] += d.profit
    
    for mg, d in sorted(magic_groups.items()):
        print(f"  Magic={mg:<10} {d['trades']:>5} trades, ${d['profit']:+>8.2f}")

# 3. Also check last 7 days for UNKNOWN magics
print(f"\n== UNKNOWN MAGICS (last 7 days) ==")
week_ago = datetime.now() - timedelta(days=7)
alld = mt5.history_deals_get(week_ago, datetime.now())
known = list(range(25062026, 25062029)) + list(range(25062036, 25062039)) + [25062016]
unknowns = {}
if alld:
    for d in alld:
        if d.magic not in known and d.magic not in unknowns:
            unknowns[d.magic] = {"trades": 0, "profit": 0.0, "comment": d.comment or ""}
        if d.magic not in known:
            unknowns[d.magic]["trades"] += 1
            unknowns[d.magic]["profit"] += d.profit
    
    if unknowns:
        for mg, d in sorted(unknowns.items()):
            print(f"  Magic={mg:<10} {d['trades']:>5} trades, ${d['profit']:+>8.2f} ({d['comment']})")
    else:
        print("  (none)")

mt5.shutdown()
