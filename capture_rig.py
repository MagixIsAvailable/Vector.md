"""
Vector Room-Scan Capture Rig
----------------------------
Drives Vector in a scan pattern while logging camera frames + his SDK pose
(wheel odometry + IMU). Output feeds COLMAP / Postshot / nerfstudio for
Gaussian-splat reconstruction.

Pattern per "station": rotate 360 deg in fixed steps, capturing a frame at
each heading. Then drive forward to the next station and repeat.

Usage (from vector-mcp folder):
  venv\Scripts\python.exe capture_rig.py                # default: 3 stations
  venv\Scripts\python.exe capture_rig.py --stations 5 --step-mm 250

Output:
  D:\3d scans\vector_room_<timestamp>\
    images\frame_00000.jpg ...
    poses.jsonl   (one JSON per frame: file, pose xyz mm, quaternion, angle)

Tips:
  - Put him on the FLOOR, mid-room, decent lighting. Not the desk.
  - His cliff sensors stop him at edges, but clear floor = better scan.
  - More stations + shorter steps = better reconstruction.
"""

import argparse
import json
import time
from pathlib import Path

import anki_vector
from anki_vector.util import degrees, distance_mm, speed_mmps


def capture_frame(robot, out_dir: Path, idx: int, log_f) -> bool:
    img = None
    for _ in range(10):
        img = robot.camera.latest_image
        if img is not None:
            break
        time.sleep(0.3)
    if img is None:
        print(f"  frame {idx}: no image, skipping")
        return False

    fname = f"frame_{idx:05d}.jpg"
    img.raw_image.save(str(out_dir / "images" / fname))

    pose = robot.pose
    rec = {
        "file": f"images/{fname}",
        "t": time.time(),
        "x_mm": pose.position.x,
        "y_mm": pose.position.y,
        "z_mm": pose.position.z,
        "q0": pose.rotation.q0,
        "q1": pose.rotation.q1,
        "q2": pose.rotation.q2,
        "q3": pose.rotation.q3,
        "angle_z_rad": pose.rotation.angle_z.radians,
    }
    log_f.write(json.dumps(rec) + "\n")
    log_f.flush()
    print(f"  frame {idx}: saved ({pose.position.x:.0f}, {pose.position.y:.0f}) mm")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stations", type=int, default=3, help="number of scan positions")
    ap.add_argument("--step-mm", type=float, default=200.0, help="distance between stations")
    ap.add_argument("--rot-steps", type=int, default=16, help="frames per 360-degree spin")
    ap.add_argument("--out", type=str, default="", help="output folder override")
    args = ap.parse_args()

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out) if args.out else Path(r"D:\3d scans") / f"vector_room_{stamp}"
    (out_dir / "images").mkdir(parents=True, exist_ok=True)

    rot_per_step = 360.0 / args.rot_steps
    idx = 0

    print(f"Output: {out_dir}")
    print(f"Plan: {args.stations} stations x {args.rot_steps} frames, "
          f"{args.step_mm}mm between stations\n")

    with anki_vector.Robot(enable_face_detection=False) as robot:
        # Firmware refuses to drive while docked -> leave the charger first.
        if robot.get_battery_state().is_on_charger_platform:
            print("On charger -> driving off first...")
            robot.behavior.drive_off_charger()
            time.sleep(1)

        robot.camera.init_camera_feed()
        robot.behavior.set_head_angle(degrees(5.0))   # look slightly up, not at floor
        robot.behavior.set_lift_height(0.0)           # arm down, out of view
        time.sleep(2)

        # Sanity check: verify turning actually works before burning frames.
        start_angle = robot.pose.rotation.angle_z.degrees
        robot.behavior.turn_in_place(degrees(15))
        time.sleep(1)
        if abs(robot.pose.rotation.angle_z.degrees - start_angle) < 5:
            raise RuntimeError(
                "Turn test failed - he isn't rotating. Is he stuck, held, "
                "or still on the charger?")
        robot.behavior.turn_in_place(degrees(-15))  # undo test turn
        time.sleep(0.5)

        with open(out_dir / "poses.jsonl", "w") as log_f:
            for station in range(args.stations):
                print(f"Station {station + 1}/{args.stations}")
                for _ in range(args.rot_steps):
                    time.sleep(0.6)  # let motion blur settle
                    if capture_frame(robot, out_dir, idx, log_f):
                        idx += 1
                    robot.behavior.turn_in_place(degrees(rot_per_step))
                if station < args.stations - 1:
                    print(f"  driving {args.step_mm}mm to next station...")
                    robot.behavior.drive_straight(
                        distance_mm(args.step_mm), speed_mmps(60)
                    )

        robot.behavior.say_text("Scan complete!")

    print(f"\nDone: {idx} frames -> {out_dir}")
    print("Next: feed the images folder to Postshot or COLMAP.")


if __name__ == "__main__":
    main()
