import json

with open(r"C:\Users\BedilGaib\.gemini\antigravity\scratch\Test PRD\backtest_scalping\output\optimization_results.json") as f:
    data = json.load(f)

print("\n=== PASS 1: EMA Optimization ===")
for i, r in enumerate(data["ema_search"][:8]):
    print(f"  {i+1}. EMA {r['ef']}/{r['es']}: {r['n']} trades, WR={r['wr']}%, PF={r['pf']:.2f}, Net=${r['net']:+.2f}, Sharpe={r['sharpe']}")

print("\n=== PASS 2: Risk Optimization (PF Ranking) ===")
print(f"  Best EMA: {data['best_ema']['ef']}/{data['best_ema']['es']}")
risk = sorted([r for r in data["risk_search"] if r.get("pf", 0) != float("inf")], key=lambda x: x.get("pf", 0), reverse=True)
for i, r in enumerate(risk[:10]):
    tr = f"{r['trail']}" if r["trail"] > 0 else "none"
    print(f"  {i+1}. SL/TP={r['sl_atr']}/{r['tp_atr']}, trail={tr}: {r['n']} trades, WR={r['wr']}%, PF={r['pf']:.2f}, Net=${r['net']:+.2f}")

print("\n=== Top 8 by Net Profit ===")
risk_net = sorted(data["risk_search"], key=lambda x: x["net"], reverse=True)
for i, r in enumerate(risk_net[:8]):
    tr = f"{r['trail']}" if r["trail"] > 0 else "none"
    print(f"  {i+1}. EMA {r['ef']}/{r['es']} SL/TP={r['sl_atr']}/{r['tp_atr']} trail={tr}: {r['n']} trades, WR={r['wr']}%, PF={r['pf']:.2f}, Net=${r['net']:+.2f}")

print("\n=== Top 8 by Trade Count ===")
risk_n = sorted(data["risk_search"], key=lambda x: x["n"], reverse=True)
for i, r in enumerate(risk_n[:8]):
    tr = f"{r['trail']}" if r["trail"] > 0 else "none"
    print(f"  {i+1}. EMA {r['ef']}/{r['es']} SL/TP={r['sl_atr']}/{r['tp_atr']} trail={tr}: {r['n']} trades, WR={r['wr']}%, PF={r['pf']:.2f}, Net=${r['net']:+.2f}")
