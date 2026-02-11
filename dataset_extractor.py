import cv2
import os

# 1. SETUP: Put the names of all your videos in this list
video_files = [ 'library-backs.mkv',  ]
output_folder = 'all_extracted_frames'
frame_interval = 60  # Extract one frame every 60 frames (adjust as needed) 

if not os.path.exists(output_folder):
    os.makedirs(output_folder)

for video_name in video_files:
    # Get the name of the video without the ".mp4" extension
    prefix = os.path.splitext(video_name)[0]
    
    cap = cv2.VideoCapture(video_name)
    count = 0
    saved_count = 0

    print(f"Extracting from {video_name}...")

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        
        if count % frame_interval == 0:
            # UNIQUE FILENAME: prefix_0001.jpg (e.g., video1_0001.jpg)
            file_name = f"{output_folder}/{prefix}_{saved_count:04d}.jpg"
            cv2.imwrite(file_name, frame)
            saved_count += 1
            
        count += 1

    cap.release()
    print(f"Finished {video_name}. Saved {saved_count} images.")

print("All videos processed without overwriting!")