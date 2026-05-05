import sqlite3
import json
from datetime import datetime, timedelta, timezone
from threading import Lock

from config import ANALYTICS_LOG_INTERVAL_SECONDS, DB_NAME, DEFAULT_ZONES

db_lock = Lock()
PH_TIMEZONE = timezone(timedelta(hours=8))


def _ph_now_str():
    return datetime.now(PH_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def _ph_cutoff_str(window_seconds):
    return (datetime.now(PH_TIMEZONE) - timedelta(seconds=window_seconds)).strftime("%Y-%m-%d %H:%M:%S")


def _ph_today_str():
    return datetime.now(PH_TIMEZONE).strftime("%Y-%m-%d")


def _normalize_time_value(value, fallback):
    if not value:
        return fallback

    normalized = str(value).strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            parsed = datetime.strptime(normalized, fmt)
            return parsed.strftime("%H:%M")
        except ValueError:
            continue
    return fallback


def _parse_history_datetime(value):
    if not value:
        return None

    normalized = value.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return None


def _floor_to_hour(value):
    if value is None:
        return None
    return value.replace(minute=0, second=0, microsecond=0)


def _floor_to_minute(value):
    if value is None:
        return None
    return value.replace(second=0, microsecond=0)


def _floor_to_day(value):
    if value is None:
        return None
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _history_bucket_strategy(start_dt, end_dt, range_key):
    if start_dt is None or end_dt is None:
        return ("hour", "%Y-%m-%d %H:00:00")

    window = end_dt - start_dt
    if window <= timedelta(hours=12):
        return ("raw", None)
    if window <= timedelta(days=2):
        return ("minute", "%Y-%m-%d %H:%M:00")
    if window <= timedelta(days=14):
        return ("hour", "%Y-%m-%d %H:00:00")
    return ("day", "%Y-%m-%d 00:00:00")


def _parse_db_timestamp(value):
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def _floor_to_interval(value, interval_seconds):
    if value is None:
        return None

    midnight = value.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed_seconds = int((value - midnight).total_seconds())
    floored_seconds = elapsed_seconds - (elapsed_seconds % interval_seconds)
    return midnight + timedelta(seconds=floored_seconds)


def _fill_raw_history(rows, start_dt, end_dt):
    if start_dt is None or end_dt is None:
        return rows

    interval_seconds = max(1, int(ANALYTICS_LOG_INTERVAL_SECONDS))
    range_start = _floor_to_hour(start_dt)
    range_end = _floor_to_interval(end_dt, interval_seconds)

    rows_by_bucket = {}
    for timestamp, total_entered, total_exited, inside_total in rows:
        bucket_dt = _floor_to_interval(_parse_db_timestamp(timestamp), interval_seconds)
        bucket_key = bucket_dt.strftime("%Y-%m-%d %H:%M:%S")
        rows_by_bucket[bucket_key] = (bucket_key, int(total_entered), int(total_exited), int(inside_total))

    filled_rows = []
    current_dt = range_start
    while current_dt <= range_end:
        bucket_key = current_dt.strftime("%Y-%m-%d %H:%M:%S")
        filled_rows.append(rows_by_bucket.get(bucket_key, (bucket_key, 0, 0, 0)))
        current_dt += timedelta(seconds=interval_seconds)

    return filled_rows


def _history_bucket_step(bucket_mode):
    if bucket_mode == "minute":
        return timedelta(minutes=1)
    if bucket_mode == "hour":
        return timedelta(hours=1)
    if bucket_mode == "day":
        return timedelta(days=1)
    return timedelta(seconds=max(1, int(ANALYTICS_LOG_INTERVAL_SECONDS)))


def _floor_history_bucket(value, bucket_mode):
    if bucket_mode == "minute":
        return _floor_to_minute(value)
    if bucket_mode == "hour":
        return _floor_to_hour(value)
    if bucket_mode == "day":
        return _floor_to_day(value)
    return _floor_to_interval(value, max(1, int(ANALYTICS_LOG_INTERVAL_SECONDS)))


def _fill_bucketed_history(rows, start_dt, end_dt, bucket_mode):
    if start_dt is None or end_dt is None:
        return rows

    range_start = _floor_history_bucket(start_dt, bucket_mode)
    range_end = _floor_history_bucket(end_dt, bucket_mode)
    step = _history_bucket_step(bucket_mode)

    rows_by_bucket = {
        row[0]: (row[0], int(row[1]), int(row[2]), int(row[3]))
        for row in rows
    }

    filled_rows = []
    current_dt = range_start
    while current_dt <= range_end:
        bucket_key = current_dt.strftime("%Y-%m-%d %H:%M:%S")
        filled_rows.append(rows_by_bucket.get(bucket_key, (bucket_key, 0, 0, 0)))
        current_dt += step

    return filled_rows


def init_db():
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute("PRAGMA journal_mode=WAL;")
            c.execute("PRAGMA synchronous=NORMAL;")
            c.execute("PRAGMA foreign_keys=ON;")

            c.execute(
                """CREATE TABLE IF NOT EXISTS cameras
                   (id TEXT PRIMARY KEY, name TEXT, url TEXT, camera_group TEXT, floor TEXT)"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS logs
                   (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    camera_id TEXT, count INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS zone_logs
                   (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    zone_name TEXT, count INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS anomaly_logs
                   (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    camera_id   TEXT,
                    camera_name TEXT,
                    zone_name   TEXT,
                    detected_count INTEGER DEFAULT 0,
                    message     TEXT,
                    is_resolved INTEGER DEFAULT 0,
                    detected_at DATETIME DEFAULT CURRENT_TIMESTAMP)"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS campus_logs
                   (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    total_count INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS gate_history_logs
                   (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    total_entered INTEGER DEFAULT 0,
                    total_exited INTEGER DEFAULT 0,
                    inside_total INTEGER DEFAULT 0,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS gate_counter_states
                   (
                        camera_id TEXT,
                        count_date TEXT,
                        entry_count INTEGER DEFAULT 0,
                        exit_count INTEGER DEFAULT 0,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (camera_id, count_date),
                        FOREIGN KEY(camera_id) REFERENCES cameras(id) ON DELETE CASCADE
                   )"""
            )
            c.execute("""CREATE TABLE IF NOT EXISTS zones (name TEXT PRIMARY KEY)""")
            c.execute(
                """CREATE TABLE IF NOT EXISTS camera_rois
                   (
                        camera_id TEXT PRIMARY KEY,
                        reference_image_path TEXT,
                        roi_points TEXT,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(camera_id) REFERENCES cameras(id) ON DELETE CASCADE
                   )"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS gate_configs
                   (
                        camera_id TEXT PRIMARY KEY,
                        reference_image_path TEXT,
                        roi_points TEXT,
                        split_x INTEGER,
                        separator_points TEXT,
                        direction TEXT,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(camera_id) REFERENCES cameras(id) ON DELETE CASCADE
                   )"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS anomaly_config
                   (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        restricted_start_time TEXT DEFAULT '21:00',
                        restricted_end_time TEXT DEFAULT '05:00',
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                   )"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS camera_anomaly_rules
                   (
                        camera_id TEXT PRIMARY KEY,
                        rule_type TEXT NOT NULL CHECK (rule_type IN ('safe', 'restricted')),
                        restricted_start_time TEXT,
                        restricted_end_time TEXT,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(camera_id) REFERENCES cameras(id) ON DELETE CASCADE
                   )"""
            )
            c.execute(
                """
                INSERT OR IGNORE INTO anomaly_config
                    (id, restricted_start_time, restricted_end_time, updated_at)
                VALUES (1, '21:00', '05:00', ?)
                """,
                (_ph_now_str(),),
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_zone_logs_timestamp ON zone_logs(timestamp)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_campus_logs_timestamp ON campus_logs(timestamp)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_gate_history_logs_timestamp ON gate_history_logs(timestamp)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_anomaly_logs_detected_at ON anomaly_logs(detected_at)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_camera_anomaly_rules_rule_type ON camera_anomaly_rules(rule_type)")

            c.execute("PRAGMA table_info(cameras)")
            camera_columns = {row[1] for row in c.fetchall()}
            if "floor" not in camera_columns:
                c.execute("ALTER TABLE cameras ADD COLUMN floor TEXT")

            c.execute("PRAGMA table_info(anomaly_logs)")
            anomaly_columns = {row[1] for row in c.fetchall()}
            if "detected_count" not in anomaly_columns:
                c.execute("ALTER TABLE anomaly_logs ADD COLUMN detected_count INTEGER DEFAULT 0")

            c.execute("PRAGMA table_info(camera_anomaly_rules)")
            camera_anomaly_rule_columns = {row[1] for row in c.fetchall()}
            if "restricted_start_time" not in camera_anomaly_rule_columns:
                c.execute("ALTER TABLE camera_anomaly_rules ADD COLUMN restricted_start_time TEXT")
            if "restricted_end_time" not in camera_anomaly_rule_columns:
                c.execute("ALTER TABLE camera_anomaly_rules ADD COLUMN restricted_end_time TEXT")

            c.execute("PRAGMA table_info(gate_configs)")
            gate_config_columns = {row[1] for row in c.fetchall()}
            if "separator_points" not in gate_config_columns:
                c.execute("ALTER TABLE gate_configs ADD COLUMN separator_points TEXT")
            if "roi_closed" not in gate_config_columns:
                c.execute("ALTER TABLE gate_configs ADD COLUMN roi_closed INTEGER DEFAULT 1")
            if "camera_role" not in gate_config_columns:
                c.execute("ALTER TABLE gate_configs ADD COLUMN camera_role TEXT DEFAULT 'entrance'")

            c.execute("PRAGMA table_info(camera_rois)")
            camera_roi_columns = {row[1] for row in c.fetchall()}
            if "roi_closed" not in camera_roi_columns:
                c.execute("ALTER TABLE camera_rois ADD COLUMN roi_closed INTEGER DEFAULT 1")

            c.execute(
                """
                UPDATE camera_rois
                SET roi_closed = 1
                WHERE roi_closed IS NULL
                """
            )
            c.execute(
                """
                UPDATE gate_configs
                SET roi_closed = 1
                WHERE roi_closed IS NULL
                """
            )
            c.execute(
                """
                UPDATE gate_configs
                SET camera_role = 'entrance'
                WHERE camera_role IS NULL OR TRIM(camera_role) = ''
                """
            )

            c.execute(
                """
                INSERT INTO camera_rois (camera_id, reference_image_path, roi_points, updated_at)
                SELECT gc.camera_id, gc.reference_image_path, gc.roi_points, gc.updated_at
                FROM gate_configs gc
                WHERE gc.roi_points IS NOT NULL
                  AND gc.roi_points != ''
                  AND NOT EXISTS (
                      SELECT 1
                      FROM camera_rois cr
                      WHERE cr.camera_id = gc.camera_id
                  )
                """
            )

            c.execute("SELECT count(*) FROM zones")
            if c.fetchone()[0] == 0:
                c.executemany("INSERT INTO zones (name) VALUES (?)", [(z,) for z in DEFAULT_ZONES])

            conn.commit()


def fetch_zones(order_by_name=True):
    query = "SELECT name FROM zones ORDER BY name" if order_by_name else "SELECT name FROM zones"
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute(query)
            return [row[0] for row in c.fetchall()]


def add_zone(zone_name):
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO zones (name) VALUES (?)", (zone_name,))
            conn.commit()


def remove_zone(zone_name):
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM zones WHERE name = ?", (zone_name,))
            c.execute("INSERT OR IGNORE INTO zones (name) VALUES ('Unassigned')")
            c.execute("UPDATE cameras SET camera_group = 'Unassigned' WHERE camera_group = ?", (zone_name,))
            conn.commit()


def fetch_cameras():
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute("SELECT id, name, url, camera_group, COALESCE(floor, '') FROM cameras")
            return c.fetchall()


def fetch_gate_configs():
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT camera_id, reference_image_path, roi_points, split_x, separator_points, direction, roi_closed, camera_role
                FROM gate_configs
                """
            )
            rows = c.fetchall()

    configs = {}
    for camera_id, image_path, roi_points, split_x, separator_points, direction, roi_closed, camera_role in rows:
        configs[camera_id] = {
            "camera_id": camera_id,
            "reference_image_path": image_path or "",
            "roi_points": json.loads(roi_points) if roi_points else [],
            "split_x": split_x,
            "separator_points": json.loads(separator_points) if separator_points else [],
            "direction": direction or "",
            "roi_closed": bool(roi_closed),
            "camera_role": camera_role or "entrance",
        }
    return configs


def fetch_gate_counter_states(count_date=None):
    target_date = count_date or _ph_today_str()
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT camera_id, entry_count, exit_count
                FROM gate_counter_states
                WHERE count_date = ?
                """,
                (target_date,),
            )
            rows = c.fetchall()

    return {
        camera_id: {
            "entry_count": int(entry_count or 0),
            "exit_count": int(exit_count or 0),
        }
        for camera_id, entry_count, exit_count in rows
    }


def upsert_gate_counter_state(camera_id, entry_count, exit_count, count_date=None):
    target_date = count_date or _ph_today_str()
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO gate_counter_states (camera_id, count_date, entry_count, exit_count, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(camera_id, count_date) DO UPDATE SET
                    entry_count = excluded.entry_count,
                    exit_count = excluded.exit_count,
                    updated_at = excluded.updated_at
                """,
                (camera_id, target_date, int(entry_count), int(exit_count), _ph_now_str()),
            )
            conn.commit()


def fetch_database_stats():
    history_tables = [
        "logs",
        "zone_logs",
        "campus_logs",
        "gate_history_logs",
        "anomaly_logs",
    ]

    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            table_counts = {}
            for table_name in history_tables:
                c.execute(f"SELECT COUNT(*) FROM {table_name}")
                table_counts[table_name] = int(c.fetchone()[0])

    db_size_bytes = 0
    try:
        import os
        db_size_bytes = os.path.getsize(DB_NAME) if os.path.exists(DB_NAME) else 0
    except OSError:
        db_size_bytes = 0

    return {
        "db_size_bytes": db_size_bytes,
        "table_counts": table_counts,
        "total_history_rows": sum(table_counts.values()),
    }


def fetch_anomaly_config():
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT restricted_start_time, restricted_end_time
                FROM anomaly_config
                WHERE id = 1
                """
            )
            config_row = c.fetchone()
            c.execute(
                """
                SELECT camera_id, rule_type, restricted_start_time, restricted_end_time
                FROM camera_anomaly_rules
                """
            )
            rule_rows = c.fetchall()

    restricted_start_time = "21:00"
    restricted_end_time = "05:00"
    if config_row:
        restricted_start_time = _normalize_time_value(config_row[0], restricted_start_time)
        restricted_end_time = _normalize_time_value(config_row[1], restricted_end_time)

    return {
        "restricted_start_time": restricted_start_time,
        "restricted_end_time": restricted_end_time,
        "camera_rules": {
            camera_id: {
                "rule_type": rule_type,
                "restricted_start_time": _normalize_time_value(rule_start_time, restricted_start_time),
                "restricted_end_time": _normalize_time_value(rule_end_time, restricted_end_time),
            }
            for camera_id, rule_type, rule_start_time, rule_end_time in rule_rows
            if rule_type in {"safe", "restricted"}
        },
    }


def save_anomaly_config(restricted_start_time, restricted_end_time, camera_rules):
    start_time = _normalize_time_value(restricted_start_time, "21:00")
    end_time = _normalize_time_value(restricted_end_time, "05:00")
    normalized_rules = []
    for camera_id, rule_config in (camera_rules or {}).items():
        clean_camera_id = str(camera_id or "").strip()
        if isinstance(rule_config, dict):
            rule_type = rule_config.get("rule_type")
            rule_start_time = _normalize_time_value(rule_config.get("restricted_start_time"), start_time)
            rule_end_time = _normalize_time_value(rule_config.get("restricted_end_time"), end_time)
        else:
            rule_type = rule_config
            rule_start_time = start_time
            rule_end_time = end_time
        clean_rule_type = str(rule_type or "").strip().lower()
        if not clean_camera_id or clean_rule_type not in {"safe", "restricted"}:
            continue
        if clean_rule_type == "safe":
            rule_start_time = None
            rule_end_time = None
        normalized_rules.append((clean_camera_id, clean_rule_type, rule_start_time, rule_end_time, _ph_now_str()))

    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO anomaly_config
                    (id, restricted_start_time, restricted_end_time, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    restricted_start_time = excluded.restricted_start_time,
                    restricted_end_time = excluded.restricted_end_time,
                    updated_at = excluded.updated_at
                """,
                (start_time, end_time, _ph_now_str()),
            )
            c.execute("DELETE FROM camera_anomaly_rules")
            c.executemany(
                """
                INSERT INTO camera_anomaly_rules
                    (camera_id, rule_type, restricted_start_time, restricted_end_time, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                normalized_rules,
            )
            conn.commit()

    return fetch_anomaly_config()


def _history_timestamp_columns():
    return {
        "logs": "timestamp",
        "zone_logs": "timestamp",
        "campus_logs": "timestamp",
        "gate_history_logs": "timestamp",
        "anomaly_logs": "detected_at",
    }


def _cleanup_cutoff_for_action(action, days_to_keep=None):
    if action == "older_than":
        keep_days = int(days_to_keep or 0)
        if keep_days < 1:
            raise ValueError("days_to_keep must be at least 1")
        return (datetime.now(PH_TIMEZONE) - timedelta(days=keep_days)).strftime("%Y-%m-%d %H:%M:%S")
    if action == "all_history":
        return None
    raise ValueError("Unsupported cleanup action")


def estimate_cleanup_database_history(action, days_to_keep=None):
    history_timestamp_columns = {
        **_history_timestamp_columns(),
    }
    cutoff = _cleanup_cutoff_for_action(action, days_to_keep)
    estimated_counts = {}

    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            for table_name, timestamp_column in history_timestamp_columns.items():
                if action == "older_than":
                    c.execute(
                        f"SELECT COUNT(*) FROM {table_name} WHERE {timestamp_column} < ?",
                        (cutoff,),
                    )
                else:
                    c.execute(f"SELECT COUNT(*) FROM {table_name}")
                estimated_counts[table_name] = int(c.fetchone()[0])

    return {
        "estimated_counts": estimated_counts,
        "estimated_total": sum(estimated_counts.values()),
        "cutoff": cutoff,
    }


def cleanup_database_history(action, days_to_keep=None, vacuum=True):
    history_timestamp_columns = _history_timestamp_columns()
    deleted_counts = {}
    cutoff = _cleanup_cutoff_for_action(action, days_to_keep)

    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            for table_name, timestamp_column in history_timestamp_columns.items():
                if action == "older_than":
                    c.execute(
                        f"DELETE FROM {table_name} WHERE {timestamp_column} < ?",
                        (cutoff,),
                    )
                else:
                    c.execute(f"DELETE FROM {table_name}")
                deleted_counts[table_name] = c.rowcount if c.rowcount is not None else 0
            conn.commit()

        if vacuum:
            with sqlite3.connect(DB_NAME, timeout=60) as vacuum_conn:
                vacuum_conn.execute("VACUUM")

    return {
        "deleted_counts": deleted_counts,
        "deleted_total": sum(deleted_counts.values()),
        "cutoff": cutoff,
    }


def delete_gate_counter_state(camera_id, count_date=None):
    target_date = count_date or _ph_today_str()
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute(
                """
                DELETE FROM gate_counter_states
                WHERE camera_id = ?
                  AND count_date = ?
                """,
                (camera_id, target_date),
            )
            conn.commit()


def fetch_camera_rois():
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT camera_id, reference_image_path, roi_points, roi_closed
                FROM camera_rois
                """
            )
            rows = c.fetchall()

    rois = {}
    for camera_id, image_path, roi_points, roi_closed in rows:
        rois[camera_id] = {
            "camera_id": camera_id,
            "reference_image_path": image_path or "",
            "roi_points": json.loads(roi_points) if roi_points else [],
            "roi_closed": bool(roi_closed),
        }
    return rois


def fetch_camera_roi(camera_id):
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT camera_id, reference_image_path, roi_points, roi_closed
                FROM camera_rois
                WHERE camera_id = ?
                """,
                (camera_id,),
            )
            row = c.fetchone()

    if not row:
        return None

    return {
        "camera_id": row[0],
        "reference_image_path": row[1] or "",
        "roi_points": json.loads(row[2]) if row[2] else [],
        "roi_closed": bool(row[3]),
    }


def fetch_gate_config(camera_id):
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT camera_id, reference_image_path, roi_points, split_x, separator_points, direction, roi_closed, camera_role
                FROM gate_configs
                WHERE camera_id = ?
                """,
                (camera_id,),
            )
            row = c.fetchone()

    if not row:
        return None

    return {
        "camera_id": row[0],
        "reference_image_path": row[1] or "",
        "roi_points": json.loads(row[2]) if row[2] else [],
        "split_x": row[3],
        "separator_points": json.loads(row[4]) if row[4] else [],
        "direction": row[5] or "",
        "roi_closed": bool(row[6]),
        "camera_role": row[7] or "entrance",
    }


def upsert_gate_config(camera_id, reference_image_path, roi_points, split_x, separator_points, direction, camera_role, roi_closed=True):
    roi_points_json = json.dumps(roi_points or [])
    separator_points_json = json.dumps(separator_points or [])
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO gate_configs (camera_id, reference_image_path, roi_points, split_x, separator_points, direction, roi_closed, camera_role, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(camera_id) DO UPDATE SET
                    reference_image_path = excluded.reference_image_path,
                    roi_points = excluded.roi_points,
                    split_x = excluded.split_x,
                    separator_points = excluded.separator_points,
                    direction = excluded.direction,
                    roi_closed = excluded.roi_closed,
                    camera_role = excluded.camera_role,
                    updated_at = excluded.updated_at
                """,
                (camera_id, reference_image_path, roi_points_json, split_x, separator_points_json, direction, int(bool(roi_closed)), camera_role, _ph_now_str()),
            )
            conn.commit()


