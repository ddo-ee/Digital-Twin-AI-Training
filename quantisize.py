from ultralytics import YOLO

print("Loading original FP32 model...")
model = YOLO("yolov11_v2.pt") # Or yolov8n.pt, whichever you are using

print("Quantizing to FP16 TensorRT Engine. This may take 5-10 minutes...")
# format="engine" tells it to build an NVIDIA TensorRT model
# half=True tells it to quantize from 32-bit to 16-bit
model.export(format="engine", half=True, device=0)

print("Quantization complete! Look for the new .engine file in your folder.")
