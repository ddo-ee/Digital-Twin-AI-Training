import os
import time
import uuid
import csv
import io

from flask import Response, jsonify, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

import anomalies
from camera_streams import generate_frames, start_camera_thread, stop_camera_thread
from config import (
    CLICKED_RESOLUTION,
    FLOOR_OPTIONS_BY_ZONE,
    GATE_CONFIG_UPLOAD_DIR,
    HISTORICAL_CHART_REFRESH_INTERVAL_MS,
    LOGIN_PASSWORD,
    LOGIN_USERNAME,
    UNITY_ANOMALY_WINDOW_SECONDS,
    UNITY_ZONE_ORDER,
)
from database import (
    add_zone,
    delete_camera_roi,
    delete_gate_counter_state,
    delete_camera,
    delete_gate_config,
    dismiss_all_anomalies,
    dismiss_anomaly,
    fetch_active_anomalies,
    fetch_anomaly_history,
    fetch_camera_roi,
    fetch_gate_config,
    fetch_history,
    fetch_recent_unresolved_anomalies,
    fetch_zones,
    insert_camera,
    remove_zone,
    upsert_camera_roi,
    upsert_gate_config,
    update_camera,
)


def _is_safe_next_url(next_url):
    return bool(next_url) and next_url.startswith("/") and not next_url.startswith("//")


def _camera_floor_lookup(camera_registry):
    return {
        cam_id: info.get("floor", "")
        for cam_id, info in camera_registry.snapshot().items()
    }


def _serialize_gate_config(config, roi_config=None):
    if not config:
        return {
            "is_gate_camera": False,
            "reference_image_path": roi_config.get("reference_image_path", "") if roi_config else "",
            "roi_points": roi_config.get("roi_points", []) if roi_config else [],
            "roi_closed": roi_config.get("roi_closed", True) if roi_config else True,
            "split_x": None,
            "separator_points": [],
            "camera_role": "entrance",
            "direction": "left_to_right_entry",
        }

    separator_points = config.get("separator_points", [])
    if not separator_points and config.get("split_x") is not None:
        separator_points = [
            {"x": int(config["split_x"]), "y": 0},
            {"x": int(config["split_x"]), "y": CLICKED_RESOLUTION[1]},
        ]

    return {
        "is_gate_camera": True,
        "reference_image_path": roi_config.get("reference_image_path", config.get("reference_image_path", "")) if roi_config else config.get("reference_image_path", ""),
        "roi_points": roi_config.get("roi_points", config.get("roi_points", [])) if roi_config else config.get("roi_points", []),
        "roi_closed": roi_config.get("roi_closed", config.get("roi_closed", True)) if roi_config else config.get("roi_closed", True),
        "split_x": config.get("split_x"),
        "separator_points": separator_points,
        "camera_role": config.get("camera_role") or "entrance",
        "direction": config.get("direction") or "left_to_right_entry",
    }


def _allowed_gate_image(filename):
    _, ext = os.path.splitext(filename.lower())
    return ext in {".jpg", ".jpeg", ".png", ".webp"}


def _history_query_args():
    return {
        "range_key": (request.args.get("range") or "10h").strip().lower(),
        "start": (request.args.get("start") or "").strip(),
        "end": (request.args.get("end") or "").strip(),
    }


