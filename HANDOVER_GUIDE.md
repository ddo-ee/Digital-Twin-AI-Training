# APOLLO Digital Twin AI Monitoring - Handover Guide

## 1. Program Overview

APOLLO is a Flask-based CCTV monitoring system for BatStateU-style campus security workflows. It connects to RTSP streams or a local camera source, runs YOLO person detection, counts people inside configured camera ROIs, tracks university gate entry/exit totals, records analytics to SQLite, and displays live monitoring, historical charts, anomaly alerts, and database management tools through a browser UI.

The main app runs from `cctv_manager.py` and serves the web dashboard at:

```powershell
python cctv_manager.py
```

Default URL:

```text
http://<server-ip>:5000
```

The system uses:

- Flask for the web server and API routes.
- SQLite for cameras, zones, ROI setup, gate setup, analytics history, anomaly logs, and anomaly configuration.
- Ultralytics YOLO for person detection and gate tracking.
- OpenCV for camera capture, frame processing, ROI drawing, and JPEG stream output.
- Chart.js for historical charts in the browser.

## 2. High-Level Architecture

Request flow:

1. `cctv_manager.py` creates the Flask app.
2. `database.init_db()` creates or migrates SQLite tables.
3. `camera_streams.load_cameras_from_db()` loads saved cameras into memory.
4. Each camera gets a background worker thread.
5. Camera workers read frames, run YOLO, count detections, update `CameraRegistry`, and buffer JPEG frames.
6. `routes.py` serves pages, video feeds, JSON APIs, configuration saves, and dashboard data.
7. `templates/index.html` and `static/style.css` render the dashboard, live viewer, settings modals, charts, anomaly manager, and notifications.

Important runtime state:

- Persistent state is in `campus_security.db`.
- Live camera state is in memory inside `CameraRegistry`.
- Camera frame buffers are in memory inside `camera_streams.global_frame_buffer`.
- Analytics are logged every `ANALYTICS_LOG_INTERVAL_SECONDS`.

## 3. Main Files And Responsibilities

| File / Folder | Purpose |
|---|---|
| `cctv_manager.py` | App entrypoint. Initializes Flask, CORS, DB, camera registry, camera workers, analytics logger, daily gate reset worker, and route registration. |
| `routes.py` | All page routes and JSON APIs for login, cameras, zones, gate setup, stats, history, database cleanup, anomalies, and Unity output. |
| `camera_streams.py` | Camera capture workers, YOLO inference, ROI counting, gate entry/exit tracking, stream frame generation, analytics logging. |
| `camera_registry.py` | Thread-safe in-memory camera state: counts, activity flags, groups, floors, gate totals, config references. |
| `database.py` | SQLite schema, migrations, and all read/write helpers. Edit here when adding persistent data. |
| `anomalies.py` | Anomaly trigger rules, restricted-time checks, safe camera behavior, unread alert count. |
| `config.py` | Constants: model path, DB name, timing, resolutions, default zones, floor options, login settings. |
| `templates/index.html` | Main dashboard UI and most frontend JavaScript. |
| `templates/login.html` | Login page. |
| `static/style.css` | Dashboard styling. |
| `static/uploads/gate_configs/` | Uploaded reference images for ROI/gate setup. |
| `coordinates/` | Legacy coordinate images/text files and optional coordinate extractor helper. |
| `DEPLOYMENT.md` | Existing quick deployment notes. |
| `deploy_check.py` | Deployment sanity checker. |
| `setup_deployment.ps1` | Windows setup script for venv/dependencies/checks. |

## 4. Features And Descriptions

### Login

The app requires login before accessing `/`.

Config:

- Username: `DT_WEBAPP_USERNAME`, default `admin`
- Password: `DT_WEBAPP_PASSWORD`, default currently from `config.py`
- Secret key: `DT_SECRET_KEY`

Production/team handover recommendation: set `DT_WEBAPP_PASSWORD` and `DT_SECRET_KEY` as environment variables.