def upsert_camera_roi(camera_id, reference_image_path, roi_points, roi_closed=True):
    roi_points_json = json.dumps(roi_points or [])
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO camera_rois (camera_id, reference_image_path, roi_points, roi_closed, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(camera_id) DO UPDATE SET
                    reference_image_path = excluded.reference_image_path,
                    roi_points = excluded.roi_points,
                    roi_closed = excluded.roi_closed,
                    updated_at = excluded.updated_at
                """,
                (camera_id, reference_image_path, roi_points_json, int(bool(roi_closed)), _ph_now_str()),
            )
            conn.commit()


def delete_camera_roi(camera_id):
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM camera_rois WHERE camera_id = ?", (camera_id,))
            conn.commit()


def delete_gate_config(camera_id):
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM gate_configs WHERE camera_id = ?", (camera_id,))
            conn.commit()


def insert_camera(camera_id, camera_name, camera_url, camera_group, camera_floor=""):
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO cameras (id, name, url, camera_group, floor) VALUES (?, ?, ?, ?, ?)",
                (camera_id, camera_name, camera_url, camera_group, camera_floor),
            )
            conn.commit()


def delete_camera(camera_id):
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM camera_anomaly_rules WHERE camera_id = ?", (camera_id,))
            c.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
            conn.commit()


def update_camera(camera_id, clean_name, new_zone=None, new_floor=None):
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            if new_zone is not None:
                c.execute(
                    "UPDATE cameras SET name = ?, camera_group = ?, floor = ? WHERE id = ?",
                    (clean_name, new_zone, new_floor or "", camera_id),
                )
            else:
                c.execute(
                    "UPDATE cameras SET name = ?, floor = ? WHERE id = ?",
                    (clean_name, new_floor or "", camera_id),
                )
            conn.commit()


def insert_analytics(cameras_snapshot, zone_counts, total_campus, gate_summary=None):
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            timestamp = _ph_now_str()
            for cam_id, info in cameras_snapshot.items():
                if not info.get("is_active", False) or int(info.get("count", 0)) <= 0:
                    continue
                c.execute(
                    "INSERT INTO logs (camera_id, count, timestamp) VALUES (?, ?, ?)",
                    (cam_id, info["count"], timestamp),
                )
            for zone, count in zone_counts.items():
                if int(count or 0) <= 0:
                    continue
                c.execute(
                    "INSERT INTO zone_logs (zone_name, count, timestamp) VALUES (?, ?, ?)",
                    (zone, count, timestamp),
                )
            c.execute(
                "INSERT INTO campus_logs (total_count, timestamp) VALUES (?, ?)",
                (total_campus, timestamp),
            )
            gate_summary = gate_summary or {}
            c.execute(
                """
                INSERT INTO gate_history_logs (total_entered, total_exited, inside_total, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (
                    int(gate_summary.get("total_entered", 0)),
                    int(gate_summary.get("total_exited", 0)),
                    int(gate_summary.get("inside_total", 0)),
                    timestamp,
                ),
            )
            conn.commit()


def fetch_history(range_key="10h", start=None, end=None):
    range_map = {
        "10h": timedelta(hours=10),
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
    }
    start_dt = _parse_history_datetime(start)
    end_dt = _parse_history_datetime(end)

    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            if end_dt is None:
                if range_key == "all" and start_dt is None:
                    c.execute("SELECT MAX(timestamp) FROM gate_history_logs")
                    max_timestamp = c.fetchone()[0]
                    end_dt = _parse_db_timestamp(max_timestamp) if max_timestamp else datetime.now(PH_TIMEZONE).replace(tzinfo=None)
                else:
                    end_dt = datetime.now(PH_TIMEZONE).replace(tzinfo=None)

            if start_dt is None:
                if range_key in range_map:
                    start_dt = end_dt - range_map[range_key]
                elif range_key == "all":
                    c.execute("SELECT MIN(timestamp) FROM gate_history_logs")
                    min_timestamp = c.fetchone()[0]
                    start_dt = _parse_db_timestamp(min_timestamp) if min_timestamp else end_dt

            where_clauses = []
            params = []
            if start_dt is not None:
                where_clauses.append("timestamp >= ?")
                params.append(start_dt.strftime("%Y-%m-%d %H:%M:%S"))
            if end_dt is not None:
                where_clauses.append("timestamp <= ?")
                params.append(end_dt.strftime("%Y-%m-%d %H:%M:%S"))

            where_sql = ""
            if where_clauses:
                where_sql = "WHERE " + " AND ".join(where_clauses)

            bucket_mode, bucket_format = _history_bucket_strategy(start_dt, end_dt, range_key)

            if bucket_mode == "raw":
                c.execute(
                    f"""
                    SELECT timestamp, total_entered, total_exited, inside_total
                    FROM gate_history_logs
                    {where_sql}
                    ORDER BY timestamp ASC, id ASC
                    """,
                    params,
                )
                raw_rows = [
                    (row[0], int(row[1]), int(row[2]), int(row[3]))
                    for row in c.fetchall()
                ]
                return _fill_raw_history(raw_rows, start_dt, end_dt)

            c.execute(
                f"""
                WITH ranged_latest AS (
                    SELECT
                        strftime('{bucket_format}', timestamp) AS time_bucket,
                        MAX(id) AS latest_id
                    FROM gate_history_logs
                    {where_sql}
                    GROUP BY time_bucket
                )
                SELECT rl.time_bucket, ghl.total_entered, ghl.total_exited, ghl.inside_total
                FROM ranged_latest rl
                JOIN gate_history_logs ghl ON ghl.id = rl.latest_id
                ORDER BY rl.time_bucket ASC
                """,
                params,
            )
            bucketed_rows = [
                (row[0], int(row[1]), int(row[2]), int(row[3]))
                for row in c.fetchall()
            ]
            return _fill_bucketed_history(bucketed_rows, start_dt, end_dt, bucket_mode)


def insert_anomaly(camera_id, camera_name, zone_name, detected_count, message):
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute(
                """INSERT INTO anomaly_logs
                   (camera_id, camera_name, zone_name, detected_count, message, is_resolved, detected_at)
                   VALUES (?, ?, ?, ?, ?, 0, ?)""",
                (camera_id, camera_name, zone_name, detected_count, message, _ph_now_str()),
            )
            conn.commit()


def fetch_active_anomalies():
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT id, camera_id, camera_name, zone_name, detected_count, message, detected_at
                FROM anomaly_logs
                WHERE is_resolved = 0
                ORDER BY id DESC
                """
            )
            return c.fetchall()


def dismiss_anomaly(anomaly_id):
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute("UPDATE anomaly_logs SET is_resolved = 1 WHERE id = ?", (anomaly_id,))
            conn.commit()


def dismiss_all_anomalies():
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute("UPDATE anomaly_logs SET is_resolved = 1 WHERE is_resolved = 0")
            conn.commit()


def fetch_anomaly_history(limit=100):
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT id, camera_id, camera_name, zone_name, detected_count, message, is_resolved, detected_at
                FROM anomaly_logs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            return c.fetchall()


def fetch_recent_unresolved_anomalies(window_seconds):
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT id, camera_id, camera_name, zone_name, detected_count, message, detected_at
                FROM anomaly_logs
                WHERE is_resolved = 0
                  AND detected_at >= ?
                ORDER BY id DESC
                """,
                (_ph_cutoff_str(window_seconds),),
            )
            return c.fetchall()
