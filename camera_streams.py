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
from database import fetch_cameras, insert_analytics


os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = OPENCV_FFMPEG_CAPTURE_OPTIONS

model = YOLO(MODEL_PATH, task=MODEL_TASK)
model_lock = Lock()

camera_threads = {}
thread_run_flags = {}
global_frame_buffer = {}


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
            insert_analytics(cameras_snapshot, zone_counts, total_campus)
        except Exception as e:
            print(f"Database write error: {e}")


def start_analytics_logger(camera_registry):
    threading.Thread(target=analytics_logger, args=(camera_registry,), daemon=True).start()


def camera_worker(camera_registry, camera_id, source):
    cap = None
    frame_counter = 0

    poly_filename = os.path.join("coordinates", f"{camera_id}_coords.txt")
    raw_clicked_poly = load_poly_from_txt(poly_filename)

    clicked_res_w, clicked_res_h = CLICKED_RESOLUTION
    scaled_poly = None

    if raw_clicked_poly is not None:
        scale_w = PROCESSING_RESOLUTION[0] / clicked_res_w
        scale_h = PROCESSING_RESOLUTION[1] / clicked_res_h
        scaled_poly = (raw_clicked_poly * [scale_w, scale_h]).astype(np.int32)

    while thread_run_flags.get(camera_id, False):
        cam_info = camera_registry.get(camera_id) or {}
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
            with model_lock:
                results = model(frame, conf=MODEL_CONFIDENCE, imgsz=MODEL_IMAGE_SIZE, verbose=False)

            annotated_frame = frame.copy()
            person_count = 0

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
                cv2.polylines(
                    annotated_frame,
                    [scaled_poly],
                    isClosed=True,
                    color=(0, 255, 255),
                    thickness=2,
                )

            if person_count > 0:
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
    for cam_id, name, url, group in rows:
        camera_registry.add(
            cam_id,
            {
                "name": name,
                "url": url,
                "group": group,
                "count": 0,
                "is_active": False,
                "last_updated": time.time(),
            },
        )
    for cam_id, _, url, _ in rows:
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
