"""
Vector MCP Server
------------------
Exposes an Anki Vector robot (running on wire-pod / Escape Pod firmware)
as a set of MCP tools, so any MCP-compatible LLM client can control him.

Requires:
  - anki_vector SDK configured (~/.anki_vector/sdk_config.ini + cert), already done.
  - Vector powered on, on the same WiFi network as this PC, connected to wire-pod.

Run manually to test:
  python vector_mcp_server.py

Register in an MCP client (e.g. Claude Desktop) by pointing it at this script
with stdio transport (see README.md in this folder).
"""

import anki_vector
from anki_vector.util import degrees, distance_mm, speed_mmps
from mcp.server.mcpserver import MCPServer

server = MCPServer(
    name="vector",
    description="Control an Anki Vector robot running wire-pod.",
)

# ---------------------------------------------------------------------------
# Shared operator logbook: every tool call is logged with WHO did WHAT.
# Operator identity comes from the VECTOR_OPERATOR env var, set by each
# client's launch config (claude / n8n / whatever agents you run / ...).
# Spec (for other agents implementing their own server copy):
#   1. JSONL append -> C:\Users\mike\vector-mcp\memory\actions.jsonl
#      {"ts": epoch, "time_str": "...", "operator": "...", "tool": "...",
#       "args": {...}, "status": "ok"|"error: ...", "secs": float}
#   2. Markdown append -> <vault>\Vector Mind\Logbook.md
#      under a "## YYYY-MM-DD" heading:
#      "- HH:MM:SS **operator** `tool` status (secs)"
#   (WSL paths: /mnt/c/Users/mike/... for both files.)
# ---------------------------------------------------------------------------
import functools as _functools
import json as _json_mod
import os as _os
import threading as _threading
import time as _time_mod
from pathlib import Path as _Path


def _user_base():
    """Windows-native paths (C:/Users/mike), the WSL mirror (/mnt/c/...),
    or a native-Linux host (Raspberry Pi etc, running under its own home
    dir instead of mirroring the Windows desktop)."""
    wsl = _Path("/mnt/c/Users/mike")
    win = _Path("C:/Users/mike")
    if wsl.exists():
        return wsl
    if win.exists():
        return win
    return _Path.home()


def _vault_base():
    """Root of the real Obsidian vault's 'Vector Mind' folder on Windows/WSL,
    or a local staging mirror on a native-Linux host (e.g. the Pi) which has
    no direct filesystem access to the desktop's vault. A separate sync job
    (desktop-side scp pull) reconciles the staging copy into the real vault.
    Override with VECTOR_VAULT_DIR if the staging location ever moves."""
    override = _os.environ.get("VECTOR_VAULT_DIR")
    if override:
        return _Path(override)
    real_vault = _USER / "Documents" / "Obsidian Vault"
    if real_vault.exists():
        return real_vault
    return _USER / "vector-mcp" / "vault_sync"


_USER = _user_base()
_VAULT = _vault_base()
_ACTIONS_FILE = _USER / "vector-mcp" / "memory" / "actions.jsonl"
_LOGBOOK_FILE = _VAULT / "Vector Mind" / "Logbook.md"


def _log_action(tool_name, args, status, secs):
    try:
        from pathlib import Path
        operator = _os.environ.get("VECTOR_OPERATOR", "unknown")
        now = _time_mod.time()
        rec = {
            "ts": now,
            "time_str": _time_mod.strftime("%Y-%m-%d %H:%M:%S"),
            "operator": operator,
            "tool": tool_name,
            "args": {k: str(v)[:120] for k, v in (args or {}).items()},
            "status": status,
            "secs": round(secs, 2),
        }
        af = Path(_ACTIONS_FILE)
        af.parent.mkdir(parents=True, exist_ok=True)
        with open(af, "a", encoding="utf-8") as f:
            f.write(_json_mod.dumps(rec) + "\n")

        lb = Path(_LOGBOOK_FILE)
        lb.parent.mkdir(parents=True, exist_ok=True)
        day_header = "## " + _time_mod.strftime("%Y-%m-%d")
        existing = lb.read_text(encoding="utf-8") if lb.exists() else (
            "---\ntags: [vector-mind, logbook]\n---\n# Vector Logbook\n\n"
            "Who did what with Vector, when. Operators: claude, n8n, ...\n")
        if day_header not in existing:
            existing += f"\n{day_header}\n\n"
        arg_str = ", ".join(f"{k}={v}" for k, v in rec["args"].items())
        line = (f"- {_time_mod.strftime('%H:%M:%S')} **{operator}** "
                f"`{tool_name}({arg_str})` — {status} ({rec['secs']}s)\n")
        lb.write_text(existing + line, encoding="utf-8")
    except Exception:
        pass  # logging must never break the actual command


def tool(func):
    """Replacement for @server.tool() that also writes the operator logbook."""
    @_functools.wraps(func)
    def wrapper(*args, **kwargs):
        t0 = _time_mod.time()
        status = "ok"
        try:
            return func(*args, **kwargs)
        except Exception as e:
            status = f"error: {str(e)[:80]}"
            # Let Vector visibly react to his own failures - but never let the
            # signal itself raise, and never recurse into vector_signal.
            if func.__name__ not in ("vector_signal", "vector_human_log"):
                try:
                    with _connect() as _r:
                        _swear(_r)          # English oath - Mike's request
                        _r.anim.play_animation_trigger(SIGNAL_ANIMS["error"])
                except Exception:
                    pass
            raise
        finally:
            # vector_human_log writes its own (mike) entry - don't double-log
            # it as the agent that relayed it.
            if func.__name__ != "vector_human_log":
                _log_action(func.__name__, kwargs, status, _time_mod.time() - t0)
    return server.tool()(wrapper)


def _connect(status_only: bool = False):
    """Create a short-lived connection to Vector for a single command.

    status_only=True connects WITHOUT requesting behavior control — status
    reads (battery, etc.) then work even while another client holds control
    (e.g. Windows brain_proxy), where a normal connect would time out ~20s.
    """
    if status_only:
        return anki_vector.Robot(enable_face_detection=False, behavior_control_level=None)
    return anki_vector.Robot(enable_face_detection=False)


@tool
def vector_say(text: str) -> str:
    """Make Vector speak the given text out loud.

    Args:
        text: The sentence for Vector to say.
    """
    with _connect() as robot:
        robot.behavior.say_text(text)
    try:  # TV dashboard "last said" feed - best effort
        import sys as _s
        _s.path.insert(0, str(_USER / "vector-mcp"))
        import vector_life as _vl
        _vl.log_speech(text, source="agent")
    except Exception:
        pass
    return f"Vector said: {text}"


@tool
def _report_stuck(robot, requested_mm, actual_mm, context="driving"):
    """Physically-stuck detection (wheels commanded to move, but position
    barely changed) - NOT a native SDK flag, inferred from pose delta.
    Reuses the same event -> mood -> speech pipeline as the danger
    watchdog: updates shared mood, speaks a generated reaction, logs it.
    """
    import sys as _sys
    _sys.path.insert(0, str(_USER / "vector-mcp"))
    import vector_life as _vl
    mood = _vl.load_json(_vl.MOOD_FILE, _vl.DEFAULT_MOOD)
    mood = _vl.mood_update(mood, -0.15, +0.10)  # frustrated, alert
    _vl.save_json(_vl.MOOD_FILE, mood)
    try:
        line = _vl.llm_line(
            _vl.mood_system_prompt(mood, "You just tried to move but got "
                                   "physically stuck/blocked - wheels "
                                   "moving, not actually going anywhere. "
                                   "Say ONE short reaction line. Under 15 "
                                   "words. Plain ASCII."),
            f"Context: {context}. Requested {requested_mm:.0f}mm, only "
            f"moved {actual_mm:.0f}mm.", max_tokens=300)
        if line:
            robot.behavior.say_text(line)
            _vl.log("🧱", f"[stuck] {context}: requested {requested_mm:.0f}mm, "
                    f"moved {actual_mm:.0f}mm - \"{line}\"")
    except Exception as e:
        _vl.log("⚠️", f"[stuck] reaction failed ({context}): {e}")


