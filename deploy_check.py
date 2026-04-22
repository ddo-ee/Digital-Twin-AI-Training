import importlib
import os
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent

REQUIRED_PYTHON = (3, 10)
REQUIRED_MODULES = {
    "flask": "Flask",
    "flask_cors": "Flask-Cors",
    "numpy": "numpy",
    "cv2": "opencv-python",
    "ultralytics": "ultralytics",
    "werkzeug": "Werkzeug",
}
CORE_FILES = [
    "cctv_manager.py",
    "camera_registry.py",
    "camera_streams.py",
    "database.py",
    "routes.py",
    "config.py",
    "templates/index.html",
    "templates/login.html",
    "static/style.css",
]
OPTIONAL_UI_ASSETS = [
    "static/bsuLogo.png",
    "static/LoginUI_BG.png",
]


def check_python():
    current = sys.version_info[:3]
    ok = current >= REQUIRED_PYTHON
    detail = f"Python {current[0]}.{current[1]}.{current[2]}"
    if ok:
        return ("PASS", f"{detail} is compatible.")
    return ("FAIL", f"{detail} is too old. Use Python {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]} or newer.")


def check_imports():
    results = []
    imported = {}

    for module_name, package_name in REQUIRED_MODULES.items():
        try:
            imported[module_name] = importlib.import_module(module_name)
            results.append(("PASS", f"Imported `{module_name}` from package `{package_name}`."))
        except Exception as exc:
            results.append(("FAIL", f"Missing `{package_name}` (`import {module_name}` failed: {exc})."))

    return results, imported


def check_core_files():
    results = []
    for relative_path in CORE_FILES:
        full_path = ROOT / relative_path
        if full_path.exists():
            results.append(("PASS", f"Found `{relative_path}`."))
        else:
            results.append(("FAIL", f"Missing required file `{relative_path}`."))
    return results


def check_optional_assets():
    results = []
    for relative_path in OPTIONAL_UI_ASSETS:
        full_path = ROOT / relative_path
        if full_path.exists():
            results.append(("PASS", f"Found optional UI asset `{relative_path}`."))
        else:
            results.append(("WARN", f"Optional UI asset `{relative_path}` is missing. The app may still run, but the interface will look incomplete."))
    return results


def check_config_runtime():
    results = []
    try:
        config = importlib.import_module("config")
    except Exception as exc:
        return [("FAIL", f"Unable to import `config.py`: {exc}")]

    db_path = ROOT / getattr(config, "DB_NAME", "campus_security.db")
    model_path = ROOT / getattr(config, "MODEL_PATH", "")
    upload_dir = ROOT / getattr(config, "GATE_CONFIG_UPLOAD_DIR", os.path.join("static", "uploads", "gate_configs"))

    if model_path.exists():
        results.append(("PASS", f"Configured model file exists: `{model_path.name}`."))
    else:
        results.append(("FAIL", f"Configured model file is missing: `{model_path.name}`."))

    if db_path.exists():
        results.append(("PASS", f"Database file exists: `{db_path.name}`."))
    else:
        results.append(("WARN", f"Database file `{db_path.name}` does not exist yet. The app can create it on first run."))

    try:
        upload_dir.mkdir(parents=True, exist_ok=True)
        test_file = upload_dir / ".deploy_check.tmp"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        results.append(("PASS", f"Upload directory is writable: `{upload_dir.relative_to(ROOT)}`."))
    except Exception as exc:
        results.append(("FAIL", f"Upload directory is not writable: `{upload_dir}` ({exc})."))

    if getattr(config, "LOGIN_PASSWORD", None) == "dash123":
        results.append(("WARN", "Default login password `dash123` is still configured. Set `DT_WEBAPP_PASSWORD` before deploying."))
    else:
        results.append(("PASS", "A non-default web login password is configured."))

    if not os.environ.get("DT_SECRET_KEY"):
        results.append(("WARN", "`DT_SECRET_KEY` is not set. Sessions will change each restart."))
    else:
        results.append(("PASS", "`DT_SECRET_KEY` is set."))

    model_suffix = model_path.suffix.lower()
    if model_suffix == ".engine":
        results.append(("WARN", "TensorRT engine selected. The target device must match the TensorRT/CUDA environment used to build this engine."))
    elif model_suffix == ".onnx":
        results.append(("WARN", "ONNX model selected. Confirm the target device has a compatible runtime path for ONNX inference."))
    else:
        results.append(("PASS", f"Model format `{model_suffix or 'unknown'}` can run through the Ultralytics loader."))

    return results


