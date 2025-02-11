# Note: You WILL have to modify the training arguments to make it compatible with your device. This training configuration was for Apple Mac M4 Pro Max

from ultralytics import YOLO

# Load YOLOv11 model
model = YOLO("../yolo11n-base.pt")  # Change if using a different checkpoint

# Train with Apple Metal (MPS)
model.train(
    data="../../datasets/COCO128/data.yaml",   # Dataset configuration
    epochs=100,         # Number of epochs
    imgsz=640,          # Image resolution
    batch=16,           # Adjust batch size based on memory
    device="mps"        # Use Apple MPS for GPU acceleration
)

model.val(data="../../datasets/COCO128/data.yaml", device="mps")