### Campus Dashboard

Purpose:

- Gives live campus-level numbers.
- Shows entry, exit, and estimated total inside campus.
- Lets operators start/pause all cameras.
- Lets operators start/pause university gate cameras.
- Shows per-zone count cards and zone start/stop controls.

Data source:

- `/api/stats`
- `/api/gate_stats`

### Live Viewer

Purpose:

- Lets operators browse zones and view camera feeds.
- Supports pagination for zones with many cameras.
- Allows individual Start/Stop Feed control.
- Opens larger live camera preview in a modal.
- Opens Camera Settings for name, zone, floor, ROI, gate setup, and deletion.

How to use:

1. Click `Live Viewer`.
2. Select a zone.
3. Click `Start Feed` for individual cameras, or start a whole zone from Campus Dashboard.
4. Click a feed image to enlarge it.
5. Click the gear icon to edit camera settings.

### Add Streams

Purpose:

- Adds a new camera stream to the system.

How to use:

1. Open `Add Streams` from the sidebar.
2. Enter location/camera name.
3. Enter RTSP URL, or `0` for the local/default camera.
4. Choose zone and floor.
5. Click `Add Stream`.

Code path:

- Form posts to `/add_camera`.
- `routes.py` calls `database.insert_camera()`.
- New camera is added to `CameraRegistry`.
- A worker starts with `start_camera_thread()`.

### Manage Zones

Purpose:

- Creates and deletes camera zones/building groups.

How to use:

1. Open `Manage Zones`.
2. Enter a new zone name.
3. Click `Create Zone`.
4. To delete a zone, go to Live Viewer zone selection and click the trash icon on a zone card.

Behavior:

- `Unassigned` is protected and cannot be deleted.
- Deleting a zone moves cameras in that zone to `Unassigned`.

### Camera Settings

Purpose:

- Edit camera name.
- Move camera to another zone.
- Assign floor classification.
- Open ROI/gate setup.
- Delete camera.

How to use:

1. Go to Live Viewer.
2. Open a zone.
3. Click the gear icon on a camera card.
4. Edit fields and click `Save Changes`.

Camera deletion uses a custom modal and removes the stream and saved setup.

### ROI Setup

Purpose:

- Limits counting to a drawn region of interest.
- Reduces false counts outside the relevant camera area.

How to use:

1. Open camera settings.
2. Click `Configure ROI / Gate`.
3. Upload a CCTV reference image.
4. Click points around the area to count.
5. Leave the ROI open if it should extend to the bottom edge, or click `Close ROI`.
6. Click `Save ROI Setup`.

Data saved:

- `camera_rois.reference_image_path`
- `camera_rois.roi_points`
- `camera_rois.roi_closed`

### Gate Counter

Purpose:

- Estimates campus entry and exit counts using configured gate cameras.
- Tracks people crossing a separator line inside an ROI.

How to use:

1. Open `Configure ROI / Gate`.
2. Upload reference image and draw ROI.
3. Enable `gate counter for this camera`.
4. Choose camera role:
   - `Entrance Camera`
   - `Exit Camera`
5. Draw exactly two separator points.
6. Choose which crossing direction means entry.
7. Save.

Important notes:

- Gate counts are stored per day in `gate_counter_states`.
- Counts reset automatically at midnight using `gate_day_reset_worker()`.
- Historical charts use `gate_history_logs`, not raw per-camera logs.

### Historical Charts

Purpose:

- Displays entry, exit, and total-inside trends.
- Supports CSV and graph downloads.
- Dashboard charts display the last 24 hours.
- Download range can be last 10h, 24h, 7d, 30d, all data, or custom range.

Data source:

- `/api/history`
- `/api/history/export`
- `database.fetch_history()`
- `gate_history_logs`

### Database Management

Purpose:

- Shows database size and history row counts.
- Keeps selected recent history while deleting older analytics/anomaly rows.
- Can delete all analytics/anomaly history.