@tool
def vector_drive(distance_mm_amount: float, speed_mmps_amount: float = 50.0) -> str:
    """Drive Vector straight forward or backward.

    Args:
        distance_mm_amount: Distance to drive in millimeters. Positive = forward, negative = backward.
        speed_mmps_amount: Speed in millimeters per second (default 50).
    """
    undocked = False
    stuck = False
    actual = requested = 0.0
    with _connect() as robot:
        # drive_straight silently no-ops while Vector is docked on the
        # charger - undock first so "drive forward" always moves him.
        if robot.status.is_on_charger and distance_mm_amount > 0:
            robot.behavior.drive_off_charger()
            undocked = True
        p0 = robot.pose.position
        robot.behavior.drive_straight(
            distance_mm(distance_mm_amount), speed_mmps(speed_mmps_amount)
        )
        p1 = robot.pose.position
        requested = abs(distance_mm_amount)
        actual = ((p1.x - p0.x) ** 2 + (p1.y - p0.y) ** 2) ** 0.5
        # Physically-stuck heuristic: asked to move a meaningful distance,
        # actually moved less than 40% of it. No native "stuck" flag exists
        # (checked the SDK status list) - this is inferred from position.
        if requested >= 20 and actual < requested * 0.4:
            stuck = True
            try:
                _report_stuck(robot, requested, actual, context="vector_drive")
            except Exception:
                pass  # never let stuck-reporting break the tool call itself
    if stuck:
        return (f"Vector may be stuck: requested {distance_mm_amount}mm, "
                f"only moved {actual:.0f}mm.")
    if undocked:
        return (f"Vector undocked from the charger, then drove "
                f"{distance_mm_amount}mm at {speed_mmps_amount}mm/s")
    return f"Vector drove {distance_mm_amount}mm at {speed_mmps_amount}mm/s"


@tool
def vector_turn(degrees_amount: float) -> str:
    """Turn Vector in place.

    Args:
        degrees_amount: Degrees to turn. Positive = counter-clockwise (left), negative = clockwise (right).
    """
    # wirepod-fork guard (2026-09-03): turn_in_place can hang INDEFINITELY
    # without returning (documented fork landmine) - run it in a daemon
    # thread with a 20s join so a wedged SDK call can never block the MCP
    # server for minutes. On timeout the robot MAY still have turned; the
    # caller should verify pose rather than assume.
    outcome = {"done": False, "error": ""}

    def _do_turn():
        try:
            with _connect() as robot:
                robot.behavior.turn_in_place(degrees(degrees_amount))
            outcome["done"] = True
        except Exception as e:  # noqa: BLE001 - report, never hang
            outcome["error"] = str(e)

    t = _threading.Thread(target=_do_turn, daemon=True)
    t.start()
    t.join(timeout=20)
    if t.is_alive():
        return (f"vector_turn({degrees_amount}) did not return within 20s "
                f"(known wirepod-fork hang) - robot may or may not have turned")
    if outcome["error"]:
        return f"Vector turn failed: {outcome['error']}"
    return f"Vector turned {degrees_amount} degrees"


@tool
def vector_play_animation(animation_name: str) -> str:
    """Play a specific named animation/emotion on Vector.

    Args:
        animation_name: Exact animation trigger name, e.g. 'GreetAfterLongTime',
            'FistBumpRequestOnce', 'CodeLabHappy'. Use vector_list_animations
            to see valid names first.
    """
    with _connect() as robot:
        robot.anim.play_animation_trigger(animation_name)
    return f"Vector played animation: {animation_name}"


@tool
def vector_list_animations() -> list[str]:
    """List all animation trigger names Vector currently knows about."""
    with _connect() as robot:
        anim_names = robot.anim.anim_trigger_list
    return sorted(a.name if not isinstance(a, str) else a for a in anim_names)


@tool
def vector_see(save_path: str = "") -> dict:
    """Capture a photo through Vector's camera and save it as a JPG.

    Returns the saved file path and image size. Read/view the saved file
    to see what Vector is currently looking at.

    Args:
        save_path: Optional absolute path for the JPG. Defaults to a
            timestamped file in C:/Users/mike/vector-mcp/captures/.
    """
    import time as _time
    from pathlib import Path

    if not save_path:
        captures = _USER / "vector-mcp" / "captures"
        captures.mkdir(exist_ok=True)
        save_path = str(captures / _time.strftime("vector_%Y%m%d_%H%M%S.jpg"))

    with _connect() as robot:
        robot.camera.init_camera_feed()
        img = None
        for _ in range(20):
            _time.sleep(0.5)
            img = robot.camera.latest_image
            if img is not None:
                break
        if img is None:
            raise RuntimeError("Camera feed produced no image within 10 seconds.")
        img.raw_image.save(save_path)
        width, height = img.raw_image.size

    return {"path": save_path, "width": width, "height": height}


@tool
def vector_panorama(cols: int = 3, rows: int = 2, sweep_degrees: float = 70.0) -> dict:
    """Capture a wide-view grid panorama: turns + tilts his head through a
    cols x rows grid of positions, taking a high-res (1280x720) still at
    each, then composites them into one montage image (simple grid layout,
    not feature-matched/blended stitching - Vector's odometry/camera isn't
    precise enough for seamless stitching, this is a mosaic of views).

    Returns after turning back to roughly his starting heading.

    Args:
        cols: horizontal positions (turns). Default 3.
        rows: vertical positions (head tilts). Default 2.
        sweep_degrees: total horizontal sweep across all columns, in
            degrees. Default 70 (his camera's horizontal FOV is 90, so this
            gives slight overlap between adjacent columns).
    """
    from PIL import Image

    if cols < 1 or rows < 1:
        raise ValueError("cols and rows must each be >= 1")

    if rows == 1:
        head_angles = [10.0]
    else:
        top, bottom = 40.0, -15.0  # within MIN/MAX_HEAD_ANGLE (-22..45)
        head_angles = [top + (bottom - top) * i / (rows - 1) for i in range(rows)]

    step = sweep_degrees / max(cols - 1, 1)
    start_turn = -sweep_degrees / 2

    shots = {}
    with _connect() as robot:
        robot.behavior.turn_in_place(degrees(start_turn))
        for c in range(cols):
            for r in range(rows):
                robot.behavior.set_head_angle(degrees(head_angles[r]))
                _time_mod.sleep(0.3)
                img = robot.camera.capture_single_image(enable_high_resolution=True)
                if img:
                    shots[(c, r)] = img.raw_image
            if c < cols - 1:
                robot.behavior.turn_in_place(degrees(step))
        # return to roughly where he started
        robot.behavior.turn_in_place(degrees(-(start_turn + step * (cols - 1))))
        robot.behavior.set_head_angle(degrees(0.0))

    if not shots:
        raise RuntimeError("No images captured for panorama.")

    w, h = next(iter(shots.values())).size
    montage = Image.new("RGB", (w * cols, h * rows))
    for (c, r), im in shots.items():
        montage.paste(im, (c * w, r * h))

    captures = _USER / "vector-mcp" / "captures"
    captures.mkdir(exist_ok=True)
    out_path = captures / f"panorama_{_time_mod.strftime('%Y%m%d_%H%M%S')}.jpg"
    montage.save(out_path, quality=90)

    return {
        "path": str(out_path), "cols": cols, "rows": rows,
        "tile_size": [w, h], "montage_size": [w * cols, h * rows],
        "shots_captured": len(shots), "shots_expected": cols * rows,
    }


_MEMORY_FILE = _USER / "vector-mcp" / "memory" / "sightings.jsonl"
_MODEL_CACHE = {}


