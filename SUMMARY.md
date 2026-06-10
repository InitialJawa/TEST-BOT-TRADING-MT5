# Project Summary — Multi-Strategy Scalping Bot (MT5)

## Overview
Automated scalping bot for MetaTrader 5 using EMA 8/34 crossover with ATR-based SL/TP and trailing stops. Originally single-pair (XAUUSD), now multi-symbol (XAUUSDm, US30m, JP225m) with an ADAPTIVE strategy engine that switches between ranging (EMA_CROSS) and trending (TREND_RE + momentum) modes based on market regime detection.

## Architecture
- `bot_live/bot.py` — Main loop: polls MT5, manages positions, handles re-entry, circuit breaker, trailing stops
- `bot_live/strategy.py` — Strategy engine: 5 strategies, indicator calculation, regime detection, signal generation
- `bot_live/config.yaml` — Live configuration (symbols, lots, strategy params, risk limits)
- `bot_live/telegram.py` — Telegram notifications
- `bot_live/export_trades.py` — CSV trade export
- `backtest_scalping/` — Original backtest framework (EMA_CROSS only, single-pair)
- `BACKTEST_RESULTS.txt` — Original EMA-only backtest summary
- `backtest_results/` — Structured JSON data for all backtest runs:
  - `multi_strategy_comparison.json` — 5 strategies on 3 pairs
  - `v2_comparison.json` — ADAPTIVE v2.0 vs v2.1 (correct 4-bar logic)
  - `5pct_risk.json` — 5% risk results + max DD

## 5 Strategies

| Strategy | Entry Condition | Re-entry | Best For |
|----------|---------------|----------|----------|
| **EMA_CROSS** | EMA fast crosses above/below EMA slow | No (1 signal per crossover) | Ranging markets |
| **MOMENTUM** | Price change >0.05% in EMA trend direction | No | Medium-trend markets |
| **PULLBACK** | Price pulls back to EMA fast in trend direction | No | Strong trends with dips |
| **TREND_RE** | Every bar in EMA trend direction | Yes (immediate re-entry) | Sustained trends |
| **ADAPTIVE** | Ranging→EMA_CROSS, Trending→TREND_RE+momentum | Yes (trending mode only) | All conditions |

### ADAPTIVE Regime Detection
```
regime_ratio = |EMA_fast - EMA_slow| / ATR
regime_ratio >= 0.7  →  TRENDING  →  4-bar momentum entry (>-0.05% for BUY, <0.05% for SELL)
regime_ratio <  0.7  →  RANGING   →  EMA_CROSS (wait for crossover)
```

**ADAPTIVE trending mode entry logic** (exact from `_sig_adaptive`):
```
BUY:  EMA_fast > EMA_slow  AND  close[now]/close[-4] - 1 > -0.0005
SELL: EMA_fast < EMA_slow  AND  close[now]/close[-4] - 1 <  0.0005
```
This uses **4-bar momentum** (not 1-bar). In uptrend, any bar that hasn't dropped ≥0.05% in the last 4 bars qualifies. Very permissive — nearly every bar triggers in strong trend.

## Parameters (all strategies)
| Param | v2.0 | v2.1 |
|-------|------|------|
| EMA fast/slow | 8 / 34 | 8 / 34 |
| ATR period | 14 | 14 |
| SL | 0.3 × ATR | 0.3 × ATR |
| TP | 0.6 × ATR | 0.6 × ATR |
| Trail activation | 0.2 × ATR | 0.2 × ATR |
| Momentum threshold | 0.0005 (4-bar) | 0.0005 (4-bar) |
| Regime threshold | **0.5** ATR | **0.7** ATR |
| Max spread | 300 points | 300 points |
| Mode | Instant | Instant |

## Lot Sizing ($300 capital, 5% risk/trade)
| Pair | Lot | Risk/trade | $/point |
|------|-----|-----------|---------|
| XAUUSDm | **0.06** | $15.51 | $6.00 |
| US30m | **0.64** | $15.05 | $0.64 |
| JP225m | **45.75** | $15.00 | $0.285 |

**Note:** Risk = SL(pts) × $/pt × lot. SL = 0.3 × ATR.
For $8,825 live balance, multiply lots by 29.4× for same 5% risk percentage.

## Backtest Results (Corrected Logic)

