"""
Scalping Bot Backtest Engine — EMA Cross + RSI + ATR
XAUUSD M5 | Python + MetaTrader5
"""
import os
import sys
import json
from datetime import datetime
from dataclasses import dataclass, field, asdict
from pathlib import Path

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import yaml
import MetaTrader5 as mt5


# ============================================================
# Data Structures
# ============================================================

@dataclass
class Trade:
    ticket: int
    side: str                    # "BUY" or "SELL"
    entry_time: datetime
    entry_price: float
    sl: float
    tp: float
    lot: float
    entry_atr: float = None
    exit_time: datetime = None
    exit_price: float = None
    exit_reason: str = None      # "TP", "SL", "TRAIL", "REVERSE", "END"
    pnl: float = 0.0
    pnl_pips: float = 0.0
    bars_held: int = 0


@dataclass
class BacktestResult:
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_profit: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_usd: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_bars_held: float = 0.0
    expectancy: float = 0.0
    final_balance: float = 0.0
    total_pips: float = 0.0
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)


# ============================================================
# Data Loader — MT5
# ============================================================

class DataLoader:
    def __init__(self, config: dict):
        self.symbol = config["symbol"]
        self.timeframe_map = {
            "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1,
        }
        self.tf = self.timeframe_map[config["timeframe"]]
        self.start = config.get("start_date")
        self.end = config.get("end_date")

    def fetch(self) -> pd.DataFrame:
        if not mt5.initialize():
            raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")

        symbol_info = mt5.symbol_info(self.symbol)
        if not symbol_info:
            raise RuntimeError(f"Symbol {self.symbol} not found in MT5")

        mt5.symbol_select(self.symbol, True)

        from_date = datetime.strptime(self.start, "%Y-%m-%d") if self.start else datetime(2020, 1, 1)
        to_date = datetime.strptime(self.end, "%Y-%m-%d") if self.end else datetime.now()

        rates = mt5.copy_rates_range(self.symbol, self.tf, from_date, to_date)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"No data for {self.symbol} {self.tf} from {from_date} to {to_date}")

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df.set_index("time", inplace=True)
        df.columns = [c.lower() for c in df.columns]

        mt5.shutdown()
        print(f"  Loaded {len(df):,} bars | {df.index[0].date()} -> {df.index[-1].date()}")
        return df


# ============================================================
# Indicator Calculator
# ============================================================

