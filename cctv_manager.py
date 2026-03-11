from cProfile import label
import sqlite3
import time
import cv2
import numpy as np
import threading
from threading import Lock
from datetime import datetime
from flask import Flask, render_template, Response, request, jsonify, redirect, url_for
from flask_cors import CORS
from ultralytics import YOLO
import os
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;2000000"

app = Flask(__name__)
CORS(app)

# --- CONFIGURATION ---
DB_NAME = "campus_security.db"
model = YOLO("yolov11_v2.engine") # Or yolov8n.pt
model_lock = Lock()

active_cameras = {}

# --- ANOMALY CONFIGURATION ---
# Groups that are restricted. Gate is intentionally NOT included.
RESTRICTED_GROUPS = [
    "Albert Einstein Building",
    "CET Building",
    "CICS Building",
    "Parking",
    "Pathways",
    "RGR Building",
    "Student Center"
]

# Friendly notification messages per group
ANOMALY_MESSAGES = {
    "Albert Einstein Building": "Unauthorized Access to Albert Einstein Building",
    "CET Building":             "Unauthorized Access to CET Building",
    "CICS Building":            "Unauthorized Access to CICS Building",
    "Parking":                  "Unauthorized Access to Parking Area",
    "Pathways":                 "Unauthorized Access to Pathways",
    "RGR Building":             "Unauthorized Access to RGR Building",
    "Student Center":           "Unauthorized Access to Student Center",
}

# Schedule: (weekday_restricted_hour, weekend_restricted_hour) in 24h format
WEEKDAY_RESTRICTED_HOUR = 21   # After 9:00 PM
WEEKEND_RESTRICTED_HOUR = 17   # After 5:00 PM

# Cooldown: seconds before the same camera can re-trigger an alert
ANOMALY_COOLDOWN_SECONDS = 30

# Unity: anomalies window in seconds (5 minutes)
UNITY_ANOMALY_WINDOW_SECONDS = 5 * 30

# Per-camera cooldown tracker { camera_id: last_alert_unix_timestamp }
anomaly_cooldowns = {}
anomaly_cooldowns_lock = Lock()

# In-memory unread count for the dashboard bell badge
unread_anomaly_count = 0
unread_lock = Lock()

# --- THREADING GLOBALS ---
camera_threads = {}
thread_run_flags = {}
global_frame_buffer = {}


# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

def is_restricted_now():
    """Return True if the current time is inside a restricted window."""
    now = datetime.now()
    weekday = now.weekday()          # 0=Monday … 6=Sunday
    hour    = now.hour

    if weekday < 5:                  # Monday–Friday
        return hour >= WEEKDAY_RESTRICTED_HOUR
    else:                            # Saturday–Sunday
        return hour >= WEEKEND_RESTRICTED_HOUR


def check_and_fire_anomaly(camera_id, camera_name, group, person_count):
    """
    Called every time AI detects ≥1 person on a restricted camera.
    Respects the 60-second per-camera cooldown.
    """
    global unread_anomaly_count

    if group not in RESTRICTED_GROUPS:
        return
    if not is_restricted_now():
        return
    if person_count < 1:
        return

    now_ts = time.time()

    with anomaly_cooldowns_lock:
        last_fired = anomaly_cooldowns.get(camera_id, 0)
        if now_ts - last_fired < ANOMALY_COOLDOWN_SECONDS:
            return                   # Still in cooldown — skip
        anomaly_cooldowns[camera_id] = now_ts

    message = ANOMALY_MESSAGES.get(group, f"Unauthorized Access to {group}")

    # Save to DB
    try:
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute(
                """INSERT INTO anomaly_logs
                   (camera_id, camera_name, zone_name, message, is_resolved)
                   VALUES (?, ?, ?, ?, 0)""",
                (camera_id, camera_name, group, message)
            )
            conn.commit()
    except Exception as e:
        print(f"[ANOMALY DB ERROR] {e}")

    with unread_lock:
        unread_anomaly_count += 1

    print(f"[ANOMALY] {message} | Camera: {camera_name} ({camera_id}) | Count: {person_count}")


