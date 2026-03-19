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

    def toggle_active(self, camera_id):
        with self._lock:
            if camera_id not in self._cameras:
                return None
            current_state = self._cameras[camera_id].get("is_active", False)
            self._cameras[camera_id]["is_active"] = not current_state
            return self._cameras[camera_id]["is_active"]
