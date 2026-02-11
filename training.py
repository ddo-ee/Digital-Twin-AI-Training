from ultralytics import YOLO
import torch

def train_model():
    # 1. VERSIONING - Change these for every new dataset/experiment
    PROJECT_NAME = "Digital_Twin_Versions" 
    MODEL_VERSION = "V1 Yolov11m" # This will be the folder name
    
    # 2. Setup Device (Safe for your 3050 Ti)
    device = 0 if torch.cuda.is_available() else 'cpu'
    
    # 3. Load YOLO11s (The Small version for better distant detection)
    model = YOLO('yolo11m.pt') 

    # 4. Train with custom save location
    model.train(
        data='data.yaml',
        epochs=50,
        imgsz=640,
        batch=16,
        device=device,
        workers=2,
        project=PROJECT_NAME,  # Main folder
        name=MODEL_VERSION,    # Sub-folder
        exist_ok=True          # If you restart the training, it won't create 'V4_2'
    )

if __name__ == '__main__':
    train_model()