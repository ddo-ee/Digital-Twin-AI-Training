import sqlite3
import time
import cv2
import threading
from flask import Flask, render_template, Response, request, jsonify, redirect, url_for
from ultralytics import YOLO

app = Flask(__name__)

# Load Model
model = YOLO("best.pt") 

DB_NAME = "campus_security.db"
active_cameras = {}

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        # Updated table with 'camera_group'
        c.execute('''CREATE TABLE IF NOT EXISTS cameras 
                     (id TEXT PRIMARY KEY, name TEXT, url TEXT, camera_group TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS logs 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                      camera_id TEXT, count INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        conn.commit()

def load_cameras_from_db():
    global active_cameras
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT id, name, url, camera_group FROM cameras")
        rows = c.fetchall()
        for row in rows:
            active_cameras[row[0]] = {
                "name": row[1],
                "url": row[2],
                "group": row[3], # New Field
                "count": 0
            }
        print(f"Loaded {len(rows)} cameras.")

init_db()
load_cameras_from_db()

def generate_frames(camera_id):
    cam_info = active_cameras.get(camera_id)
    if not cam_info: return

    source = cam_info['url']
    if source == '0': source = 0 
    
    cap = cv2.VideoCapture(source)
    while True:
        success, frame = cap.read()
        if not success:
            time.sleep(1)
            cap.open(source)
            continue
        
        results = model(frame, conf=0.5, verbose=False)
        annotated_frame = results[0].plot()
        
        person_count = len([r for r in results[0].boxes.cls if model.names[int(r)] == 'Person'])
        
        if camera_id in active_cameras:
            active_cameras[camera_id]['count'] = person_count
        
        ret, buffer = cv2.imencode('.jpg', annotated_frame)
        yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

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
    camera_group = request.form.get('camera_group') # New Input
    
    new_id = f"cam_{int(time.time())}"
    
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO cameras (id, name, url, camera_group) VALUES (?, ?, ?, ?)", 
                  (new_id, camera_name, camera_url, camera_group))
        conn.commit()

    active_cameras[new_id] = {
        "name": camera_name, "url": camera_url, "group": camera_group, "count": 0
    }
    return redirect(url_for('index'))

@app.route('/remove_camera/<camera_id>')
def remove_camera(camera_id):
    if camera_id in active_cameras:
        del active_cameras[camera_id]
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
            conn.commit()
    return redirect(url_for('index'))

@app.route('/api/stats')
def get_stats():
    # Return data including groups
    data = { cid: {"name": i['name'], "count": i['count'], "group": i['group']} for cid, i in active_cameras.items() }
    return jsonify(data)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, threaded=True)