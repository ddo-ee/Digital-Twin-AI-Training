import sys
import cv2
import time
import threading
import sqlite3
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel, QGridLayout, 
                             QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit, 
                             QScrollArea, QGroupBox, QMessageBox, QFrame)
from PyQt5.QtGui import QImage, QPixmap, QFont
from PyQt5.QtCore import Qt, pyqtSignal, QThread
from ultralytics import YOLO

# --- DATABASE & AI SETUP ---
DB_NAME = "campus_cctv.db"
MODEL_PATH = "best.pt"
FALLBACK_PATH = "yolov8n.pt"

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS cameras 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, url TEXT, camera_group TEXT)''')
        conn.commit()

def get_all_cameras():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM cameras")
        return c.fetchall()

init_db()

try:
    model = YOLO(MODEL_PATH) if os.path.exists(MODEL_PATH) else YOLO(FALLBACK_PATH)
except:
    model = YOLO(FALLBACK_PATH)

model_lock = threading.Lock()

# --- WORKER THREAD ---
class CameraThread(QThread):
    change_pixmap_signal = pyqtSignal(QImage)
    update_count_signal = pyqtSignal(int)

    def __init__(self, camera_id, source, name):
        super().__init__()
        self.camera_id = camera_id
        self.source = 0 if source == '0' else source
        self.name = name
        self.is_running = True

    def run(self):
        cap = cv2.VideoCapture(self.source)
        frame_counter = 0
        last_frame = None

        while self.is_running:
            ret, frame = cap.read()
            if not ret:
                time.sleep(1); cap.open(self.source); continue

            frame_counter += 1
            if frame_counter % 4 == 0:
                frame = cv2.resize(frame, (640, 360))
                with model_lock:
                    results = model(frame, conf=0.45, imgsz=320, verbose=False)
                annotated_frame = results[0].plot()
                count = len(results[0].boxes)
                last_frame = annotated_frame
                if self.is_running: self.update_count_signal.emit(count)
            else:
                annotated_frame = last_frame if last_frame is not None else cv2.resize(frame, (640, 360))

            if self.is_running:
                rgb_image = cv2.cvtColor(annotated_frame, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb_image.shape
                qt_image = QImage(rgb_image.data, w, h, ch * w, QImage.Format_RGB888)
                self.change_pixmap_signal.emit(qt_image)
        cap.release()

    def stop(self):
        self.is_running = False; self.quit(); self.wait()

# --- MAIN WINDOW ---
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BatStateU Digital Twin | AI Control Center")
        self.setGeometry(50, 50, 1400, 900)
        
        self.current_cols = 3  # Default grid
        self.total_people = 0
        self.camera_counts = {}

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.outer_layout = QHBoxLayout(self.central_widget)
        self.outer_layout.setContentsMargins(0, 0, 0, 0)
        self.outer_layout.setSpacing(0)

        # 1. SIDEBAR
        self.create_sidebar()

        # 2. MAIN CONTENT
        self.main_content = QWidget()
        self.main_content.setObjectName("mainContent") # This matches the #mainContent in the CSS
        self.main_layout = QVBoxLayout(self.main_content)
        self.main_layout.setContentsMargins(20, 0, 20, 20)
        
        self.create_top_strip()
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("border: none; background-color: transparent;")
        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setSpacing(15)
        self.scroll_area.setWidget(self.grid_container)
        
        self.main_layout.addWidget(self.scroll_area)
        self.outer_layout.addWidget(self.main_content)

        self.apply_styles()
        self.threads = {}
        self.refresh_cameras()

    def create_sidebar(self):
        self.sidebar = QFrame()
        self.sidebar.setFixedWidth(260)
        self.sidebar.setObjectName("sidebar")
        layout = QVBoxLayout(self.sidebar)
        layout.setAlignment(Qt.AlignTop)

        title = QLabel("Live Viewer")
        title.setObjectName("sidebarTitle")
        layout.addWidget(title)
        
        subtitle = QLabel("BatStateU Digital Twin")
        subtitle.setObjectName("sidebarSubtitle")
        layout.addWidget(subtitle)

        layout.addSpacing(30)

        # Total Count Display
        self.total_box = QGroupBox("LIVE CAMPUS ANALYTICS")
        total_layout = QVBoxLayout()
        self.lbl_total_count = QLabel("0")
        self.lbl_total_count.setStyleSheet("font-size: 48px; font-weight: bold; color: #cf6679; padding: 10px;")
        self.lbl_total_count.setAlignment(Qt.AlignCenter)
        total_layout.addWidget(self.lbl_total_count)
        total_layout.addWidget(QLabel("TOTAL PEOPLE DETECTED"))
        self.total_box.setLayout(total_layout)
        layout.addWidget(self.total_box)

        layout.addStretch()

        status_box = QGroupBox("System Status")
        status_layout = QVBoxLayout()
        status_layout.addWidget(QLabel("● AI Model: YOLOv8"))
        status_layout.addWidget(QLabel("● System: Active"))
        status_box.setLayout(status_layout)
        layout.addWidget(status_box)

        self.outer_layout.addWidget(self.sidebar)

    def create_top_strip(self):
        self.top_strip = QFrame()
        self.top_strip.setFixedHeight(110)
        layout = QVBoxLayout(self.top_strip)
        
        # Row 1: Add Camera
        input_row = QHBoxLayout()
        self.input_name = QLineEdit(); self.input_name.setPlaceholderText("Location Name")
        self.input_url = QLineEdit(); self.input_url.setPlaceholderText("RTSP Link or '0'")
        btn_add = QPushButton("ADD STREAM")
        btn_add.setObjectName("addBtn")
        btn_add.clicked.connect(self.add_camera)
        input_row.addWidget(self.input_name, 2); input_row.addWidget(self.input_url, 3); input_row.addWidget(btn_add, 1)
        
        # Row 2: Grid Controls
        grid_row = QHBoxLayout()
        grid_row.addWidget(QLabel("VIEWPORT GRID: "))
        for n in [1, 3, 5, 7]:
            btn = QPushButton(f"{n}x{n}")
            btn.setFixedWidth(60)
            btn.clicked.connect(lambda checked, cols=n: self.update_grid(cols))
            grid_row.addWidget(btn)
        grid_row.addStretch()
        
        layout.addLayout(input_row)
        layout.addLayout(grid_row)
        self.main_layout.addWidget(self.top_strip)

    def update_grid(self, cols):
        self.current_cols = cols
        self.refresh_cameras()

    def apply_styles(self):
        # Path to your image - Use forward slashes / even on Windows!
        bg_path = "LoginUI_BG.png" 
        
        self.setStyleSheet(f"""
            QMainWindow {{ 
                background-color: #0d1117; 
            }}
            
            /* This targets the area where cameras are displayed */
            #mainContent {{
                border-image: url("{bg_path}") 0 0 0 0 stretch stretch;
            }}

            #sidebar {{ 
                background-color: rgba(22, 27, 34, 230); /* Semi-transparent Sidebar */
                border-right: 1px solid #30363d; 
            }}
            
            #sidebarTitle {{ color: #cf6679; font-size: 22px; font-weight: bold; margin-top: 20px; background: transparent; }}
            #sidebarSubtitle {{ color: #8b949e; font-size: 12px; margin-bottom: 20px; background: transparent; }}
            
            QLineEdit {{ background-color: #0d1117; border: 1px solid #30363d; padding: 8px; border-radius: 5px; color: white; }}
            #addBtn {{ background-color: #238636; color: white; font-weight: bold; }}
            
            /* Camera Cards - "Glassmorphism" effect */
            QGroupBox {{ 
                background-color: rgba(22, 27, 34, 180); /* 180 is the transparency (0-255) */
                border: 1px solid #58a6ff; 
                border-radius: 8px; 
                margin-top: 15px; 
                font-weight: bold; 
                color: #58a6ff; 
                padding-top: 15px; 
            }}
            
            /* Make sure the scroll area is transparent to show the background */
            QScrollArea, QWidget#grid_container {{ 
                background: transparent; 
                border: none;
            }}

            QPushButton {{ background-color: #21262d; border: 1px solid #30363d; color: #c9d1d9; padding: 5px; border-radius: 5px; }}
            QPushButton:hover {{ background-color: #30363d; }}
        """)

    def refresh_cameras(self):
        for t in self.threads.values():
            try:
                t.change_pixmap_signal.disconnect()
                t.update_count_signal.disconnect()
            except: pass
            t.stop()
        self.threads.clear()
        self.camera_counts.clear()

        for i in reversed(range(self.grid_layout.count())): 
            w = self.grid_layout.itemAt(i).widget()
            if w: w.setParent(None); w.deleteLater()
        
        cameras = get_all_cameras()
        row, col = 0, 0
        for cam in cameras:
            self.create_camera_card(cam[0], cam[1], cam[2], row, col)
            col += 1
            if col >= self.current_cols: col = 0; row += 1

    def create_camera_card(self, cam_id, name, url, row, col):
        card = QGroupBox(f"  {name.upper()}")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(5, 20, 5, 5)

        video_label = QLabel()
        video_label.setAlignment(Qt.AlignCenter)
        video_label.setStyleSheet("background-color: #000; border-radius: 4px;")
        
        # Dynamic Scaler
        scale_w = 960 if self.current_cols == 1 else (400 if self.current_cols == 3 else 200)
        scale_h = 540 if self.current_cols == 1 else (225 if self.current_cols == 3 else 112)
        video_label.setFixedSize(scale_w, scale_h)

        footer = QHBoxLayout()
        count_label = QLabel("COUNT: 0")
        count_label.setStyleSheet("font-weight: bold; color: #03dac6; font-size: 11px;")
        
        btn_del = QPushButton("REMOVE")
        btn_del.setFixedWidth(70); btn_del.setStyleSheet("font-size: 9px; color: #8b949e;")
        btn_del.clicked.connect(lambda: self.remove_camera(cam_id))

        footer.addWidget(count_label); footer.addStretch(); footer.addWidget(btn_del)
        layout.addWidget(video_label); layout.addLayout(footer)
        self.grid_layout.addWidget(card, row, col)

        thread = CameraThread(cam_id, url, name)
        thread.change_pixmap_signal.connect(lambda q, l=video_label, w=scale_w, h=scale_h: self.safe_upd(l, q, w, h))
        thread.update_count_signal.connect(lambda c, l=count_label, cid=cam_id: self.safe_txt(l, c, cid))
        thread.start()
        self.threads[cam_id] = thread

    def safe_upd(self, lbl, qimg, w, h):
        try: lbl.setPixmap(QPixmap.fromImage(qimg).scaled(w, h, Qt.KeepAspectRatio))
        except RuntimeError: pass

    def safe_txt(self, lbl, c, cid):
        try: 
            lbl.setText(f"COUNT: {c}")
            self.camera_counts[cid] = c
            total = sum(self.camera_counts.values())
            self.lbl_total_count.setText(str(total))
        except RuntimeError: pass

    def add_camera(self):
        if self.input_name.text() and self.input_url.text():
            with sqlite3.connect(DB_NAME) as conn:
                conn.cursor().execute("INSERT INTO cameras (name, url, camera_group) VALUES (?, ?, ?)", 
                                     (self.input_name.text(), self.input_url.text(), "General"))
            self.input_name.clear(); self.input_url.clear(); self.refresh_cameras()

    def remove_camera(self, cam_id):
        if QMessageBox.question(self, 'Delete', "Remove stream?", QMessageBox.Yes|QMessageBox.No) == QMessageBox.Yes:
            with sqlite3.connect(DB_NAME) as conn:
                conn.cursor().execute("DELETE FROM cameras WHERE id=?", (cam_id,))
            self.refresh_cameras()

    def closeEvent(self, event):
        for t in self.threads.values(): t.stop()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())