Important behavior:

- Camera setup, zones, ROI, gate setup, and current gate counters are kept.
- Cleanup includes an estimate before deletion.
- SQLite `VACUUM` is run after cleanup to compact the file.

History tables managed:

- `logs`
- `zone_logs`
- `campus_logs`
- `gate_history_logs`
- `anomaly_logs`

### Anomaly Notifications

Purpose:

- Alerts when people are detected in monitored cameras during restricted windows.
- Shows unread count, toast notifications, active alerts, dismiss actions, and anomaly history.

Current behavior:

- Unconfigured cameras use the default restricted time window.
- Safe cameras never trigger anomalies.
- Custom restricted cameras use their own start/end window.
- `Unassigned` cameras do not trigger anomalies.
- A camera fires one anomaly when presence starts, then does not fire again until the count returns to zero.

How to use:

1. Click `Anomaly Configurations`.
2. Set the default restricted time window.
3. For each camera, choose:
   - `Default Restricted Window`
   - `Custom Restricted Window`
   - `Safe Camera`
4. Save configuration.
5. Check bell notifications for active anomalies.

### Unity API

Purpose:

- Provides a structured campus state payload for external visualization or Unity integration.

Endpoint:

```text
GET /api/unity
```

Includes:

- Campus overview totals.
- Gate summary.
- Zone list with counts/activity/anomaly state.
- Camera list.
- Recent unresolved anomalies.

## 5. API Route Map

Main UI:

- `GET /login`
- `POST /login`
- `GET /logout`
- `GET /`

Camera and zone management:

- `POST /add_zone`
- `POST /api/remove_zone/<zone_name>`
- `POST /add_camera`
- `GET /remove_camera/<camera_id>`
- `POST /api/update_camera/<camera_id>`

Video and camera control:

- `GET /video_feed/<camera_id>`
- `GET /api/stats`
- `POST /api/toggle/<camera_id>`
- `POST /api/toggle_group/<group_name>`
- `POST /api/toggle_all`
- `POST /api/toggle_gate_cameras`

Gate and ROI setup:

- `GET /api/gate_config/<camera_id>`
- `POST /api/gate_config/<camera_id>`
- `POST /api/gate_config/<camera_id>/reset`
- `GET /api/gate_stats`

History and database:

- `GET /api/history`
- `GET /api/history/export`
- `GET /api/database/stats`
- `POST /api/database/cleanup/estimate`
- `POST /api/database/cleanup`

Anomalies:

- `GET /api/anomaly_config`
- `POST /api/anomaly_config`
- `GET /api/anomalies`
- `GET /api/anomalies/unread_count`
- `POST /api/anomalies/dismiss/<anomaly_id>`
- `POST /api/anomalies/dismiss_all`
- `GET /api/anomalies/history`

External integration:

- `GET /api/unity`

## 6. Database Overview

Database file:

```text
campus_security.db
```

Main tables:

| Table | Purpose |
|---|---|
| `cameras` | Saved camera ID, name, URL, zone, floor. |
| `zones` | Zone/building names. |
| `camera_rois` | Normal ROI setup per camera. |
| `gate_configs` | Gate camera separator, role, direction, and gate setup. |
| `gate_counter_states` | Current per-day gate entry/exit counts. |
| `logs` | Optimized per-camera analytics rows, only active non-zero counts. |
| `zone_logs` | Optimized per-zone analytics rows, only non-zero counts. |
| `campus_logs` | Campus total count over time. |
| `gate_history_logs` | Gate totals over time; used by historical charts. |
| `anomaly_config` | Default anomaly restricted time window. |
| `camera_anomaly_rules` | Safe cameras and custom camera restricted windows. |
| `anomaly_logs` | Active/resolved anomaly alerts and history. |

When adding a new persistent feature:

1. Add or migrate table/columns in `database.init_db()`.
2. Add helper functions in `database.py`.
3. Import helpers into `routes.py`.
4. Add API route in `routes.py`.
5. Add frontend UI and fetch calls in `templates/index.html`.

