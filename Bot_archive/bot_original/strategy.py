"""
Strategy Engine ΓÇö EMA 8/34 Cross + ATR SL/TP + Trailing
"""
import pandas as pd
import MetaTrader5 as mt5


class ScalpingStrategy:
    def __init__(self, config: dict):
        s = config["strategy"]
        self.ema_fast = s["ema_fast"]
        self.ema_slow = s["ema_slow"]
        self.atr_period = s["atr_period"]
        self.sl_atr_mult = s["sl_atr_mult"]
        self.tp_atr_mult = s["tp_atr_mult"]
        self.trail_activation = s["trailing_activation"]
        self.max_spread = config["max_spread_points"]
        self.symbol = config["symbol"]
        self.lot = config["lot_size"]
        self.magic = config["magic_number"]
        self.comment = config["comment"]

    def calculate_indicators(self, rates):
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df.set_index("time", inplace=True)
        c = df["close"]
        df["ema_fast"] = c.ewm(span=self.ema_fast, adjust=False).mean()
        df["ema_slow"] = c.ewm(span=self.ema_slow, adjust=False).mean()
        h, l = df["high"], df["low"]
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        df["atr"] = tr.ewm(span=self.atr_period, adjust=False).mean()
        return df

    def get_signal(self):
        rates = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_M5, 1, 100)
        if rates is None or len(rates) < self.ema_slow + 5:
            return None

        df = self.calculate_indicators(rates)
        if len(df) < 2:
            return None

        prev = df.iloc[-2]
        curr = df.iloc[-1]

        spread = curr.get("spread", 0)
        if spread > self.max_spread:
            return None

        bull = prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"]
        bear = prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"]

        if not (bull or bear):
            return None

        side = "buy" if bull else "sell"
        price = curr["close"]
        atr = curr["atr"]

        if side == "buy":
            sl = round(price - atr * self.sl_atr_mult, 2)
            tp = round(price + atr * self.tp_atr_mult, 2)
        else:
            sl = round(price + atr * self.sl_atr_mult, 2)
            tp = round(price - atr * self.tp_atr_mult, 2)

        return {
            "side": side,
            "price": price,
            "sl": sl,
            "tp": tp,
            "atr": round(atr, 2),
            "time": curr.name,
        }

    def update_trailing_stop(self, position):
        rates = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_M5, 1, 3)
        if rates is None:
            return None
        current_price = rates[-1][2]

        entry_price = position.price_open
        atr = self._get_current_atr()
        if atr is None:
            return None

        activation = atr * self.trail_activation

        if position.type == mt5.ORDER_TYPE_BUY:
            profit = current_price - entry_price
            if profit > activation:
                new_sl = current_price - activation
                if new_sl > position.sl:
                    return round(new_sl, 2)
        elif position.type == mt5.ORDER_TYPE_SELL:
            profit = entry_price - current_price
            if profit > activation:
                new_sl = current_price + activation
                if new_sl < position.sl:
                    return round(new_sl, 2)
        return None

    def _get_current_atr(self):
        rates = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_M5, 1, 50)
        if rates is None or len(rates) < self.atr_period + 5:
            return None
        df = self.calculate_indicators(rates)
        if len(df) < 2:
            return None
        return df.iloc[-1]["atr"]
