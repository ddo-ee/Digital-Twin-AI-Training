import cv2
import socket
import struct
import math
import time
from ultralytics import YOLO

# --- CONFIGURATION ---
UDP_IP = "127.0.0.1"
UDP_PORT = 5005
MODEL_PATH = "best.pt" # Use 'best.pt' if you have it

# Initialize UDP Socket
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)

# Load Model
print("Loading YOLO model...")
model = YOLO(MODEL_PATH)

# Start Camera (0 is usually your webcam)
cap = cv2.VideoCapture("rtsp://twin:Tw!nd@w!@172.16.126.203:554/stream1")

print(f"Streaming to Unity at {UDP_IP}:{UDP_PORT}...")
print("Press 'q' to stop.")

while True:
    ret, frame = cap.read()
    if not ret:
        break

    # 1. RESIZE (Crucial for UDP speed)
    # 640x360 is a standard 16:9 aspect ratio that fits in a UDP packet
    frame = cv2.resize(frame, (640, 360))

    # 2. RUN AI DETECTION
    results = model(frame, verbose=False, conf=0.5)
    
    # 3. DRAW BOXES & COUNT ON THE IMAGE
    annotated_frame = results[0].plot()
    
    # Count people
    person_count = len([x for x in results[0].boxes.cls if int(x) == 0]) # 0 is 'person'
    
    # Draw a big counter on the video
    cv2.rectangle(annotated_frame, (10, 10), (250, 60), (0, 0, 0), -1)
    cv2.putText(annotated_frame, f"Total Person: {person_count}", (20, 45), 
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

    # 4. COMPRESS TO JPEG
    # quality=50 is a good balance between speed and looks
    _, buffer = cv2.imencode('.jpg', annotated_frame, [cv2.IMWRITE_JPEG_QUALITY, 100])
    
    # 5. SEND OVER UDP
    try:
        # We only send if the packet is small enough (safe limit ~60kb)
        if len(buffer) < 60000:
            sock.sendto(buffer.tobytes(), (UDP_IP, UDP_PORT))
        else:
            print("Frame too big, skipping...")
    except Exception as e:
        print(f"Error: {e}")

    # Optional: Show on screen to verify Python is working
    cv2.imshow("Python Sender", annotated_frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()