**Important correction:** Early backtests used 1-bar momentum in ADAPTIVE trending mode. The actual `_sig_adaptive()` uses **4-bar momentum** (`close[-1]/close[-5] - 1 > -0.0005`). Re-running with exact code logic gives significantly lower trade counts and profit. The 1-bar backtests were overly optimistic.

### Full Period — Strategies Comparison (all with 4-bar logic where applicable)

```
                  ADAPTIVE    EMA_CROSS   MOMENTUM   PULLBACK   TREND_RE
XAUUSD  Trades      30          72           6         118         93
        Win%       43.3%       51.4%       0.0%       56.8%      49.5%
        PF          1.21        1.58        0.00        2.28       1.54
        AdjNet    +$10.83     +$72.77     -$13.69    +$378.68    +$60.23

US30    Trades      32          19          50         164         49
        Win%       53.1%       42.1%      60.0%       59.1%      49.0%
        PF          1.92        1.00        2.62        2.37       1.72
        AdjNet    +$22.43      +$0.02     +$73.83    +$371.74    +$32.27

JP225   Trades      24          45          50          48          9
        Win%       45.8%       60.0%      48.0%       52.1%      22.2%
        PF          1.44        3.09        1.52        1.78       0.64
        AdjNet     +$1.40     +$14.81      +$7.43     +$12.71     -$0.41

TOTAL   AdjNet   +$34.66     +$87.60     +$67.57    +$763.13    +$92.09
```

**Note:** ADAPTIVE's low trade count is by design — 4-bar momentum threshold means
only bars with sustained pressure qualify. In ranging mode, EMA crossovers are rare
(once EMA 8/34 diverge, they stop crossing). This is a known limitation.

### ADAPTIVE Multi-Period Consistency (v2.0, exact code logic)

```
XAUUSD:
        FULL       P1        P2        P3        Trend
Trades   30        30        88         2
Adj$   +$10.83   +$10.83  +$138.78  -$28.04     ⚠️ P3 loss (2 trades)

US30:
        FULL       P1        P2        P3        Trend
Trades   32        32        37        58
Adj$   +$22.43   +$22.43   +$13.06   +$22.18    ✅ 3/3 positive

JP225:
        FULL       P1        P2        P3        Trend
Trades   24        24       236        61
Adj$    +$1.40    +$1.40   +$20.86    +$6.13    ✅ 3/3 positive

ALL PAIRS FULL: +$34.66 over 3 pairs x 8 months = ~$4.33/mo on $300 (1.4% ROI/mo)
```

### ADAPTIVE v2.0 vs v2.1 (exact code logic, 4-bar momentum)

```
Pair      Period   v2.0(th=0.5)  v2.1(th=0.7)   Δ
XAUUSD    FULL     +$10.83        +$12.54      +$1.71
XAUUSD    P1       +$10.83        +$12.54      +$1.71
XAUUSD    P2      +$138.78        +$31.53     -$107.25  ⚠️
XAUUSD    P3       -$28.04        -$28.04       $0.00

US30      FULL     +$22.43        +$18.90      -$3.53
US30      P1       +$22.43        +$18.90      -$3.53
US30      P2       +$13.06         +$8.00      -$5.05
US30      P3       +$22.18        +$10.64     -$11.54

JP225     FULL      +$1.40         +$1.51      +$0.10
JP225     P1        +$1.40         +$1.51      +$0.10
JP225     P2       +$20.86        +$17.64      -$3.22
JP225     P3        +$6.13         +$8.20      +$2.08

TOTAL     FULL     +$34.66        +$32.95      -$1.71
```

v2.1 underperforms v2.0 by -$1.71 (-5%). Difference is negligible. v2.1 chosen
for fewer trades (lower transaction costs) + session filter + cooldown (live benefits).

## Key Findings

1. **END trade artifact**: TREND_RE appeared to have PF 9.71 / +$858 in first backtest. Actual cause: 1 single position still open at end of data was closed at final market price (+$776.60). With conservative close-at-SL, TREND_RE is PF 1.54 / +$60.23.

2. **ADAPTIVE is most consistent**: 8/9 period-pair combinations positive (89%). Modest returns: ~$35/8mo = ~$4.33/mo on $300 (1.4% ROI/mo). Low trade count (86 across 3 pairs in 8 months) means ADAPTIVE is very selective.

3. **PULLBACK has highest total but is unstable**: XAUUSD PULLBACK net $378.68, but 94% comes from Period 1 only. Likely overfit to specific market conditions in late 2025.