def register_routes(app, camera_registry):
    @app.before_request
    def require_login():
        allowed_endpoints = {"login", "logout", "static"}
        if request.endpoint in allowed_endpoints or request.path == "/login":
            return None

        if request.path == "/":
            if session.get("authenticated"):
                return None
            return redirect(url_for("login", next=request.path))

        return None

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        next_url = request.args.get("next")

        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            next_url = request.form.get("next") or next_url

            if username == LOGIN_USERNAME and password == LOGIN_PASSWORD:
                session.clear()
                session["authenticated"] = True
                session["username"] = username
                if _is_safe_next_url(next_url):
                    return redirect(next_url)
                return redirect(url_for("index"))

            error = "Invalid username or password."

        if session.get("authenticated"):
            return redirect(url_for("index"))

        return render_template("login.html", error=error, next_url=next_url, username=LOGIN_USERNAME)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    def index():
        return render_template(
            "index.html",
            cameras=camera_registry.snapshot(),
            clicked_resolution=CLICKED_RESOLUTION,
            floor_options_by_zone=FLOOR_OPTIONS_BY_ZONE,
            historical_chart_refresh_interval_ms=HISTORICAL_CHART_REFRESH_INTERVAL_MS,
            all_zones=fetch_zones(order_by_name=True),
        )

    @app.route("/add_zone", methods=["POST"])
    def add_zone_route():
        zone_name = request.form.get("zone_name")
        if zone_name and zone_name.strip():
            try:
                add_zone(zone_name.strip())
            except Exception:
                pass
        return redirect(url_for("index"))

    @app.route("/api/remove_zone/<zone_name>", methods=["POST"])
    def remove_zone_route(zone_name):
        try:
            remove_zone(zone_name)
            camera_registry.move_group_to_unassigned(zone_name)
            return jsonify({"status": "success"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/video_feed/<camera_id>")
    def video_feed(camera_id):
        return Response(generate_frames(camera_id), mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.route("/add_camera", methods=["POST"])
    def add_camera_route():
        camera_name = request.form.get("camera_name")
        camera_url = request.form.get("camera_url")
        camera_group = request.form.get("camera_group")
        camera_floor = request.form.get("camera_floor", "").strip()
        new_id = f"cam_{int(time.time())}"

        insert_camera(new_id, camera_name, camera_url, camera_group, camera_floor)
        camera_registry.add(
            new_id,
            {
                "name": camera_name,
                "url": camera_url,
                "group": camera_group,
                "floor": camera_floor,
                "count": 0,
                "is_active": False,
                "last_updated": time.time(),
                "is_gate_camera": False,
                "gate_role": "",
                "gate_direction": "",
                "gate_split_x": None,
                "roi_points": [],
                "reference_image_path": "",
                "gate_reference_image_path": "",
                "entry_count": 0,
                "exit_count": 0,
            },
        )
        start_camera_thread(camera_registry, new_id, camera_url)
        return redirect(url_for("index"))

    @app.route("/remove_camera/<camera_id>")
    def remove_camera_route(camera_id):
        if camera_registry.has(camera_id):
            stop_camera_thread(camera_id)
            camera_registry.remove(camera_id)
            delete_gate_counter_state(camera_id)
            delete_gate_config(camera_id)
            delete_camera(camera_id)
        return redirect(url_for("index"))

    @app.route("/api/update_camera/<camera_id>", methods=["POST"])
    def update_camera_route(camera_id):
        new_name = request.json.get("new_name")
        new_zone = request.json.get("new_zone")
        new_floor = request.json.get("new_floor", "")

        if not new_name or not new_name.strip():
            return jsonify({"status": "error", "message": "Name cannot be empty"}), 400

        if camera_registry.has(camera_id):
            clean_name = new_name.strip()
            clean_floor = (new_floor or "").strip()
            try:
                update_camera(camera_id, clean_name, new_zone, clean_floor)
                camera_registry.update_camera(
                    camera_id,
                    name=clean_name,
                    group=new_zone if new_zone else None,
                    floor=clean_floor,
                )
                return jsonify(
                    {
                        "status": "success",
                        "new_name": clean_name,
                        "new_zone": new_zone,
                        "new_floor": clean_floor,
                    }
                )
            except Exception as e:
                return jsonify({"status": "error", "message": str(e)}), 500

        return jsonify({"status": "error", "message": "Camera not found"}), 404

    @app.route("/api/gate_config/<camera_id>")
    def get_gate_config_route(camera_id):
        if not camera_registry.has(camera_id):
            return jsonify({"status": "error", "message": "Camera not found"}), 404
        gate_config = fetch_gate_config(camera_id)
        roi_config = fetch_camera_roi(camera_id)
        return jsonify({"status": "success", "config": _serialize_gate_config(gate_config, roi_config)})

    @app.route("/api/gate_config/<camera_id>", methods=["POST"])
    def save_gate_config_route(camera_id):
        if not camera_registry.has(camera_id):
            return jsonify({"status": "error", "message": "Camera not found"}), 404

        roi_points_raw = request.form.get("roi_points", "[]")
        separator_points_raw = request.form.get("separator_points", "[]")
        roi_closed = (request.form.get("roi_closed") or "").lower() == "true"
        split_x_raw = request.form.get("split_x", "").strip()
        direction = (request.form.get("direction") or "").strip()
        camera_role = (request.form.get("camera_role") or "").strip().lower()
        is_gate_camera = (request.form.get("is_gate_camera") or "").lower() == "true"
        existing_config = fetch_gate_config(camera_id)
        existing_roi = fetch_camera_roi(camera_id)

        try:
            import json
            roi_points = json.loads(roi_points_raw)
            separator_points = json.loads(separator_points_raw)
        except Exception:
            return jsonify({"status": "error", "message": "ROI or separator payload is invalid."}), 400

        if not isinstance(roi_points, list) or len(roi_points) < 3:
            return jsonify({"status": "error", "message": "Please plot at least 3 ROI points."}), 400
        normalized_roi_points = []
        for point in roi_points:
            if not isinstance(point, dict):
                return jsonify({"status": "error", "message": "ROI points must be objects."}), 400
            try:
                x = int(point["x"])
                y = int(point["y"])
            except Exception:
                return jsonify({"status": "error", "message": "ROI point coordinates are invalid."}), 400
            normalized_roi_points.append({"x": x, "y": y})

        normalized_separator_points = []
        if is_gate_camera:
            if camera_role not in {"entrance", "exit"}:
                return jsonify({"status": "error", "message": "Camera role is invalid."}), 400
            if not isinstance(separator_points, list) or len(separator_points) != 2:
                return jsonify({"status": "error", "message": "Please plot exactly 2 separator points."}), 400

            for point in separator_points:
                if not isinstance(point, dict):
                    return jsonify({"status": "error", "message": "Separator points must be objects."}), 400
                try:
                    x = int(point["x"])
                    y = int(point["y"])
                except Exception:
                    return jsonify({"status": "error", "message": "Separator point coordinates are invalid."}), 400
                normalized_separator_points.append({"x": x, "y": y})

            if direction not in {"left_to_right_entry", "right_to_left_entry"}:
                return jsonify({"status": "error", "message": "Direction is invalid."}), 400
        else:
            camera_role = ""
            direction = ""

        split_x = None

        reference_image_path = ""
        if existing_roi:
            reference_image_path = existing_roi.get("reference_image_path", "")
        elif existing_config:
            reference_image_path = existing_config.get("reference_image_path", "")
        uploaded_image = request.files.get("reference_image")
        if uploaded_image and uploaded_image.filename:
            if not _allowed_gate_image(uploaded_image.filename):
                return jsonify({"status": "error", "message": "Reference image must be JPG, PNG, or WEBP."}), 400

            os.makedirs(GATE_CONFIG_UPLOAD_DIR, exist_ok=True)
            _, ext = os.path.splitext(uploaded_image.filename)
            safe_name = secure_filename(f"{camera_id}_{uuid.uuid4().hex}{ext.lower()}")
            saved_path = os.path.join(GATE_CONFIG_UPLOAD_DIR, safe_name)
            uploaded_image.save(saved_path)
            reference_image_path = "/" + saved_path.replace("\\", "/")

        if not reference_image_path:
            return jsonify({"status": "error", "message": "Please upload a CCTV reference image first."}), 400

        upsert_camera_roi(
            camera_id,
            reference_image_path,
            normalized_roi_points,
            roi_closed,
        )

        camera_registry.set_gate_config(
            camera_id,
            {
                "reference_image_path": reference_image_path,
                "roi_points": normalized_roi_points,
                "roi_closed": roi_closed,
                "split_x": split_x,
                "separator_points": normalized_separator_points,
                "camera_role": camera_role,
                "direction": direction,
            } if is_gate_camera else None,
        )
        current_info = camera_registry.get(camera_id)
        if current_info:
            camera_registry.add(
                camera_id,
                {
                    **current_info,
                    "roi_points": normalized_roi_points,
                    "roi_closed": roi_closed,
                    "reference_image_path": reference_image_path,
                },
            )

        if is_gate_camera:
            gate_config = {
                "reference_image_path": reference_image_path,
                "roi_points": normalized_roi_points,
                "roi_closed": roi_closed,
                "split_x": split_x,
                "separator_points": normalized_separator_points,
                "camera_role": camera_role,
                "direction": direction,
            }
            upsert_gate_config(
                camera_id,
                reference_image_path,
                normalized_roi_points,
                split_x,
                normalized_separator_points,
                direction,
                camera_role,
                roi_closed,
            )
        else:
            gate_config = None
            delete_gate_config(camera_id)

        camera_registry.reset_gate_totals(camera_id)
        delete_gate_counter_state(camera_id)
        serialized_config = _serialize_gate_config(
            {"camera_id": camera_id, **(gate_config or {})} if gate_config else None,
            {"camera_id": camera_id, "reference_image_path": reference_image_path, "roi_points": normalized_roi_points, "roi_closed": roi_closed},
        )
        return jsonify({"status": "success", "config": serialized_config})

    @app.route("/api/gate_config/<camera_id>/reset", methods=["POST"])
    def reset_gate_config_route(camera_id):
        if not camera_registry.has(camera_id):
            return jsonify({"status": "error", "message": "Camera not found"}), 404

        delete_gate_config(camera_id)
        delete_camera_roi(camera_id)
        delete_gate_counter_state(camera_id)
        camera_registry.set_gate_config(camera_id, None)
        camera_registry.reset_gate_totals(camera_id)
        current_info = camera_registry.get(camera_id)
        if current_info:
            camera_registry.add(
                camera_id,
                {
                    **current_info,
                    "roi_points": [],
                    "roi_closed": True,
                    "reference_image_path": "",
                },
            )
        return jsonify({"status": "success"})

    @app.route("/api/stats")
    def get_stats():
        cameras_snapshot = camera_registry.snapshot()
        data = {
            cid: {
                "name": info["name"],
                "count": info["count"],
                "group": info["group"],
                "floor": info.get("floor", ""),
                "is_active": info.get("is_active", False),
                "last_updated": info.get("last_updated", time.time()),
                "is_gate_camera": info.get("is_gate_camera", False),
                "gate_role": info.get("gate_role", ""),
                "gate_direction": info.get("gate_direction", ""),
                "entry_count": info.get("entry_count", 0),
                "exit_count": info.get("exit_count", 0),
                "gate_split_x": info.get("gate_split_x"),
            }
            for cid, info in cameras_snapshot.items()
        }
        return jsonify(data)

    @app.route("/api/gate_stats")
    def get_gate_stats():
        return jsonify(camera_registry.get_gate_summary())

    @app.route("/api/toggle_group/<group_name>", methods=["POST"])
    def toggle_group(group_name):
        action = request.json.get("action")
        camera_registry.set_group_active(group_name, action == "start")
        return jsonify({"status": "success", "zone": group_name, "action": action})

    @app.route("/api/toggle_all", methods=["POST"])
    def toggle_all():
        action = request.json.get("action")
        camera_registry.set_all_active(action == "start")
        return jsonify({"status": "success", "action": action})

    @app.route("/api/toggle_gate_cameras", methods=["POST"])
    def toggle_gate_cameras():
        action = request.json.get("action")
        affected = camera_registry.set_gate_cameras_active(action == "start")
        return jsonify({"status": "success", "action": action, "affected": affected})

    @app.route("/api/toggle/<camera_id>", methods=["POST"])
    def toggle_camera(camera_id):
        new_state = camera_registry.toggle_active(camera_id)
        if new_state is not None:
            return jsonify({"status": "success", "is_active": new_state})
        return jsonify({"status": "error"})

    @app.route("/api/history")
    def get_history():
        gate_history = fetch_history(**_history_query_args())

        labels = [row[0] for row in gate_history]
        total_entered = [row[1] for row in gate_history]
        total_exited = [row[2] for row in gate_history]
        inside_total = [row[3] for row in gate_history]

        return jsonify({
            "labels": labels,
            "gate_history": {
                "in": total_entered,
                "out": total_exited,
                "inside": inside_total,
            },
        })

    @app.route("/api/history/export")
    def export_history():
        gate_history = fetch_history(**_history_query_args())

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["timestamp", "total_entered", "total_exited", "inside_total"])
        for timestamp, total_entered, total_exited, inside_total in gate_history:
            writer.writerow([timestamp, total_entered, total_exited, inside_total])

        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=gate-history.csv",
            },
        )

    @app.route("/api/anomalies")
    def get_anomalies():
        rows = fetch_active_anomalies()
        floor_lookup = _camera_floor_lookup(camera_registry)
        alerts = [
            {
                "id": row[0],
                "camera_id": row[1],
                "camera_name": row[2],
                "zone_name": row[3],
                "detected_count": row[4],
                "floor": floor_lookup.get(row[1], ""),
                "message": row[5],
                "detected_at": row[6],
            }
            for row in rows
        ]
        anomalies.reset_unread_count()
        return jsonify({"alerts": alerts, "total": len(alerts)})

    @app.route("/api/anomalies/unread_count")
    def get_unread_count():
        return jsonify({"unread": anomalies.get_unread_count()})

    @app.route("/api/anomalies/dismiss/<int:anomaly_id>", methods=["POST"])
    def dismiss_anomaly_route(anomaly_id):
        try:
            dismiss_anomaly(anomaly_id)
            return jsonify({"status": "success", "id": anomaly_id})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/api/anomalies/dismiss_all", methods=["POST"])
    def dismiss_all_anomalies_route():
        try:
            dismiss_all_anomalies()
            return jsonify({"status": "success"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/api/anomalies/history")
    def get_anomaly_history():
        rows = fetch_anomaly_history(limit=100)
        floor_lookup = _camera_floor_lookup(camera_registry)
        alerts = [
            {
                "id": row[0],
                "camera_id": row[1],
                "camera_name": row[2],
                "zone_name": row[3],
                "detected_count": row[4],
                "floor": floor_lookup.get(row[1], ""),
                "message": row[5],
                "is_resolved": bool(row[6]),
                "detected_at": row[7],
            }
            for row in rows
        ]
        return jsonify({"alerts": alerts, "total": len(alerts)})

    @app.route("/api/unity")
    def get_unity_data():
        gate_summary = camera_registry.get_gate_summary()
        unity_payload = {
            "estimated_campus_overview": {
                "active_live_people": 0,
                "total_known_people": 0,
                "total_entered": gate_summary["total_entered"],
                "total_exited": gate_summary["total_exited"],
                "estimated_inside": gate_summary["inside_total"],
            },
            "gate_summary": gate_summary,
            "zones_list": [],
            "cameras": [],
            "recent_anomalies": [],
        }

        zones_temp = {
            zone_name: {
                "name": zone_name,
                "total_count": 0,
                "is_active": False,
                "has_anomaly": False,
            }
            for zone_name in fetch_zones(order_by_name=False)
        }

        cameras_snapshot = camera_registry.snapshot()

        for cam_id, info in cameras_snapshot.items():
            count = info["count"]
            group = info["group"]
            is_active = info.get("is_active", False)

            unity_payload["estimated_campus_overview"]["total_known_people"] += count
            if is_active:
                unity_payload["estimated_campus_overview"]["active_live_people"] += count

            zones_temp.setdefault(
                group,
                {
                    "name": group,
                    "total_count": 0,
                    "is_active": False,
                    "has_anomaly": False,
                },
            )
            zones_temp[group]["total_count"] += count
            if is_active:
                zones_temp[group]["is_active"] = True

            unity_payload["cameras"].append({
                "id": cam_id,
                "name": info["name"],
                "zone": group,
                "floor": info.get("floor", ""),
                "count": count,
                "is_active": is_active,
                "is_gate_camera": info.get("is_gate_camera", False),
                "gate_role": info.get("gate_role", ""),
                "gate_direction": info.get("gate_direction", ""),
                "entry_count": info.get("entry_count", 0),
                "exit_count": info.get("exit_count", 0),
            })

        try:
            anomaly_rows = fetch_recent_unresolved_anomalies(UNITY_ANOMALY_WINDOW_SECONDS)
            floor_lookup = _camera_floor_lookup(camera_registry)
            for row in anomaly_rows:
                zone_name = row[3]
                unity_payload["recent_anomalies"].append({
                    "id": row[0],
                    "camera_id": row[1],
                    "camera_name": row[2],
                    "zone_name": zone_name,
                    "detected_count": row[4],
                    "floor": floor_lookup.get(row[1], ""),
                    "message": row[5],
                    "detected_at": row[6],
                })
                if zone_name in zones_temp:
                    zones_temp[zone_name]["has_anomaly"] = True
        except Exception as e:
            print(f"[UNITY ANOMALY FETCH ERROR] {e}")

        def sort_by_priority(zone_dict):
            try:
                return UNITY_ZONE_ORDER.index(zone_dict["name"])
            except ValueError:
                return 999

        unity_payload["zones_list"] = sorted(list(zones_temp.values()), key=sort_by_priority)
        return jsonify(unity_payload)