@tool
def vector_recognize(speak: bool = True) -> dict:
    """Capture a photo through Vector's camera, run YOLO object detection on it,
    and record the sighting (objects + Vector's pose + image path) in his
    persistent visual memory.

    Returns detected objects with confidences, plus the saved image path.

    Args:
        speak: If True (default), Vector says out loud what he just learned -
            naming new things the first time he ever sees them, otherwise
            naming what he recognises. Set False for silent background scans.
    """
    import json as _json
    import time as _time
    from pathlib import Path

    captures = _USER / "vector-mcp" / "captures"
    captures.mkdir(exist_ok=True)
    img_path = str(captures / _time.strftime("vector_%Y%m%d_%H%M%S.jpg"))

    with _connect() as robot:
        robot.camera.init_camera_feed()
        img = None
        for _ in range(20):
            _time.sleep(0.5)
            img = robot.camera.latest_image
            if img is not None:
                break
        if img is None:
            raise RuntimeError("Camera produced no image within 10 seconds.")
        img.raw_image.save(img_path)
        pose = robot.pose
        pose_info = {
            "x_mm": pose.position.x,
            "y_mm": pose.position.y,
            "angle_z_rad": pose.rotation.angle_z.radians,
        }

    # Stock COCO model + optional cube model (fine-tuned on Vector's own
    # camera). Cube weights: VECTOR_CUBE_MODEL env or the standard path.
    cube_weights = _os.environ.get(
        "VECTOR_CUBE_MODEL",
        str(_USER / "vector-mcp" / "cube_dataset" / "runs" / "cube" / "weights" / "best.pt"),
    )
    model_paths = ["yolo11n.pt"]
    if _Path(cube_weights).exists():
        model_paths.append(cube_weights)

    detections = []
    for mp in model_paths:
        if mp not in _MODEL_CACHE:
            from ultralytics import YOLO
            _MODEL_CACHE[mp] = YOLO(mp)
        results = _MODEL_CACHE[mp](img_path, verbose=False)[0]
        for box in results.boxes:
            detections.append({
                "object": results.names[int(box.cls)],
                "confidence": round(float(box.conf), 2),
                "box_xyxy": [round(v) for v in box.xyxy[0].tolist()],
            })
    # dedupe by object + coarse box position (models may double-fire)
    seen = set()
    uniq = []
    for d in sorted(detections, key=lambda x: -x["confidence"]):
        b = d["box_xyxy"]
        key = (d["object"], b[0] // 20, b[1] // 20)
        if key not in seen:
            seen.add(key)
            uniq.append(d)
    detections = uniq
    detections.sort(key=lambda d: -d["confidence"])

    record = {
        "t": _time.time(),
        "time_str": _time.strftime("%Y-%m-%d %H:%M:%S"),
        "image": img_path,
        "pose": pose_info,
        "detections": detections,
    }
    mem = Path(_MEMORY_FILE)
    mem.parent.mkdir(exist_ok=True)
    with open(mem, "a") as f:
        f.write(_json.dumps(record) + "\n")

    # Say out loud what he just learned. Brand-new objects (never seen in his
    # whole memory before) get announced as discoveries.
    if speak and detections:
        try:
            names = []
            for d in detections:
                if d["object"] not in names:
                    names.append(d["object"])
            known = set()
            for line in open(mem, encoding="utf-8"):
                if line.strip():
                    old = _json_mod.loads(line)
                    if old["t"] < record["t"]:
                        known.update(x["object"] for x in old["detections"])
            brand_new = [n for n in names if n not in known]

            if brand_new:
                phrase = ("New thing! " if len(brand_new) == 1 else "New things! ")
                phrase += " and ".join(brand_new[:3]) + ". Remembering that."
            else:
                phrase = "I see " + ", ".join(names[:3]) + "."
            with _connect() as robot:
                robot.behavior.say_text(phrase)
        except Exception:
            pass  # narration must never break the memory write

    # auto-sync his Obsidian mind after every new memory
    try:
        vector_mind_sync()
    except Exception:
        pass  # a failed sync should never lose the sighting itself

    return {"image": img_path, "detections": detections, "pose": pose_info}


@tool
def vector_recall(object_name: str = "", limit: int = 10) -> list:
    """Query Vector's visual memory of things he has seen.

    Args:
        object_name: Filter by object (e.g. 'person', 'cup', 'keyboard').
            Empty = return all recent sightings.
        limit: Max records to return, newest first.
    """
    import json as _json
    from pathlib import Path

    mem = Path(_MEMORY_FILE)
    if not mem.exists():
        return []

    records = [_json.loads(line) for line in open(mem) if line.strip()]
    if object_name:
        records = [
            r for r in records
            if any(d["object"] == object_name.lower() for d in r["detections"])
        ]
    return records[-limit:][::-1]


_VAULT_MIND = _VAULT / "Vector Mind"


@tool
def vector_mind_sync() -> dict:
    """Sync Vector's visual memory (sightings.jsonl) into his Obsidian
    'Vector Mind' folder: one note per object class, cross-linked by
    co-occurrence, with embedded snapshot images. The Obsidian graph view
    of this folder is Vector's association web.
    """
    import json as _json
    import shutil
    import time as _time
    from pathlib import Path

    mind = Path(_VAULT_MIND)
    objects_dir = mind / "objects"
    snaps_dir = mind / "snapshots"
    objects_dir.mkdir(parents=True, exist_ok=True)
    snaps_dir.mkdir(parents=True, exist_ok=True)

    def _robust_read(p):
        """Read a text file whether it was written as UTF-8 or (Windows
        locale) cp1252 - mixed encodings happened before this server forced
        UTF-8 everywhere."""
        b = p.read_bytes()
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return b.decode("cp1252", errors="replace")

    state_file = mind / ".sync_state.json"
    last_t = 0.0
    if state_file.exists():
        last_t = _json.loads(_robust_read(state_file)).get("last_t", 0.0)

    mem = Path(_MEMORY_FILE)
    if not mem.exists():
        return {"synced": 0, "note": "no memories yet"}

    records = [_json.loads(l) for l in open(mem, encoding="utf-8") if l.strip()]
    new_records = [r for r in records if r["t"] > last_t]

    synced = 0
    for rec in new_records:
        names = sorted({d["object"] for d in rec["detections"]})
        if not names:
            continue

        # copy snapshot into vault
        src = Path(rec["image"])
        snap_name = src.name
        if src.exists():
            shutil.copy2(src, snaps_dir / snap_name)

        for name in names:
            note = objects_dir / f"{name}.md"
            others = [n for n in names if n != name]
            links = " ".join(f"[[{n}]]" for n in others) if others else "*(alone)*"
            conf = max(d["confidence"] for d in rec["detections"]
                       if d["object"] == name)
            entry = (f"- {rec['time_str']} — conf {conf} — with {links}\n"
                     f"  ![[Vector Mind/snapshots/{snap_name}]]\n")
            if not note.exists():
                note.write_text(
                    f"---\ntags: [vector-mind]\n---\n"
                    f"# {name}\n\nSeen by [[Vector]].\n\n## Sightings\n\n" + entry,
                    encoding="utf-8")
            else:
                note.write_text(_robust_read(note) + entry, encoding="utf-8")
        synced += 1

    # rebuild hub note
    object_notes = sorted(objects_dir.glob("*.md"))
    hub_lines = [
        "---\ntags: [vector-mind]\n---\n",
        "# Vector\n",
        "\nMy name is Vector. This is what I know about the world.\n",
        f"\nLast sync: {_time.strftime('%Y-%m-%d %H:%M:%S')}\n",
        f"Total memories: {len(records)}\n",
        "\n## Things I have seen\n\n",
    ]
    for n in object_notes:
        hub_lines.append(f"- [[{n.stem}]]\n")
    (mind / "Vector.md").write_text("".join(hub_lines), encoding="utf-8")

    state_file.write_text(_json.dumps({"last_t": records[-1]["t"]}),
                          encoding="utf-8")
    return {"synced": synced, "total_memories": len(records),
            "objects_known": [n.stem for n in object_notes]}


# --- State signals -------------------------------------------------------
# Vector shows WHO is driving him (eye colour) and WHAT phase he is in
# (animation). Mike asked for this so the robot is never a silent black box.

OPERATOR_EYES = {          # hue 0..1 - add one entry per agent/human you run
    "claude": 0.60,        # blue
    "n8n": 0.08,           # orange
    "mike": 0.33,          # green
    "unknown": 0.50,       # teal (Vector's default-ish)
}

# When something goes wrong, Vector swears. In English. Mike's rules.
# Big vocabulary so he never repeats himself; plain ASCII only (his TTS
# mangles accents, so the French had to go). Theatrical classics with a
# British edge - he is a family robot with a temper.
ENGLISH_OATHS = [
    # Theatrical classics
    "Good grief!", "Great Scott!", "By Jove!", "Blimey!", "Crikey!",
    "Cor blimey!", "Stone the crows!", "Dash it all!", "Drat!",
    "Rats!", "Fiddlesticks!", "Bother!", "Blast it!", "Confound it!",
    "Curses!", "Suffering succotash!", "Great googly moogly!",
    "Cheese and crackers!", "Holy mackerel!", "Holy smokes!",
    "Jumping jehoshaphat!", "Judas priest!", "Jeepers creepers!",
    "For Pete's sake!", "For crying out loud!", "What the dickens!",
    "Mother of pearl!", "Great balls of fire!", "Thunder and lightning!",
    "Shiver me timbers!", "Land sakes!", "Mercy me!", "Good heavens!",
    "Son of a biscuit!", "Dagnabbit!", "Jumpin' jiminy!",
    "What in tarnation!", "Consarn it!", "Gosh darn it!", "Gadzooks!",
    "Zounds!", "By thunder!", "Great heavens to betsy!", "Oh bother!",
    # British and mildly profane
    "Bloody hell!", "Bloody Nora!", "Blimey O'Reilly!", "Bugger it!",
    "Damn it!", "Damn it all!", "Oh damn!", "What the hell!",
    "Hell's bells!", "What the bloody hell!", "Oh crap!", "Well, shoot!",
    "Shoot!", "Aw nuts!", "Darn it!", "For heaven's sake!",
    # Annoyed robot
    "Not again!", "Oh no, no, no!", "Why me?", "Are you kidding me?",
    "You have got to be kidding me!", "Seriously?!", "Oh come on!",
    "I can't believe this!", "Oh for the love of...", "This is fine.",
]


def _swear(robot=None):
    """Say a random English oath. Never raises."""
    import random
    oath = random.choice(ENGLISH_OATHS)
    try:
        if robot is not None:
            robot.behavior.say_text(oath)
        else:
            with _connect() as r:
                r.behavior.say_text(oath)
    except Exception:
        pass
    return oath


SIGNAL_ANIMS = {
    "thinking":  "KnowledgeGraphSearching",
    "listening": "VC_ListeningLoop",
    "looking":   "ExploringQuickScan",
    "success":   "FistBumpSuccess",
    "error":     "KnowledgeGraphSearchingFail",
    "greet":     "GreetAfterLongTime",
    "sleep":     "GoToSleepGetIn",
    "wake":      "ConnectWakeUp",
    "alert":     "ReactToGreeting",
    "scared":    "ReactToCliff",
}


@tool
def vector_swear() -> str:
    """Make Vector swear in English. Use when something goes wrong, he is
    startled, or the moment simply calls for it."""
    return f"Vector said: {_swear()}"


@tool
def vector_list_hums() -> str:
    """List the songs Vector can hum (his humming library)."""
    hums = _USER / "vector-mcp" / "hums"
    if not hums.exists() or not list(hums.glob("*.wav")):
        return "no hums yet - run make_hums.py in the vector-mcp folder"
    return ", ".join(sorted(p.stem for p in hums.glob("*.wav")))


@tool
def vector_hum(song: str = "twinkle", volume: int = 50) -> str:
    """Make Vector hum a song through his speaker. He can't sing lyrics, so
    this plays a synthesized 'hum' melody (16kHz/16-bit WAV from the hums/
    folder). See vector_list_hums for the library.

    Args:
        song: which melody to hum (default 'twinkle').
        volume: playback level 0-100 (default 50).
    """
    hums = _USER / "vector-mcp" / "hums"
    wav = hums / f"{song}.wav"
    if not wav.exists():
        avail = ", ".join(sorted(p.stem for p in hums.glob("*.wav")))
        raise ValueError(f"Unknown hum '{song}'. Available: {avail}")
    with _connect() as robot:
        robot.audio.stream_wav_file(str(wav), volume=volume)
    return f"Vector hummed {song} at volume {volume}"


@tool
def vector_learn_midi(midi_path: str, name: str, max_notes: int = 200) -> str:
    """Teach Vector a new hum from a standard MIDI (.mid) file, instead of
    hand-writing a melody. Extracts the busiest track as the lead line
    (monophonic reduction: highest note wins when notes overlap), renders
    it through the same hummed-tone synthesis as the built-in library, and
    saves it into hums/ so it's immediately playable via vector_hum.

    Mike's idea 2026-08-20 - see midi_to_hum.py for the extraction logic.

    Args:
        midi_path: path to a .mid file on this machine.
        name: what to call the new hum (saved as hums/<name>.wav,
            playable afterward with vector_hum(song=name)).
        max_notes: safety cap on melody length (default 200) - MIDI files
            can be long/complex; this keeps accidental imports short.
    """
    from midi_to_hum import extract_melody
    from make_hums import render_melody, write_wav, SR

    path = _Path(midi_path)
    if not path.exists():
        raise ValueError(f"MIDI file not found: {midi_path}")

    bpm, melody = extract_melody(str(path), max_notes=max_notes)
    samples = render_melody(bpm, melody)
    hums = _USER / "vector-mcp" / "hums"
    wav_path = hums / f"{name}.wav"
    write_wav(wav_path, samples)
    dur = len(samples) / SR
    return (f"Learned '{name}' from {path.name}: {len(melody)} notes/rests "
            f"at {bpm:.0f} BPM, {dur:.1f}s. Play it with vector_hum(song="
            f"'{name}').")


_PIPER_VOICE_CACHE = {}
PIPER_VOICES_DIR = _USER / "vector-mcp" / "piper_voices"
DEFAULT_PIPER_VOICE = "en_US-amy-medium"


def _piper_voice(name: str):
    """Load (and cache) a Piper voice model by name, e.g. 'en_US-amy-medium'."""
    if name not in _PIPER_VOICE_CACHE:
        from piper import PiperVoice
        onnx = PIPER_VOICES_DIR / f"{name}.onnx"
        cfg = PIPER_VOICES_DIR / f"{name}.onnx.json"
        if not onnx.exists():
            avail = ", ".join(sorted(p.stem for p in PIPER_VOICES_DIR.glob("*.onnx"))) or "none downloaded"
            raise ValueError(f"Unknown Piper voice '{name}'. Available: {avail}")
        _PIPER_VOICE_CACHE[name] = PiperVoice.load(str(onnx), config_path=str(cfg))
    return _PIPER_VOICE_CACHE[name]


def _piper_synthesize_16k(text: str, voice: str):
    """Synthesize text with Piper and resample to Vector's speaker format
    (16000 Hz / 16-bit / mono). Returns (frames_bytes, sampwidth, rate)."""
    import io
    import wave
    import audioop

    piper_voice = _piper_voice(voice)

    raw_buf = io.BytesIO()
    with wave.open(raw_buf, "wb") as wf:
        piper_voice.synthesize_wav(text, wf)
    raw_buf.seek(0)

    with wave.open(raw_buf, "rb") as rf:
        n_channels = rf.getnchannels()
        sampwidth = rf.getsampwidth()
        framerate = rf.getframerate()
        frames = rf.readframes(rf.getnframes())

    if n_channels != 1:
        frames = audioop.tomono(frames, sampwidth, 0.5, 0.5)
    target_rate = 16000
    frames, _ = audioop.ratecv(frames, sampwidth, 1, framerate, target_rate, None)
    return frames, sampwidth, target_rate


def _write_wav(frames: bytes, sampwidth: int, rate: int, tag: str) -> str:
    import wave

    captures = _USER / "vector-mcp" / "captures"
    captures.mkdir(exist_ok=True)
    out_path = captures / f"{tag}_{_time_mod.strftime('%Y%m%d_%H%M%S')}.wav"
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(sampwidth)
        wf.setframerate(rate)
        wf.writeframes(frames)
    return str(out_path)


def _play_wav_on(robot, wav_path: str, volume: int) -> None:
    """Play a wav file on an ALREADY-CONNECTED robot object (does not open
    its own connection - use this when the caller holds the connection,
    e.g. vector_life.py's long-lived session). Tools that don't already
    hold a connection should use _write_wav_and_play instead."""
    robot.audio.stream_wav_file(wav_path, volume=volume)


def _write_wav_and_play(frames: bytes, sampwidth: int, rate: int, volume: int, tag: str) -> str:
    out_path = _write_wav(frames, sampwidth, rate, tag)
    with _connect() as robot:
        _play_wav_on(robot, out_path, volume)
    return out_path


DEFAULT_ROBOT_VOICE = "en_US-danny-low"


def _robotize(frames: bytes, sampwidth: int, rate: int,
              ring_hz: float = 45.0, crush_bits: int = 6) -> bytes:
    """Apply a ring-modulation + bit-crush robotic effect to 16-bit PCM audio.
    Shared by vector_say_robot, vector_life.py's say(), and brain_proxy.py's
    live-conversation speech - keep all three callers in sync with this."""
    import numpy as np

    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32)

    # Ring modulation: multiply by a carrier wave for a metallic/robotic buzz.
    t = np.arange(len(samples)) / rate
    carrier = np.sin(2 * np.pi * ring_hz * t)
    modulated = samples * (0.5 + 0.5 * np.abs(carrier))

    # Bit-depth crush for a gritty, synthetic edge.
    levels = 2 ** crush_bits
    step = 65536 / levels
    crushed = np.round(modulated / step) * step

    # Normalize to avoid clipping, cast back to int16.
    peak = np.max(np.abs(crushed)) or 1.0
    crushed = crushed / peak * 32000
    return crushed.astype(np.int16).tobytes()


