from datetime import timedelta

from flask import Flask

from camera_registry import CameraRegistry
from camera_streams import load_cameras_from_db, start_analytics_logger
from config import (
    LOGIN_PASSWORD,
    LOGIN_PASSWORD_IS_GENERATED,
    LOGIN_USERNAME,
    SECRET_KEY,
    SESSION_COOKIE_HTTPONLY,
    SESSION_COOKIE_SAMESITE,
    SESSION_COOKIE_SECURE,
    SESSION_TIMEOUT_MINUTES,
)
from database import init_db
from routes import register_routes


app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = SESSION_COOKIE_HTTPONLY
app.config["SESSION_COOKIE_SAMESITE"] = SESSION_COOKIE_SAMESITE
app.config["SESSION_COOKIE_SECURE"] = SESSION_COOKIE_SECURE
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=SESSION_TIMEOUT_MINUTES)


@app.after_request
def apply_security_headers(response):
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    return response

camera_registry = CameraRegistry()

init_db()
load_cameras_from_db(camera_registry)
start_analytics_logger(camera_registry)
register_routes(app, camera_registry)

if LOGIN_PASSWORD_IS_GENERATED:
    print("[SECURITY] No DT_WEBAPP_PASSWORD was set.")
    print(f"[SECURITY] Temporary login credentials: {LOGIN_USERNAME} / {LOGIN_PASSWORD}")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
