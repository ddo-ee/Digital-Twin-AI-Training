import os
import time
import threading
from threading import Lock

import cv2
import numpy as np
from ultralytics import YOLO

from anomalies import check_and_fire_anomaly
from config import (
    ANALYTICS_LOG_INTERVAL_SECONDS,
    CAMERA_RETRY_DELAY_SECONDS,
    CLICKED_RESOLUTION,
    DETECTION_FRAME_SKIP,
    GATE_TRACK_COUNT_COOLDOWN_SECONDS,
    GATE_TRACK_STALE_SECONDS,
    MODEL_CONFIDENCE,
    MODEL_IMAGE_SIZE,
    MODEL_PATH,
    MODEL_TASK,
    OPENCV_FFMPEG_CAPTURE_OPTIONS,
    PAUSED_CAMERA_SLEEP_SECONDS,
    PROCESSING_RESOLUTION,
    STREAM_FRAME_DELAY_SECONDS,
    WORKER_LOOP_DELAY_SECONDS,
)
from database import fetch_camera_rois, fetch_cameras, fetch_gate_configs, insert_analytics


os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = OPENCV_FFMPEG_CAPTURE_OPTIONS

model = YOLO(MODEL_PATH, task=MODEL_TASK)
model_lock = Lock()

camera_threads = {}
thread_run_flags = {}
global_frame_buffer = {}
gate_camera_runtime = {}


def load_poly_from_txt(filename):
    pts = []
    if not os.path.exists(filename):
        return None
    with open(filename, "r") as f:
        for line in f:
            if line.strip():
                x, y = line.strip().split(",")
                pts.append([int(x), int(y)])
    return np.array(pts, np.float32)


def _scale_poly_points(raw_points):
    if not raw_points:
        return None

    normalized_points = []
    for point in raw_points:
        if isinstance(point, dict):
            normalized_points.append([int(point["x"]), int(point["y"])])
        else:
            normalized_points.append([int(point[0]), int(point[1])])

    clicked_res_w, clicked_res_h = CLICKED_RESOLUTION
    scale_w = PROCESSING_RESOLUTION[0] / clicked_res_w
    scale_h = PROCESSING_RESOLUTION[1] / clicked_res_h
    return (np.array(normalized_points, np.float32) * [scale_w, scale_h]).astype(np.int32)


def _build_effective_roi_poly(raw_points, roi_closed=True):
    scaled_points = _scale_poly_points(raw_points)
    if scaled_points is None or len(scaled_points) < 3:
        return None

    if roi_closed:
        return scaled_points

    bottom_y = PROCESSING_RESOLUTION[1] - 1
    first_point = scaled_points[0]
    last_point = scaled_points[-1]
    extension_points = np.array(
        [
            [last_point[0], bottom_y],
            [first_point[0], bottom_y],
        ],
        dtype=np.int32,
    )
    return np.vstack([scaled_points, extension_points])


def _build_gate_camera_defaults(gate_config=None, camera_roi=None):
    is_gate_camera = bool(gate_config)
    return {
        "roi_points": (camera_roi or {}).get("roi_points", []),
        "roi_closed": (camera_roi or {}).get("roi_closed", True),
        "reference_image_path": (camera_roi or {}).get("reference_image_path", ""),
        "is_gate_camera": is_gate_camera,
        "gate_role": gate_config.get("camera_role", "entrance") if is_gate_camera else "",
        "gate_direction": gate_config.get("direction", "") if is_gate_camera else "",
        "gate_split_x": gate_config.get("split_x") if is_gate_camera else None,
        "gate_separator_points": gate_config.get("separator_points", []) if is_gate_camera else [],
        "gate_reference_image_path": gate_config.get("reference_image_path", "") if is_gate_camera else "",
        "entry_count": 0,
        "exit_count": 0,
    }


def _scale_point(point):
    scale_w = PROCESSING_RESOLUTION[0] / CLICKED_RESOLUTION[0]
    scale_h = PROCESSING_RESOLUTION[1] / CLICKED_RESOLUTION[1]
    if isinstance(point, dict):
        return {
            "x": int(point["x"] * scale_w),
            "y": int(point["y"] * scale_h),
        }
    return {
        "x": int(point[0] * scale_w),
        "y": int(point[1] * scale_h),
    }


