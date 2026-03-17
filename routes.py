import time

from flask import Response, jsonify, redirect, render_template, request, url_for

import anomalies
from camera_streams import generate_frames, start_camera_thread, stop_camera_thread
from config import UNITY_ANOMALY_WINDOW_SECONDS, UNITY_ZONE_ORDER
from database import (
    add_zone,
    delete_camera,
    dismiss_all_anomalies,
    dismiss_anomaly,
    fetch_active_anomalies,
    fetch_anomaly_history,
    fetch_history,
    fetch_recent_unresolved_anomalies,
    fetch_zones,
    insert_camera,
    remove_zone,
    update_camera,
)


def register_routes(app, camera_registry):
    @app.route("/")
    def index():
        return render_template(
            "index.html",
            cameras=camera_registry.snapshot(),
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
        new_id = f"cam_{int(time.time())}"

        insert_camera(new_id, camera_name, camera_url, camera_group)
        camera_registry.add(
            new_id,
            {
                "name": camera_name,
                "url": camera_url,
                "group": camera_group,
                "count": 0,
                "is_active": False,
                "last_updated": time.time(),
            },
        )
        start_camera_thread(camera_registry, new_id, camera_url)
        return redirect(url_for("index"))

    @app.route("/remove_camera/<camera_id>")
    def remove_camera_route(camera_id):
        if camera_registry.has(camera_id):
            stop_camera_thread(camera_id)
            camera_registry.remove(camera_id)
            delete_camera(camera_id)
        return redirect(url_for("index"))

    @app.route("/api/update_camera/<camera_id>", methods=["POST"])
    def update_camera_route(camera_id):
        new_name = request.json.get("new_name")
        new_zone = request.json.get("new_zone")

        if not new_name or not new_name.strip():
            return jsonify({"status": "error", "message": "Name cannot be empty"}), 400

        if camera_registry.has(camera_id):
            clean_name = new_name.strip()
            try:
                update_camera(camera_id, clean_name, new_zone)
                camera_registry.update_camera(
                    camera_id,
                    name=clean_name,
                    group=new_zone if new_zone else None,
                )
                return jsonify({"status": "success", "new_name": clean_name, "new_zone": new_zone})
            except Exception as e:
                return jsonify({"status": "error", "message": str(e)}), 500

        return jsonify({"status": "error", "message": "Camera not found"}), 404

    @app.route("/api/stats")
    def get_stats():
        cameras_snapshot = camera_registry.snapshot()
        data = {
            cid: {
                "name": info["name"],
                "count": info["count"],
                "group": info["group"],
                "is_active": info.get("is_active", False),
                "last_updated": info.get("last_updated", time.time()),
            }
            for cid, info in cameras_snapshot.items()
        }
        return jsonify(data)

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

    @app.route("/api/toggle/<camera_id>", methods=["POST"])
    def toggle_camera(camera_id):
        new_state = camera_registry.toggle_active(camera_id)
        if new_state is not None:
            return jsonify({"status": "success", "is_active": new_state})
        return jsonify({"status": "error"})

    @app.route("/api/history")
    def get_history():
        campus_data, zone_data = fetch_history()

        labels = [row[0].split(" ")[1] for row in campus_data]
        campus_counts = [row[1] for row in campus_data]

        zones = {}
        for row in zone_data:
            z_name, z_count = row[1], row[2]
            zones.setdefault(z_name, [])
            zones[z_name].append(z_count)
            if len(zones[z_name]) > 20:
                zones[z_name] = zones[z_name][-20:]

        return jsonify({
            "labels": labels,
            "campus_overview": campus_counts,
            "zones": zones,
        })

    @app.route("/api/anomalies")
    def get_anomalies():
        rows = fetch_active_anomalies()
        alerts = [
            {
                "id": row[0],
                "camera_id": row[1],
                "camera_name": row[2],
                "zone_name": row[3],
                "message": row[4],
                "detected_at": row[5],
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
        alerts = [
            {
                "id": row[0],
                "camera_id": row[1],
                "camera_name": row[2],
                "zone_name": row[3],
                "message": row[4],
                "is_resolved": bool(row[5]),
                "detected_at": row[6],
            }
            for row in rows
        ]
        return jsonify({"alerts": alerts, "total": len(alerts)})

    @app.route("/api/unity")
    def get_unity_data():
        unity_payload = {
            "campus_overview": {
                "active_live_people": 0,
                "total_known_people": 0,
            },
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

            unity_payload["campus_overview"]["total_known_people"] += count
            if is_active:
                unity_payload["campus_overview"]["active_live_people"] += count

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
                "count": count,
                "is_active": is_active,
            })

        try:
            anomaly_rows = fetch_recent_unresolved_anomalies(UNITY_ANOMALY_WINDOW_SECONDS)
            for row in anomaly_rows:
                zone_name = row[3]
                unity_payload["recent_anomalies"].append({
                    "id": row[0],
                    "camera_id": row[1],
                    "camera_name": row[2],
                    "zone_name": zone_name,
                    "message": row[4],
                    "detected_at": row[5],
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
