# Summary — Multi-Bot Scalping Project

## Goal
Maximize profit via **TREND_RE** strategy with auto-compounding lot adjustment based on equity growth.

---

## Account (New Demo)

| Item | Value |
|------|-------|
| Broker | Exness-MT5Trial6 (demo) |
| Login | 413889745 |
| Balance start | 5.000.000 IDR (~$278 USD) |
| Leverage | 1:2000 |
| Pairs | XAUUSDm, JP225m (US30 skipped until equity ≥ $3K) |
| Timeframe | M5 |
| USDIDR rate | 17.960 |
| Telegram | DISABLED |

---

## Active Bot

### bot_fabio (Fabio) — ✅ RUNNING
- **Dir:** `bot_fabio/`
- **Strategy:** TREND_RE (all pairs)
- **Pairs:** XAUUSD (0.01 lot) + JP225 (1.0 lot) + US30 (0.0 lot/skip)
- **Magics:** XAU=25062026, US30=25062027, JP225=25062028
- **Mode:** instant
- **ATR SL/TP:** 0.3 / 0.6
- **Fixed issues:** JP225 lot step 1.0 (was 0.09 → error 10027), lot=0 symbols now skipped

### bot_hendro (Hendro) — ❌ STOPPED
- Session filter disabled (cost 40% profit)
- All lots set to 0.0

### bot_original — ❌ DELETED (archived to `Bot_archive/`)
### bot_live — ❌ DELETED (archived to `Bot_archive/`)

---

## Key Decisions

| Decision | Detail |
|----------|--------|
| **TREND_RE untuk semua pair** | Backtest profit tertinggi dibanding EMA_CROSS/MOMENTUM/PULLBACK |
| **Auto-tuner safety net only** | 3 skema diuji — semuanya tidak improve vs static; threshold diperlonggar |
| **Regime switching discarded** | ADX/vol/direction switching rugi $16K vs static |
| **Compound manager pair-specific ratio** | XAU=eq/30000, JP225=eq/300 (bukan satu ratio) — karena JP225 min lot 1.0 |
| **US30 multiplier 1 (bukan 100)** | Semua backtest sebelumnya overestimate 100x |
| **JP225 profit dalam JPY** | Di backtest terlihat $4,6M tapi sebenarnya JPY, perlu konversi USDJPY (~160) |
| **Session filter disabled** | Rugi 40% profit saat diaktifkan di bot_hendro |
| **US30 skip until $3K** | DD overshoot jika equity kecil |

---

## Compound Manager (`compound_manager.py`)

- **Jadwal:** Task Scheduler tiap 1 jam
- **Logic:** Auto-adjust lot per pair berdasarkan equity USD
- **Pair ratios:**
  - XAU: lot = equity_usd / 30000 (0.01 per $300)
  - JP225: lot = equity_usd / 300 (1.0 lot per $300) — min lot enforced
  - US30: skip if eq < $3000
- **Enforces symbol min_lot & lot_step** (JP225 min 1.0, step 1.0)
- Restarts bot otomatis setelah update config

---

## Backtest Results (Corrected)

| Strategy | WR | PF | Notes |
|----------|----|----|-------|
| TREND_RE | 46–47% | 1.35–1.40 | Most profitable overall |
| MOMENTUM | ~50% | 1.41–1.47 | Best PF, fewer trades |
| EMA_CROSS | ~40% | 1.50 (range) | Few trades |
| PULLBACK | ~45% | 1.38 (US30) | Limited scope |

- **US30 backtest corrected:** multiplier 100→1 (overestimate 100x)
- **Proyeksi conservative 5M IDR:** ~$1.099/bln (~19.7M IDR) dari backtest lot 0.01

---

## Sessions Log

| Date | Activity |
|------|----------|
| Jun 10–11 | Initial setup: 3 bots (Fabio/Hendro/Original) running simultaneously on Exness trial 7 ($9K) |
| Jun 11 (later) | Strategy comparison: TREND_RE wins; bot_original/live archived; moved to new demo 5M IDR |
| Jun 11 (this session) | JP225 min lot 1.0 fix (error 10027); lot=0 skip; compound manager with pair ratios; US30 backtest correction |

---

## Files & Directories

```
├── bot_fabio/              # Active bot (TREND_RE, 3 pairs)
│   ├── bot.py              # Fixed: skip lot=0 symbols
│   ├── config.yaml         # XAU 0.01, JP225 1.0, US30 0.0
│   ├── strategy.py         # TREND_RE / MOMENTUM / PULLBACK / EMA_CROSS
│   └── logs/               # bot_YYYYMMDD.log
├── bot_hendro/             # Stopped (all lots 0.0)
├── Bot_archive/            # bot_original & bot_live
├── Audit/                  # Backtest & comparison scripts
├── compound_manager.py     # Auto-adjust lot per pair (IDR-aware)
├── auto_tuner.py           # Auto-tuner (safety net mode)
├── SUMMARY.md              # this file
```

---

## Issues & Risks

1. **US30 tidak aktif** — equity masih < $3K, US30 backtest overestimate 100x sudah dikoreksi
2. **JP225 min lot 1.0** — lot step 1.0 bikin granularity kasar, lot loncat dari 0→1 langsung
3. **Compound manager accuracy** — perlu diverifikasi setelah beberapa hari jalan
4. **TREND_RE WR 46%** — lebih banyak lose trade, reliance pada win size > loss size
