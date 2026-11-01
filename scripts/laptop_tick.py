"""Run at most once per current IST window, without replaying missed windows."""
import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
STATE = DATA / "laptop-schedule.json"
IST = timezone(timedelta(hours=5, minutes=30))
MIN_GAP = 6 * 3600


def decision(now, state):
    local = now.astimezone(IST)
    if local.hour < 9:
        return None, "Waiting for 09:00 IST"
    slot = f"{local.date()}-{'morning' if local.hour < 19 else 'evening'}"
    if state:
        last_slot = state["slot"]
        last_start = state["started_at"]
        if not isinstance(last_slot, str) or not isinstance(last_start, (int, float)) or not math.isfinite(last_start):
            raise ValueError("Invalid scheduler state")
        if last_slot == slot:
            return None, "Current window already attempted"
        if now.timestamp() - last_start < MIN_GAP:
            return None, "Waiting for six-hour minimum gap (or clock correction)"
    return slot, "Current window is due"


def read_state():
    if not STATE.exists():
        return {}
    state = json.loads(STATE.read_text())
    if not isinstance(state, dict) or not state:
        raise ValueError("Invalid scheduler state; inspect it before resetting")
    return state


def write_state(state):
    temporary = STATE.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(state, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(STATE)
    directory = os.open(STATE.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def online():
    # No credentials or API calls; defer offline attempts to the next tick.
    for host in ("generativelanguage.googleapis.com", "api.telegram.org"):
        try:
            with socket.create_connection((host, 443), timeout=5):
                return True
        except OSError:
            pass
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="Inspect only; no network or pipeline calls")
    args = parser.parse_args()
    DATA.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[RotatingFileHandler(DATA / "laptop-scheduler.log", maxBytes=200_000, backupCount=2)])
    try:
        # Share the portable runner's lock. Inherit it into the child so killing
        # this wrapper alone cannot unlock a still-running pipeline.
        with (DATA / "pipeline.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if args.status:
                    print("Pipeline is already running")
                return 0
            state = read_state()
            now = datetime.now(IST)
            slot, reason = decision(now, state)
            if args.status:
                print(json.dumps({"now_ist": now.isoformat(), "due_slot": slot,
                                  "reason": reason, "last_attempt": state}, indent=2))
                return 0
            if not slot:
                return 0
            if not online():
                logging.info("Offline; retry eligibility at next tick")
                return 0
            # Recalculate in case the network probe crossed a window boundary.
            now = datetime.now(IST)
            slot, reason = decision(now, state)
            if not slot:
                return 0
            if not (ROOT / ".env").is_file() or not (ROOT / "profile.yaml").is_file():
                raise RuntimeError("Missing .env or profile.yaml")
            # Claim BEFORE launching. Reboots/crashes never replay this window.
            state = {"slot": slot, "started_at": now.timestamp(), "status": "started"}
            write_state(state)
            logging.info("Starting %s", slot)
            with (DATA / "laptop-run.log").open("w") as output:
                result = subprocess.run(
                    ["timeout", "--kill-after=30s", "3600", sys.executable, "main.py", "profile.yaml"],
                    cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, pass_fds=(lock.fileno(),),
                )
            state.update(status="finished" if result.returncode == 0 else "failed",
                         exit_code=result.returncode, finished_at=datetime.now(IST).isoformat())
            write_state(state)
            logging.info("Finished %s with exit code %s", slot, result.returncode)
            return result.returncode
    except Exception:
        logging.exception("Laptop scheduler failed; no automatic state reset")
        return 1


if __name__ == "__main__":
    sys.exit(main())