class Indicators:
    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def sma(series: pd.Series, period: int) -> pd.Series:
        return series.rolling(window=period).mean()

    @staticmethod
    def rsi(series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
        rs = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high, low, close = df["high"], df["low"], df["close"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()

    @staticmethod
    def bollinger(series: pd.Series, period: int = 20, std: float = 2.0):
        sma = series.rolling(window=period).mean()
        sd = series.rolling(window=period).std()
        upper = sma + std * sd
        lower = sma - std * sd
        return sma, upper, lower


# ============================================================
# Backtest Engine
# ============================================================

class BacktestEngine:
    def __init__(self, df: pd.DataFrame, config: dict):
        self.df = df.copy()
        self.cfg = config
        self.strat = config["strategy"]
        self.risk = config["risk"]
        self.lot = config["lot_size"]
        self.initial_balance = config["initial_balance"]
        self.balance = self.initial_balance
        self.equity = self.initial_balance
        self.peak_balance = self.initial_balance
        self.trades: list[Trade] = []
        self.equity_curve: list[dict] = []
        self.consecutive_losses = 0
        self.daily_pnl = 0.0
        self.last_date = None
        self.circuit_breaked = False

    def _calculate_indicators(self):
        df = self.df
        s = self.strat
        df["ema_fast"] = Indicators.ema(df["close"], s["ema_fast"])
        df["ema_slow"] = Indicators.ema(df["close"], s["ema_slow"])
        df["atr"] = Indicators.atr(df, s["atr_period"])
        df["spread_points"] = df["spread"]
        df.dropna(inplace=True)

    def _calc_position_size(self) -> float:
        return self.lot

    def _get_sl_tp(self, side: str, entry: float, atr_val: float) -> tuple:
        s = self.strat
        sl_dist = atr_val * s["sl_atr_mult"]
        tp_dist = atr_val * s["tp_atr_mult"]
        if side == "BUY":
            return entry - sl_dist, entry + tp_dist
        else:
            return entry + sl_dist, entry - tp_dist

    def _update_trailing_stop(self, trade: Trade, bar):
        if trade.entry_atr is None:
            return
        activation = trade.entry_atr * self.strat["trailing_activation"]
        if trade.side == "BUY":
            profit = bar["close"] - trade.entry_price
            if profit > activation:
                new_sl = bar["close"] - activation
                if new_sl > trade.sl:
                    trade.sl = new_sl
        else:
            profit = trade.entry_price - bar["close"]
            if profit > activation:
                new_sl = bar["close"] + activation
                if new_sl < trade.sl:
                    trade.sl = new_sl

    def _calc_pip_value(self, price: float) -> float:
        return 0.01  # Gold: 1 point = $0.01

    def _check_exit_conditions(self, bar, trade: Trade, idx: int) -> tuple:
        high = bar["high"]
        low = bar["low"]
        close = bar["close"]

        if trade.side == "BUY":
            if high >= trade.tp:
                return trade.tp, "TP"
            if low <= trade.sl:
                return trade.sl, "SL"
        else:
            if low <= trade.tp:
                return trade.tp, "TP"
            if high >= trade.sl:
                return trade.sl, "SL"

        return None, None

    def run(self) -> BacktestResult:
        print("\n  Running backtest...")
        self._calculate_indicators()
        df = self.df

        active_trade = None
        entry_idx = 0

        for idx in range(1, len(df)):
            bar = df.iloc[idx]
            prev = df.iloc[idx - 1]
            date = bar.name

            # — Daily P&L reset & circuit breaker —
            if self.last_date and date.date() != self.last_date.date():
                self.daily_pnl = 0.0
            self.last_date = date

            if self.circuit_breaked:
                self.equity_curve.append({"date": date, "balance": self.balance, "equity": self.balance})
                continue

            # — Manage active trade —
            if active_trade:
                if self.strat.get("use_trailing"):
                    self._update_trailing_stop(active_trade, bar)
                exit_price, exit_reason = self._check_exit_conditions(bar, active_trade, idx)
                if exit_price:
                    self._close_trade(active_trade, exit_price, exit_reason, bar, idx)
                    active_trade = None
                else:
                    active_trade.bars_held += 1
                    unrealized = (bar["close"] - active_trade.entry_price) * active_trade.lot * 100 if active_trade.side == "BUY" else (active_trade.entry_price - bar["close"]) * active_trade.lot * 100
                    self.equity = self.balance + unrealized
                    self.equity_curve.append({"date": date, "balance": self.balance, "equity": self.equity})
                    continue

            # — Signal detection —
            if self.consecutive_losses >= self.risk["max_consecutive_losses"]:
                self.equity_curve.append({"date": date, "balance": self.balance, "equity": self.balance})
                continue

            if self.daily_pnl <= -self.risk["max_daily_loss_pct"] / 100 * self.balance:
                self.equity_curve.append({"date": date, "balance": self.balance, "equity": self.balance})
                continue

            if bar["spread_points"] > self.strat["max_spread_points"]:
                self.equity_curve.append({"date": date, "balance": self.balance, "equity": self.balance})
                continue

            ema_bull = prev["ema_fast"] <= prev["ema_slow"] and bar["ema_fast"] > bar["ema_slow"]
            ema_bear = prev["ema_fast"] >= prev["ema_slow"] and bar["ema_fast"] < bar["ema_slow"]

            if ema_bull:
                side = "BUY"
            elif ema_bear:
                side = "SELL"
            else:
                self.equity_curve.append({"date": date, "balance": self.balance, "equity": self.balance})
                continue

            # — Entry —
            sl, tp = self._get_sl_tp(side, bar["close"], bar["atr"])
            trade = Trade(
                ticket=len(self.trades) + 1,
                side=side,
                entry_time=date,
                entry_price=bar["close"],
                sl=sl,
                tp=tp,
                lot=self._calc_position_size(),
                entry_atr=bar["atr"],
            )
            self.trades.append(trade)
            active_trade = trade
            entry_idx = idx

            self.equity_curve.append({"date": date, "balance": self.balance, "equity": self.balance})

        # — Close any open trade at end of data —
        if active_trade:
            last = df.iloc[-1]
            self._close_trade(active_trade, last["close"], "END", last, len(df) - 1)

        result = self._compute_results()
        self._print_summary(result)
        return result

    def _close_trade(self, trade: Trade, exit_price: float, reason: str, bar, idx: int):
        trade.exit_time = bar.name
        trade.exit_price = exit_price
        trade.exit_reason = reason

        pip_val = self._calc_pip_value(trade.entry_price)
        if trade.side == "BUY":
            trade.pnl = (exit_price - trade.entry_price) * trade.lot * 100
            trade.pnl_pips = (exit_price - trade.entry_price) / pip_val
        else:
            trade.pnl = (trade.entry_price - exit_price) * trade.lot * 100
            trade.pnl_pips = (trade.entry_price - exit_price) / pip_val

        self.balance += trade.pnl
        self.daily_pnl += trade.pnl
        self.equity = self.balance

        if trade.pnl > 0:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1

        # Circuit breaker
        dd_pct = (self.peak_balance - self.balance) / self.peak_balance * 100
        if dd_pct > self.risk["circuit_breaker_pct"]:
            print(f"  !! Circuit breaker triggered at {dd_pct:.1f}% drawdown")
            self.circuit_breaked = True

    def _compute_results(self) -> BacktestResult:
        r = BacktestResult()
        r.trades = self.trades
        r.final_balance = self.balance
        r.total_trades = len(self.trades)

        if r.total_trades == 0:
            return r

        pnls = [t.pnl for t in self.trades]
        r.winning_trades = sum(1 for p in pnls if p > 0)
        r.losing_trades = sum(1 for p in pnls if p <= 0)
        r.win_rate = r.winning_trades / r.total_trades * 100 if r.total_trades > 0 else 0
        r.net_profit = sum(pnls)
        r.gross_profit = sum(p for p in pnls if p > 0)
        r.gross_loss = abs(sum(p for p in pnls if p < 0))
        r.profit_factor = r.gross_profit / r.gross_loss if r.gross_loss > 0 else float("inf")
        r.avg_win = r.gross_profit / r.winning_trades if r.winning_trades > 0 else 0
        r.avg_loss = r.gross_loss / r.losing_trades if r.losing_trades > 0 else 0
        r.expectancy = r.net_profit / r.total_trades
        r.total_pips = sum(t.pnl_pips for t in self.trades)
        r.avg_bars_held = np.mean([t.bars_held for t in self.trades]) if self.trades else 0

        # Equity curve from tracked data
        ec = pd.DataFrame(self.equity_curve)
        if len(ec) > 0:
            r.equity_curve = ec.to_dict("records")
            peak = ec["equity"].cummax()
            dd = (ec["equity"] - peak) / peak * 100
            r.max_drawdown_pct = dd.min()
            r.max_drawdown_usd = (peak - ec["equity"]).max()

            # Sharpe ratio
            returns = ec["equity"].pct_change().dropna()
            if len(returns) > 1 and returns.std() > 0:
                r.sharpe_ratio = np.sqrt(252 * 24 * 12) * returns.mean() / returns.std()

        return r

    def _print_summary(self, r: BacktestResult):
        print("\n  ========================================")
        print("           BACKTEST RESULTS")
        print("  ========================================")
        print(f"  Symbol:         {self.cfg['symbol']} ({self.cfg['timeframe']})")
        print(f"  Period:         {self.df.index[0].date()} -> {self.df.index[-1].date()}")
        print(f"  Initial Bal:    ${self.initial_balance:,.2f}")
        print(f"  Final Bal:      ${r.final_balance:,.2f}")
        print(f"  Net Profit:     ${r.net_profit:+,.2f}")
        print(f"  {'─'*44}")
        print(f"  Total Trades:   {r.total_trades}")
        print(f"  Win Rate:       {r.win_rate:.1f}%")
        print(f"  Profit Factor:  {r.profit_factor:.2f}")
        print(f"  Expectancy:     ${r.expectancy:+.2f}/trade")
        print(f"  {'─'*44}")
        print(f"  Avg Win:        ${r.avg_win:+.2f}")
        print(f"  Avg Loss:       ${-r.avg_loss:+.2f}")
        print(f"  Gross Profit:   ${r.gross_profit:+,.2f}")
        print(f"  Gross Loss:     ${r.gross_loss:+,.2f}")
        print(f"  {'─'*44}")
        print(f"  Max DD:         {r.max_drawdown_pct:.2f}% (${r.max_drawdown_usd:,.2f})")
        print(f"  Sharpe Ratio:   {r.sharpe_ratio:.2f}")
        print(f"  Avg Bars Held:  {r.avg_bars_held:.1f}")
        print(f"  Total Pips:     {r.total_pips:+.1f}")


# ============================================================
# Plotter
# ============================================================

class Plotter:
    @staticmethod
    def plot(result: BacktestResult, df: pd.DataFrame, symbol: str, save_path: str = None):
        if len(result.trades) == 0:
            print("  No trades to plot.")
            return

        has_indicators = all(c in df.columns for c in ["ema_fast", "ema_slow", "atr"])

        fig, axes = plt.subplots(3, 1, figsize=(14, 10), gridspec_kw={"height_ratios": [3, 1, 1]})
        fig.suptitle(f"{symbol} M5 Scalping - Backtest", fontsize=14, fontweight="bold")

        # Price + Trades
        ax1 = axes[0]
        ax1.plot(df.index, df["close"], label="Close", color="gray", linewidth=0.7, alpha=0.7)
        if has_indicators:
            ax1.plot(df.index, df["ema_fast"], label="EMA Fast", color="blue", linewidth=0.6, alpha=0.6)
            ax1.plot(df.index, df["ema_slow"], label="EMA Slow", color="red", linewidth=0.6, alpha=0.6)

        for t in result.trades:
            color = "green" if t.pnl > 0 else "red"
            marker = "^" if t.side == "BUY" else "v"
            ax1.scatter(t.entry_time, t.entry_price, marker=marker, color=color, s=80, zorder=5)
            ax1.plot([t.entry_time, t.exit_time], [t.entry_price, t.exit_price], color=color, linewidth=0.8, alpha=0.6)

        ax1.set_ylabel("Price")
        ax1.legend(loc="upper left", fontsize=8)
        ax1.grid(True, alpha=0.2)

        # ATR
        ax2 = axes[1]
        if has_indicators:
            ax2.plot(df.index, df["atr"], color="purple", linewidth=0.7)
        ax2.set_ylabel("ATR")
        ax2.grid(True, alpha=0.2)

        # Equity Curve
        ax3 = axes[2]
        ec = pd.DataFrame(result.equity_curve)
        if len(ec) > 0:
            ax3.plot(pd.to_datetime(ec["date"]), ec["equity"], label="Equity", color="blue", linewidth=0.8)
            ax3.fill_between(pd.to_datetime(ec["date"]), ec["equity"], alpha=0.1, color="blue")
        ax3.set_ylabel("Equity ($)")
        ax3.set_xlabel("Date")
        ax3.legend(loc="upper left", fontsize=8)
        ax3.grid(True, alpha=0.2)

        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches="tight")
            print(f"  Chart saved: {save_path}")
        plt.close()


# ============================================================
# Main
# ============================================================

def main():
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    symbol = config["symbol"]
    timeframe = config["timeframe"]
    print(f"\n  {'='*45}")
    print(f"  Scalping Bot Backtest")
    print(f"  {symbol} {timeframe}")
    print(f"  {'='*45}")

    # Load data
    loader = DataLoader(config)
    df = loader.fetch()

    # Run backtest
    engine = BacktestEngine(df, config)
    result = engine.run()

    # Plot
    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(exist_ok=True)
    chart_path = output_dir / f"backtest_{symbol}_{timeframe}.png"
    plot_df = engine.df
    Plotter.plot(result, plot_df, symbol, str(chart_path))

    # Export trades
    trades_df = pd.DataFrame([asdict(t) for t in result.trades])
    csv_path = output_dir / f"trades_{symbol}_{timeframe}.csv"
    trades_df.to_csv(csv_path, index=False)
    print(f"  Trades exported: {csv_path}")

    # Export summary JSON
    summary = {
        "symbol": symbol,
        "timeframe": timeframe,
        "strategy": config["strategy"]["name"],
        "parameters": config["strategy"],
        "result": {k: v for k, v in asdict(result).items() if k not in ("trades", "equity_curve")},
    }
    summary_path = output_dir / f"summary_{symbol}_{timeframe}.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"  Summary saved: {summary_path}")
    print(f"  {'='*45}\n")


if __name__ == "__main__":
    main()
