"""
Scalping Bot — Multi-Symbol Live Trading Engine
EMA 8/34 Cross + ATR SL/TP + Trailing Stop
"""
import os, sys, time, logging, json, csv
from datetime import datetime, timedelta
from pathlib import Path

import yaml
import MetaTrader5 as mt5

from strategy import ScalpingStrategy
from telegram import send_order, send_close, send_error, send_startup

logger = logging.getLogger(__name__)


class SymbolHandler:
    """Manages trading for a single symbol"""
    def __init__(self, cfg: dict, shared_strategy: dict):
        self.name = cfg["name"]
        self.lot = cfg["lot_size"]
        self.magic = cfg["magic_number"]
        self.comment = cfg["comment"]
        self.timeframe_str = cfg.get("timeframe", "M5")
        self.timeframe_minutes = 1 if self.timeframe_str == "M1" else 5
        self.strategy_type = cfg.get("strategy_type", "EMA_CROSS")

        full_config = {
            "symbol": self.name,
            "lot_size": self.lot,
            "magic_number": self.magic,
            "comment": self.comment,
            "max_spread_points": cfg.get("max_spread_points", 300),
            "timeframe": self.timeframe_str,
            "strategy_type": self.strategy_type,
            "strategy": shared_strategy,
        }
        self.strategy = ScalpingStrategy(full_config)

        self.state = {
            "last_candle_time": None,
            "daily_pnl": 0.0,
            "consecutive_losses": 0,
            "last_date": None,
            "circuit_breaked": False,
            "session_trades": 0,
            "last_signal_candle": None,
        }
        self._load_state()

    def state_path(self):
        return Path(__file__).parent / f"state_{self.name}.json"

    def _load_state(self):
        p = self.state_path()
        if p.exists():
            try:
                with open(p) as f:
                    saved = json.load(f)
                self.state.update(saved)
            except Exception:
                pass

    def save_state(self):
        try:
            with open(self.state_path(), "w") as f:
                json.dump(self.state, f, indent=2, default=str)
        except Exception:
            pass

    def get_position(self):
        positions = mt5.positions_get(symbol=self.name)
        if positions is None:
            return None
        for pos in positions:
            if pos.comment == self.comment or pos.magic == self.magic:
                return pos
        return None

    def init_mt5(self):
        if not mt5.symbol_select(self.name, True):
            logger.error(f"Symbol {self.name} not found")
            return False
        return True

    def place_order(self, signal: dict):
        side = mt5.ORDER_TYPE_BUY if signal["side"] == "buy" else mt5.ORDER_TYPE_SELL
        tick = mt5.symbol_info_tick(self.name)
        price = tick.ask if side == mt5.ORDER_TYPE_BUY else tick.bid

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.name,
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
            logger.error(f"[{self.name}] Order failed: {result.retcode}")
            send_error(f"[{self.name}] Order failed: {result.retcode}")
            return False

        logger.info(f"[{self.name}] OPENED {signal['side'].upper()} @ {price} SL={signal['sl']} TP={signal['tp']}")
        send_order(signal["side"], self.name, price, signal["sl"], signal["tp"], self.lot)
        self.state["session_trades"] += 1
        self.save_state()
        return True

    def update_trailing(self, position):
        new_sl = self.strategy.update_trailing_stop(position)
        if not new_sl:
            return

        side = mt5.ORDER_TYPE_BUY if position.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_SELL
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": self.name,
            "position": position.ticket,
            "sl": new_sl,
            "tp": position.tp,
            "magic": self.magic,
            "comment": self.comment,
        }
        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"[{self.name}] Trailing SL: {position.sl} -> {new_sl}")
        else:
            logger.warning(f"[{self.name}] Trailing SL fail: {result.retcode}")


