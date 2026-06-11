"""
Compound Manager — auto-adjust lot berdasarkan equity
Handle IDR & USD accounts.
Pair ratios (USD-based):
  XAU:   lot = usd_eq / 30000  (0.01 lot per $300)
  JP225: lot = usd_eq / 3000   (0.10 lot per $300)
  US30:  skip if usd_eq < $3000, then usd_eq / 30000
Jalan tiap 1 jam via Task Scheduler.
"""
import json, os, signal, time, subprocess
from datetime import datetime
from pathlib import Path
import MetaTrader5 as mt5
import yaml

BASE = Path(__file__).parent
LOG_FILE = BASE / "logs" / "compound.log"
STATE_FILE = BASE / "compound_state.json"
PYTHON = "C:\\Python314\\python.exe"

PAIR_CONFIG = {
    "XAUUSDm": {"ratio_usd": 30000,  "min_eq_usd": 0,    "min_lot": 0.01, "lot_step": 0.01},
    "JP225m":  {"ratio_usd": 300,    "min_eq_usd": 0,    "min_lot": 1.0,  "lot_step": 1.0},
    "US30m":   {"ratio_usd": 30000,  "min_eq_usd": 3000, "min_lot": 0.01, "lot_step": 0.01},
}

BOT_CONFIGS = {
    "bot_fabio": BASE / "bot_fabio" / "config.yaml",
}


def log(msg):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    t = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"{t} {msg}\n")
    print(f"{t} {msg}")


def read_config(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def write_config(path, cfg):
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def calc_lot(usd_eq, ratio_usd, min_lot=0.01, lot_step=0.01):
    raw = usd_eq / ratio_usd
    steps = round(raw / lot_step) if lot_step > 0 else 0
    lot = max(steps * lot_step, min_lot)
    return round(lot, 2)


def get_usd_equity():
    if not mt5.initialize():
        return None, "init_fail"
    acc = mt5.account_info()
    if not acc:
        mt5.shutdown()
        return None, "no_account"
    eq = acc.equity
    currency = acc.currency
    mt5.shutdown()

    if currency == "USD":
        return eq, "USD"
    if currency == "IDR":
        mt5.initialize()
        info = mt5.symbol_info("USDIDRm")
        if info:
            rate = info.bid
        else:
            rate = 17000
        mt5.shutdown()
        return eq / rate, f"IDR (rate={rate:.0f})"
    # Fallback: try to find conversion pair
    return eq, currency


def update_lots(cfg, usd_eq):
    changes = []
    for s in cfg.get("symbols", []):
        sym = s["name"]
        pc = PAIR_CONFIG.get(sym)
        if not pc:
            continue
        if usd_eq < pc["min_eq_usd"]:
            old = s["lot_size"]
            s["lot_size"] = 0.0
            if old and old != 0.0:
                changes.append(f"{sym}: {old} -> 0 (skip, eq < ${pc['min_eq_usd']})")
            continue
        new_lot = calc_lot(usd_eq, pc["ratio_usd"], pc["min_lot"], pc["lot_step"])
        old = s["lot_size"]
        s["lot_size"] = new_lot
        if old is None or abs(old - new_lot) > 0.005:
            changes.append(f"{sym}: {old} -> {new_lot}")
    return changes


def run():
    log("=" * 50)
    log("Compound Manager check started")

    usd_eq, note = get_usd_equity()
    if usd_eq is None:
        log(f"Failed to get equity: {note}")
        return
    log(f"USD equity: ${usd_eq:.2f} ({note})")

    state = {}
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())

    all_changes = {}
    for bot_name, cfg_path in BOT_CONFIGS.items():
        cfg = read_config(cfg_path)
        changes = update_lots(cfg, usd_eq)
        if changes:
            write_config(cfg_path, cfg)
            all_changes[bot_name] = changes
            for c in changes:
                log(f"  [{bot_name}] {c}")

    if all_changes:
        import psutil as ps
        for proc in ps.process_iter(["pid", "cmdline", "cwd"]):
            cmd = " ".join(proc.info.get("cmdline") or [])
            if "bot.py" not in cmd:
                continue
            cwd = proc.info.get("cwd") or ""
            for name in BOT_CONFIGS:
                if name in cwd:
                    try:
                        os.kill(proc.info["pid"], signal.SIGTERM)
                        log(f"Killed {name} PID={proc.info['pid']}")
                    except:
                        pass
        time.sleep(3)
        for name in BOT_CONFIGS:
            bot_dir = BOT_CONFIGS[name].parent
            subprocess.Popen(
                [PYTHON, "bot.py"],
                cwd=str(bot_dir),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            log(f"Started {name}")
    else:
        log("No lot changes")

    state["last_equity_usd"] = round(usd_eq, 2)
    state["last_check"] = datetime.now().isoformat()
    STATE_FILE.write_text(json.dumps(state, indent=2))
    log("Done")


if __name__ == "__main__":
    run()
