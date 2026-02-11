from sahi import AutoDetectionModel
from sahi.predict import get_sliced_prediction
import cv2

# 1. Load your custom YOLOv11 model into the SAHI wrapper
detection_model = AutoDetectionModel.from_pretrained(
    model_type="ultralytics",
    model_path="path/to/your/best.pt", # Use your V5 model
    confidence_threshold=0.3,
    device="cuda:0" # Uses your 3050 Ti
)

# 2. Read your amphitheater image
image = cv2.imread("amphitheater_frame.jpg")
image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

# 3. Perform Sliced Inference
result = get_sliced_prediction(
    image_rgb,
    detection_model,
    slice_height=320, # Slices the 640 or 1080p image into smaller windows
    slice_width=320,
    overlap_height_ratio=0.2, # Overlap helps detect people cut by the slice edge
    overlap_width_ratio=0.2
)

# 4. View results
result.export_visuals(export_dir="outputs/")
print(f"People detected with Tiling: {len(result.object_prediction_list)}")