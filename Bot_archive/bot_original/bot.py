"""
Scalping Bot ΓÇö Live Trading Engine
EMA 8/34 Cross + ATR SL/TP + Trailing Stop
"""
import os
import sys
import time
import logging
import json
import csv
from datetime import datetime, timedelta
from pathlib import Path

import yaml
import MetaTrader5 as mt5

from strategy import ScalpingStrategy
from telegram import send_order, send_close, send_error, send_startup

logger = logging.getLogger(__name__)


class ScalpingBot:
    def __init__(self, config_path: str):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)

        self.symbol = self.config["symbol"]
        self.lot = self.config["lot_size"]
        self.magic = self.config["magic_number"]
        self.comment = self.config["comment"]
        self.poll_interval = self.config["poll_interval_seconds"]
        self.risk = self.config["risk"]

        self.strategy = ScalpingStrategy(self.config)
        self._setup_logging()

        self.state = {
            "last_candle_time": None,
            "daily_pnl": 0.0,
            "consecutive_losses": 0,
            "last_date": None,
            "circuit_breaked": False,
            "session_trades": 0,
        }
        self._load_state()

    def _setup_logging(self):
        log_dir = Path(__file__).parent / "logs"
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / f"bot_{datetime.now().strftime('%Y%m%d')}.log"
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler(),
            ],
        )

    def _state_path(self):
        return Path(__file__).parent / f"state_{self.symbol}.json"

    def _load_state(self):
        path = self._state_path()
        if path.exists():
            try:
                with open(path) as f:
                    saved = json.load(f)
                self.state.update(saved)
                logger.info(f"State loaded: {len(self.state)} keys")
            except Exception as e:
                logger.warning(f"State load failed: {e}")

    def _save_state(self):
        try:
            with open(self._state_path(), "w") as f:
                json.dump(self.state, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"State save failed: {e}")

    def _init_mt5(self):
        if not mt5.initialize():
            logger.error(f"MT5 init failed: {mt5.last_error()}")
            return False

        if not mt5.symbol_select(self.symbol, True):
            logger.error(f"Symbol {self.symbol} not found")
            mt5.shutdown()
            return False

        logger.info(f"MT5 connected | {self.symbol}")
        return True

    def _get_open_position(self):
        positions = mt5.positions_get(symbol=self.symbol)
        if positions is None:
            return None
        for pos in positions:
            if pos.comment == self.comment or pos.magic == self.magic:
                return pos
        return None

    def _get_account_info(self):
        info = mt5.account_info()
        if info:
            return {"balance": info.balance, "equity": info.equity, "profit": info.profit}
        return None

    def _check_circuit_breaker(self):
        account = self._get_account_info()
        if not account:
            return False

        if self.state["circuit_breaked"]:
            logger.warning("Circuit breaker active - no new trades")
            return True

        dd_pct = (account["balance"] - account["equity"]) / account["balance"] * 100
        if dd_pct > self.risk["circuit_breaker_dd_pct"]:
            logger.error(f"Circuit breaker triggered! DD={dd_pct:.1f}%")
            self.state["circuit_breaked"] = True
            self._save_state()
            send_error(f"Circuit breaker triggered! DD={dd_pct:.1f}%")
            return True
        return False

    def _check_daily_reset(self):
        today = datetime.now().date()
        if self.state["last_date"] and today != self.state["last_date"]:
            self.state["daily_pnl"] = 0.0
            self.state["consecutive_losses"] = 0
            logger.info(f"Daily reset: new trading day {today}")
        self.state["last_date"] = today

    def _place_order(self, signal: dict):
        side = mt5.ORDER_TYPE_BUY if signal["side"] == "buy" else mt5.ORDER_TYPE_SELL
        price = mt5.symbol_info_tick(self.symbol).ask if side == mt5.ORDER_TYPE_BUY else mt5.symbol_info_tick(self.symbol).bid

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": self.lot,
            "type": side,
            "price": price,
            "sl": signal["sl"],
            "tp": signal["tp"],
            "deviation": 10,
            "magic": self.magic,
            "comment": self.comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Order failed: {result.retcode} - {result.comment}")
            send_error(f"Order failed: {result.retcode}")
            return False

        logger.info(f"ORDER OPENED: {signal['side'].upper()} {self.symbol} @ {price} | SL={signal['sl']} TP={signal['tp']}")
        send_order(signal["side"], self.symbol, price, signal["sl"], signal["tp"], self.lot)
        self.state["session_trades"] += 1
        self._save_state()
        return True

    def _update_position_sl(self, position, new_sl: float):
        side = mt5.ORDER_TYPE_BUY if position.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_SELL
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": self.symbol,
            "position": position.ticket,
            "sl": new_sl,
            "tp": position.tp,
            "magic": self.magic,
            "comment": self.comment,
        }
        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"Trailing SL updated: {position.sl} -> {new_sl}")
        else:
            logger.warning(f"Trailing SL failed: {result.retcode}")

    def _log_trade_to_csv(self, entry_deal, exit_deal, pnl):
        csv_path = Path(__file__).parent / "trades.csv"
        is_new = not csv_path.exists()
        with open(csv_path, "a", newline="") as f:
            w = csv.writer(f)
            if is_new:
                w.writerow(["time", "side", "entry_price", "exit_price", "volume", "profit", "sl", "tp", "reason"])
            side = "BUY" if exit_deal.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_BUY_LIMIT) else "SELL"
            reason = "SL" if pnl <= 0 else "TP"
            w.writerow([
                datetime.fromtimestamp(exit_deal.time),
                side,
                entry_deal.price,
                exit_deal.price,
                entry_deal.volume,
                round(pnl, 2),
                getattr(entry_deal, "sl", ""),
                getattr(entry_deal, "tp", ""),
                reason,
            ])

    def _check_closed_positions(self):
        yesterday = datetime.now() - timedelta(days=1)
        deals = mt5.history_deals_get(yesterday, datetime.now())
        if deals is None:
            return

        for deal in deals:
            if deal.comment != self.comment and deal.magic != self.magic:
                continue
            if deal.entry != mt5.DEAL_ENTRY_OUT:
                continue

            position = mt5.history_deals_get(ticket=deal.position_id)
            if position and len(position) > 0:
                entry_deal = position[0]
                pnl = deal.profit

                side = "BUY" if deal.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_BUY_LIMIT) else "SELL"
                reason = "SL" if pnl <= 0 else "TP"

                logger.info(f"POSITION CLOSED: {side} PnL=${pnl:.2f} ({reason})")
                send_close(side, self.symbol, entry_deal.price, deal.price, pnl, reason)
                self._log_trade_to_csv(entry_deal, deal, pnl)

                if pnl <= 0:
                    self.state["consecutive_losses"] += 1
                else:
                    self.state["consecutive_losses"] = 0
                self.state["daily_pnl"] += pnl
                self._save_state()

    def _is_new_candle(self):
        now = datetime.now()
        current_candle = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)

        if self.state["last_candle_time"] is None or current_candle > datetime.fromisoformat(str(self.state["last_candle_time"])):
            self.state["last_candle_time"] = current_candle.isoformat()
            self._save_state()
            return True
        return False

    def _place_test_trade(self):
        if self._get_open_position():
            logger.info("Test mode: position already exists, skipping")
            return

        tick = mt5.symbol_info_tick(self.symbol)
        if tick is None:
            logger.error("Test mode: cannot get tick")
            return

        atr = self.strategy._get_current_atr()
        if atr is None:
            logger.error("Test mode: cannot get ATR")
            return

        price = tick.ask
        sl = round(price - atr * self.strategy.sl_atr_mult, 2)
        tp = round(price + atr * self.strategy.tp_atr_mult, 2)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": self.lot,
            "type": mt5.ORDER_TYPE_BUY,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 10,
            "magic": self.magic,
            "comment": self.comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"TEST TRADE: BUY {self.lot} lot {self.symbol} @ {price}")
            logger.info(f"  SL: {sl} | TP: {tp}")
            self.state["session_trades"] += 1
            self._save_state()
        else:
            logger.error(f"Test trade failed: {result.retcode} - {result.comment}")

    def run(self):
        logger.info("=" * 50)
        logger.info("BOT STARTING")
        logger.info(f"Symbol: {self.symbol} M5")
        logger.info(f"Strategy: EMA {self.strategy.ema_fast}/{self.strategy.ema_slow}")
        logger.info(f"SL/TP: {self.strategy.sl_atr_mult}/{self.strategy.tp_atr_mult} ATR")
        mode = self.config.get("mode", "candle")
        logger.info(f"Mode: {mode}")
        logger.info("=" * 50)

        send_startup(self.config)

        if self.config.get("test_mode"):
            self._place_test_trade()

        while True:
            try:
                self._tick()
            except KeyboardInterrupt:
                logger.info("Bot stopped by user")
                send_error("Bot stopped by user")
                break
            except Exception as e:
                logger.exception(f"Unexpected error: {e}")
                send_error(f"Unexpected error: {e}")
                time.sleep(30)

            time.sleep(self.poll_interval)

        mt5.shutdown()

    def _tick(self):
        if not mt5.terminal_info():
            logger.warning("MT5 disconnected, reconnecting...")
            if not self._init_mt5():
                time.sleep(10)
                return

        self._check_daily_reset()

        if self._check_circuit_breaker():
            return

        # Risk checks
        if self.state["consecutive_losses"] >= self.risk["max_consecutive_losses"]:
            return

        account = self._get_account_info()
        if account and self.state["daily_pnl"] <= -self.risk["max_daily_loss_pct"] / 100 * account["balance"]:
            return

        # Check open position
        position = self._get_open_position()

        if position:
            new_sl = self.strategy.update_trailing_stop(position)
            if new_sl:
                self._update_position_sl(position, new_sl)
            return

        # New signal?
        mode = self.config.get("mode", "candle")
        if mode == "candle":
            if not self._is_new_candle():
                return
        else:
            now = datetime.now()
            candle_start = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)
            if self.state.get("last_signal_candle") == candle_start.isoformat():
                return

        signal = self.strategy.get_signal()
        if signal is None:
            return

        if mode != "candle":
            now = datetime.now()
            candle_start = now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)
            self.state["last_signal_candle"] = candle_start.isoformat()
            self._save_state()

        self._place_order(signal)


def main():
    config_path = Path(__file__).parent / "config.yaml"
    bot = ScalpingBot(str(config_path))

    if not bot._init_mt5():
        sys.exit(1)

    bot.run()


if __name__ == "__main__":
    main()
