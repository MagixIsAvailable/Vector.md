r"""
Vector Animation Tour
-----------------------
Plays through his 476 animation triggers one at a time, for documenting/
recording what each one looks like. Standalone script (not an MCP tool -
a full run takes way too long for a single blocking tool call).

Writes a CSV manifest (index, name, start timestamp) alongside the run so
a recording can be synced up afterward, even if you don't use --speak-names.

Usage:
    venv\Scripts\python.exe vector_animation_tour.py                     # all 476, with spoken names
    venv\Scripts\python.exe vector_animation_tour.py --no-speak-names    # faster, log-only sync
    venv\Scripts\python.exe vector_animation_tour.py --filter ReactTo    # only matching names
    venv\Scripts\python.exe vector_animation_tour.py --start-at 120      # resume from index 120
    venv\Scripts\python.exe vector_animation_tour.py --pause 2.5         # longer gap between clips
    venv\Scripts\python.exe vector_animation_tour.py --list-only         # just print the list, don't touch the robot

A full run at default pacing (~1.5s pause + animation length, spoken names
add ~1-2s each) is a LONG session - budget something like 40-60+ minutes
for all 476. Use --filter for a shorter documentation pass on a category
at a time (e.g. "ReactTo", "Cube", "Blackjack", "Petting").
"""
import argparse
import csv
import sys
import time
from pathlib import Path

# Repo was split into core/ life/ tools/ folders 2026-09-20 - this file lives
# in tools/ but imports vector_mcp_server (core/), so make it importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
import vector_mcp_server as vms  # noqa: E402  (reuse animation list + _connect)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--filter", default="", help="only animations whose name contains this substring")
    ap.add_argument("--pause", type=float, default=1.5, help="seconds between animations")
    ap.add_argument("--start-at", type=int, default=0, help="resume from this index in the (filtered) list")
    ap.add_argument("--speak-names", dest="speak_names", action="store_true", default=True)
    ap.add_argument("--no-speak-names", dest="speak_names", action="store_false")
    ap.add_argument("--list-only", action="store_true", help="print the filtered list and exit, no robot connection")
    args = ap.parse_args()

    fn = vms.vector_list_animations.fn if hasattr(vms.vector_list_animations, "fn") else vms.vector_list_animations
    names = fn()  # opens its own short-lived connection

    if args.filter:
        names = [n for n in names if args.filter.lower() in n.lower()]
    names = names[args.start_at:]

    print(f"{len(names)} animations to play"
          + (f" (filtered by '{args.filter}')" if args.filter else "")
          + f", pause={args.pause}s, speak_names={args.speak_names}")

    if args.list_only:
        for i, n in enumerate(names):
            print(i, n)
        return

    captures = Path("C:/Users/mike/vector-mcp/captures")
    captures.mkdir(exist_ok=True)
    manifest_path = captures / f"anim_tour_{time.strftime('%Y%m%d_%H%M%S')}.csv"

    with vms._connect() as robot, open(manifest_path, "w", newline="", encoding="utf-8") as f:
        # Pre-warm the trigger list ONCE, up front, with room to breathe -
        # ListAnimationTriggers has a 10s gRPC deadline and 476 triggers can
        # be slow to enumerate on a cold connection. Doing this lazily on
        # the first play_animation_trigger() call (the SDK's default
        # behaviour) races against that same call's own timing and can
        # fail outright - confirmed live 2026-08-19.
        print("Pre-warming animation trigger list...")
        try:
            robot.anim.load_animation_trigger_list()
        except Exception as e:
            print(f"  (pre-warm failed, continuing anyway: {e})")

        writer = csv.writer(f)
        writer.writerow(["index", "name", "start_time", "status"])
        for i, name in enumerate(names):
            ts = time.strftime("%H:%M:%S")
            print(f"[{i+1}/{len(names)}] {ts}  {name}")
            status = "ok"
            try:
                if args.speak_names:
                    robot.behavior.say_text(name)
                robot.anim.play_animation_trigger(name)
            except Exception as e:
                print(f"    (failed: {e})")
                status = "FAILED"
            writer.writerow([i, name, ts, status])
            f.flush()
            time.sleep(args.pause)

    print(f"Done. Manifest written to {manifest_path}")


if __name__ == "__main__":
    main()
