import cv2
from ultralytics import YOLO

# 1. Load your model
model = YOLO('runs/detect/Digital_Twin_Versions/Trial1/weights/best.pt')

# 2. Set the source
rtsp_url = "rtsp://twin:Tw!nd@w!@172.16.126.203:554/stream1"

# Use the stream directly
results = model.predict(source=rtsp_url, imgsz=1080, show=False, stream=True)

# Create a named window
cv2.namedWindow("CCTV_Detection", cv2.WINDOW_NORMAL)

for r in results:
    # Get the frame with the bounding boxes
    annotated_frame = r.plot(labels=False, conf=False)
    
    # --- ADD COUNTER LOGIC ---
    # Count how many boxes were detected in this specific frame
    person_count = len(r.boxes)
    
    # Define text settings
    text = f"Total Persons: {person_count}"
    font = cv2.FONT_HERSHEY_SIMPLEX
    org = (50, 50) # Position: 50 pixels from left, 50 from top
    fontScale = 1.2
    color = (0, 255, 0) # Green text
    thickness = 3

    # Optional: Draw a dark semi-transparent rectangle behind the text for visibility
    cv2.rectangle(annotated_frame, (30, 10), (450, 70), (0, 0, 0), -1)
    
    # Put the count text on the frame
    cv2.putText(annotated_frame, text, org, font, fontScale, color, thickness, cv2.LINE_AA)
    # -------------------------

    # Resize the display window
    display_frame = cv2.resize(annotated_frame, (1280, 720))
    
    cv2.imshow("CCTV_Detection", display_frame)
    
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cv2.destroyAllWindows()