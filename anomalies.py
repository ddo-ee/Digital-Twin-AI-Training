import time
from datetime import datetime
from threading import Lock

from config import (
    ANOMALY_COOLDOWN_SECONDS,
    SAFE_CAMERAS,
    UNITY_ANOMALY_WINDOW_SECONDS,
    UNITY_ZONE_ORDER,
    WEEKDAY_RESTRICTED_HOUR,
    WEEKEND_RESTRICTED_HOUR,
)
from database import insert_anomaly

anomaly_cooldowns = {}
anomaly_cooldowns_lock = Lock()
unread_anomaly_count = 0
unread_lock = Lock()


def is_restricted_now():
    now = datetime.now()
    weekday = now.weekday()
    hour = now.hour

    if weekday < 5:
        return hour >= WEEKDAY_RESTRICTED_HOUR
    return hour >= WEEKEND_RESTRICTED_HOUR


def check_and_fire_anomaly(camera_id, camera_name, group, person_count):
    global unread_anomaly_count

    if camera_name in SAFE_CAMERAS:
        return
    if group == "Unassigned":
        return
    if not is_restricted_now():
        return
    if person_count < 1:
        return

    now_ts = time.time()
    with anomaly_cooldowns_lock:
        last_fired = anomaly_cooldowns.get(camera_id, 0)
        if now_ts - last_fired < ANOMALY_COOLDOWN_SECONDS:
            return
        anomaly_cooldowns[camera_id] = now_ts

    message = f"Unauthorized Access to {group}"

    try:
        insert_anomaly(camera_id, camera_name, group, message)
    except Exception as e:
        print(f"[ANOMALY DB ERROR] {e}")

    with unread_lock:
        unread_anomaly_count += 1

    print(f"[ANOMALY] {message} | Camera: {camera_name} | Count: {person_count}")


def reset_unread_count():
    global unread_anomaly_count
    with unread_lock:
        unread_anomaly_count = 0


def get_unread_count():
    with unread_lock:
        return unread_anomaly_count