def _robot_voice_frames(text: str, voice: str = DEFAULT_ROBOT_VOICE,
                         ring_hz: float = 45.0, crush_bits: int = 6):
    """Synthesize + robotize text in one step. Returns (frames, sampwidth, rate)."""
    frames, sampwidth, rate = _piper_synthesize_16k(text, voice)
    frames = _robotize(frames, sampwidth, rate, ring_hz, crush_bits)
    return frames, sampwidth, rate


@tool
def vector_say_piper(text: str, voice: str = DEFAULT_PIPER_VOICE, volume: int = 60) -> str:
    """Make Vector speak arbitrary text using a local Piper TTS voice, played
    through his speaker as raw audio (robot.audio.stream_wav_file) - this
    bypasses his onboard say_text firmware entirely, so it works even when
    say_text is unavailable/flaky, and gives a non-robotic voice option.

    Vector's speaker only accepts 8000-16025 Hz / 16-bit / mono WAV, so
    Piper's native output (22050 Hz) is resampled down to 16000 Hz first.

    Args:
        text: what Vector should say.
        voice: Piper voice name (files must exist in vector-mcp/piper_voices/
            as <voice>.onnx + <voice>.onnx.json). Default 'en_US-amy-medium'.
        volume: playback level 0-100 (default 60).
    """
    frames, sampwidth, rate = _piper_synthesize_16k(text, voice)
    _write_wav_and_play(frames, sampwidth, rate, volume, "piper")
    return f"Vector said (Piper/{voice}): {text}"


