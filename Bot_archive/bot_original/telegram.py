"""
Telegram Notifier
"""
import os
import logging
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

_ENABLED = bool(BOT_TOKEN and CHAT_ID)


def send(msg: str):
    if not _ENABLED:
        logger.info(f"[Telegram DISABLED] (configure .env to enable)")
        return

    try:
        import requests
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
        if resp.status_code != 200:
            logger.warning(f"Telegram send failed: {resp.text}")
    except Exception as e:
        logger.warning(f"Telegram error: {e}")


def send_order(side: str, symbol: str, price: float, sl: float, tp: float, lot: float):
    send(
        f"<b>ORDER OPEN</b>\n"
        f"<b>{side.upper()}</b> {symbol}\n"
        f"Price: {price}\n"
        f"SL: {sl} | TP: {tp}\n"
        f"Lot: {lot}"
    )


def send_close(side: str, symbol: str, entry: float, exit_px: float, pnl: float, reason: str):
    emoji = "≡ƒƒó" if pnl > 0 else "≡ƒö┤"
    send(
        f"{emoji} <b>ORDER CLOSED</b>\n"
        f"<b>{side.upper()}</b> {symbol}\n"
        f"Entry: {entry} | Exit: {exit_px}\n"
        f"P&L: ${pnl:.2f}\n"
        f"Reason: {reason}"
    )


def send_error(msg: str):
    send(f"ΓÜá∩╕Å <b>BOT ERROR</b>\n{msg}")


def send_startup(config: dict):
    send(
        f"≡ƒñû <b>Scalping Bot Started</b>\n"
        f"Symbol: {config['symbol']} M5\n"
        f"Strategy: EMA {config['strategy']['ema_fast']}/{config['strategy']['ema_slow']}\n"
        f"SL/TP: {config['strategy']['sl_atr_mult']}/{config['strategy']['tp_atr_mult']} ATR\n"
        f"Lot: {config['lot_size']}"
    )
