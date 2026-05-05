from flask import Flask
from flask_cors import CORS

from camera_registry import CameraRegistry
from camera_streams import load_cameras_from_db, start_analytics_logger, start_gate_day_reset_worker
from config import LOGIN_PASSWORD, LOGIN_PASSWORD_IS_GENERATED, LOGIN_USERNAME, SECRET_KEY
from database import init_db
from routes import register_routes


app = Flask(__name__)
CORS(app)
app.secret_key = SECRET_KEY

camera_registry = CameraRegistry()

init_db()
load_cameras_from_db(camera_registry)
start_analytics_logger(camera_registry)
start_gate_day_reset_worker(camera_registry)
register_routes(app, camera_registry)

if LOGIN_PASSWORD_IS_GENERATED:
    print("[SECURITY] No DT_WEBAPP_PASSWORD was set.")
    print(f"[SECURITY] Temporary login credentials: {LOGIN_USERNAME} / {LOGIN_PASSWORD}")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