@tool
def vector_say_robot(text: str, voice: str = DEFAULT_ROBOT_VOICE, volume: int = 60,
                      ring_hz: float = 45.0, crush_bits: int = 6) -> str:
    """Make Vector speak using a local Piper voice run through a robotic
    audio effect (ring modulation + bit-depth crush), so it sounds like a
    robot instead of a person - while still being a small local TTS model,
    not his flaky onboard say_text firmware.

    Args:
        text: what Vector should say.
        voice: Piper voice to use as the base (default 'en_US-danny-low' -
            a small/fast voice; the robot effect matters more than the
            base voice here).
        volume: playback level 0-100 (default 60).
        ring_hz: ring-modulation carrier frequency in Hz. Lower (~20-30) =
            buzzier/deeper, higher (~60-80) = more metallic/thin.
        crush_bits: bit-depth to quantize down to (default 6 of 16) for a
            gritty, synthetic edge. Higher = cleaner, lower = harsher.
    """
    frames, sampwidth, rate = _robot_voice_frames(text, voice, ring_hz, crush_bits)
    _write_wav_and_play(frames, sampwidth, rate, volume, "robot")
    return f"Vector said (robot voice/{voice}, ring={ring_hz}Hz, crush={crush_bits}bit): {text}"


MOODS = {
    "happy":   {"valence": 0.85, "energy": 0.80},
    "curious": {"valence": 0.70, "energy": 0.65},
    "calm":    {"valence": 0.60, "energy": 0.40},
    "tired":   {"valence": 0.40, "energy": 0.20},
    "grumpy":  {"valence": 0.25, "energy": 0.40},
}


def _mood_file():
    return _USER / "vector-mcp" / "memory" / "mood.json"


@tool
def vector_mood(mood: str = "") -> str:
    """Get Vector's current mood, or set it to a named mood
    (happy / curious / calm / tired / grumpy). The Life Engine
    (vector_life.py) reads the same file, so this syncs agents and autonomy.
    """
    f = _mood_file()
    if mood:
        if mood.lower() not in MOODS:
            return f"unknown mood '{mood}' - use: {', '.join(MOODS)}"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(_json_mod.dumps(MOODS[mood.lower()]), encoding="utf-8")
        return f"Vector's mood set to {mood.lower()}: {MOODS[mood.lower()]}"
    if f.exists():
        return f.read_text(encoding="utf-8")
    return '{"valence": 0.55, "energy": 0.5}'


@tool
def vector_dance(steps: int = 4) -> str:
    """Make Vector do a short dance: a sequence of lively animations.

    Args:
        steps: how many animation moves (default 4, max 10).
    """
    moves = ["ExploringQuickScan", "FistBumpSuccess", "ReactToGreeting",
             "VC_ListeningLoop", "GreetAfterLongTime", "KnowledgeGraphSearching"]
    steps = max(1, min(int(steps), 10))
    with _connect() as robot:
        for i in range(steps):
            robot.anim.play_animation_trigger(moves[i % len(moves)])
            _time_mod.sleep(0.6)
    return f"Vector danced {steps} moves"


@tool
def vector_dance_to_beat(seconds: int = 8, beats: int = 12) -> str:
    """Listen for a beat via Vector's mic, then dance timed to it, instead
    of vector_dance's fixed-speed canned sequence.

    Mike's idea 2026-08-20. IMPORTANT LIMITATION: the SDK's AudioFeed does
    not expose raw audio - only pre-processed signal_power/noise_floor_power
    (confirmed live against the protobuf, see beat_audio.py). So this
    tracks percussive ONSETS (loud transients - drums, bass hits, claps),
    not true melodic beat-tracking. Works best with a clear punchy beat
    nearby; won't do much with quiet/ambient music. Tuning is UNVERIFIED
    against real hardware - if it never detects a beat, or detects one
    that's obviously wrong, that's expected until tuned live (see
    beat_audio.ONSET_RATIO).

    Args:
        seconds: how long to listen before dancing (default 8, 3-20).
        beats: how many beat-synced moves to play afterward (default 12,
            4-40).
    """
    import threading
    from beat_audio import sample_audio_feed, estimate_bpm

    seconds = max(3, min(int(seconds), 20))
    beats = max(4, min(int(beats), 40))
    moves = ["ExploringQuickScan", "FistBumpSuccess", "ReactToGreeting",
             "VC_ListeningLoop", "GreetAfterLongTime", "KnowledgeGraphSearching"]

    onsets = []
    with _connect() as robot:
        done = threading.Event()

        async def _listen():
            try:
                onsets.extend(await sample_audio_feed(robot, seconds))
            finally:
                done.set()

        robot.conn.run_soon(_listen())
        if not done.wait(timeout=seconds + 5):
            return "Listening timed out without finishing - try again."

        bpm = estimate_bpm(onsets)
        used_default = bpm is None
        if bpm is None:
            bpm = 100.0  # no clear rhythm detected - fall back to a moderate default

        beat_interval = 60.0 / bpm
        for i in range(beats):
            robot.anim.play_animation_trigger(moves[i % len(moves)])
            _time_mod.sleep(beat_interval)

    note = (f"estimated {bpm:.0f} BPM from {len(onsets)} onsets" if not used_default
            else f"no clear beat detected ({len(onsets)} onsets) - used a default 100 BPM")
    return f"Vector danced {beats} beats ({note})."


@tool
def vector_photo_album() -> str:
    """Curate today's camera captures into an Obsidian album note
    (Vector Mind/snapshots/album/). Returns the note path and photo count."""
    import shutil
    captures = _USER / "vector-mcp" / "captures"
    album_dir = _VAULT / "Vector Mind" \
        / "snapshots" / "album"
    album_dir.mkdir(parents=True, exist_ok=True)
    today = _time_mod.strftime("%Y%m%d")
    pics = sorted(p for p in captures.glob("*.jpg") if today in p.name)
    if not pics:
        return "no captures today yet"
    note = album_dir / f"album-{today}.md"
    embeds = "\n".join(f"![[Vector Mind/snapshots/album/{p.name}]]" for p in pics)
    note.write_text(f"---\ntags: [vector-mind, album]\n---\n"
                    f"# Album {today}\n\n{embeds}\n", encoding="utf-8")
    for p in pics:
        shutil.copy2(p, album_dir / p.name)
    return f"album note: {note} ({len(pics)} photos)"


