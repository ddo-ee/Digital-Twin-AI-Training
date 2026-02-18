import cv2
import mmap
import time
import struct
from ultralytics import YOLO

# --- CONFIGURATION ---
# Resolution must match Unity exactly
WIDTH = 640
HEIGHT = 360
CHANNELS = 3 # RGB
FRAME_SIZE = WIDTH * HEIGHT * CHANNELS
MAP_NAME = "UnityVision" # The "Password" Unity needs to find the memory

# Load Model
model = YOLO("yolov11_v2.pt")
cap = cv2.VideoCapture("rtsp://twin:Tw!nd@w!@172.16.126.203:554/stream1") # Or your RTSP Link

# Create Shared Memory
# We create a memory block big enough for 1 frame + 4 bytes (for a frame ID counter)
shm = mmap.mmap(-1, FRAME_SIZE + 4, tagname=MAP_NAME)

print(f"Streaming {WIDTH}x{HEIGHT} via Shared Memory '{MAP_NAME}'...")

frame_id = 0

while True:
    ret, frame = cap.read()
    if not ret: break

    # 1. Resize to match Unity
    frame = cv2.resize(frame, (WIDTH, HEIGHT))

    # 2. AI Detection
    results = model(frame, verbose=False, conf=0.5)
    annotated = results[0].plot()
    
    # 3. Flip Color (OpenCV is BGR, Unity is RGB)
    # If we don't do this, people will look blue/smurf-like
    annotated = cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)

    # 4. Write to Shared Memory
    try:
        shm.seek(0)
        # Write Frame ID (so Unity knows when image changed)
        shm.write(struct.pack('I', frame_id))
        # Write Raw Pixels
        shm.write(annotated.tobytes())
        
        frame_id += 1
    except Exception as e:
        print(f"Memory Error: {e}")

    # Optional: Show locally
    cv2.imshow("Sender", cv2.cvtColor(annotated, cv2.COLOR_RGB2BGR))
    if cv2.waitKey(1) == ord('q'): break

shm.close()
cap.release()
cv2.destroyAllWindows()