def _build_default_separator_points(scaled_poly, configured_split_x=None):
    min_x = int(np.min(scaled_poly[:, 0]))
    max_x = int(np.max(scaled_poly[:, 0]))
    min_y = int(np.min(scaled_poly[:, 1]))
    max_y = int(np.max(scaled_poly[:, 1]))
    if configured_split_x is not None:
        scale_w = PROCESSING_RESOLUTION[0] / CLICKED_RESOLUTION[0]
        scaled_split_x = configured_split_x * scale_w
        split_x = int(max(min_x, min(max_x, scaled_split_x)))
    else:
        split_x = int((min_x + max_x) / 2)
    return [
        {"x": split_x, "y": min_y},
        {"x": split_x, "y": max_y},
    ]


def _resolve_separator_points(scaled_poly, separator_points=None, configured_split_x=None):
    if separator_points and len(separator_points) >= 2:
        return [_scale_point(separator_points[0]), _scale_point(separator_points[1])]
    return _build_default_separator_points(scaled_poly, configured_split_x)


def _resolve_gate_side(point_x, point_y, separator_points):
    start_point, end_point = separator_points[0], separator_points[1]
    line_dx = end_point["x"] - start_point["x"]
    line_dy = end_point["y"] - start_point["y"]
    point_dx = point_x - start_point["x"]
    point_dy = point_y - start_point["y"]
    cross_product = (line_dx * point_dy) - (line_dy * point_dx)
    return "left" if cross_product < 0 else "right"


def _extract_track_id(box):
    track_id = getattr(box, "id", None)
    if track_id is None:
        return None

    try:
        if hasattr(track_id, "item"):
            return int(track_id.item())
        return int(track_id)
    except Exception:
        return None


def _is_entry_crossing(previous_side, current_side, gate_direction):
    moved_left_to_right = previous_side == "left" and current_side == "right"
    moved_right_to_left = previous_side == "right" and current_side == "left"

    if gate_direction == "left_to_right_entry":
        return moved_left_to_right
    if gate_direction == "right_to_left_entry":
        return moved_right_to_left
    return False


def _update_gate_estimate(camera_id, gate_role, gate_direction, tracked_points, now_ts):
    runtime = gate_camera_runtime.setdefault(
        camera_id,
        {
            "track_states": {},
        },
    )
    track_states = runtime["track_states"]
    entry_increment = 0
    exit_increment = 0

    for tracked_point in tracked_points:
        track_id = tracked_point["track_id"]
        current_side = tracked_point["side"]
        state = track_states.get(
            track_id,
            {
                "last_side": current_side,
                "last_seen": now_ts,
                "last_counted_at": 0.0,
            },
        )

        previous_side = state.get("last_side")
        crossed_separator = previous_side != current_side
        cooldown_ready = (now_ts - state.get("last_counted_at", 0.0)) >= GATE_TRACK_COUNT_COOLDOWN_SECONDS

        if crossed_separator and cooldown_ready:
            is_entry_crossing = _is_entry_crossing(previous_side, current_side, gate_direction)
            if gate_role == "entrance" and is_entry_crossing:
                entry_increment += 1
                state["last_counted_at"] = now_ts
            elif gate_role == "exit" and not is_entry_crossing:
                exit_increment += 1
                state["last_counted_at"] = now_ts

        state["last_side"] = current_side
        state["last_seen"] = now_ts
        track_states[track_id] = state

    stale_track_ids = [
        track_id
        for track_id, state in track_states.items()
        if now_ts - state.get("last_seen", now_ts) > GATE_TRACK_STALE_SECONDS
    ]
    for track_id in stale_track_ids:
        track_states.pop(track_id, None)

    runtime["track_states"] = track_states
    return entry_increment, exit_increment


def analytics_logger(camera_registry):
    while True:
        time.sleep(ANALYTICS_LOG_INTERVAL_SECONDS)

        cameras_snapshot = camera_registry.snapshot()
        if not cameras_snapshot:
            continue

        total_campus = 0
        zone_counts = {}

        for info in cameras_snapshot.values():
            count = info["count"]
            group = info["group"]
            total_campus += count
            zone_counts.setdefault(group, 0)
            zone_counts[group] += count

        try:
            insert_analytics(
                cameras_snapshot,
                zone_counts,
                total_campus,
                camera_registry.get_gate_summary(),
            )
        except Exception as e:
            print(f"Database write error: {e}")