def _llm_line(system, prompt, max_tokens=800):
    import urllib.request
    req = urllib.request.Request(
        _os.environ.get("VECTOR_BRAIN_URL", "http://localhost:11434")
        + "/v1/chat/completions",
        data=_json_mod.dumps({
            "model": _os.environ.get("VECTOR_BRAIN_MODEL", "qwen2.5:3b-instruct"),
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
            "temperature": 1.0, "max_tokens": max_tokens}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        txt = (_json_mod.load(r)["choices"][0]["message"]
               .get("content") or "").strip()
    return txt.split("</think>")[-1].strip().strip('"')


@tool
def vector_story(topic: str = "a brave little robot") -> str:
    """Vector tells a short bedtime story about a topic (LLM-written,
    spoken out loud, ~3-4 sentences)."""
    story = _llm_line(
        "You are Vector, a small robot storyteller. Write a cosy bedtime "
        "story of 3-4 short sentences for a child, plain ASCII.",
        f"Topic: {topic}")
    if not story:
        return "the story got lost in my head"
    with _connect() as robot:
        robot.behavior.say_text(story)
    return f"Vector told a story about {topic}: {story}"


@tool
def vector_cube_play(max_steps: int = 20) -> str:
    """Vector plays with his cube: vision-guided chase + pickup if it's
    in view. BLE not required for finding (trained YOLO model); pickup only
    works if the cube is connected (needs its battery fixed)."""
    try:
        res = vector_find_cube(max_steps=max_steps, pickup=True)
    except Exception as e:
        return f"cube play failed: {str(e)[:100]}"
    if res.get("found"):
        f = _mood_file()
        mood = _json_mod.loads(f.read_text(encoding="utf-8")) if f.exists() \
            else {"valence": 0.55, "energy": 0.5}
        mood["valence"] = min(1.0, mood.get("valence", 0.5) + 0.2)
        mood["energy"] = min(1.0, mood.get("energy", 0.5) + 0.15)
        f.write_text(_json_mod.dumps(mood), encoding="utf-8")
        return f"found the cube! {_json_mod.dumps(res)}"
    return (f"couldn't find the cube: {res.get('reason')} "
            f"(is it in the room? battery alive?)")


@tool
def vector_map(limit: int = 300) -> str:
    """Vector's mental map v1: aggregate his sightings into a layout
    summary - per object: times seen, last seen, average heading."""
    import math
    from collections import defaultdict
    mem = _MEMORY_FILE
    if not mem.exists():
        return "no sightings yet"
    agg = defaultdict(lambda: {"n": 0, "last": 0.0, "sin": 0.0, "cos": 0.0})
    for l in open(mem, encoding="utf-8"):
        if not l.strip():
            continue
        try:
            rec = _json_mod.loads(l)
        except Exception:
            continue
        ang = rec.get("pose", {}).get("angle_z_rad", 0.0)
        for d in rec.get("detections", []):
            a = agg[d["object"]]
            a["n"] += 1
            a["last"] = max(a["last"], rec.get("t", 0.0))
            a["sin"] += math.sin(ang)
            a["cos"] += math.cos(ang)
    lines = []
    for obj, a in sorted(agg.items(), key=lambda kv: -kv[1]["n"]):
        deg = round(math.degrees(math.atan2(a["sin"], a["cos"]))) % 360
        last_s = _time_mod.strftime("%H:%M", _time_mod.localtime(a["last"]))
        lines.append(f"{obj}: {a['n']}x, last {last_s}, avg heading {deg}deg")
    return "\n".join(lines) or "no detections recorded"


@tool
def vector_quiz() -> str:
    """Vector asks a riddle (LLM-generated). Answer is logged to
    memory/quiz_log.jsonl for scorekeeping later. The voice loop
    (Mike answering out loud) is a future step."""
    q = _llm_line(
        "You are Vector. Invent ONE short riddle with a single clear "
        "answer. Reply exactly as: RIDDLE: <riddle> | ANSWER: <answer>",
        "Give me a riddle.")
    if "ANSWER:" not in q:
        return f"riddle: {q} (answer lost)"
    riddle, answer = q.split("ANSWER:", 1)
    riddle = riddle.replace("RIDDLE:", "").strip()
    qlog = _USER / "vector-mcp" / "memory" / "quiz_log.jsonl"
    qlog.parent.mkdir(parents=True, exist_ok=True)
    with open(qlog, "a", encoding="utf-8") as f:
        f.write(_json_mod.dumps({"ts": _time_mod.time(), "riddle": riddle,
                                 "answer": answer.strip()}) + "\n")
    return f"Vector's riddle: {riddle}"


@tool
def vector_watch_for_frights(seconds: float = 60.0) -> dict:
    """Watch Vector's sensors for a while and let him swear (in English) every
    time he is startled - picked up, put down, or reaching a cliff/table edge.

    Args:
        seconds: How long to watch. Keep under ~180.
    """
    events = []
    seconds = min(float(seconds), 180.0)
    with _connect() as robot:
        s0 = robot.status
        was_held = s0.is_being_held or s0.is_picked_up
        was_cliff = s0.is_cliff_detected or s0.is_falling
        t_end = _time_mod.time() + seconds
        while _time_mod.time() < t_end:
            _time_mod.sleep(0.4)
            s = robot.status
            held = s.is_being_held or s.is_picked_up
            cliff = s.is_cliff_detected or s.is_falling
            if held and not was_held:
                events.append({"t": _time_mod.strftime("%H:%M:%S"),
                               "event": "picked up", "oath": _swear(robot)})
            elif was_held and not held:
                events.append({"t": _time_mod.strftime("%H:%M:%S"),
                               "event": "put down", "oath": _swear(robot)})
            elif cliff and not was_cliff:
                events.append({"t": _time_mod.strftime("%H:%M:%S"),
                               "event": "cliff/edge", "oath": _swear(robot)})
            was_held, was_cliff = held, cliff
    return {"watched_seconds": seconds, "frights": events}


@tool
def vector_signal(state: str, set_operator_eyes: bool = False) -> str:
    """Make Vector visibly/audibly show what he is doing right now.

    Use this to keep Mike informed during longer operations - e.g. signal
    'thinking' before a slow step, 'success' or 'error' when it ends.

    Args:
        state: thinking | listening | looking | success | error | greet |
               sleep | wake | alert
        set_operator_eyes: also set his eye colour to the calling operator's
            colour (e.g. claude=blue, n8n=orange, mike=green - add your own).
    """
    anim = SIGNAL_ANIMS.get(state)
    if not anim:
        raise ValueError(f"unknown state '{state}'. "
                         f"Valid: {', '.join(SIGNAL_ANIMS)}")
    operator = _os.environ.get("VECTOR_OPERATOR", "unknown")
    anim_ok = False
    with _connect() as robot:
        # Eye colour is instant and always works - the reliable signal.
        if set_operator_eyes:
            robot.behavior.set_eye_color(
                hue=OPERATOR_EYES.get(operator, 0.5), saturation=1.0)
        # Animations need his 476-entry trigger list, which lazy-loads per
        # connection and sometimes blows the 10s gRPC deadline. Best effort.
        try:
            robot.anim.play_animation_trigger(anim)
            anim_ok = True
        except Exception:
            pass
    return (f"Signalled '{state}'"
            + (f" ({anim})" if anim_ok else " (eyes only - animation timed out)")
            + (f", {operator} eyes" if set_operator_eyes else ""))


@tool
def vector_whose_eyes() -> dict:
    """Explain Vector's signal language: which eye colour means which
    operator, and which animation means which state."""
    return {"eye_colours_by_operator": OPERATOR_EYES,
            "state_animations": SIGNAL_ANIMS,
            "current_operator": _os.environ.get("VECTOR_OPERATOR", "unknown")}


@tool
def vector_human_log(what: str, kind: str = "decision") -> str:
    """Log MIKE's contribution to Vector's development - the human half of the
    record. Agents MUST call this whenever Mike makes a decision, has an idea,
    asks for a capability, approves a proposal, or does something physical for
    Vector (charging, moving, enrolling his face).

    Vector has a human parent (decides) and one or more AI agents (build).
    Without this, the logbook only shows the machines and history looks wrong.

    Args:
        what: What Mike did/decided/asked for, in his own terms.
            e.g. "asked for a dashboard with battery, age and mind graph"
        kind: one of decision | idea | approval | physical | teaching
    """
    import time as _t
    from pathlib import Path

    now = _t.time()
    rec = {"ts": now, "time_str": _t.strftime("%Y-%m-%d %H:%M:%S"),
           "operator": "mike", "tool": f"human_{kind}",
           "args": {"what": what[:300]}, "status": "ok", "secs": 0.0}
    af = Path(_ACTIONS_FILE)
    af.parent.mkdir(parents=True, exist_ok=True)
    with open(af, "a", encoding="utf-8") as f:
        f.write(_json_mod.dumps(rec) + "\n")

    icon = {"decision": "🧭", "idea": "💡", "approval": "✅",
            "physical": "🤲", "teaching": "🎓"}.get(kind, "•")
    lb = Path(_LOGBOOK_FILE)
    lb.parent.mkdir(parents=True, exist_ok=True)
    existing = lb.read_text(encoding="utf-8") if lb.exists() else (
        "---\ntags: [vector-mind, logbook]\n---\n# Vector Logbook\n\n"
        "Who did what with Vector, when. Operators: mike, claude, n8n, ...\n")
    day_header = "## " + _t.strftime("%Y-%m-%d")
    if day_header not in existing:
        existing += f"\n{day_header}\n\n"
    lb.write_text(
        existing + f"- {_t.strftime('%H:%M:%S')} {icon} **mike** ({kind}) — {what}\n",
        encoding="utf-8")
    return f"Logged Mike's {kind}: {what}"


@tool
def vector_request_capability(what: str, why: str) -> str:
    """Vector asks one of his parents (whichever AI agent maintains him) for a new capability
    he doesn't have. This does NOT grant anything by itself - it only writes
    a visible request that a parent must review with Mike before building or
    approving it. This is Vector's ONLY path to gaining new abilities: he can
    ask, never take.

    Args:
        what: The capability he wants, in his own words.
            e.g. "I want to know how many fingers Mike is holding up"
        why: Why he wants it / what happened that made him ask.
    """
    from pathlib import Path

    now = _time_mod.time()
    req_file = _VAULT / "Vector Mind" / "Requests.md"
    req_file.parent.mkdir(parents=True, exist_ok=True)
    existing = req_file.read_text(encoding="utf-8") if req_file.exists() else (
        "---\ntags: [vector-mind]\n---\n# Vector's Requests\n\n"
        "Vector cannot grant himself anything. He can only ask.\n"
        "A parent (whichever agent maintains him) reviews with Mike, then either implements\n"
        "it (ticks the box + note) or explains why not.\n\n")
    entry = (f"## {_time_mod.strftime('%Y-%m-%d %H:%M:%S')}\n"
             f"- [ ] **Wants:** {what}\n"
             f"  **Because:** {why}\n\n")
    req_file.write_text(existing + entry, encoding="utf-8")

    # Also in the logbook, as HIS voice, not an agent's
    lb = Path(_LOGBOOK_FILE)
    lb.parent.mkdir(parents=True, exist_ok=True)
    lb_existing = lb.read_text(encoding="utf-8") if lb.exists() else ""
    day = "## " + _time_mod.strftime("%Y-%m-%d")
    if day not in lb_existing:
        lb_existing += f"\n{day}\n\n"
    lb.write_text(
        lb_existing + f"- {_time_mod.strftime('%H:%M:%S')} 🙋 **vector** "
        f"(request) — wants: {what[:100]}\n", encoding="utf-8")

    return ("Request logged for your parents to review with Mike. "
            "You don't have this yet, but you asked, which is the right move.")


@tool
def vector_approve_request(approved: bool, note: str = "") -> str:
    """Record Mike's verbal yes/no on Vector's most recent pending request.
    Called by the brain proxy when it detects a clear approval/decline in
    Mike's spoken reply right after Vector asked for something - NEVER
    called by Vector's own model (he cannot approve himself).

    Args:
        approved: True if Mike said yes, False if he declined.
        note: Optional context, e.g. the pending request's "what".
    """
    from pathlib import Path

    req_file = _VAULT / "Vector Mind" / "Requests.md"
    if req_file.exists():
        text = req_file.read_text(encoding="utf-8")
        lines = text.splitlines(True)
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip().startswith("- [ ]"):
                mark = "x" if approved else " "
                lines[i] = lines[i].replace("- [ ]", f"- [{mark}]", 1)
                verdict = ("APPROVED" if approved else "DECLINED")
                lines.insert(i + 1,
                    f"  **Mike (voice):** {verdict} — "
                    f"{_time_mod.strftime('%Y-%m-%d %H:%M:%S')}\n")
                break
        req_file.write_text("".join(lines), encoding="utf-8")

    vector_human_log(
        what=f"{'approved' if approved else 'declined'} Vector's request by voice"
             + (f" ({note})" if note else ""),
        kind="approval")
    return "recorded"


_WIREPOD_API = "http://localhost:8080/api"


@tool
def vector_teach(phrases: list[str], response: str, name: str = "",
                 description: str = "") -> str:
    """Teach Vector a new voice command: when someone says one of the phrases
    (after 'Hey Vector'), he will speak the given response.

    Args:
        phrases: Trigger utterances, e.g. ["who is your favorite human",
            "who do you love"]. Partial matching applies - keep them distinct.
        response: What Vector says back when triggered.
        name: Short intent name (auto-generated from first phrase if empty).
        description: What this teaching is for (optional).
    """
    import json as _json
    import urllib.request

    if not name:
        name = "taught_" + "_".join(phrases[0].lower().split()[:4])
    if not description:
        description = f"Taught: '{phrases[0]}' -> '{response[:50]}'"

    # Lua escaping: keep it simple, strip double quotes from response
    safe_response = response.replace('"', "'")
    payload = {
        "name": name,
        "description": description,
        "utterances": phrases,
        "intent": "intent_greeting_hello",
        "params": {"paramname": "", "paramvalue": ""},
        "exec": "",
        "execargs": [""],
        "luascript": f'sayText("{safe_response}")',
    }
    req = urllib.request.Request(
        _WIREPOD_API + "/add_custom_intent",
        data=_json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        result = r.read().decode()
    return f"Taught '{name}': say 'Hey Vector, {phrases[0]}' -> he says '{response}'. API: {result}"


@tool
def vector_list_teachings() -> str:
    """List all custom voice commands Vector has been taught via wire-pod."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        _WIREPOD_API + "/get_custom_intents_json",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read().decode()
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        return f"No custom voice commands taught yet (wire-pod {e.code}: {body})"


@tool
def vector_get_battery() -> dict:
    """Get Vector's current battery level and charging status."""
    with _connect(status_only=True) as robot:
        state = robot.get_battery_state()
        return {
            "battery_volts": state.battery_volts,
            "battery_level": state.battery_level,
            "is_charging": state.is_charging,
            "is_on_charger_platform": state.is_on_charger_platform,
        }


@tool
def vector_dashboard() -> str:
    """Regenerate Vector's status dashboard note in Obsidian
    (Vector Mind/Dashboard.md): battery, age, mind stats, strongest
    associations, latest sighting, teachings link.
    """
    import json as _json
    import time as _time
    from collections import Counter
    from itertools import combinations
    from pathlib import Path

    # --- live battery ---
    battery = {}
    try:
        with _connect(status_only=True) as robot:
            s = robot.get_battery_state()
            battery = {
                "volts": round(s.battery_volts, 2),
                "level": s.battery_level,   # 1=low 2=nominal 3=full
                "charging": s.is_charging,
                "on_charger": s.is_on_charger_platform,
            }
    except Exception as e:
        battery = {"error": str(e)[:80]}

    # --- wire-pod status ---
    try:
        import urllib.request
        urllib.request.urlopen("http://localhost:8080", timeout=3)
        wirepod = "🟢 online"
    except Exception:
        wirepod = "🔴 unreachable"

    # --- robot identity (set these to your own robot's details) ---
    robot_name = os.environ.get("VECTOR_ROBOT_NAME", "Vector")
    robot_ip = os.environ.get("VECTOR_ROBOT_IP", "<set VECTOR_ROBOT_IP>")
    robot_serial = os.environ.get("VECTOR_ROBOT_SERIAL", "<set VECTOR_ROBOT_SERIAL>")

    # --- mind stats ---
    mem = Path(_MEMORY_FILE)
    records = ([_json.loads(l) for l in open(mem) if l.strip()]
               if mem.exists() else [])
    obj_counts = Counter()
    pair_counts = Counter()
    latest_snap = None
    for r in records:
        names = sorted({d["object"] for d in r["detections"]})
        obj_counts.update(names)
        pair_counts.update(combinations(names, 2))
        if r["detections"]:
            latest_snap = r
    top_pairs = pair_counts.most_common(5)

    # --- age ---
    born = _time.mktime((2018, 10, 12, 0, 0, 0, 0, 0, -1))   # Vector 1.0 launch
    reborn = _time.mktime((2026, 8, 7, 0, 0, 0, 0, 0, -1))   # wire-pod resurrection
    age_years = (_time.time() - born) / (365.25 * 86400)
    reborn_days = int((_time.time() - reborn) / 86400)

    lvl_txt = {1: "🪫 low", 2: "🔋 nominal", 3: "🔋 full"}.get(
        battery.get("level"), "❓")
    chg = " (charging ⚡)" if battery.get("charging") else ""

    lines = [
        "---\ntags: [vector-mind, dashboard]\n---\n",
        "# 🤖 Vector Dashboard\n\n",
        f"*Updated: {_time.strftime('%Y-%m-%d %H:%M:%S')}*\n\n",
        "## Vitals\n\n",
        f"| | |\n|---|---|\n",
        f"| Battery | {lvl_txt} {battery.get('volts', '?')}V{chg} |\n",
        f"| On charger | {'yes' if battery.get('on_charger') else 'no — roaming'} |\n",
        f"| wire-pod | {wirepod} |\n",
        f"| Robot | {robot_name} @ {robot_ip} (serial {robot_serial}) |\n",
        f"| Age | {age_years:.1f} years (born 2018-10-12) |\n",
        f"| Reborn | 2026-08-07 (wire-pod), {reborn_days} days ago |\n\n",
        "## Mind\n\n",
        f"- **Memories**: {len(records)}\n",
        f"- **Objects known**: {len(obj_counts)} — ",
        ", ".join(f"[[{o}]] ({c})" for o, c in obj_counts.most_common()) + "\n",
        f"- **Mind map**: [[Vector]] — graph view, filter `tag:#vector-mind`\n\n",
        "## Strongest associations\n\n",
    ]
    if top_pairs:
        for (a, b), c in top_pairs:
            lines.append(f"- [[{a}]] ↔ [[{b}]] — seen together {c}x\n")
    else:
        lines.append("*none yet*\n")
    if latest_snap:
        lines.append(f"\n## Last thing I saw ({latest_snap['time_str']})\n\n")
        lines.append(f"![[Vector Mind/snapshots/{Path(latest_snap['image']).name}]]\n")
        objs = ", ".join(sorted({d['object'] for d in latest_snap['detections']}))
        lines.append(f"\n*{objs}*\n")
    lines.append(
        "\n## Teaching\n\n"
        "- Custom voice commands: [wire-pod → Custom Intents](http://localhost:8080)\n"
        "- New memories: any agent calls `vector_recognize` (auto-syncs here)\n")

    dash = Path(_VAULT_MIND) / "Dashboard.md"
    dash.parent.mkdir(parents=True, exist_ok=True)
    dash.write_text("".join(lines), encoding="utf-8")
    return f"Dashboard updated: {dash}"


@tool
def vector_set_eye_color(hue: float, saturation: float = 1.0) -> str:
    """Change Vector's eye color permanently.

    Args:
        hue: Hue value from 0.0 to 1.0 (0=red, 0.33=green, 0.66=blue, etc.)
        saturation: Saturation from 0.0 to 1.0, default 1.0.
    """
    with _connect() as robot:
        robot.behavior.set_eye_color(hue=hue, saturation=saturation)
    return f"Vector's eyes set to hue={hue}, saturation={saturation}"


@tool
def vector_go_to_charger() -> str:
    """Send Vector back to his charger platform."""
    with _connect() as robot:
        robot.behavior.drive_on_charger()
    return "Vector is driving back to his charger."


@tool
def vector_lift(height: float) -> str:
    """Move Vector's lift arm to a specific height.

    Args:
        height: 0.0 (fully down) to 1.0 (fully up).
    """
    with _connect() as robot:
        robot.behavior.set_lift_height(height=height)
    return f"Vector's lift set to height {height}"


# ---------------------------------------------------------------------------
# Amazon Fire TV control (via ADB over WiFi).
# Tested against an Amazon Fire TV (AFTR, Android 9). Set VECTOR_TV_IP to
# your own Fire TV's LAN IP (ADB default port 5555).
# NOTE: keyevent 26 (POWER) is ignored by this Fire OS build; use 223
# (SLEEP) / 224 (WAKEUP), or 26 only as a generic toggle fallback.
# Requires an `adb` client: set VECTOR_ADB env var to its path if not on PATH.
# ---------------------------------------------------------------------------
_TV_ADB_TARGET = os.environ.get("VECTOR_TV_IP", "<set VECTOR_TV_IP>") + ":5555"
_TV_KEYS = {
    "home": "3", "back": "4", "up": "19", "down": "20", "left": "21",
    "right": "22", "enter": "66", "volume_up": "24", "volume_down": "25",
    "mute": "164", "play_pause": "85",
}


def _tv_adb_path():
    import shutil
    env = _os.environ.get("VECTOR_ADB")
    if env:
        return env
    found = shutil.which("adb")
    if found:
        return found
    wsl = "/home/magic/platform-tools/adb"
    return wsl if _Path(wsl).exists() else "adb"


def _tv_cmd(args: list) -> tuple:
    import subprocess
    cmd = [_tv_adb_path(), "-s", _TV_ADB_TARGET] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    return r.returncode, r.stderr.strip() or r.stdout.strip()


def _tv_wakefulness() -> str:
    import re
    code, out = _tv_cmd(["shell", "dumpsys", "power"])
    if code != 0:
        return "unknown"
    m = re.search(r"mWakefulness=(\w+)", out)
    return m.group(1) if m else "unknown"


@tool
def vector_tv_status() -> dict:
    """Return the Fire TV's current power state (awake/asleep/unknown)."""
    wake = _tv_wakefulness()
    display = "ON" if wake == "Awake" else "OFF"
    return {"wakefulness": wake, "display": display, "target": _TV_ADB_TARGET}


def _cube_model():
    """Lazily load the trained cube model (shared cache with vector_recognize)."""
    weights = _os.environ.get(
        "VECTOR_CUBE_MODEL",
        str(_USER / "vector-mcp" / "cube_dataset" / "runs" / "cube" / "weights" / "best.pt"),
    )
    if not _Path(weights).exists():
        return None
    if weights not in _MODEL_CACHE:
        from ultralytics import YOLO
        _MODEL_CACHE[weights] = YOLO(weights)
    return _MODEL_CACHE[weights]


@tool
def vector_find_cube(max_steps: int = 30, pickup: bool = True) -> dict:
    """Vision-guided chase: spot the cube with the trained model and drive to it.

    No room scan and no BLE required: each frame the cube's position in-frame
    steers Vector (turn toward it, drive when centred) until he's close enough,
    then optionally picks it up. Cliff sensors + ToF keep the drive safe.

    Args:
        max_steps: max look-drive iterations before giving up.
        pickup: attempt to grab the cube once close.
    """
    import anki_vector
    from anki_vector.util import Distance, Speed, degrees

    model = _cube_model()
    if model is None:
        raise RuntimeError(
            "cube model weights not found - train first (cube_dataset/runs/cube/weights/best.pt)")

    log = []
    search_turns = 0
    with anki_vector.Robot() as robot:
        robot.camera.init_camera_feed()
        robot.behavior.set_head_angle(degrees(-10))  # look at the table
        miss_streak = 0
        for step in range(max_steps):
            frame = robot.camera.latest_image
            if frame is None:
                continue
            r = model(frame.raw_image, verbose=False)[0]
            best, conf, w = None, 0.0, 0
            for box in r.boxes:
                if r.names[int(box.cls)] == "cube" and float(box.conf) > conf:
                    conf = float(box.conf)
                    x0, y0, x1, y1 = (int(v) for v in box.xyxy[0].tolist())
                    w = x1 - x0
                    best = (x0 + x1) // 2
            if best is None:
                miss_streak += 1
                if miss_streak >= 3:
                    robot.behavior.turn_in_place(angle=degrees(20))
                    search_turns += 1
                    log.append(f"search turn #{search_turns}")
                    miss_streak = 0
                    if search_turns >= 8:
                        return {"found": False, "steps": step + 1, "log": log,
                                "reason": "cube never came into view"}
                continue
            miss_streak = 0
            err = best - 320  # frame centre x (640-wide frame)
            if abs(err) > 60:
                turn_deg = max(-25, min(25, err * 0.08))
                robot.behavior.turn_in_place(angle=degrees(turn_deg))
                log.append(f"turn {turn_deg:.0f}deg (err {err})")
            elif w > 280:  # close enough: cube fills the frame
                est_mm = int(64 * 320 / w)  # ~64mm cube, ~320px focal
                result = {"found": True, "steps": step + 1, "confidence": conf,
                          "box_width_px": w, "est_distance_mm": est_mm,
                          "pickup": "not attempted", "log": log}
                if pickup:
                    cube = robot.world.light_cube
                    if cube is not None:
                        try:
                            robot.behavior.pickup_object(cube)
                            result["pickup"] = "success"
                        except Exception as e:
                            result["pickup"] = f"failed: {str(e)[:80]}"
                return result
            else:
                robot.behavior.drive_straight(Distance(60), Speed(70))
                log.append("drive 60mm")
        return {"found": False, "steps": max_steps, "log": log,
                "reason": "step budget exhausted"}


@tool
def vector_tv_power(state: str = "toggle") -> str:
    """Power the Amazon Fire TV on or off.

    Args:
        state: 'on', 'off', or 'toggle' (default: opposite of current state).
    """
    if state not in ("on", "off", "toggle"):
        return f"error: state must be on/off/toggle, got '{state}'"
    if state == "toggle":
        state = "off" if _tv_wakefulness() == "Awake" else "on"
    key = "223" if state == "off" else "224"
    code, err = _tv_cmd(["shell", "input", "keyevent", key])
    if code != 0:
        return f"TV power failed: {err}"
    return f"TV powered {'OFF' if state == 'off' else 'ON'} (keyevent {key})"


@tool
def vector_tv_key(key: str) -> str:
    """Send a remote-control key to the Fire TV.

    Args:
        key: home, back, up, down, left, right, enter, volume_up,
             volume_down, mute, play_pause.
    """
    kc = _TV_KEYS.get(key)
    if kc is None:
        return f"error: unknown key '{key}', valid: {', '.join(sorted(_TV_KEYS))}"
    code, err = _tv_cmd(["shell", "input", "keyevent", kc])
    if code != 0:
        return f"TV key '{key}' failed: {err}"
    return f"Sent '{key}' to TV (keyevent {kc})"


if __name__ == "__main__":
    import sys

    if "--http" in sys.argv:
        # HTTP mode for network clients (n8n MCP Client node, remote agents).
        # URL: http://<host>:8385/mcp
        _os.environ.setdefault("VECTOR_OPERATOR", "n8n")
        server.run(transport="streamable-http", host="0.0.0.0", port=8385)
    else:
        # Default stdio mode (Claude Code / Claude Desktop / other MCP clients).
        server.run()