4. **JP225 safest with ADAPTIVE**: Pure TREND_RE loses (-$0.41), pure EMA_CROSS profits (+$14.81). ADAPTIVE gives only +$1.40 with 24 trades — the 4-bar momentum filter is very restrictive on volatile JP225.

5. **Daily loss circuit (5%) & max consecutive losses (5)** protect against drawdown: max DD across all strategies < 7%.

6. **Price-vs-EMA filter** (`_sig_trend_re`): hanya entry jika `price > EMA_fast` untuk BUY atau `price < EMA_fast` untuk SELL. Mencegah entry saat harga sudah terlalu jauh dari EMA (late entry).

7. **Session filter**: XAUUSD terbatas London+NY session (07:00-22:00 UTC) — hipotesis: Asian session terlalu choppy (belum dibuktikan, perlu backtest setelah data terkumpul).

8. **Cooldown 3 loss**: skip entry setelah 3 loss berturut-turut, reset otomatis saat WIN. Berbeda dari circuit breaker yang stop total.

## Incident Log — June 2026

Bot berjalan dengan **config lama (v1.x)** di PC live. Config baru (ADAPTIVE) belum di-deploy.

| Masalah | Dampak |
|---------|--------|
| XAUUSDm: TREND_RE + lot 0.10 (bukan 0.03) | Risk 3.3x lebih besar, strategy salah |
| Tidak ada session filter | Trade masuk di Asian session (choppy) |
| regime_threshold 0.5 (terlalu sensitif) | Terlalu sering masuk TRENDING mode |

**Live 30 hari:** 208 trades, 43% WR, **-$1,174.67** (XAUUSD: 25% WR, -$1,156)

---

## Current Configuration (v2.1)
File: `bot_live/config.yaml`
- All 3 pairs use `ADAPTIVE` strategy
- Lot sizes: XAUUSD **0.06**, US30 **0.64**, JP225 **45.75** (5% risk/trade)
- Mode: instant (with ADAPTIVE re-entry in trending mode only)
- Regime threshold: **0.7** ATR (dinaikkan dari 0.5 — lebih selektif)
- Session filter: XAUUSD 07:00-22:00 UTC, US30 13:30-20:00 UTC, JP225 00:00-08:00 UTC
- Cooldown: skip entry after 3 consecutive losses (reset on win)

## 5% Risk Backtest Results (v2.1, th=0.7)

```
Pair        Lot   Trades  WR%    PF     Adj$     MaxDD    /bln
XAUUSD     0.06    28    42.9%  1.28  +$25.08   8.0%    $3.14
US30       0.64    30    50.0%  1.78  +$60.47   8.6%    $7.56
JP225     45.75    21    47.6%  1.58  +$13.79   3.8%    $1.72
───────────────────────────────────────────────────────────────
TOTAL                    79           +$99.34          $12.42/mo
```

Return **$12.42/bln (4.1% ROI/bln)** with max DD **8.6%** ($25.80 on $300).
Circuit breaker at 15% — masih ada 6.4% buffer.

---

## Threshold Decision (0.5 vs 0.7) — v2.1 Exact Logic

| Threshold | XAUUSD | US30 | JP225 | **Total** | Trades |
|-----------|--------|------|-------|-----------|--------|
| 0.5 | +$10.83 | +$22.43 | +$1.40 | **+$34.66** | 86 |
| 0.7 | +$12.54 | +$18.90 | +$1.51 | **+$32.95** | 79 |

Virtually identical return (-$1.71). 0.7 chosen for **fewer trades** (lower spreads/costs) + session filter & cooldown (live only benefits).

**Total realistic ADAPTIVE return: ~$33-35 over 8 months on $300 ≈ $4/mo ≈ 1.4% ROI/mo.**

## File Changes (v2.1)
| File | Change |
|------|--------|
| `bot_live/strategy.py` | Added `_is_within_session()`, price-vs-EMA filter in `_sig_trend_re()`, session filter check in `get_signal()` + `get_trend_signal()` |
| `bot_live/bot.py` | Added `cooldown_cleared` state tracking, cooldown check in `_tick_handler` (skip entry after 3 consecutive losses until win), session_filter passthrough in handler config |
| `bot_live/config.yaml` | Added `session_filter` per symbol (XAU/US30/JP225), changed `regime_threshold: 0.5 → 0.7` |
