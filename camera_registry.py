from threading import Lock


class CameraRegistry:
    def __init__(self):
        self._lock = Lock()
        self._cameras = {}

    def snapshot(self):
        with self._lock:
            return {
                cam_id: info.copy()
                for cam_id, info in self._cameras.items()
            }

    def get(self, camera_id):
        with self._lock:
            camera = self._cameras.get(camera_id)
            return camera.copy() if camera else None

    def has(self, camera_id):
        with self._lock:
            return camera_id in self._cameras

    def add(self, camera_id, camera_data):
        with self._lock:
            self._cameras[camera_id] = camera_data.copy()

    def remove(self, camera_id):
        with self._lock:
            return self._cameras.pop(camera_id, None)

    def update_detection(self, camera_id, count, last_updated):
        with self._lock:
            if camera_id in self._cameras:
                self._cameras[camera_id]["count"] = count
                self._cameras[camera_id]["last_updated"] = last_updated
                return True
            return False

    def update_gate_totals(self, camera_id, entry_increment=0, exit_increment=0):
        with self._lock:
            if camera_id not in self._cameras:
                return None

            info = self._cameras[camera_id]
            info["entry_count"] = info.get("entry_count", 0) + entry_increment
            info["exit_count"] = info.get("exit_count", 0) + exit_increment
            return {
                "entry_count": info["entry_count"],
                "exit_count": info["exit_count"],
            }

    def set_gate_config(self, camera_id, gate_config):
        with self._lock:
            if camera_id not in self._cameras:
                return False

            info = self._cameras[camera_id]
            if gate_config:
                info["is_gate_camera"] = True
                info["gate_role"] = gate_config.get("camera_role", "entrance")
                info["gate_direction"] = gate_config.get("direction", "")
                info["gate_split_x"] = gate_config.get("split_x")
                info["gate_separator_points"] = gate_config.get("separator_points", [])
                info["gate_roi_points"] = gate_config.get("roi_points", [])
                info["gate_reference_image_path"] = gate_config.get("reference_image_path", "")
            else:
                info["is_gate_camera"] = False
                info["gate_role"] = ""
                info["gate_direction"] = ""
                info["gate_split_x"] = None
                info["gate_separator_points"] = []
                info["gate_roi_points"] = []
                info["gate_reference_image_path"] = ""
            return True

    def reset_gate_totals(self, camera_id):
        with self._lock:
            if camera_id not in self._cameras:
                return False
            self._cameras[camera_id]["entry_count"] = 0
            self._cameras[camera_id]["exit_count"] = 0
            return True

    def reset_all_gate_totals(self):
        with self._lock:
            reset_camera_ids = []
            for camera_id, info in self._cameras.items():
                if not info.get("is_gate_camera", False):
                    continue

                info["entry_count"] = 0
                info["exit_count"] = 0
                reset_camera_ids.append(camera_id)

            return reset_camera_ids

    def get_gate_summary(self):
        with self._lock:
            configured_cameras = []
            total_entered = 0
            total_exited = 0

            for cam_id, info in self._cameras.items():
                if not info.get("is_gate_camera", False):
                    continue

                entry_count = info.get("entry_count", 0)
                exit_count = info.get("exit_count", 0)
                total_entered += entry_count
                total_exited += exit_count
                configured_cameras.append({
                    "id": cam_id,
                    "name": info.get("name", cam_id),
                    "camera_role": info.get("gate_role", ""),
                    "direction": info.get("gate_direction", ""),
                    "entry_count": entry_count,
                    "exit_count": exit_count,
                })

            return {
                "total_entered": total_entered,
                "total_exited": total_exited,
                "inside_total": total_entered - total_exited,
                "cameras": configured_cameras,
            }

    def update_camera(self, camera_id, *, name=None, group=None, floor=None):
        with self._lock:
            if camera_id not in self._cameras:
                return False
            if name is not None:
                self._cameras[camera_id]["name"] = name
            if group is not None:
                self._cameras[camera_id]["group"] = group
            if floor is not None:
                self._cameras[camera_id]["floor"] = floor
            return True

    def move_group_to_unassigned(self, zone_name):
        with self._lock:
            for info in self._cameras.values():
                if info["group"] == zone_name:
                    info["group"] = "Unassigned"

    def set_group_active(self, group_name, is_active):
        with self._lock:
            for info in self._cameras.values():
                if info["group"] == group_name:
                    info["is_active"] = is_active

    def set_all_active(self, is_active):
        with self._lock:
            for info in self._cameras.values():
                info["is_active"] = is_active

    def set_gate_cameras_active(self, is_active):
        with self._lock:
            affected = 0
            for info in self._cameras.values():
                if info.get("is_gate_camera", False):
                    info["is_active"] = is_active
                    affected += 1
            return affected

    def toggle_active(self, camera_id):
        with self._lock:
            if camera_id not in self._cameras:
                return None
            current_state = self._cameras[camera_id].get("is_active", False)
            self._cameras[camera_id]["is_active"] = not current_state
            return self._cameras[camera_id]["is_active"]