## 7. How To Edit Or Extend The Program

### Add A New Dashboard Card

Edit:

- `routes.py` if new data is needed from backend.
- `templates/index.html` inside the `/api/stats` polling section.
- `static/style.css` if new styling is needed.

Recommended pattern:

1. Add data to `/api/stats` or create a new `/api/...` route.
2. Render the card in the dashboard polling block.
3. Keep styling consistent with `.stat-card`.

### Add A New Camera Setting

Edit:

- `database.py`: add column/table and helper.
- `routes.py`: include setting in get/save route.
- `camera_registry.py`: store setting in memory if workers need it.
- `camera_streams.py`: use setting during frame processing if needed.
- `templates/index.html`: add form input and JavaScript save/load.

### Add A New Persistent Configuration Page

Follow the Anomaly Configurations pattern:

1. Create DB table in `database.init_db()`.
2. Add `fetch_*` and `save_*` helpers.
3. Add `GET /api/...` and `POST /api/...` routes.
4. Add a sidebar link.
5. Add a `<div id="...-view" class="view-section">`.
6. Add JavaScript load/save functions.
7. Update `switchView()`/navigation state if the page should restore after reload.

### Add A New Detection Rule

Edit:

- `anomalies.py` for anomaly firing rules.
- `camera_streams.py` if the rule depends on per-frame detection details.
- `database.py` and `routes.py` if the rule needs saved configuration.
- `templates/index.html` if operators need controls.

Be careful:

- `camera_worker()` runs continuously in a thread per camera.
- Avoid database writes on every frame unless absolutely necessary.
- Cache configuration if workers need frequent access.

### Change YOLO Model Or Detection Tuning

Edit `config.py`:

- `MODEL_PATH`
- `MODEL_TASK`
- `MODEL_CONFIDENCE`
- `MODEL_IMAGE_SIZE`
- `DETECTION_FRAME_SKIP`

Model file must exist in the project root unless `MODEL_PATH` is changed to another path.

### Change Timing And Performance Settings

Edit `config.py`:

- `ANALYTICS_LOG_INTERVAL_SECONDS`
- `HISTORICAL_CHART_REFRESH_INTERVAL_MS`
- `CAMERA_RETRY_DELAY_SECONDS`
- `PAUSED_CAMERA_SLEEP_SECONDS`
- `STREAM_FRAME_DELAY_SECONDS`
- `WORKER_LOOP_DELAY_SECONDS`

Notes:

- Lower intervals increase responsiveness but use more CPU/database writes.
- Higher `DETECTION_FRAME_SKIP` reduces GPU/CPU load but reduces detection update frequency.

### Add Or Edit Floor Options

Edit `config.py`:

- Add floor list constants.
- Update `FLOOR_OPTIONS_BY_ZONE`.

The frontend gets this mapping from the `index.html` template context.

### Add A New Unity Field

Edit:

- `routes.py`, inside `/api/unity`.

If the field comes from live camera state, read from `camera_registry.snapshot()`.

If the field comes from history/configuration, add a database helper first.

## 8. Operational Guide

### First-Time Setup On A New Machine

Requirements:

- Python 3.10 or newer.
- Network access to RTSP camera sources.
- Model file referenced by `MODEL_PATH`.
- Write permission to project folder.

Recommended commands:

```powershell
.\setup_deployment.ps1
setx DT_WEBAPP_PASSWORD "change-me"
setx DT_SECRET_KEY "replace-with-a-long-random-secret"
.\.venv\Scripts\python.exe deploy_check.py
.\.venv\Scripts\python.exe cctv_manager.py
```

Open:

```text
http://<server-ip>:5000
```

### Daily Operation

1. Start the app.
2. Log in.
3. Check Campus Dashboard.
4. Start required zones or all cameras.
5. Check Live Viewer camera feeds.
6. Monitor anomaly bell notifications.
7. Use Historical Charts for reports.
8. Use Database Management to keep only necessary history.