def start_analytics_logger(camera_registry):
    threading.Thread(target=analytics_logger, args=(camera_registry,), daemon=True).start()


def camera_worker(camera_registry, camera_id, source):
    cap = None
    frame_counter = 0
    tracking_model = None

    poly_filename = os.path.join("coordinates", f"{camera_id}_coords.txt")
    fallback_raw_poly = load_poly_from_txt(poly_filename)

    while thread_run_flags.get(camera_id, False):
        cam_info = camera_registry.get(camera_id) or {}
        roi_points = cam_info.get("roi_points") or []
        scaled_poly = _build_effective_roi_poly(roi_points, cam_info.get("roi_closed", True)) if roi_points else None
        if scaled_poly is None and fallback_raw_poly is not None:
            scaled_poly = _scale_poly_points(fallback_raw_poly.tolist())

        if not cam_info.get("is_active", False):
            if cap is not None:
                cap.release()
                cap = None

            offline_frame = np.zeros((PROCESSING_RESOLUTION[1], PROCESSING_RESOLUTION[0], 3), dtype=np.uint8)
            text = "SYSTEM PAUSED"
            font = cv2.FONT_HERSHEY_SIMPLEX
            text_size = cv2.getTextSize(text, font, 1, 1)[0]
            cv2.putText(
                offline_frame,
                text,
                ((PROCESSING_RESOLUTION[0] - text_size[0]) // 2, (PROCESSING_RESOLUTION[1] + text_size[1]) // 2),
                font,
                1,
                (100, 100, 100),
                2,
            )
            ret, buffer = cv2.imencode(".jpg", offline_frame)
            if ret:
                global_frame_buffer[camera_id] = buffer.tobytes()
            time.sleep(PAUSED_CAMERA_SLEEP_SECONDS)
            continue

        if cap is None:
            cap = cv2.VideoCapture(source if source != "0" else 0)

        success, frame = cap.read()

        if not success:
            offline_frame = np.zeros((PROCESSING_RESOLUTION[1], PROCESSING_RESOLUTION[0], 3), dtype=np.uint8)
            text = "CAMERA OFFLINE"
            font = cv2.FONT_HERSHEY_SIMPLEX
            text_size = cv2.getTextSize(text, font, 1, 1)[0]
            cv2.putText(
                offline_frame,
                text,
                ((PROCESSING_RESOLUTION[0] - text_size[0]) // 2, (PROCESSING_RESOLUTION[1] + text_size[1]) // 2),
                font,
                1,
                (0, 0, 255),
                1,
            )
            ret, buffer = cv2.imencode(".jpg", offline_frame)
            if ret:
                global_frame_buffer[camera_id] = buffer.tobytes()
            time.sleep(CAMERA_RETRY_DELAY_SECONDS)
            cap.open(source if source != "0" else 0)
            continue

        frame_counter += 1

        if frame_counter % DETECTION_FRAME_SKIP == 0:
            frame = cv2.resize(frame, PROCESSING_RESOLUTION)
            is_gate_camera = cam_info.get("is_gate_camera", False) and scaled_poly is not None
            gate_role = cam_info.get("gate_role", "entrance")
            gate_direction = cam_info.get("gate_direction", "left_to_right_entry")
            gate_separator_points = None

            if is_gate_camera:
                gate_separator_points = _resolve_separator_points(
                    scaled_poly,
                    cam_info.get("gate_separator_points"),
                    cam_info.get("gate_split_x"),
                )
                if tracking_model is None:
                    tracking_model = YOLO(MODEL_PATH, task=MODEL_TASK)
                results = tracking_model.track(
                    frame,
                    conf=MODEL_CONFIDENCE,
                    imgsz=MODEL_IMAGE_SIZE,
                    persist=True,
                    verbose=False,
                    tracker="bytetrack.yaml",
                )
            else:
                with model_lock:
                    results = model(frame, conf=MODEL_CONFIDENCE, imgsz=MODEL_IMAGE_SIZE, verbose=False)

            # Gate cameras keep full tracked overlays, but normal ROI cameras
            # should only show detections that actually count inside the ROI.
            annotated_frame = results[0].plot(labels=False, conf=False) if is_gate_camera else frame.copy()
            person_count = 0
            tracked_points = []

            for box in results[0].boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                foot_x = int((x1 + x2) / 2)
                foot_y = int(y2)
                track_id = _extract_track_id(box)

                if scaled_poly is not None:
                    is_inside = cv2.pointPolygonTest(scaled_poly, (foot_x, foot_y), False)
                    if is_inside >= 0:
                        person_count += 1
                        if not is_gate_camera:
                            cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                        cv2.circle(annotated_frame, (foot_x, foot_y), 5, (0, 0, 255), -1)
                        if is_gate_camera and track_id is not None:
                            gate_side = _resolve_gate_side(foot_x, foot_y, gate_separator_points)
                            tracked_points.append(
                                {
                                    "track_id": track_id,
                                    "x": foot_x,
                                    "y": foot_y,
                                    "side": gate_side,
                                }
                            )
                            cv2.putText(
                                annotated_frame,
                                f"ID {track_id} {gate_side.upper()}",
                                (x1, max(20, y1 - 8)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.45,
                                (0, 255, 255),
                                1,
                            )
                else:
                    person_count += 1
                    if not is_gate_camera:
                        cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)

            if scaled_poly is not None:
                cv2.polylines(
                    annotated_frame,
                    [scaled_poly],
                    isClosed=True,
                    color=(0, 255, 255),
                    thickness=2,
                )

            if is_gate_camera:
                cv2.line(
                    annotated_frame,
                    (gate_separator_points[0]["x"], gate_separator_points[0]["y"]),
                    (gate_separator_points[1]["x"], gate_separator_points[1]["y"]),
                    (255, 180, 0),
                    2,
                )

                entry_increment, exit_increment = _update_gate_estimate(
                    camera_id,
                    gate_role,
                    gate_direction,
                    tracked_points,
                    time.time(),
                )
                if entry_increment or exit_increment:
                    camera_registry.update_gate_totals(camera_id, entry_increment, exit_increment)

                updated_info = camera_registry.get(camera_id) or cam_info
                count_text = (
                    f"ENTRY CAM | IN {updated_info.get('entry_count', 0)}"
                    if gate_role == "entrance"
                    else f"EXIT CAM | OUT {updated_info.get('exit_count', 0)}"
                )
                cv2.putText(
                    annotated_frame,
                    count_text,
                    (12, 28),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 255),
                    2,
                )
            else:
                gate_camera_runtime.pop(camera_id, None)

            check_and_fire_anomaly(
                camera_id,
                cam_info.get("name", camera_id),
                cam_info.get("group", ""),
                person_count,
            )

            camera_registry.update_detection(camera_id, person_count, time.time())

            ret, buffer = cv2.imencode(".jpg", annotated_frame)
            if ret:
                global_frame_buffer[camera_id] = buffer.tobytes()

        time.sleep(WORKER_LOOP_DELAY_SECONDS)

    if cap is not None:
        cap.release()


def start_camera_thread(camera_registry, camera_id, url):
    if camera_id not in camera_threads:
        thread_run_flags[camera_id] = True
        thread = threading.Thread(target=camera_worker, args=(camera_registry, camera_id, url), daemon=True)
        thread.start()
        camera_threads[camera_id] = thread


def stop_camera_thread(camera_id):
    thread_run_flags[camera_id] = False
    if camera_id in camera_threads:
        del camera_threads[camera_id]


def load_cameras_from_db(camera_registry):
    rows = fetch_cameras()
    camera_rois = fetch_camera_rois()
    gate_configs = fetch_gate_configs()
    for cam_id, name, url, group, floor in rows:
        gate_defaults = _build_gate_camera_defaults(gate_configs.get(cam_id), camera_rois.get(cam_id))
        camera_registry.add(
            cam_id,
            {
                "name": name,
                "url": url,
                "group": group,
                "floor": floor or "",
                "count": 0,
                "is_active": False,
                "last_updated": time.time(),
                **gate_defaults,
            },
        )
    for cam_id, _, url, _, _ in rows:
        start_camera_thread(camera_registry, cam_id, url)
    print(f"Loaded {len(rows)} cameras.")


def generate_frames(camera_id):
    while True:
        frame_bytes = global_frame_buffer.get(camera_id)
        if frame_bytes:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
            )
        time.sleep(STREAM_FRAME_DELAY_SECONDS)
