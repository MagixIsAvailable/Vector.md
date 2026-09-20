"""
Charge trend monitor
--------------------
Samples Vector's battery every N minutes and appends to charge_log.csv.
Purpose: determine whether he is ACTUALLY taking charge while docked, as
opposed to what is_charging claims. Voltage trend over time is the ground
truth; a single reading is not (an SDK connect wakes him and sags the pack).

New battery era (2026-08-19, 3.7V 503040 600mAh LiPo): use a FRESH file so
the first cycles of the new cell are easy to analyse:
  --file charge_log_new_battery.csv

Usage: venv\\Scripts\\python.exe charge_monitor.py --file charge_log_new_battery.csv --minutes 150 --every 3
"""

import argparse
import csv
import os
import time

import anki_vector

DEFAULT_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "charge_log.csv")


def sample():
    robot = anki_vector.Robot(behavior_control_level=None)
    robot.connect()
    s = robot.get_battery_state()
    row = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "volts": round(s.battery_volts, 4),
        "level": s.battery_level,
        "charging": s.is_charging,
        "on_charger": s.is_on_charger_platform,
    }
    robot.disconnect()
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--every", type=float, default=3,
                    help="sample interval in MINUTES")
    ap.add_argument("--file", default=DEFAULT_LOG,
                    help="log file (use a fresh one per battery era)")
    args = ap.parse_args()

    new_file = not os.path.exists(args.file)
    end = time.time() + args.minutes * 60
    fields = ["time", "volts", "level", "charging", "on_charger"]

    with open(args.file, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new_file:
            w.writeheader()
        while time.time() < end:
            try:
                row = sample()
                w.writerow(row)
                f.flush()
                print(row, flush=True)
            except Exception as e:
                print(f"sample failed: {e}", flush=True)
            time.sleep(args.every * 60)


if __name__ == "__main__":
    main()
