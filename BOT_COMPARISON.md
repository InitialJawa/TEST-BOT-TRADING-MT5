# Bot Comparison — Full Config, Results & Effectiveness

---

## 1. bot_fabio (Fabio — Multi-Strategy)
**Dir:** `bot_fabio/` | **Status:** ✅ Running

### Config
```yaml
symbols:
  XAUUSDm: MOMENTUM   lot=0.1  magic=25062026  TF=M5  spread_max=300
  US30m:   PULLBACK   lot=0.1  magic=25062027  TF=M5  spread_max=300
  JP225m:  TREND_RE   lot=47   magic=25062028  TF=M5  spread_max=300

strategy: ema_fast=8 ema_slow=34 atr_period=14 sl=0.3 tp=0.6 trail=0.2 mom_thresh=0.0005
risk: max_daily_loss=5% max_consec=5 max_pos=3 circuit_breaker=15%
mode: instant | poll: 5s | candle: pos=1 (completed candle ✅)
```

### Strategies
| Strategy | Logic |
|----------|-------|
| **EMA_CROSS** | Crossover EMA 8/34 klasik |
| **MOMENTUM** | Trend + momentum threshold (close/close[-3] > 0.05%) |
| **PULLBACK** | Trend + harga sentuh/silang EMA fast lawan arah trend |
| **TREND_RE** | Entry tiap bar sesuai arah trend (re-entry after SL allowed) |

### Live Today (2026-06-11)
| Symbol | Trades | WR | PnL |
|--------|--------|----|-----|
| XAUUSD | 166 | ~50% | +$514 |
| US30 | 113 | ~45% | +$9 |
| JP225 | 1,021 | 22% | -$61 |
| **Total** | **1,300** | | **+$462** |

---

## 2. bot_hendro (Hendro — ADAPTIVE Regime-Switching)
**Dir:** `bot_hendro/` | **Status:** ✅ Running

### Config
```yaml
symbols:
  XAUUSDm: ADAPTIVE  lot=0.06  magic=25062036  TF=M5  session=07:00-22:00UTC
  # US30m & JP225m configured but DISABLED (removed from running)

strategy: ema_fast=8 ema_slow=34 atr_period=14 sl=0.3 tp=0.6 trail=0.2 mom_thresh=0.0005 regime_thresh=0.7
risk: max_daily_loss=5% max_consec=5 max_pos=3 circuit_breaker=15%
mode: instant | poll: 5s | candle: pos=0 (forming bar ❌)
```

### Strategy: ADAPTIVE
```
Regime Detection: EMA_Spread / ATR >= 0.7 → trending, < 0.7 → ranging

RANGING → EMA_CROSS (krossover klasik)
TRENDING → TREND_RE + momentum threshold (ikut trend)
```
- Session filter: 07:00-22:00 UTC (XAU only)
- Re-entry setelah SL: ✅

### Live Today (2026-06-11)
| Symbol | Trades | WR | PnL |
|--------|--------|----|-----|
| XAUUSD | 38 | 26% | -$13 |

---

## 3. bot_original (Original — Simple EMA 8/34)
**Dir:** `bot_original/` | **Status:** ❌ Dead

### Config
```yaml
symbol: XAUUSDm  lot=0.01  magic=25062016  TF=M5  spread_max=300
strategy: ema_fast=8 ema_slow=34 atr_period=14 sl=0.3 tp=0.6 trail=0.2
risk: max_daily_loss=5% max_consec=5 max_pos=1 circuit_breaker=15%
mode: instant | poll: 5s | candle: pos=0 (forming bar ❌)
```

### Strategy
```
EMA 8/34 Crossover → entry sesuai arah cross
ATR-based SL/TP (0.3/0.6)
Trailing stop setelah profit > 0.2x ATR
```
- **Tidak ada** momentum, pullback, regime detection
- Re-entry setelah SL: ❌

### Live Today (2026-06-11)
| Symbol | Trades | WR | PnL |
|--------|--------|----|-----|
| XAUUSD | 0 | — | $0 |

---

## 4. bot_live (Predecessor — Same as Fabio)
**Dir:** `bot_live/` | **Status:** ❌ Stopped 14:15

Sama persis dengan bot_fabio (config, strategy, magic numbers identik).
Jalan dari **kemarin (Jun 10)** sampai **mati hari ini jam 14:15**.
Digantikan bot_fabio karena tabrakan magic number.

---

## Backtest Results (Oct 2025 – Jun 2026 | M5 | Lot 0.01)

