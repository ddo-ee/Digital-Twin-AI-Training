# Deployment Notes

## Code Structure

- `cctv_manager.py`: application entrypoint. Builds the Flask app, initializes the database, loads cameras, starts background analytics logging, and exposes the web server on `0.0.0.0:5000`.
- `routes.py`: web routes and JSON APIs for login, camera management, ROI/gate setup, live feeds, history, anomaly handling, and Unity-facing data.
- `camera_streams.py`: RTSP/local camera workers, YOLO inference, ROI counting, gate entry/exit tracking, and JPEG frame buffering for the browser.
- `camera_registry.py`: in-memory shared state for cameras, counts, activity flags, and gate totals.
- `database.py`: SQLite schema creation plus all data access for cameras, zones, analytics, ROI, gate configs, and anomalies.
- `anomalies.py`: restricted-hour anomaly rules and unread notification tracking.
- `config.py`: environment-driven secrets plus project constants such as model path, counting thresholds, zone lists, and timing settings.
- `templates/` and `static/`: browser UI.
- `coordinates/coordinates_extractor.py`: optional legacy helper for manually clicking ROI coordinates with OpenCV.

## What The Other Device Needs

- Python 3.10 or newer.
- The packages listed in `requirements.txt`.
- The selected model file from `config.py` present in the project root.
- Read/write access to:
  - `campus_security.db`
  - `static/uploads/gate_configs`
- Network reachability to the RTSP camera sources.
- A non-default `DT_WEBAPP_PASSWORD`.
- A stable `DT_SECRET_KEY`.

## Recommended Setup

```powershell
.\setup_deployment.ps1
setx DT_WEBAPP_PASSWORD "change-me"
setx DT_SECRET_KEY "replace-with-a-long-random-secret"
.\.venv\Scripts\python.exe deploy_check.py
.\.venv\Scripts\python.exe cctv_manager.py
```

Open `http://<device-ip>:5000`.

`setup_deployment.ps1` always creates or reuses the virtual environment in the folder where you run the script, so if you copy this project to another device and open PowerShell in that copied folder, the `.venv` will be created there.

Optional flags:

```powershell
.\setup_deployment.ps1 -VenvName env
.\setup_deployment.ps1 -SkipPipUpgrade
.\setup_deployment.ps1 -SkipCheck
```

## Deployment Checker

Run:

```powershell
python deploy_check.py
```

It checks:

- Python version
- Required Python imports
- Required project files
- Model file presence
- SQLite readability
- Upload folder writability
- Default secret/password warnings
- Optional CUDA / OpenCV-FFmpeg hints
