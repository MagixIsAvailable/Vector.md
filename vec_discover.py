#!/usr/bin/env python3
"""vec_discover.py — revive Vector's discovery loop on the Pi.

Every run (scheduled cron, 15 min, daytime): control-free camera grab ->
YOLO on the frame -> diff the labels against everything he has ever seen
(sightings.jsonl + the vault object catalog). A label he has NEVER seen
before = a discovery: the photo is saved to captures/, the sighting is
appended to his memory, and he announces it aloud, in character.

Quiet when: Vector is asleep (no frame), or nothing new is in view.
Never hard-fails; every path prints one JSON line for the cron log.

Patterns borrowed from the proven pieces: vec_cam.py's control-free
connect, vector_mcp_server.py's YOLO block, vec_talk.py's guarded
say_text thread.
"""
import json
import sys
import threading
import time
from pathlib import Path

BASE = Path("/home/magix/vector-mcp")
CAPTURES = BASE / "captures"
SIGHTINGS = BASE / "memory" / "sightings.jsonl"
YOLO_WEIGHTS = BASE / "yolo11n.pt"
VAULT_OBJECTS = Path("/home/magix/obsidian-vault/Vector Mind/objects")
TMP = Path("/tmp/vec_discover.jpg")
CONF_MIN = 0.5          # discovery must be a confident call
SAY_TIMEOUT = 25        # seconds; watchdog behavior-contention guard


def grab_frame():
    """Control-free camera grab (never contends with the watchdog).
    Returns a PIL image or None (asleep / no frame within ~10 s)."""
    import anki_vector  # noqa: E402 — vector-mcp-venv

    with anki_vector.Robot(enable_face_detection=False,
                           behavior_control_level=None) as robot:
        robot.camera.init_camera_feed()
        for _ in range(20):  # up to ~10 s for the first frame
            time.sleep(0.5)
            img = robot.camera.latest_image
            if img is not None:
                return img
    return None


def grab_frame_safe():
    """grab_frame behind a hard wall-clock guard. A sleepy Vector can
    wedge the anki_vector connect/cleanup (property not ready, context
    exit hangs) — if the grab hasn't answered in 35 s we treat it as
    'asleep/busy' and move on; the daemon thread dies with the process."""
    import queue

    q = queue.Queue()

    def worker():
        try:
            q.put(grab_frame())
        except Exception:  # noqa: BLE001 — asleep/busy/error == no frame
            q.put(None)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    try:
        return q.get(timeout=35)
    except queue.Empty:
        return None


def detect():
    """YOLO labels + confidences on the frame (conf >= CONF_MIN)."""
    from ultralytics import YOLO  # noqa: E402 — vector-mcp-venv

    res = YOLO(str(YOLO_WEIGHTS))(str(TMP), verbose=False)[0]
    hits = []
    for box in res.boxes:
        conf = float(box.conf)
        if conf >= CONF_MIN:
            hits.append({
                "object": res.names[int(box.cls)],
                "confidence": round(conf, 2),
                "box_xyxy": [round(v) for v in box.xyxy[0].tolist()],
            })
    # dedupe by object + coarse box (models can double-fire)
    seen = set()
    uniq = []
    for d in sorted(hits, key=lambda x: -x["confidence"]):
        b = d["box_xyxy"]
        key = (d["object"], b[0] // 20, b[1] // 20)
        if key not in seen:
            seen.add(key)
            uniq.append(d)
    uniq.sort(key=lambda d: -d["confidence"])
    return uniq


def known_objects():
    """Every object he has ever seen: sightings detections + vault catalog."""
    known = set()
    if SIGHTINGS.exists():
        for line in SIGHTINGS.read_text(errors="replace").splitlines():
            try:
                for d in json.loads(line).get("detections", []):
                    known.add(str(d.get("object", "")).lower())
            except Exception:  # noqa: BLE001
                continue
    if VAULT_OBJECTS.is_dir():
        for p in VAULT_OBJECTS.glob("*.md"):
            known.add(p.stem.lower())
    known.discard("")
    return known


def announce(text):
    """Say the discovery aloud; guarded so we never fight the watchdog."""
    errs = []

    def worker():
        try:
            import anki_vector  # noqa: E402
            with anki_vector.Robot() as robot:
                robot.behavior.say_text(text)
        except Exception as e:  # noqa: BLE001
            errs.append("%s: %s" % (type(e).__name__, str(e)[:120]))

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(SAY_TIMEOUT)
    if t.is_alive():
        return False, "say_text still running after %ds (busy?)" % SAY_TIMEOUT
    if errs:
        return False, errs[0]
    return True, None


def main():
    out = {"ok": False}
    try:
        img = grab_frame_safe()
        if img is None:
            out.update(ok=True, skipped="asleep or no frame (35s guard)")
            print(json.dumps(out))
            return 0
        TMP.parent.mkdir(parents=True, exist_ok=True)
        img.raw_image.save(TMP)
        dets = detect()
        if not dets:
            out.update(ok=True, skipped="nothing detected")
            print(json.dumps(out))
            return 0

        labels = sorted({d["object"] for d in dets})
        known = known_objects()
        new = [l for l in labels if l.lower() not in known]

        if not new:
            out.update(ok=True, seen=labels, new=[])
            print(json.dumps(out))
            return 0

        # --- discovery: save the photo + file the sighting + announce ---
        CAPTURES.mkdir(parents=True, exist_ok=True)
        ts = time.time()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        shot = CAPTURES / ("vector_%s.jpg" % stamp)
        img.raw_image.save(shot)
        record = {
            "t": ts,
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S"),
            "image": str(shot),
            "pose": {"x_mm": None, "y_mm": None, "angle_z_rad": None},
            "detections": dets,
            "discovery": new,  # extension: which labels were first-ever
        }
        with SIGHTINGS.open("a") as f:
            f.write(json.dumps(record) + "\n")

        first = new[0]
        line = ("A %s! I have never laid optics upon a %s before. "
                "The empire records its first sighting." % (first, first))
        spoke, err = announce(line)
        out.update(ok=True, seen=labels, new=new, photo=str(shot),
                   spoke=spoke)
        if err:
            out["announce_error"] = err
        print(json.dumps(out))
        return 0
    except Exception as e:  # noqa: BLE001
        out["error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
        print(json.dumps(out))
        return 1


if __name__ == "__main__":
    sys.exit(main())
