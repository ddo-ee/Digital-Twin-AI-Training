import cv2
import os

# --- SETTINGS ---
CAMERA_ID = "cam_1771471864"  # Change this for each camera
FILENAME = f"{CAMERA_ID}_coords.txt"
points = []

def click_event(event, x, y, flags, params):
    global points
    if event == cv2.EVENT_LBUTTONDOWN:
        points.append([x, y])
        print(f"Added: [{x}, {y}]")
        
        # Draw on image
        cv2.circle(img_display, (x, y), 5, (0, 0, 255), -1)
        if len(points) > 1:
            cv2.line(img_display, tuple(points[-2]), tuple(points[-1]), (0, 255, 255), 2)
        cv2.imshow('Coordinate Finder', img_display)

# 1. Load your image
img_path = os.path.join("coordinates", "aeb-1sf_0000.jpg")
img_raw = cv2.imread(img_path)
# Standardize the window size so coordinates are consistent
img_display = cv2.resize(img_raw, (1920, 1080))

cv2.namedWindow('Coordinate Finder', cv2.WINDOW_NORMAL)
cv2.setMouseCallback('Coordinate Finder', click_event)

print(f"--- {CAMERA_ID} CONFIGURATION ---")
print("1. Click points to define the polygon.")
print("2. Press 's' to SAVE to text file.")
print("3. Press 'c' to CLEAR points and start over.")
print("4. Press 'q' to QUIT.")

while True:
    cv2.imshow('Coordinate Finder', img_display)
    key = cv2.waitKey(1) & 0xFF
    
    if key == ord('s'):
        with open(FILENAME, 'w') as f:
            for p in points:
                f.write(f"{p[0]},{p[1]}\n")
        print(f"DONE! Coordinates saved to {FILENAME}")
        break
        
    elif key == ord('c'):
        points = []
        img_display = cv2.resize(img_raw, (2160, 1140))
        print("Cleared all points.")
        
    elif key == ord('q'):
        break

cv2.destroyAllWindows()