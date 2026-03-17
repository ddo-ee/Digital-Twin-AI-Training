from flask import Flask
from flask_cors import CORS

from camera_registry import CameraRegistry
from camera_streams import load_cameras_from_db, start_analytics_logger
from database import init_db
from routes import register_routes


app = Flask(__name__)
CORS(app)

camera_registry = CameraRegistry()

init_db()
load_cameras_from_db(camera_registry)
start_analytics_logger(camera_registry)
register_routes(app, camera_registry)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
