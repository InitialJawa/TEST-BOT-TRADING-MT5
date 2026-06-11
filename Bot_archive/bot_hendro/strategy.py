"""
Strategy Engine ΓÇö Multi-Strategy: EMA_CROSS / MOMENTUM / PULLBACK / TREND_RE / ADAPTIVE
ATR-based SL/TP + trailing for all strategies
"""
import pandas as pd
import MetaTrader5 as mt5
from datetime import datetime, timezone, time as dtime

TF_MAP = {"M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5}

VALID_STRATEGIES = {"EMA_CROSS", "MOMENTUM", "PULLBACK", "TREND_RE", "ADAPTIVE"}


class ScalpingStrategy:
    def __init__(self, config: dict):
        s = config["strategy"]
        self.ema_fast = s["ema_fast"]
        self.ema_slow = s["ema_slow"]
        self.atr_period = s["atr_period"]
        self.sl_atr_mult = s["sl_atr_mult"]
        self.tp_atr_mult = s["tp_atr_mult"]
        self.trail_activation = s["trailing_activation"]
        self.mom_threshold = s.get("momentum_threshold", 0.0005)
        self.regime_threshold = s.get("regime_threshold", 0.5)

        self.max_spread = config["max_spread_points"]
        self.session_filter = config.get("session_filter", {})
        self.symbol = config["symbol"]
        self.lot = config["lot_size"]
        self.magic = config["magic_number"]
        self.comment = config["comment"]
        self.timeframe_str = config.get("timeframe", "M5")
        self.timeframe = TF_MAP.get(self.timeframe_str, mt5.TIMEFRAME_M5)
        self.strategy_type = config.get("strategy_type", "EMA_CROSS")
        if self.strategy_type not in VALID_STRATEGIES:
            raise ValueError(f"Unknown strategy: {self.strategy_type}")

    # ---- indicators ----

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

    def _get_trend_dir(self, df):
        if len(df) < 2:
            return None
        prev = df.iloc[-2]
        curr = df.iloc[-1]
        if prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"]:
            return "up"
        if prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"]:
            return "down"
        if curr["ema_fast"] > curr["ema_slow"]:
            return "up"
        if curr["ema_fast"] < curr["ema_slow"]:
            return "down"
        return None

    def _is_trend_up(self, df):
        return df.iloc[-1]["ema_fast"] > df.iloc[-1]["ema_slow"]

    def _is_trend_down(self, df):
        return df.iloc[-1]["ema_fast"] < df.iloc[-1]["ema_slow"]

    # ---- session filter ----

    def _is_within_session(self):
        sess = self.session_filter
        if not sess.get("enabled", False):
            return True
        now = datetime.now(timezone.utc).time()
        open_h, open_m = map(int, sess["open_utc"].split(":"))
        close_h, close_m = map(int, sess["close_utc"].split(":"))
        session_open = dtime(open_h, open_m)
        session_close = dtime(close_h, close_m)
        if session_open <= session_close:
            return session_open <= now <= session_close
        else:
            return now >= session_open or now <= session_close

    # ---- signal builders ----

    def _build_signal(self, side, price, atr, timestamp):
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
            "time": timestamp,
        }

    def _sig_ema_cross(self, df):
        if len(df) < 3:
            return None
        prev = df.iloc[-2]
        curr = df.iloc[-1]
        bull = prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"]
        bear = prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"]
        if not (bull or bear):
            return None
        side = "buy" if bull else "sell"
        return self._build_signal(side, curr["close"], curr["atr"], curr.name)

    def _sig_momentum(self, df):
        if len(df) < 4:
            return None
        trend_up = self._is_trend_up(df)
        trend_down = self._is_trend_down(df)
        if not trend_up and not trend_down:
            return None
        curr = df.iloc[-1]
        prev3 = df.iloc[-4]
        mom = curr["close"] / prev3["close"] - 1
        if trend_up and mom > self.mom_threshold:
            return self._build_signal("buy", curr["close"], curr["atr"], curr.name)
        if trend_down and mom < -self.mom_threshold:
            return self._build_signal("sell", curr["close"], curr["atr"], curr.name)
        return None

    def _sig_pullback(self, df):
        if len(df) < 3:
            return None
        trend_up = self._is_trend_up(df)
        trend_down = self._is_trend_down(df)
        if not trend_up and not trend_down:
            return None
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        if trend_up and curr["low"] <= prev["ema_fast"]:
            return self._build_signal("buy", curr["close"], curr["atr"], curr.name)
        if trend_down and curr["high"] >= prev["ema_fast"]:
            return self._build_signal("sell", curr["close"], curr["atr"], curr.name)
        return None

    def _sig_trend_re(self, df):
        if len(df) < 3:
            return None
        trend_up = self._is_trend_up(df)
        trend_down = self._is_trend_down(df)
        if not trend_up and not trend_down:
            return None
        curr = df.iloc[-1]
        # Only enter if price is positioned relative to EMA fast (avoid late entries)
        if trend_up and curr["close"] <= curr["ema_fast"]:
            return None
        if trend_down and curr["close"] >= curr["ema_fast"]:
            return None
        side = "buy" if trend_up else "sell"
        return self._build_signal(side, curr["close"], curr["atr"], curr.name)

    def _detect_regime(self, df):
        """Detect market regime: 'ranging' or 'trending' based on EMA spread vs ATR"""
        if len(df) < 3:
            return "ranging"
        curr = df.iloc[-1]
        ema_spread = abs(curr["ema_fast"] - curr["ema_slow"])
        atr_val = curr["atr"]
        if atr_val == 0:
            return "ranging"
        ratio = ema_spread / atr_val
        return "trending" if ratio >= self.regime_threshold else "ranging"

    def _sig_adaptive(self, df):
        """Adaptive multi-strategy:
        - Ranging market: EMA crossover (catches reversals)
        - Trending market: TREND_RE + momentum confirmation (rides trend)
        """
        if len(df) < 4:
            return None
        regime = self._detect_regime(df)
        curr = df.iloc[-1]

        if regime == "ranging":
            sig = self._sig_ema_cross(df)
            if sig:
                sig["regime"] = "ranging"
            return sig
        else:
            trend_up = self._is_trend_up(df)
            trend_down = self._is_trend_down(df)
            if not trend_up and not trend_down:
                return None
            mom = curr["close"] / df.iloc[-4]["close"] - 1
            if trend_up and mom > -self.mom_threshold:
                sig = self._build_signal("buy", curr["close"], curr["atr"], curr.name)
                sig["regime"] = "trending"
                return sig
            if trend_down and mom < self.mom_threshold:
                sig = self._build_signal("sell", curr["close"], curr["atr"], curr.name)
                sig["regime"] = "trending"
                return sig
            return None

    @property
    def is_reentry_strategy(self):
        """TREND_RE and ADAPTIVE support re-entry after SL"""
        return self.strategy_type in ("TREND_RE", "ADAPTIVE")

    # ---- public API ----

    def get_signal(self):
        rates = mt5.copy_rates_from_pos(self.symbol, self.timeframe, 1, 100)
        if rates is None or len(rates) < self.ema_slow + 5:
            return None
        df = self.calculate_indicators(rates)
        if len(df) < 2:
            return None
        curr = df.iloc[-1]

        spread = curr.get("spread", 0)
        if spread > self.max_spread:
            return None

        if not self._is_within_session():
            return None

        if self.strategy_type == "EMA_CROSS":
            return self._sig_ema_cross(df)
        elif self.strategy_type == "MOMENTUM":
            return self._sig_momentum(df)
        elif self.strategy_type == "PULLBACK":
            return self._sig_pullback(df)
        elif self.strategy_type == "TREND_RE":
            return self._sig_trend_re(df)
        elif self.strategy_type == "ADAPTIVE":
            return self._sig_adaptive(df)
        return None

    def get_trend_signal(self):
        """Quick trend-direction entry (for TREND_RE / ADAPTIVE re-entry)."""
        rates = mt5.copy_rates_from_pos(self.symbol, self.timeframe, 1, 10)
        if rates is None or len(rates) < 5:
            return None
        df = self.calculate_indicators(rates)
        if len(df) < 2:
            return None
        curr = df.iloc[-1]
        spread = curr.get("spread", 0)
        if spread > self.max_spread:
            return None
        if not self._is_within_session():
            return None
        if self.strategy_type == "ADAPTIVE":
            regime = self._detect_regime(df)
            if regime != "trending":
                return None
            mom = curr["close"] / df.iloc[-4]["close"] - 1
            if self._is_trend_up(df) and mom > -self.mom_threshold:
                sig = self._build_signal("buy", curr["close"], curr["atr"], curr.name)
                sig["regime"] = "trending"
                return sig
            if self._is_trend_down(df) and mom < self.mom_threshold:
                sig = self._build_signal("sell", curr["close"], curr["atr"], curr.name)
                sig["regime"] = "trending"
                return sig
            return None
        if self._is_trend_up(df) and curr["close"] > curr["ema_fast"]:
            return self._build_signal("buy", curr["close"], curr["atr"], curr.name)
        if self._is_trend_down(df) and curr["close"] < curr["ema_fast"]:
            return self._build_signal("sell", curr["close"], curr["atr"], curr.name)
        return None

    def update_trailing_stop(self, position):
        rates = mt5.copy_rates_from_pos(self.symbol, self.timeframe, 1, 3)
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
        rates = mt5.copy_rates_from_pos(self.symbol, self.timeframe, 1, 50)
        if rates is None or len(rates) < self.atr_period + 5:
            return None
        df = self.calculate_indicators(rates)
        if len(df) < 2:
            return None
        return df.iloc[-1]["atr"]
