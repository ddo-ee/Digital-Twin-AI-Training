from datetime import datetime
from threading import Lock

from config import SAFE_CAMERAS, WEEKDAY_RESTRICTED_HOUR, WEEKEND_RESTRICTED_HOUR
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
            _reset_camera_tracking(_get_camera_state(camera_id))
        return
    if group == "Unassigned":
        with camera_anomaly_states_lock:
            _reset_camera_tracking(_get_camera_state(camera_id))
        return
    if not is_restricted_now():
        with camera_anomaly_states_lock:
            _reset_camera_tracking(_get_camera_state(camera_id))
        return

    fire_count = None

    with camera_anomaly_states_lock:
        state = _get_camera_state(camera_id)

        if person_count < 1:
            _reset_camera_tracking(state)
        elif not state["presence_active"]:
            state["presence_active"] = True
            fire_count = person_count

    if fire_count is not None:
        _store_anomaly(camera_id, camera_name, group, fire_count)


def reset_unread_count():
    global unread_anomaly_count
    with unread_lock:
        unread_anomaly_count = 0


def get_unread_count():
    with unread_lock:
        return unread_anomaly_count
