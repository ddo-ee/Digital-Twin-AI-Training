import time
from datetime import datetime
from threading import Lock

from database import fetch_anomaly_config, insert_anomaly

camera_anomaly_states = {}
camera_anomaly_states_lock = Lock()
unread_anomaly_count = 0
unread_lock = Lock()
anomaly_config_cache = None
anomaly_config_cache_expires_at = 0.0
anomaly_config_cache_lock = Lock()
ANOMALY_CONFIG_CACHE_SECONDS = 2.0


def _parse_time_minutes(value, fallback):
    try:
        hour_text, minute_text = str(value or "").split(":")[:2]
        hour = int(hour_text)
        minute = int(minute_text)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return (hour * 60) + minute
    except Exception:
        pass
    return fallback


def _get_anomaly_config():
    global anomaly_config_cache, anomaly_config_cache_expires_at

    now = time.monotonic()
    with anomaly_config_cache_lock:
        if anomaly_config_cache is not None and now < anomaly_config_cache_expires_at:
            return anomaly_config_cache

        try:
            anomaly_config_cache = fetch_anomaly_config()
        except Exception as e:
            print(f"[ANOMALY CONFIG ERROR] {e}")
            anomaly_config_cache = {
                "restricted_start_time": "21:00",
                "restricted_end_time": "05:00",
                "camera_rules": {},
            }
        anomaly_config_cache_expires_at = now + ANOMALY_CONFIG_CACHE_SECONDS
        return anomaly_config_cache


def invalidate_anomaly_config_cache():
    global anomaly_config_cache_expires_at
    with anomaly_config_cache_lock:
        anomaly_config_cache_expires_at = 0.0


def _is_time_window_active(start_time, end_time):
    now = datetime.now()
    current_minutes = (now.hour * 60) + now.minute
    start_minutes = _parse_time_minutes(start_time, 21 * 60)
    end_minutes = _parse_time_minutes(end_time, 5 * 60)

    if start_minutes == end_minutes:
        return True
    if start_minutes < end_minutes:
        return start_minutes <= current_minutes < end_minutes
    return current_minutes >= start_minutes or current_minutes < end_minutes


def is_restricted_now(config=None, camera_rule=None):
    config = config or _get_anomaly_config()
    camera_rule = camera_rule or {}
    return _is_time_window_active(
        camera_rule.get("restricted_start_time") or config.get("restricted_start_time"),
        camera_rule.get("restricted_end_time") or config.get("restricted_end_time"),
    )


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
    config = _get_anomaly_config()
    camera_rule = config.get("camera_rules", {}).get(camera_id, {})
    camera_rule_type = camera_rule.get("rule_type", "default")

    if camera_rule_type == "safe":
        with camera_anomaly_states_lock:
            _reset_camera_tracking(_get_camera_state(camera_id))
        return
    if group == "Unassigned":
        with camera_anomaly_states_lock:
            _reset_camera_tracking(_get_camera_state(camera_id))
        return
    if not is_restricted_now(config, camera_rule):
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
