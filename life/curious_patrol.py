r"""
Vector Curious Patrol
----------------------
Extends his native curiosity beyond "cube only" (his firmware's actual
limit - he has no onboard object classification besides faces and his
cube). This drives him in short legs, and after each leg runs
vector_recognize so he reacts to whatever YOLO actually sees - a real
capability gap his stock behavior can't close on its own.

Mike's request: "he pushes things, tries to climb, beeps around" (native
firmware curiosity) -> "expand that" -> give his curiosity real object
awareness during the loop.

Safety: short legs, small turns, stops if picked up/held or on a cliff -
never a long blind drive.

Run:
  venv\Scripts\python.exe curious_patrol.py --legs 4
"""

import argparse
import sys
import time
from pathlib import Path

import anki_vector
from anki_vector.util import degrees, distance_mm, speed_mmps

# Repo was split into core/ life/ tools/ folders 2026-09-20 - this file lives
# in life/ but imports vector_mcp_server (core/), so make it importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
import vector_mcp_server as vms  # noqa: E402


def patrol_leg(robot, leg_num):
    s = robot.status
    if s.is_being_held or s.is_picked_up:
        print(f"  leg {leg_num}: being held, pausing")
        return False

    # short, curious movement - not a blind long drive
    turn = 35 * (1 if leg_num % 2 == 0 else -1)
    robot.behavior.turn_in_place(degrees(turn))
    time.sleep(0.3)
    if robot.status.is_cliff_detected:
        print(f"  leg {leg_num}: cliff ahead, backing off instead")
        robot.behavior.drive_straight(distance_mm(-60), speed_mmps(40))
        return True
    robot.behavior.drive_straight(distance_mm(90), speed_mmps(45))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--legs", type=int, default=4)
    args = ap.parse_args()

    with anki_vector.Robot(enable_face_detection=False) as robot:
        if robot.get_battery_state().is_on_charger_platform:
            robot.behavior.drive_off_charger()
            time.sleep(1)

        for i in range(args.legs):
            ok = patrol_leg(robot, i)
            if not ok:
                break
            time.sleep(0.5)

    # vector_recognize opens its own connection right after we release
    # control - his own native curiosity behavior sometimes grabs control
    # back in that gap (a real, good sign he's autonomous), which can
    # cancel our speak call. Give it a beat to settle.
    time.sleep(1.5)
    result = vms.vector_recognize(speak=True)
    names = sorted({d["object"] for d in result["detections"]}) or ["nothing new"]
    print(f"patrol complete - saw: {', '.join(names)}")


if __name__ == "__main__":
    main()
