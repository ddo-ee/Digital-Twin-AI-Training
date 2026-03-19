import sqlite3
from datetime import datetime, timedelta, timezone
from threading import Lock

from config import DB_NAME, DEFAULT_ZONES

db_lock = Lock()
PH_TIMEZONE = timezone(timedelta(hours=8))


def _ph_now_str():
    return datetime.now(PH_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def _ph_cutoff_str(window_seconds):
    return (datetime.now(PH_TIMEZONE) - timedelta(seconds=window_seconds)).strftime("%Y-%m-%d %H:%M:%S")


def init_db():
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute("PRAGMA journal_mode=WAL;")
            c.execute("PRAGMA synchronous=NORMAL;")

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
            c.execute("""CREATE TABLE IF NOT EXISTS zones (name TEXT PRIMARY KEY)""")

            c.execute("PRAGMA table_info(cameras)")
            camera_columns = {row[1] for row in c.fetchall()}
            if "floor" not in camera_columns:
                c.execute("ALTER TABLE cameras ADD COLUMN floor TEXT")

            c.execute("PRAGMA table_info(anomaly_logs)")
            anomaly_columns = {row[1] for row in c.fetchall()}
            if "detected_count" not in anomaly_columns:
                c.execute("ALTER TABLE anomaly_logs ADD COLUMN detected_count INTEGER DEFAULT 0")

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


def insert_analytics(cameras_snapshot, zone_counts, total_campus):
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            timestamp = _ph_now_str()
            for cam_id, info in cameras_snapshot.items():
                c.execute(
                    "INSERT INTO logs (camera_id, count, timestamp) VALUES (?, ?, ?)",
                    (cam_id, info["count"], timestamp),
                )
            for zone, count in zone_counts.items():
                c.execute(
                    "INSERT INTO zone_logs (zone_name, count, timestamp) VALUES (?, ?, ?)",
                    (zone, count, timestamp),
                )
            c.execute(
                "INSERT INTO campus_logs (total_count, timestamp) VALUES (?, ?)",
                (total_campus, timestamp),
            )
            conn.commit()


def fetch_history():
    with db_lock:
        with sqlite3.connect(DB_NAME, timeout=15) as conn:
            c = conn.cursor()
            c.execute("SELECT timestamp, total_count FROM campus_logs ORDER BY id DESC LIMIT 20")
            campus_data = c.fetchall()[::-1]
            c.execute("SELECT timestamp, zone_name, count FROM zone_logs ORDER BY id DESC LIMIT 200")
            zone_data = c.fetchall()[::-1]
    return campus_data, zone_data


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