### Backup

Back up these items before major changes:

- `campus_security.db`
- `static/uploads/gate_configs/`
- `.py` source files
- `templates/`
- `static/style.css`
- model files such as `Yolov8m_v7.pt`

Do not back up only the database if ROI reference images are needed, because image paths point into `static/uploads/gate_configs/`.

## 9. Troubleshooting

### App Opens But Cameras Are Black Or Offline

Check:

- RTSP URL is reachable from the server.
- Camera username/password are correct.
- Camera is not blocked by firewall/VLAN.
- `OPENCV_FFMPEG_CAPTURE_OPTIONS` in `config.py`.
- The camera was started in the UI.

### Model Fails To Load

Check:

- `MODEL_PATH` in `config.py`.
- The model file exists in the project root.
- Ultralytics is installed.
- CUDA/GPU setup if using GPU acceleration.

### Login Fails

Check:

- `DT_WEBAPP_USERNAME`
- `DT_WEBAPP_PASSWORD`
- Browser session/cache if credentials recently changed.

### Database File Gets Large

Use:

- Campus Dashboard > Historical Charts > Database Management.
- `Keep Selected History`.

Current optimization:

- Inactive and zero-count camera rows are not saved to `logs`.
- Zero-count zones are not saved to `zone_logs`.
- `campus_logs` and `gate_history_logs` are preserved.

### Historical Charts Look Empty

Check:

- Gate cameras are configured.
- Gate counter is enabled.
- Gate separator and direction are correct.
- `gate_history_logs` has recent rows.
- The selected download range has data.

### Anomalies Do Not Fire

Check:

- Camera is not `Safe Camera`.
- Current time is inside the default/custom restricted window.
- Camera is not in `Unassigned`.
- Person count goes from zero to one or more.
- `/api/anomaly_config` returns expected config.

### Anomalies Fire Too Often

Current logic should fire once per presence period. If it fires repeatedly:

- Confirm the camera count is not flickering between zero and one.
- Check ROI quality.
- Increase model confidence in `config.py`.
- Improve camera angle/lighting.

## 10. Development Notes And Safety Rules

- The app is multi-threaded. Camera workers, analytics logging, and Flask requests run concurrently.
- Use `CameraRegistry` methods instead of directly mutating shared camera dictionaries.
- Database writes are protected by `db_lock`.
- Avoid long-running work inside Flask request handlers.
- Avoid database writes inside every frame unless required.
- Keep UI state changes in `templates/index.html` consistent with API response fields.
- When adding new routes, return JSON with a clear `status` field for frontend consistency.
- When changing schema, make migrations idempotent in `init_db()` so old databases upgrade safely.

## 11. Suggested Future Improvements

- Split `templates/index.html` JavaScript into separate static JS modules.
- Add formal unit tests for database helpers and anomaly rules.
- Add a role-based login model if multiple operators/admins are needed.
- Add scheduled automatic cleanup policy.
- Add configurable model settings in the UI.
- Add per-camera health/status page.
- Add export/import for system configuration.
- Add a migration version table for more formal DB upgrades.

## 12. Quick Change Reference

| Task | Edit Here |
|---|---|
| Change model | `config.py` |
| Change default zones | `config.py`, `DEFAULT_ZONES` |
| Change floor options | `config.py`, `FLOOR_OPTIONS_BY_ZONE` |
| Add route/API | `routes.py` |
| Add database table/column | `database.py`, `init_db()` |
| Add frontend view/modal | `templates/index.html` |
| Add styling | `static/style.css` |
| Change anomaly rules | `anomalies.py` |
| Change detection/ROI/gate logic | `camera_streams.py` |
| Change in-memory camera state behavior | `camera_registry.py` |
| Change login defaults | `config.py` or environment variables |
| Change chart data | `database.fetch_history()` and `/api/history` |

