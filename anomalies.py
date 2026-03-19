import time
from datetime import datetime
from threading import Lock

from config import (
    ANOMALY_DEBUG_LOGS,
    SAFE_CAMERAS,
    WEEKDAY_RESTRICTED_HOUR,
    WEEKEND_RESTRICTED_HOUR,
)
from database import insert_anomaly

camera_anomaly_states = {}
camera_anomaly_states_lock = Lock()
unread_anomaly_count = 0
unread_lock = Lock()


def is_restricted_now():
    now = datetime.now()
    weekday = now.weekday()
    hour = now.hour

    if weekday < 5:
        return hour >= WEEKDAY_RESTRICTED_HOUR
    return hour >= WEEKEND_RESTRICTED_HOUR


def _get_camera_state(camera_id):
    return camera_anomaly_states.setdefault(
        camera_id,
        {
            "presence_active": False,
        },
    )


def _reset_camera_tracking(state):
    state["presence_active"] = False


def _debug_log(camera_id, camera_name, group, person_count, event, state=None, **extra):
    if not ANOMALY_DEBUG_LOGS:
        return

    parts = [
        f"event={event}",
        f"camera_id={camera_id}",
        f"camera_name={camera_name}",
        f"group={group}",
        f"person_count={person_count}",
    ]

    if state is not None:
        parts.extend(
            [
                f"presence_active={state.get('presence_active')}",
            ]
        )

    for key, value in extra.items():
        parts.append(f"{key}={value}")

    print("[ANOMALY DEBUG] " + " | ".join(parts))


def _store_anomaly(camera_id, camera_name, group, person_count):
    global unread_anomaly_count

    message = f"Unauthorized Access to {group}"

    try:
        insert_anomaly(camera_id, camera_name, group, person_count, message)
    except Exception as e:
        print(f"[ANOMALY DB ERROR] {e}")

    with unread_lock:
        unread_anomaly_count += 1

    print(f"[ANOMALY] {message} | Camera: {camera_name} | Count: {person_count}")


def check_and_fire_anomaly(camera_id, camera_name, group, person_count):
    if camera_name in SAFE_CAMERAS:
        with camera_anomaly_states_lock:
            state = _get_camera_state(camera_id)
            _reset_camera_tracking(state)
            _debug_log(camera_id, camera_name, group, person_count, "skip_safe_camera", state)
        return
    if group == "Unassigned":
        with camera_anomaly_states_lock:
            state = _get_camera_state(camera_id)
            _reset_camera_tracking(state)
            _debug_log(camera_id, camera_name, group, person_count, "skip_unassigned", state)
        return
    if not is_restricted_now():
        with camera_anomaly_states_lock:
            state = _get_camera_state(camera_id)
            _reset_camera_tracking(state)
            _debug_log(camera_id, camera_name, group, person_count, "skip_outside_restricted_hours", state)
        return

    now_ts = time.time()
    fire_count = None
    debug_event = "no_change"
    debug_extra = {}

    with camera_anomaly_states_lock:
        state = _get_camera_state(camera_id)

        if person_count < 1:
            _reset_camera_tracking(state)
            debug_event = "reset_no_people"
        elif not state["presence_active"]:
            state["presence_active"] = True
            fire_count = person_count
            debug_event = "fire_initial_presence"
        else:
            debug_event = "presence_already_active"

        _debug_log(camera_id, camera_name, group, person_count, debug_event, state, **debug_extra)

    if fire_count is not None:
        _store_anomaly(camera_id, camera_name, group, fire_count)


def reset_unread_count():
    global unread_anomaly_count
    with unread_lock:
        unread_anomaly_count = 0


def get_unread_count():
    with unread_lock:
        return unread_anomaly_count