### Overall (ALL Market Conditions)

| Strategy | XAUUSD | | US30 | | JP225 | |
|----------|--------|--|------|--|-------|--|
| | PF | Net $ | PF | Net $ | PF | Net $ |
| **EMA_CROSS** | 1.38 | +$280 | **1.58** | +$1,957 | **1.51** | +$3,141 |
| **MOMENTUM** | **1.41** | +$2,136 | 1.46 | +$7,883 | 1.47 | +$21,152 |
| **PULLBACK** | 1.26 | +$1,644 | 1.40 | +$11,477 | 1.38 | +$21,141 |
| **TREND_RE** | 1.37 | +$3,746 | 1.38 | +$17,776 | 1.38 | +$35,229 |

### Per Regime — Best Strategy

| Pair | TREND | RANGE | Notes |
|------|-------|-------|-------|
| **XAUUSD** | MOMENTUM (PF=1.41) | EMA_CROSS (PF=1.50) | MOMENTUM paling stabil overall |
| **US30** | EMA_CROSS (PF=1.78) | PULLBACK (PF=1.38) | EMA_CROSS jarang tp akurat |
| **JP225** | MOMENTUM (PF=1.43) | EMA_CROSS (PF=1.89) | MOMENTUM PF tertinggi overall |

### Key Findings
- **TREND_RE** paling banyak trade (11k/4mo) tapi **PF terendah** (1.37-1.38) — overtrading
- **MOMENTUM** paling konsisten: PF 1.41-1.47 di semua pair
- **EMA_CROSS** bagus di RANGE (PF 1.50-1.89) tapi trade sangat jarang
- **PULLBACK** lumayan di US30 (PF 1.38-1.42)

---

## Live vs Backtest Comparison

| Bot | Pair | Strategy | Backtest PF | Live WR | Live PnL | Match? |
|-----|------|----------|-------------|---------|----------|--------|
| Fabio | XAU | MOMENTUM | 1.41 | ~50% | +$514 | ✅ |
| Fabio | US30 | PULLBACK | 1.40 | ~45% | +$9 | ⚠️ Low |
| **Fabio** | **JP225** | **TREND_RE** | **1.38** | **22%** | **-$61** | **❌ Overtrading** |
| Hendro | XAU | ADAPTIVE | — | 26% | -$13 | N/A |
| Original | XAU | EMA_CROSS | 1.38 | — | $0 | N/A |

---

## Summary Table

| Aspect | bot_fabio | bot_hendro | bot_original |
|--------|-----------|------------|--------------|
| **Engine** | MOMENTUM/PULLBACK/TREND_RE | ADAPTIVE (regime-switching) | EMA 8/34 CROSS |
| **Pairs** | 3 (XAU+US30+JP225) | 1 (XAU only) | 1 (XAU only) |
| **Lot** | XAU=0.1, US30=0.1, JP225=47 | 0.06 | 0.01 |
| **Candle** | pos=1 ✅ | pos=0 ❌ | pos=0 ❌ |
| **Session filter** | No | Yes (07-22UTC) | No |
| **Re-entry after SL** | TREND_RE only | Yes (ADAPTIVE) | No |
| **Today Trades** | 1,300 | 38 | 0 |
| **Today PnL** | +$462 | -$13 | $0 |
| **Backtest PF (XAU)** | 1.26-1.41 | N/A | 1.38 |
| **Live** | ✅ Running | ✅ Running | ❌ Dead |

---

## Current Issues

1. **Fabio JP225 (TREND_RE)** — 1,021 trades/hari, 22% WR, PF jauh di bawah backtest (1.38 → ~0.9). Terlalu agresif. → **Saranka ganti ke MOMENTUM**.
2. **Hendro & Original pake pos=0** — sinyal dari forming candle, bisa berubah-ubah. Tidak reliable.
3. **Original mati** — 0 trades, last log 45 menit lalu.
4. **Magics** — udah unik per bot, aman jalan bareng (Fabio 25062026/27/28, Hendro 25062036, Original 25062016).
5. **Magic=0 di account** — unknown EA trial, PnL ~$9.8k/7d. Bukan punya kita, diabaikan.

---

## Recommended Changes

1. Fabio JP225: **TREND_RE → MOMENTUM** (kurangi trade 1,021→~300/hari, PF expected naik)
2. bot_original: fix `pos=1` atau matikan aja (EMA_CROSS jarang trade, nggak nambah value)
3. bot_hendro: fix `pos=1` untuk completed candle signals