def check_sqlite():
    db_path = ROOT / "campus_security.db"
    if not db_path.exists():
        return [("WARN", "SQLite check skipped because `campus_security.db` does not exist yet.")]

    try:
        with sqlite3.connect(db_path, timeout=5) as conn:
            conn.execute("SELECT name FROM sqlite_master LIMIT 1")
        return [("PASS", "SQLite database can be opened successfully.")]
    except Exception as exc:
        return [("FAIL", f"SQLite database could not be opened: {exc}")]


def check_opencv_runtime(imported_modules):
    cv2 = imported_modules.get("cv2")
    results = []
    if cv2 is None:
        return results

    try:
        build_info = cv2.getBuildInformation()
        if "FFMPEG:                      YES" in build_info:
            results.append(("PASS", "OpenCV was built with FFmpeg support."))
        else:
            results.append(("WARN", "OpenCV FFmpeg support was not detected. RTSP streams may fail on this device."))
    except Exception as exc:
        results.append(("WARN", f"Could not inspect OpenCV build flags: {exc}"))

    return results


def check_torch_runtime():
    try:
        torch = importlib.import_module("torch")
    except Exception as exc:
        return [("WARN", f"`torch` is not importable yet: {exc}. Ultralytics may install it transitively, but verify this on the target device.")]

    if torch.cuda.is_available():
        return [("PASS", f"CUDA is available through torch ({torch.cuda.get_device_name(0)}).")]
    return [("WARN", "CUDA is not available. The app can still run on CPU, but inference will likely be much slower.")]


def print_section(title, results):
    print(f"\n[{title}]")
    for status, message in results:
        print(f"{status:<5} {message}")


def summarize(all_results):
    fail_count = sum(1 for status, _ in all_results if status == "FAIL")
    warn_count = sum(1 for status, _ in all_results if status == "WARN")

    print("\n[SUMMARY]")
    print(f"FAIL: {fail_count}")
    print(f"WARN: {warn_count}")
    if fail_count:
        print("Deployment readiness: NOT READY")
        return 1
    if warn_count:
        print("Deployment readiness: READY WITH WARNINGS")
        return 0
    print("Deployment readiness: READY")
    return 0


def main():
    all_results = []

    python_results = [check_python()]
    print_section("Python", python_results)
    all_results.extend(python_results)

    import_results, imported_modules = check_imports()
    print_section("Python Packages", import_results)
    all_results.extend(import_results)

    core_file_results = check_core_files()
    print_section("Project Files", core_file_results)
    all_results.extend(core_file_results)

    optional_asset_results = check_optional_assets()
    print_section("Optional UI Assets", optional_asset_results)
    all_results.extend(optional_asset_results)

    config_results = check_config_runtime()
    print_section("Runtime Configuration", config_results)
    all_results.extend(config_results)

    sqlite_results = check_sqlite()
    print_section("SQLite", sqlite_results)
    all_results.extend(sqlite_results)

    opencv_results = check_opencv_runtime(imported_modules)
    print_section("OpenCV Runtime", opencv_results)
    all_results.extend(opencv_results)

    torch_results = check_torch_runtime()
    print_section("Torch Runtime", torch_results)
    all_results.extend(torch_results)

    raise SystemExit(summarize(all_results))


if __name__ == "__main__":
    main()
