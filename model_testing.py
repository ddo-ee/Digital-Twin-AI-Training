import cv2
from ultralytics import YOLO
import time

# 1. Load your model
model = YOLO('runs/detect/Digital_Twin_Versions/Trial1/weights/best.pt')

# 2. Set the source
rtsp_url = "rtsp://twin:Tw!nd@w!@172.16.126.203:554/stream1"

# Use the stream directly
results = model.predict(source=rtsp_url, imgsz=1080, show=False, stream=True)

# Create a named window
cv2.namedWindow("CCTV_Detection", cv2.WINDOW_NORMAL)

# Timer for terminal logging (so it prints every 2 seconds instead of every frame)
last_log_time = time.time()

for r in results:
    # Get the frame with the bounding boxes
    annotated_frame = r.plot(labels=False, conf=False)
    
    # --- METRICS LOGIC ---
    person_count = len(r.boxes)
    
    # Get all confidence scores for this frame
    confidences = r.boxes.conf.tolist()
    if confidences:
        avg_conf = (sum(confidences) / len(confidences)) * 100
    else:
        avg_conf = 0.0

    # --- TERMINAL LOGGING (For Documentation) ---
    current_time = time.time()
    if current_time - last_log_time > 2.0: # Print summary every 2 seconds
        print(f"[REPORT] Time: {time.strftime('%H:%M:%S')} | "
              f"Count: {person_count} | "
              f"Avg Confidence: {avg_conf:.2f}%")
        last_log_time = current_time
    
    # --- ONSCREEN DISPLAY ---
    # Draw background box for text
    cv2.rectangle(annotated_frame, (30, 10), (600, 110), (0, 0, 0), -1)
    
    # Display Person Count
    cv2.putText(annotated_frame, f"Total Persons: {person_count}", (50, 50), 
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 3, cv2.LINE_AA)
    
    # Display Average Confidence
    cv2.putText(annotated_frame, f"Avg Confidence: {avg_conf:.1f}%", (50, 95), 
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)

    # Resize and Show
    display_frame = cv2.resize(annotated_frame, (1280, 720))
    cv2.imshow("CCTV_Detection", display_frame)
    
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cv2.destroyAllWindows()