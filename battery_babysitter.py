"""
Vector Battery Babysitter
-------------------------
Checks Vector's battery (observer connection - no behavior control needed).
If voltage is low and he's not charging, sends him home to his charger.

Exit output is JSON so n8n can branch on it.

Usage: venv\Scripts\python.exe battery_babysitter.py [--threshold 3.6]
"""

import argparse
import json
import sys

import anki_vector


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=3.8,
                    help="voltage below which he gets sent home "
                         "(3.8, not 3.6 - wire-pod's Reborn patch fakes "
                         "battery as 'full' at ~3.68V, see DEVLOG.md)")
    args = ap.parse_args()

    # Observer connection: reads state without stealing behavior control
    robot = anki_vector.Robot(behavior_control_level=None)
    robot.connect()
    try:
        s = robot.get_battery_state()
        state = {
            "volts": round(s.battery_volts, 2),
            "level": s.battery_level,
            "charging": s.is_charging,
            "on_charger": s.is_on_charger_platform,
        }
    finally:
        robot.disconnect()

    # wire-pod reports battery_volts as 0.0 (not a real low reading) while
    # Vector is powered on but off the charger - see vector_life.py's same
    # guard. Without this, needs_home would be true almost every time he's
    # off the charger, regardless of real battery level.
    needs_home = (state["volts"] > 0
                  and state["volts"] < args.threshold
                  and not state["charging"]
                  and not state["on_charger"])
    state["action"] = "none"

    if needs_home:
        # Second connection WITH control, only when actually needed
        try:
            with anki_vector.Robot() as bot:
                bot.behavior.drive_on_charger()
            state["action"] = "sent_home"
        except Exception as e:
            state["action"] = f"failed: {str(e)[:60]}"

    print(json.dumps(state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