class ScalpingBot:
    def __init__(self, config_path: str):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)

        self.poll_interval = self.config.get("poll_interval_seconds", 5)
        self.risk = self.config["risk"]
        self.test_mode = self.config.get("test_mode", False)
        self.mode = self.config.get("mode", "candle")

        self.shared_strategy = self.config["strategy"]
        symbol_configs = self.config["symbols"]

        self.handlers: list[SymbolHandler] = []
        for sc in symbol_configs:
            self.handlers.append(SymbolHandler(sc, self.shared_strategy))

        self._setup_logging()

    def _setup_logging(self):
        log_dir = Path(__file__).parent / "logs"
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / f"bot_{datetime.now().strftime('%Y%m%d')}.log"
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
        )

    def _init_mt5(self):
        if not mt5.initialize():
            logger.error(f"MT5 init failed: {mt5.last_error()}")
            return False

        for h in self.handlers:
            if not h.init_mt5():
                mt5.shutdown()
                return False
        logger.info(f"MT5 connected | {', '.join(h.name for h in self.handlers)}")
        return True

    def _get_account_info(self):
        info = mt5.account_info()
        if info:
            return {"balance": info.balance, "equity": info.equity, "profit": info.profit}
        return None

    def _check_global_circuit(self):
        account = self._get_account_info()
        if not account:
            return False
        dd_pct = (account["balance"] - account["equity"]) / account["balance"] * 100
        if dd_pct > self.risk.get("circuit_breaker_dd_pct", 15):
            logger.error(f"Global circuit breaker! DD={dd_pct:.1f}%")
            send_error(f"Circuit breaker! DD={dd_pct:.1f}%")
            return True
        return False

    def _check_closed_positions(self):
        yesterday = datetime.now() - timedelta(days=1)
        deals = mt5.history_deals_get(yesterday, datetime.now())
        if deals is None:
            return

        handler_map = {}
        for h in self.handlers:
            handler_map[h.magic] = h
            handler_map[h.comment] = h

        for deal in deals:
            if deal.entry != mt5.DEAL_ENTRY_OUT:
                continue
            handler = handler_map.get(deal.magic) or handler_map.get(deal.comment)
            if not handler:
                continue

            position = mt5.history_deals_get(ticket=deal.position_id)
            if not position or len(position) == 0:
                continue

            entry_deal = position[0]
            pnl = deal.profit
            side = "BUY" if deal.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_BUY_LIMIT) else "SELL"
            reason = "SL" if pnl <= 0 else "TP"

            logger.info(f"[{handler.name}] CLOSED {side} PnL=${pnl:.2f} ({reason})")
            send_close(side, handler.name, entry_deal.price, deal.price, pnl, reason)
            self._log_trade_to_csv(entry_deal, deal, pnl, handler.name)

            if pnl <= 0:
                handler.state["consecutive_losses"] += 1
            else:
                handler.state["consecutive_losses"] = 0
            handler.state["daily_pnl"] += pnl
            handler.save_state()

    def _log_trade_to_csv(self, entry_deal, exit_deal, pnl, symbol_name):
        csv_path = Path(__file__).parent / "trades.csv"
        is_new = not csv_path.exists()
        with open(csv_path, "a", newline="") as f:
            w = csv.writer(f)
            if is_new:
                w.writerow(["time", "symbol", "side", "entry_price", "exit_price", "volume", "profit", "reason"])
            side = "BUY" if exit_deal.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_BUY_LIMIT) else "SELL"
            reason = "SL" if pnl <= 0 else "TP"
            w.writerow([
                datetime.fromtimestamp(exit_deal.time).strftime("%m/%d %H:%M"),
                symbol_name,
                side,
                entry_deal.price,
                exit_deal.price,
                entry_deal.volume,
                round(pnl, 2),
                reason,
            ])

    def _daily_reset(self, handler):
        today = datetime.now().date()
        if handler.state["last_date"] and today != handler.state["last_date"]:
            handler.state["daily_pnl"] = 0.0
            handler.state["consecutive_losses"] = 0
        handler.state["last_date"] = today

    def _is_new_candle_allowed(self, handler):
        tf = handler.timeframe_minutes
        now = datetime.now()
        candle = now.replace(minute=(now.minute // tf) * tf, second=0, microsecond=0)

        if self.mode == "candle":
            key = handler.state.get("last_candle_time")
            if key is None or candle > datetime.fromisoformat(str(key)):
                handler.state["last_candle_time"] = candle.isoformat()
                handler.save_state()
                return True
            return False
        else:
            if handler.state.get("last_signal_candle") == candle.isoformat():
                return False
            handler.state["last_signal_candle"] = candle.isoformat()
            handler.save_state()
            return True

    def _tick_handler(self, handler):
        if self._check_global_circuit():
            return

        self._daily_reset(handler)

        if handler.state["circuit_breaked"]:
            return
        if handler.state["consecutive_losses"] >= self.risk.get("max_consecutive_losses", 5):
            return

        account = self._get_account_info()
        if account:
            max_loss = self.risk.get("max_daily_loss_pct", 5) / 100 * account["balance"]
            if handler.state["daily_pnl"] <= -max_loss:
                return

        # Manage open position
        position = handler.get_position()
        if position:
            handler.update_trailing(position)
            return

        # Wait for new candle
        if not self._is_new_candle_allowed(handler):
            return

        signal = handler.strategy.get_signal()
        if signal:
            handler.place_order(signal)

    def run(self):
        names = ", ".join(f"{h.name}({h.strategy_type}/{h.timeframe_str})" for h in self.handlers)
        logger.info("=" * 50)
        logger.info("BOT STARTING")
        logger.info(f"Symbols: {names}")
        logger.info(f"Strategy: EMA {self.shared_strategy['ema_fast']}/{self.shared_strategy['ema_slow']}")
        logger.info(f"SL/TP: {self.shared_strategy['sl_atr_mult']}/{self.shared_strategy['tp_atr_mult']} ATR")
        logger.info(f"Mode: {self.mode}")
        logger.info("=" * 50)

        send_startup({"symbols": names, "config": self.config})

        if self.test_mode:
            handler = self.handlers[0]
            self._place_test_trade(handler)

        while True:
            try:
                if not mt5.terminal_info():
                    logger.warning("MT5 disconnected, reconnecting...")
                    if not self._init_mt5():
                        time.sleep(10)
                        continue

                self._check_closed_positions()

                for handler in self.handlers:
                    try:
                        self._tick_handler(handler)
                    except Exception as e:
                        logger.exception(f"[{handler.name}] Handler error: {e}")

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

    def _place_test_trade(self, handler):
        if handler.get_position():
            logger.info(f"[{handler.name}] Test: already has position")
            return

        tick = mt5.symbol_info_tick(handler.name)
        if tick is None:
            logger.error(f"[{handler.name}] Test: no tick")
            return

        atr = handler.strategy._get_current_atr()
        if atr is None:
            logger.error(f"[{handler.name}] Test: no ATR")
            return

        price = tick.ask
        sl = round(price - atr * handler.strategy.sl_atr_mult, 2)
        tp = round(price + atr * handler.strategy.tp_atr_mult, 2)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": handler.name,
            "volume": handler.lot,
            "type": mt5.ORDER_TYPE_BUY,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 10,
            "magic": handler.magic,
            "comment": handler.comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"[{handler.name}] TEST TRADE: BUY @ {price}")
            handler.state["session_trades"] += 1
            handler.save_state()
        else:
            logger.error(f"[{handler.name}] Test trade failed: {result.retcode}")


def main():
    config_path = Path(__file__).parent / "config.yaml"
    bot = ScalpingBot(str(config_path))

    if not bot._init_mt5():
        sys.exit(1)

    bot.run()


if __name__ == "__main__":
    main()