def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        c.execute('''CREATE TABLE IF NOT EXISTS cameras
                     (id TEXT PRIMARY KEY, name TEXT, url TEXT, camera_group TEXT)''')

        c.execute('''CREATE TABLE IF NOT EXISTS logs
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      camera_id TEXT, count INTEGER,
                      timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')

        c.execute('''CREATE TABLE IF NOT EXISTS zone_logs
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      zone_name TEXT, count INTEGER,
                      timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')

        c.execute('''CREATE TABLE IF NOT EXISTS campus_logs
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      total_count INTEGER,
                      timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')

        # NEW: Anomaly / unauthorized-access log
        c.execute('''CREATE TABLE IF NOT EXISTS anomaly_logs
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      camera_id   TEXT,
                      camera_name TEXT,
                      zone_name   TEXT,
                      message     TEXT,
                      is_resolved INTEGER DEFAULT 0,
                      detected_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')

        conn.commit()


def load_poly_from_txt(filename):
    pts = []
    if not os.path.exists(filename):
        return None
    with open(filename, 'r') as f:
        for line in f:
            if line.strip():
                x, y = line.strip().split(',')
                pts.append([int(x), int(y)])
    return np.array(pts, np.float32)


# ─────────────────────────────────────────────
#  BACKGROUND THREADS
# ─────────────────────────────────────────────

def analytics_logger():
    while True:
        time.sleep(5)

        if not active_cameras:
            continue

        total_campus = 0
        zone_counts  = {}

        for cam_id, info in active_cameras.items():
            count = info['count']
            group = info['group']
            total_campus += count
            zone_counts.setdefault(group, 0)
            zone_counts[group] += count

        try:
            with sqlite3.connect(DB_NAME) as conn:
                c = conn.cursor()
                for cam_id, info in active_cameras.items():
                    c.execute("INSERT INTO logs (camera_id, count) VALUES (?, ?)",
                              (cam_id, info['count']))
                for zone, count in zone_counts.items():
                    c.execute("INSERT INTO zone_logs (zone_name, count) VALUES (?, ?)",
                              (zone, count))
                c.execute("INSERT INTO campus_logs (total_count) VALUES (?)", (total_campus,))
                conn.commit()
        except Exception as e:
            print(f"Database write error: {e}")


threading.Thread(target=analytics_logger, daemon=True).start()


def camera_worker(camera_id, source):
    cap           = None
    frame_counter = 0

    poly_filename    = os.path.join("coordinates", f"{camera_id}_coords.txt")
    raw_clicked_poly = load_poly_from_txt(poly_filename)

    CLICKED_RES_W = 1920
    CLICKED_RES_H = 1080
    scaled_poly   = None

    if raw_clicked_poly is not None:
        scale_w     = 640 / CLICKED_RES_W
        scale_h     = 360 / CLICKED_RES_H
        scaled_poly = (raw_clicked_poly * [scale_w, scale_h]).astype(np.int32)

    while thread_run_flags.get(camera_id, False):

        # 1. Manual toggle check
        if not active_cameras.get(camera_id, {}).get('is_active', False):
            if cap is not None:
                cap.release()
                cap = None

            offline_frame = np.zeros((360, 640, 3), dtype=np.uint8)
            text      = "SYSTEM PAUSED"
            font      = cv2.FONT_HERSHEY_SIMPLEX
            text_size = cv2.getTextSize(text, font, 1, 1)[0]
            cv2.putText(offline_frame, text,
                        ((640 - text_size[0]) // 2, (360 + text_size[1]) // 2),
                        font, 1, (100, 100, 100), 2)
            ret, buffer = cv2.imencode('.jpg', offline_frame)
            if ret:
                global_frame_buffer[camera_id] = buffer.tobytes()
            time.sleep(1)
            continue

        # 2. Connect to camera
        if cap is None:
            cap = cv2.VideoCapture(source if source != '0' else 0)

        success, frame = cap.read()

        if not success:
            offline_frame = np.zeros((360, 640, 3), dtype=np.uint8)
            text      = "CAMERA OFFLINE"
            font      = cv2.FONT_HERSHEY_SIMPLEX
            text_size = cv2.getTextSize(text, font, 1, 1)[0]
            cv2.putText(offline_frame, text,
                        ((640 - text_size[0]) // 2, (360 + text_size[1]) // 2),
                        font, 1, (0, 0, 255), 1)
            ret, buffer = cv2.imencode('.jpg', offline_frame)
            if ret:
                global_frame_buffer[camera_id] = buffer.tobytes()
            time.sleep(5)
            cap.open(source if source != '0' else 0)
            continue

        frame_counter += 1

        # 3. Run YOLO AI every 5 frames
        if frame_counter % 5 == 0:
            frame = cv2.resize(frame, (640, 360))
            with model_lock:
                results = model(frame, conf=0.65, imgsz=640, verbose=False)

            annotated_frame = frame.copy()
            person_count    = 0

            for box in results[0].boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                foot_x = int((x1 + x2) / 2)
                foot_y = int(y2)

                if scaled_poly is not None:
                    is_inside = cv2.pointPolygonTest(scaled_poly, (foot_x, foot_y), False)
                    if is_inside >= 0:
                        person_count += 1
                        cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                        cv2.circle(annotated_frame, (foot_x, foot_y), 5, (0, 0, 255), -1)
                else:
                    person_count += 1
                    cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)

            if scaled_poly is not None:
                cv2.polylines(annotated_frame, [scaled_poly],
                              isClosed=True, color=(0, 255, 255), thickness=2)

            # ── ANOMALY CHECK (new) ──────────────────────────────────────
            if person_count > 0:
                cam_info = active_cameras.get(camera_id, {})
                check_and_fire_anomaly(
                    camera_id,
                    cam_info.get('name', camera_id),
                    cam_info.get('group', ''),
                    person_count
                )
            # ────────────────────────────────────────────────────────────

            if camera_id in active_cameras:
                active_cameras[camera_id]['count']        = person_count
                active_cameras[camera_id]['last_updated'] = time.time()

            ret, buffer = cv2.imencode('.jpg', annotated_frame)
            if ret:
                global_frame_buffer[camera_id] = buffer.tobytes()

        time.sleep(0.01)

    if cap is not None:
        cap.release()


def start_camera_thread(camera_id, url):
    if camera_id not in camera_threads:
        thread_run_flags[camera_id] = True
        t = threading.Thread(target=camera_worker, args=(camera_id, url))
        t.daemon = True
        t.start()
        camera_threads[camera_id] = t


def stop_camera_thread(camera_id):
    thread_run_flags[camera_id] = False
    if camera_id in camera_threads:
        del camera_threads[camera_id]


def load_cameras_from_db():
    global active_cameras
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT id, name, url, camera_group FROM cameras")
        rows = c.fetchall()
        for row in rows:
            cam_id, name, url, group = row
            active_cameras[cam_id] = {
                "name":         name,
                "url":          url,
                "group":        group,
                "count":        0,
                "is_active":    False,
                "last_updated": time.time()
            }
            start_camera_thread(cam_id, url)
        print(f"Loaded {len(rows)} cameras.")


init_db()
load_cameras_from_db()


# ─────────────────────────────────────────────
#  WEB STREAMING
# ─────────────────────────────────────────────

def generate_frames(camera_id):
    while True:
        frame_bytes = global_frame_buffer.get(camera_id)
        if frame_bytes:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        time.sleep(0.1)


# ─────────────────────────────────────────────
#  FLASK ROUTES — EXISTING
# ─────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html', cameras=active_cameras)


@app.route('/video_feed/<camera_id>')
def video_feed(camera_id):
    return Response(generate_frames(camera_id),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/add_camera', methods=['POST'])
def add_camera():
    camera_name  = request.form.get('camera_name')
    camera_url   = request.form.get('camera_url')
    camera_group = request.form.get('camera_group')
    new_id       = f"cam_{int(time.time())}"

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO cameras (id, name, url, camera_group) VALUES (?, ?, ?, ?)",
                  (new_id, camera_name, camera_url, camera_group))
        conn.commit()

    active_cameras[new_id] = {
        "name":      camera_name,
        "url":       camera_url,
        "group":     camera_group,
        "count":     0,
        "is_active": False
    }
    start_camera_thread(new_id, camera_url)
    return redirect(url_for('index'))


@app.route('/remove_camera/<camera_id>')
def remove_camera(camera_id):
    if camera_id in active_cameras:
        stop_camera_thread(camera_id)
        del active_cameras[camera_id]
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
            conn.commit()
    return redirect(url_for('index'))

@app.route('/api/rename_camera/<camera_id>', methods=['POST'])
def rename_camera(camera_id):
    new_name = request.json.get('new_name')
    
    if not new_name or not new_name.strip():
        return jsonify({"status": "error", "message": "Name cannot be empty"}), 400
        
    if camera_id in active_cameras:
        clean_name = new_name.strip()
        # 1. Update active memory
        active_cameras[camera_id]['name'] = clean_name
        
        # 2. Update the SQLite Database
        try:
            with sqlite3.connect(DB_NAME) as conn:
                c = conn.cursor()
                c.execute("UPDATE cameras SET name = ? WHERE id = ?", (clean_name, camera_id))
                conn.commit()
            return jsonify({"status": "success", "new_name": clean_name})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
            
    return jsonify({"status": "error", "message": "Camera not found"}), 404

@app.route('/api/stats')
def get_stats():
    data = {
        cid: {
            "name":         i['name'],
            "count":        i['count'],
            "group":        i['group'],
            "is_active":    i.get('is_active', False),
            "last_updated": i.get('last_updated', time.time())
        }
        for cid, i in active_cameras.items()
    }
    return jsonify(data)


@app.route('/api/toggle_group/<group_name>', methods=['POST'])
def toggle_group(group_name):
    action = request.json.get('action')
    for cam_id, info in active_cameras.items():
        if info['group'] == group_name:
            info['is_active'] = (action == 'start')
    return jsonify({"status": "success", "zone": group_name, "action": action})


@app.route('/api/toggle_all', methods=['POST'])
def toggle_all():
    action = request.json.get('action')
    for cam_id, info in active_cameras.items():
        info['is_active'] = (action == 'start')
    return jsonify({"status": "success", "action": action})


@app.route('/api/toggle/<camera_id>', methods=['POST'])
def toggle_camera(camera_id):
    if camera_id in active_cameras:
        current_state = active_cameras[camera_id].get('is_active', False)
        active_cameras[camera_id]['is_active'] = not current_state
        return jsonify({"status": "success",
                        "is_active": active_cameras[camera_id]['is_active']})
    return jsonify({"status": "error"})


@app.route('/api/history')
def get_history():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT timestamp, total_count FROM campus_logs ORDER BY id DESC LIMIT 20")
        campus_data = c.fetchall()[::-1]

        c.execute("SELECT timestamp, zone_name, count FROM zone_logs ORDER BY id DESC LIMIT 200")
        zone_data = c.fetchall()[::-1]

    labels        = [row[0].split(" ")[1] for row in campus_data]
    campus_counts = [row[1] for row in campus_data]

    zones = {}
    for row in zone_data:
        z_name, z_count = row[1], row[2]
        zones.setdefault(z_name, [])
        zones[z_name].append(z_count)
        if len(zones[z_name]) > 20:
            zones[z_name] = zones[z_name][-20:]

    return jsonify({
        "labels":          labels,
        "campus_overview": campus_counts,
        "zones":           zones
    })


# ─────────────────────────────────────────────
#  FLASK ROUTES — ANOMALY / NOTIFICATION (NEW)
# ─────────────────────────────────────────────

@app.route('/api/anomalies')
def get_anomalies():
    """
    Returns all unresolved anomaly alerts for the web dashboard.
    Also resets the unread badge count once the panel is opened.
    """
    global unread_anomaly_count

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT id, camera_id, camera_name, zone_name, message, detected_at
            FROM anomaly_logs
            WHERE is_resolved = 0
            ORDER BY id DESC
        """)
        rows = c.fetchall()

    alerts = [
        {
            "id":          row[0],
            "camera_id":   row[1],
            "camera_name": row[2],
            "zone_name":   row[3],
            "message":     row[4],
            "detected_at": row[5]
        }
        for row in rows
    ]

    # Reset unread badge now that the user has opened the panel
    with unread_lock:
        unread_anomaly_count = 0

    return jsonify({"alerts": alerts, "total": len(alerts)})


@app.route('/api/anomalies/unread_count')
def get_unread_count():
    """
    Lightweight poll endpoint for the bell badge.
    The dashboard polls this every 5 seconds.
    """
    with unread_lock:
        count = unread_anomaly_count
    return jsonify({"unread": count})


@app.route('/api/anomalies/dismiss/<int:anomaly_id>', methods=['POST'])
def dismiss_anomaly(anomaly_id):
    """Mark a single alert as resolved (keeps history)."""
    try:
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("UPDATE anomaly_logs SET is_resolved = 1 WHERE id = ?",
                      (anomaly_id,))
            conn.commit()
        return jsonify({"status": "success", "id": anomaly_id})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/anomalies/dismiss_all', methods=['POST'])
def dismiss_all_anomalies():
    """Mark ALL unresolved alerts as resolved."""
    try:
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("UPDATE anomaly_logs SET is_resolved = 1 WHERE is_resolved = 0")
            conn.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/anomalies/history')
def get_anomaly_history():
    """Returns ALL anomalies (resolved + unresolved) for a history log view."""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("""
            SELECT id, camera_id, camera_name, zone_name, message, is_resolved, detected_at
            FROM anomaly_logs
            ORDER BY id DESC
            LIMIT 100
        """)
        rows = c.fetchall()

    alerts = [
        {
            "id":          row[0],
            "camera_id":   row[1],
            "camera_name": row[2],
            "zone_name":   row[3],
            "message":     row[4],
            "is_resolved": bool(row[5]),
            "detected_at": row[6]
        }
        for row in rows
    ]
    return jsonify({"alerts": alerts, "total": len(alerts)})


# ─────────────────────────────────────────────
#  UNITY API (UPDATED)
# ─────────────────────────────────────────────

@app.route('/api/unity')
def get_unity_data():
    unity_payload = {
        "campus_overview": {
            "active_live_people":  0,
            "total_known_people":  0
        },
        "zones_list":       [],
        "cameras":          [],
        "recent_anomalies": []      # NEW: last-5-min unauthorized access alerts
    }

    zones_temp = {}

    for cam_id, info in active_cameras.items():
        count     = info['count']
        group     = info['group']
        is_active = info.get('is_active', False)

        unity_payload["campus_overview"]["total_known_people"] += count
        if is_active:
            unity_payload["campus_overview"]["active_live_people"] += count

        zones_temp.setdefault(group, {
            "name":        group,
            "total_count": 0,
            "is_active":   False,
            "has_anomaly": False   # NEW: flag so Unity can highlight the building
        })
        zones_temp[group]["total_count"] += count
        if is_active:
            zones_temp[group]["is_active"] = True

        unity_payload["cameras"].append({
            "id":        cam_id,
            "name":      info['name'],
            "zone":      group,
            "count":     count,
            "is_active": is_active
        })

    # Fetch recent anomalies (last 5 minutes, unresolved only)
    cutoff_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("""
                SELECT id, camera_id, camera_name, zone_name, message, detected_at
                FROM anomaly_logs
                WHERE is_resolved = 0
                  AND detected_at >= datetime('now', ?)
                ORDER BY id DESC
            """, (f"-{UNITY_ANOMALY_WINDOW_SECONDS} seconds",))
            anomaly_rows = c.fetchall()

        for row in anomaly_rows:
            zone_name = row[3]
            unity_payload["recent_anomalies"].append({
                "id":          row[0],
                "camera_id":   row[1],
                "camera_name": row[2],
                "zone_name":   zone_name,
                "message":     row[4],
                "detected_at": row[5]
            })
            # Flag the zone so Unity can highlight it red
            if zone_name in zones_temp:
                zones_temp[zone_name]["has_anomaly"] = True

    except Exception as e:
        print(f"[UNITY ANOMALY FETCH ERROR] {e}")

    unity_payload["zones_list"] = list(zones_temp.values())
    return jsonify(unity_payload)


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, threaded=True)