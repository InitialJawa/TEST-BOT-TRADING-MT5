"""
Corrected backtest — US30 multiplier 1x (not 100x)
TREND_RE | Oct 2025 - Jun 2026 | M5
"""
import sys, json
from pathlib import Path
from datetime import datetime
import pandas as pd
import MetaTrader5 as mt5

EMA_FAST=8; EMA_SLOW=34; ATR_PERIOD=14
SL_ATR=0.3; TP_ATR=0.6; TRAIL_ACT=0.2
MAX_SPREAD=300
START='2025-10-01'; END='2026-06-10'

PAIRS = [
    {"sym": "XAUUSDm", "mult": 100, "lot": 0.01},
    {"sym": "US30m",   "mult": 1,   "lot": 0.01},
    {"sym": "JP225m",  "mult": 1,   "lot": 0.01},
]

def load_prep(sym):
    mt5.initialize()
    mt5.symbol_select(sym,True)
    r=mt5.copy_rates_range(sym,mt5.TIMEFRAME_M5,datetime.strptime(START,'%Y-%m-%d'),datetime.strptime(END,'%Y-%m-%d'))
    mt5.shutdown()
    df=pd.DataFrame(r); df['time']=pd.to_datetime(df['time'],unit='s'); df.set_index('time',inplace=True)
    df.columns=[c.lower() for c in df.columns]
    c=df['close']; df['ef']=c.ewm(span=EMA_FAST,adjust=False).mean(); df['es']=c.ewm(span=EMA_SLOW,adjust=False).mean()
    h,l=df['high'],df['low']
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    df['atr']=tr.ewm(span=ATR_PERIOD,adjust=False).mean(); df.dropna(inplace=True)
    return df

def bt(df,lot,mult):
    tr=0;pnl=0.0;dd=0.0;peak=300.0
    active=False;side=entry=sl=tp=atr_e=0
    for idx in range(1,len(df)):
        bar=df.iloc[idx]
        if active:
            if TRAIL_ACT and atr_e:
                act=atr_e*TRAIL_ACT
                if side==1:
                    p=bar['close']-entry
                    if p>act:
                        ns=bar['close']-act
                        if ns>sl: sl=ns
                else:
                    p=entry-bar['close']
                    if p>act:
                        ns=bar['close']+act
                        if ns<sl: sl=ns
            exit_px=None
            if (side==1 and bar['high']>=tp) or (side==-1 and bar['low']<=tp): exit_px=tp
            elif (side==1 and bar['low']<=sl) or (side==-1 and bar['high']>=sl): exit_px=sl
            if exit_px:
                pnl_v=(exit_px-entry) if side==1 else (entry-exit_px)
                pnl_u=pnl_v*lot*mult
                tr+=1;pnl+=pnl_u;eq=300+pnl;peak=max(peak,eq);dd=max(dd,peak-eq)
                active=False
            continue
        if bar['spread']>MAX_SPREAD: continue
        if idx<2: continue
        tu=bar['ef']>bar['es'];td=bar['ef']<bar['es']
        sig=1 if tu else (-1 if td else 0)
        if sig==0: continue
        side=sig;entry=bar['close'];atr_e=bar['atr']
        if side==1: sl=entry-atr_e*SL_ATR;tp=entry+atr_e*TP_ATR
        else: sl=entry+atr_e*SL_ATR;tp=entry-atr_e*TP_ATR
        active=True
    return tr,pnl,dd

print("="*70)
print("  CORRECTED BACKTEST — TREND_RE | Oct 2025 - Jun 2026 | M5")
print("  Mult: XAU=100, US30=1, JP225=1")
print("="*70)

results=[]
for p in PAIRS:
    print(f"\nLoading {p['sym']}...",end="")
    df=load_prep(p['sym'])
    tr,pnl,dd=bt(df,p['lot'],p['mult'])
    results.append({"sym":p['sym'],"trades":tr,"profit":pnl,"dd":dd})
    print(f" {len(df):,} bars | lot {p['lot']} mult={p['mult']}")
    print(f"  Trades: {tr} | Profit: ${pnl:,.2f} | MaxDD: ${dd:.2f}")

print("\n"+"-"*70)
total_p=sum(r['profit'] for r in results)
total_d=sum(r['dd'] for r in results)
total_t=sum(r['trades'] for r in results)
print(f"  TOTAL 3 PAIR lot 0.01:")
print(f"  Trades:  {total_t}")
print(f"  Profit:  ${total_p:,.2f}")
print(f"  /bulan:  ${total_p/8:,.0f}")
print(f"  MaxDD:   ${total_d:,.2f}")
print(f"  ROE:     {total_p/300*100:.0f}%")
print(f"  ROE/mo:  {total_p/300/8:.1f}%")

# Comparison with old wrong numbers
print(f"\n{'='*70}")
print(f"  COMPARISON: Old (wrong) vs Corrected")
print(f"{'='*70}")
print(f"  {'Pair':>8} {'Old profit':>14} {'Correct':>14} {'Diff':>14}")
print(f"  {'-'*52}")
old_p = {"XAUUSDm":7805, "US30m":42904, "JP225m":992}
for r in results:
    old = old_p.get(r['sym'],0)
    print(f"  {r['sym']:>8} ${old:>10,.2f}  ${r['profit']:>10,.2f}  {r['profit']/old*100-100:>+10.0f}%")
old_total = sum(old_p.values())
print(f"  {'-'*52}")
print(f"  {'TOTAL':>8} ${old_total:>10,.2f}  ${total_p:>10,.2f}  {total_p/old_total*100-100:>+10.0f}%")
