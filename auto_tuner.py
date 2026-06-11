"""
Auto-Tuner Level 1 — Monitor PnL/WR, auto-switch strategy if performance bad
Jalanin via Task Scheduler tiap 30 menit
"""

import json, os, sys, time, subprocess, signal
from datetime import datetime, timedelta
from pathlib import Path

import MetaTrader5 as mt5
import yaml

BASE = Path(__file__).parent
STATE_FILE = BASE / "auto_tuner_state.json"
LOG_FILE = BASE / "logs" / "auto_tuner.log"

# Strategy rotation: if current strategy fails, try next
ROTATION = {
    "MOMENTUM": ["PULLBACK", "TREND_RE", "EMA_CROSS", "MOMENTUM"],
    "PULLBACK": ["MOMENTUM", "TREND_RE", "EMA_CROSS", "PULLBACK"],
    "TREND_RE": ["MOMENTUM", "PULLBACK", "EMA_CROSS", "TREND_RE"],
    "EMA_CROSS": ["MOMENTUM", "PULLBACK", "TREND_RE", "EMA_CROSS"],
    "ADAPTIVE": ["MOMENTUM", "PULLBACK", "TREND_RE", "ADAPTIVE"],
}

THRESHOLDS = {
    "MIN_TRADES": 100,
    "WR_MIN": 20,
    "PNL_MIN": -200,
}

BOT_DIRS = {
    "FABIO": BASE / "bot_fabio",
    "HENDRO": BASE / "bot_hendro",
}


def log(msg):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    t = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"{t} {msg}\n")
    print(f"{t} {msg}")


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def get_deals_today():
    mt5.initialize()
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    deals = mt5.history_deals_get(today, datetime.now())
    mt5.shutdown()
    return deals


def calc_perf(deals):
    data = {}
    for d in deals:
        key = d.magic
        if key not in data:
            data[key] = {"trades": 0, "profit": 0.0, "wins": 0, "losses": 0}
        data[key]["trades"] += 1
        data[key]["profit"] += d.profit
        if d.profit > 0:
            data[key]["wins"] += 1
        else:
            data[key]["losses"] += 1
    return data


def read_config(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def write_config(path, cfg):
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def get_current_strategies(cfg):
    if "symbols" in cfg:
        return {s["name"]: s["strategy_type"] for s in cfg["symbols"]}
    return {cfg["symbol"]: "EMA_CROSS"}


def update_strategy(cfg, symbol, new_strategy):
    if "symbols" in cfg:
        for s in cfg["symbols"]:
            if s["name"] == symbol:
                s["strategy_type"] = new_strategy
                return True
    return False


def find_fabio_pids():
    pids = []
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
            try:
                name = proc.info["name"] or ""
                if "python" not in name.lower():
                    continue
                cmd = " ".join(proc.info["cmdline"] or [])
                if "bot.py" not in cmd:
                    continue
                cwd = proc.info["cwd"] or ""
                if "bot_fabio" in cwd:
                    pids.append(proc.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except ImportError:
        import subprocess as sp
        out = sp.check_output(["wmic", "process", "where", 'name="python.exe"', "get", "processid,commandline"], text=True)
        for line in out.splitlines():
            if "bot.py" in line and "bot_fabio" in line:
                parts = line.strip().split()
                for p in parts:
                    if p.isdigit():
                        pids.append(int(p))
    return pids


def run():
    log("=" * 50)
    log("Auto-Tuner check started")

    state = load_state()
    deals = get_deals_today()
    if not deals:
        log("No trades today, skipping")
        return

    perf = calc_perf(deals)

    def check_and_switch(bot_name, cfg_path):
        cfg = read_config(cfg_path)
        if "symbols" not in cfg:
            return None, []

        local_changes = []
        for sym_cfg in cfg["symbols"]:
            sym = sym_cfg["name"]
            magic = sym_cfg["magic_number"]
            current_strat = sym_cfg["strategy_type"]
            p = perf.get(magic, {"trades": 0, "profit": 0.0, "wins": 0})
            trades = p["trades"]
            wr = (p["wins"] / trades * 100) if trades else 0
            pnl = p["profit"]

            log(f"  [{bot_name}] {sym} (magic={magic}) strat={current_strat} trades={trades} WR={wr:.0f}% PnL=${pnl:.2f}")

            if trades < THRESHOLDS["MIN_TRADES"]:
                continue

            if wr < THRESHOLDS["WR_MIN"] or pnl < THRESHOLDS["PNL_MIN"]:
                rotation = ROTATION.get(current_strat, ROTATION["MOMENTUM"])
                new_strat = rotation[0]

                sym_state = state.get(sym, {})
                tried = sym_state.get("tried", [])
                if new_strat in tried and len(rotation) > 1:
                    new_strat = rotation[1]

                if new_strat == current_strat:
                    continue

                log(f"    -> SWITCH: {current_strat} -> {new_strat} (WR={wr:.0f}% PnL=${pnl:.2f})")
                update_strategy(cfg, sym, new_strat)

                state.setdefault(sym, {"tried": []})
                state[sym]["tried"].append(current_strat)
                state[sym]["switched_at"] = datetime.now().isoformat()
                state[sym]["reason"] = f"WR={wr:.0f}% PnL=${pnl:.2f}"

                local_changes.append(f"{sym}: {current_strat} -> {new_strat}")
            else:
                state.pop(sym, None)

        return cfg, local_changes

    def find_bot_pids(bot_dir_name):
        pids = []
        try:
            import psutil
            for proc in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
                try:
                    name = proc.info["name"] or ""
                    if "python" not in name.lower(): continue
                    cmd = " ".join(proc.info["cmdline"] or [])
                    if "bot.py" not in cmd: continue
                    cwd = proc.info["cwd"] or ""
                    if bot_dir_name in cwd:
                        pids.append(proc.info["pid"])
                except: pass
        except ImportError:
            import subprocess as sp
            out = sp.check_output(["wmic", "process", "where", 'name="python.exe"', "get", "processid,commandline"], text=True)
            for line in out.splitlines():
                if "bot.py" in line and bot_dir_name in line:
                    parts = line.strip().split()
                    for p in parts:
                        if p.isdigit(): pids.append(int(p))
        return pids

    def restart_bot(bot_dir_name):
        pids = find_bot_pids(bot_dir_name)
        if pids:
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                    log(f"Killed {bot_dir_name} PID={pid}")
                except: pass
            time.sleep(3)

        subprocess.Popen(
            ["C:\\Python314\\python.exe", "bot.py"],
            cwd=str(BOT_DIRS[bot_dir_name]),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        log(f"{bot_dir_name} restarted with new config")

    all_changes = {}
    for bot_name, bot_dir in BOT_DIRS.items():
        cfg_path = bot_dir / "config.yaml"
        cfg, changes = check_and_switch(bot_name, cfg_path)
        if changes:
            write_config(cfg_path, cfg)
            all_changes[bot_name] = changes

    save_state(state)

    if all_changes:
        for bot_name, changes in all_changes.items():
            log(f"{bot_name} config updated: {', '.join(changes)}")
            restart_bot(bot_name)
    else:
        log("No changes needed")

    log("Auto-Tuner check done")


if __name__ == "__main__":
    run()
