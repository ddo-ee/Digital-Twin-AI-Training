from cProfile import label
import sqlite3
import time
import cv2
import numpy as np
import threading
from threading import Lock
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

# --- THREADING GLOBALS ---
camera_threads = {}       
thread_run_flags = {}     
global_frame_buffer = {}  

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS cameras 
                     (id TEXT PRIMARY KEY, name TEXT, url TEXT, camera_group TEXT)''')
        # 1. Individual Camera Logs
        c.execute('''CREATE TABLE IF NOT EXISTS logs 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                      camera_id TEXT, count INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        # 2. Zone/Building Logs
        c.execute('''CREATE TABLE IF NOT EXISTS zone_logs 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                      zone_name TEXT, count INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        # 3. Total Campus Logs
        c.execute('''CREATE TABLE IF NOT EXISTS campus_logs 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                      total_count INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        conn.commit()

# --- THE MASTER ANALYTICS LOGGER ---
# This runs once every 5 seconds to calculate and save all totals safely.
def analytics_logger():
    while True:
        time.sleep(5) # Save data every 5 seconds
        
        if not active_cameras:
            continue # Skip if no cameras are active

        total_campus = 0
        zone_counts = {}

        # 1. Tally up the current numbers
        for cam_id, info in active_cameras.items():
            count = info['count']
            group = info['group']
            
            total_campus += count
            if group not in zone_counts:
                zone_counts[group] = 0
            zone_counts[group] += count

        # 2. Save everything to the database in one clean sweep
        try:
            with sqlite3.connect(DB_NAME) as conn:
                c = conn.cursor()
                
                # Save Individual Cameras
                for cam_id, info in active_cameras.items():
                    c.execute("INSERT INTO logs (camera_id, count) VALUES (?, ?)", (cam_id, info['count']))
                
                # Save Zone Totals
                for zone, count in zone_counts.items():
                    c.execute("INSERT INTO zone_logs (zone_name, count) VALUES (?, ?)", (zone, count))
                
                # Save Campus Total
                c.execute("INSERT INTO campus_logs (total_count) VALUES (?)", (total_campus,))
                
                conn.commit()
        except Exception as e:
            print(f"Database write error: {e}")

# Start the Master Logger in the background
threading.Thread(target=analytics_logger, daemon=True).start()


# --- THE BACKGROUND AI WORKER ---

def camera_worker(camera_id, source):
    cap = None
    frame_counter = 0
    
    while thread_run_flags.get(camera_id, False):
        
        # 1. THE MANUAL TOGGLE CHECK
        if not active_cameras.get(camera_id, {}).get('is_active', False):
            if cap is not None:
                cap.release() # Drop the network connection!
                cap = None
            
            # Draw a paused screen so the UI doesn't break
            offline_frame = np.zeros((360, 640, 3), dtype=np.uint8)
            text = "SYSTEM PAUSED"
            font = cv2.FONT_HERSHEY_SIMPLEX
            text_size = cv2.getTextSize(text, font, 1, 1)[0]
            cv2.putText(offline_frame, text, ((640 - text_size[0]) // 2, (360 + text_size[1]) // 2), font, 1, (100, 100, 100), 2)
            ret, buffer = cv2.imencode('.jpg', offline_frame)
            if ret: global_frame_buffer[camera_id] = buffer.tobytes()
            
            time.sleep(1) # Sleep peacefully without hurting the router
            continue

        # 2. IF ACTIVE, CONNECT TO THE CAMERA
        if cap is None:
            cap = cv2.VideoCapture(source if source != '0' else 0)
            
        success, frame = cap.read()
        
        # --- THE "OFFLINE" CATCHER ---
        if not success:
            offline_frame = np.zeros((360, 640, 3), dtype=np.uint8)
            text = "CAMERA OFFLINE"
            font = cv2.FONT_HERSHEY_SIMPLEX
            text_size = cv2.getTextSize(text, font, 1, 1)[0]
            cv2.putText(offline_frame, text, ((640 - text_size[0]) // 2, (360 + text_size[1]) // 2), font, 1, (0, 0, 255), 1)
            ret, buffer = cv2.imencode('.jpg', offline_frame)
            if ret: global_frame_buffer[camera_id] = buffer.tobytes()
            time.sleep(5)
            cap.open(source if source != '0' else 0)
            continue
        
        frame_counter += 1
        
        # 3. RUN YOLO AI
        if frame_counter % 5 == 0:
            frame = cv2.resize(frame, (640, 360)) 
            with model_lock:
                results = model(frame, conf=0.4, imgsz=640, verbose=False)
            
            annotated_frame = results[0].plot(labels=False)
            person_count = len(results[0].boxes)

            if camera_id in active_cameras:
                active_cameras[camera_id]['count'] = person_count
                active_cameras[camera_id]['last_updated'] = time.time()
            
            ret, buffer = cv2.imencode('.jpg', annotated_frame)
            if ret: global_frame_buffer[camera_id] = buffer.tobytes()
        
        time.sleep(0.01) 
        
    if cap is not None: cap.release()

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
            # THIS IS THE MISSING LINE: Unpack the variables from the database!
            cam_id, name, url, group = row 
            
            # Now Python knows what 'name' is and can save it:
            active_cameras[cam_id] = {"name": name, "url": url, "group": group, "count": 0, "is_active": False, "last_updated": time.time()}
            start_camera_thread(cam_id, url)
            
        print(f"Loaded {len(rows)} cameras.")

init_db()
load_cameras_from_db()

# --- THE WEB API ---
def generate_frames(camera_id):
    while True:
        frame_bytes = global_frame_buffer.get(camera_id)
        if frame_bytes:
            yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        time.sleep(0.1) 

@app.route('/')
def index():
    return render_template('index.html', cameras=active_cameras)

@app.route('/video_feed/<camera_id>')
def video_feed(camera_id):
    return Response(generate_frames(camera_id), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/add_camera', methods=['POST'])
def add_camera():
    camera_name = request.form.get('camera_name')
    camera_url = request.form.get('camera_url')
    camera_group = request.form.get('camera_group')
    
    new_id = f"cam_{int(time.time())}"
    
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO cameras (id, name, url, camera_group) VALUES (?, ?, ?, ?)", 
                  (new_id, camera_name, camera_url, camera_group))
        conn.commit()

    # ---> ADDED "is_active": False RIGHT HERE <---
    active_cameras[new_id] = { "name": camera_name, "url": camera_url, "group": camera_group, "count": 0, "is_active": False }
    
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

@app.route('/api/stats')
def get_stats():
    data = { cid: {
        "name": i['name'], 
        "count": i['count'], 
        "group": i['group'], 
        "is_active": i.get('is_active', False),
        "last_updated": i.get('last_updated', time.time()) # NEW: Send time to the browser
    } for cid, i in active_cameras.items() }
    return jsonify(data)

@app.route('/api/toggle_group/<group_name>', methods=['POST'])
def toggle_group(group_name):
    data = request.json
    action = data.get('action') # Will be 'start' or 'stop'
    
    # Loop through all cameras and flip the switch if they belong to this building
    for cam_id, info in active_cameras.items():
        if info['group'] == group_name:
            info['is_active'] = (action == 'start')
            
    return jsonify({"status": "success", "zone": group_name, "action": action})

@app.route('/api/toggle_all', methods=['POST'])
def toggle_all():
    data = request.json
    action = data.get('action') # Will be 'start' or 'stop'
    
    # Loop through EVERY camera and flip the switch
    for cam_id, info in active_cameras.items():
        info['is_active'] = (action == 'start')
            
    return jsonify({"status": "success", "action": action})

@app.route('/api/toggle/<camera_id>', methods=['POST'])
def toggle_camera(camera_id):
    if camera_id in active_cameras:
        current_state = active_cameras[camera_id].get('is_active', False)
        active_cameras[camera_id]['is_active'] = not current_state # Flip the switch
        return jsonify({"status": "success", "is_active": active_cameras[camera_id]['is_active']})
    return jsonify({"status": "error"})

@app.route('/api/history')
def get_history():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        
        # 1. Get the last 20 overall campus totals
        c.execute("SELECT timestamp, total_count FROM campus_logs ORDER BY id DESC LIMIT 20")
        campus_data = c.fetchall()[::-1] # Reverse list so oldest is on the left of the chart
        
        # 2. Get the recent zone data
        c.execute("SELECT timestamp, zone_name, count FROM zone_logs ORDER BY id DESC LIMIT 200")
        zone_data = c.fetchall()[::-1]

    # Format the time labels (extract just the HH:MM:SS part)
    labels = [row[0].split(" ")[1] for row in campus_data]
    campus_counts = [row[1] for row in campus_data]
    
    # Group the zone data dynamically
    zones = {}
    for row in zone_data:
        z_name = row[1]
        z_count = row[2]
        if z_name not in zones:
            zones[z_name] = []
        zones[z_name].append(z_count)
        # Keep zone arrays the same length as labels (last 20 entries)
        if len(zones[z_name]) > 20:
            zones[z_name] = zones[z_name][-20:]

    return jsonify({
        "labels": labels,
        "campus": campus_counts,
        "zones": zones
    })
# ==========================================
# --- DORMANT UNITY 3D INTEGRATION API ---
# ==========================================
@app.route('/api/unity')
def get_unity_data():
    now = time.time()
    unity_payload = {
        "campus": { "total_known_people": 0, "active_live_people": 0, "is_fully_live": True },
        "zones": {},
        "cameras": {}
    }
    
    for cam_id, info in active_cameras.items():
        count = info['count']
        group = info['group']
        is_active = info.get('is_active', False)
        last_updated = info.get('last_updated', now)
        
        # 1. Package Cameras
        unity_payload["cameras"][cam_id] = {
            "name": info['name'],
            "zone": group,
            "count": count,
            "is_active": is_active,
            "seconds_since_update": round(now - last_updated, 1)
        }
        
        # 2. Package Zones
        if group not in unity_payload["zones"]:
            unity_payload["zones"][group] = { "total_count": 0, "is_active": False, "seconds_since_update": 0 }
        
        unity_payload["zones"][group]["total_count"] += count
        if is_active:
            unity_payload["zones"][group]["is_active"] = True
            unity_payload["zones"][group]["seconds_since_update"] = 0
        elif (now - last_updated) < unity_payload["zones"][group].get("seconds_since_update", 999999):
            unity_payload["zones"][group]["seconds_since_update"] = round(now - last_updated, 1)
            
        # 3. Package Campus Totals
        unity_payload["campus"]["total_known_people"] += count
        if is_active:
            unity_payload["campus"]["active_live_people"] += count
        else:
            unity_payload["campus"]["is_fully_live"] = False
            
    return jsonify(unity_payload)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, threaded=True)