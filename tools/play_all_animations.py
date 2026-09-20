"""Play every animation trigger Vector knows about, back to back, once.

Built 2026-09-08 for a full-library video recording session. Opens ONE
robot connection (holds behavior control for the whole run instead of
grabbing/releasing per animation - cheaper and avoids control-contention
races), plays every trigger from robot.anim.anim_trigger_list in sorted
order with a 1s gap between each, and logs OK/FAIL per animation to
play_all_animations.log so a run can be resumed from wherever it left off.
"""
import sys, time
sys.path.insert(0, '/home/magix/vector-mcp')
import anki_vector

LOG = '/home/magix/vector-mcp/play_all_animations.log'

def log(msg):
    line = f'{time.strftime("%H:%M:%S")} {msg}'
    print(line, flush=True)
    with open(LOG, 'a') as f:
        f.write(line + '\n')

with anki_vector.Robot(enable_face_detection=False) as robot:
    names = sorted(a.name if not isinstance(a, str) else a for a in robot.anim.anim_trigger_list)
    log(f'START total={len(names)}')
    for i, name in enumerate(names, 1):
        try:
            robot.anim.play_animation_trigger(name)
            log(f'{i}/{len(names)} OK {name}')
        except Exception as e:
            log(f'{i}/{len(names)} FAIL {name} :: {e}')
        time.sleep(1.0)
    log('DONE')
