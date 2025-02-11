# ON RASPBERRYPI >> libcamera-vid -t 0 --width 1280 --height 720 --framerate 30 --codec h264 -o - | gst-launch-1.0 fdsrc ! h264parse ! rtph264pay config-interval=1 pt=96 ! udpsink host=<IP ADDRESS> port=5000
# REPLACE IP ADDRESS WITH IP ADDRESS
#
# ON YOUR LAPTOP: gst-launch-1.0 udpsrc port=5000 ! application/x-rtp, encoding-name=H264 ! rtph264depay ! avdec_h264 ! videoconvert ! autovideosink
#
# Fixing GStreamer=ON for OpenCV: https://discuss.bluerobotics.com/t/opencv-python-with-gstreamer-backend/8842
#
# # <navigate to where you want the opencv-python repo to be stored>
# git clone --recursive https://github.com/skvark/opencv-python.git
# cd opencv-python
# export CMAKE_ARGS="-DWITH_GSTREAMER=ON"
# pip install --upgrade pip wheel
# # this is the build step - the repo estimates it can take from 5 
# #   mins to > 2 hrs depending on your computer hardware
# pip wheel . --verbose
# pip install opencv_python*.whl
# # note, wheel may be generated in dist/ directory, so may have to cd first

from ultralytics import YOLO
import cv2
import time
import subprocess
import threading
import queue
import traceback

# Create a thread-safe queue for TTS messages.
tts_queue = queue.Queue()

def tts_worker():
    """
    Worker thread that continuously processes the TTS queue.
    Instead of using pyttsx3, this worker uses macOS's built-in `say` command.
    """
    print("[TTS] Worker started using mac's say command, waiting for messages...")
    while True:
        message = tts_queue.get()
        if message is None:  # A None message signals the worker to exit.
            print("[TTS] Received termination signal. Exiting worker thread.")
            break
        try:
            print("[TTS] Speaking message using say:", message)
            # Call macOS's say command. Optionally, you can specify a voice with -v (e.g., -v Samantha)
            subprocess.run(["say", message])
            print("[TTS] Finished speaking message.")
        except Exception as e:
            print("[TTS] Error in TTS worker using say:", e)
            traceback.print_exc()
        tts_queue.task_done()
    print("[TTS] Worker thread exiting.")

# we convert the detected objects into cohesive sentences by running it through llama3.2:latest (hosted locally via Ollama)
def query_llama(prompt: str) -> str:
    """
    Query the local Llama 3.2 model (llama3.2:latest) via Ollama with the given prompt.
    The system prompt instructs the model as follows:
      - You are a helpful assistant for a smart AI cane project designed for blind or visually impaired people.
      - The cane has an integrated camera that detects objects in real-time and informs the user via voice messages to their earbuds.
      - Given a list of objects, generate a single, clear sentence of 8 to 10 words max that describes these objects.
      - Do not add any details not provided.
    Returns the generated response as a string.
    """
    system_prompt = (
        "You are a helpful assistant for a smart AI cane project designed for blind or visually impaired people. "
        "The cane has an integrated camera that detects objects in real-time and informs the user via voice messages to their earbuds. "
        "Given a list of objects, generate a single, clear sentence of 8 to 10 words max that describes these objects. "
        "Do not add any details not provided. "
    )
    full_prompt = system_prompt + prompt
    print("[LLAMA] Full prompt:", full_prompt)
    try:
        result = subprocess.run(
            ["ollama", "run", "llama3.2:latest", full_prompt],
            capture_output=True,
            text=True,
            check=True
        )
        response = result.stdout.strip()
        print("[LLAMA] Response received:", response)
        return response
    except subprocess.CalledProcessError as e:
        print("[LLAMA] Error querying Llama:", e)
        traceback.print_exc()
        return prompt  # Fallback: return the original prompt as a response.

def main():
    print("[MAIN] Starting application.")
    try:
        # Start the TTS worker thread (non-daemon so it keeps the process alive)
        tts_thread = threading.Thread(target=tts_worker)
        tts_thread.start()
        print("[MAIN] TTS worker thread started.")

        # Load the YOLO model.
        model = YOLO("./models/yolo-tuned.pt")
        print("[MAIN] YOLO model loaded successfully.")

        # Optionally, define a GStreamer pipeline (if you wish to use it) ** This is used for broadcasting the camera's live feed from our RaspberryPI **
        gstreamer_pipeline = (
            "udpsrc port=5000 ! application/x-rtp, encoding-name=H264 ! "
            "rtph264depay ! avdec_h264 ! videoconvert ! appsink"
        )

        # Option 1: Use GStreamer pipeline
        # cap = cv2.VideoCapture(gstreamer_pipeline, cv2.CAP_GSTREAMER)

        # Option 2: Use the default webcam (uncomment if desired)
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("[MAIN] Error: Could not open the video source.")
            return
        else:
            print("[MAIN] Video source opened successfully.")

        # Variables for managing voice announcements.
        last_announced_time = time.time()
        announcement_interval = 3  # seconds between announcements
        last_announced_objects = set()
        frame_count = 0

        print("[MAIN] Entering main loop. Press 'q' in the display window to quit.")

        while True:
            frame_count += 1
            current_time = time.time()
            ret, frame = cap.read()

            if not ret:
                print("[MAIN] Warning: Failed to capture frame. Attempting to reinitialize camera...")
                cap.release()
                time.sleep(1)
                cap = cv2.VideoCapture(0)
                if not cap.isOpened():
                    print("[MAIN] Error: Camera reinitialization failed. Retrying...")
                    continue
                else:
                    print("[MAIN] Camera reinitialized successfully.")
                    continue

            print(f"[MAIN] Processing frame #{frame_count} at {current_time:.2f}")
            try:
                results = model(frame)
            except Exception as e:
                print(f"[MAIN] Error during YOLO inference on frame #{frame_count}: {e}")
                traceback.print_exc()
                continue

            current_objects = set()
            if results and len(results) > 0:
                try:
                    boxes = results[0].boxes
                except Exception as e:
                    print(f"[MAIN] Error accessing boxes on frame #{frame_count}: {e}")
                    boxes = None

                if boxes is not None:
                    for box in boxes:
                        try:
                            coords = box.xyxy.cpu().numpy()[0].astype(int)
                            conf = float(box.conf.cpu().numpy()[0])
                            cls = int(box.cls.cpu().numpy()[0])
                        except Exception as e:
                            print(f"[MAIN] Error processing detection on frame #{frame_count}: {e}")
                            continue

                        if hasattr(model, 'names') and (cls in model.names):
                            label = model.names[cls]
                        else:
                            label = str(cls)
                        current_objects.add(label)

                        x1, y1, x2, y2 = coords
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                        cv2.putText(frame, f'{label} {conf:.2f}', (x1, max(y1 - 10, 0)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                else:
                    print(f"[MAIN] No boxes detected on frame #{frame_count}.")

            # Announce if it's time and if the detected objects have changed.
            if current_time - last_announced_time > announcement_interval:
                if current_objects != last_announced_objects:
                    if current_objects:
                        prompt = "Objects detected: " + ", ".join(current_objects) + "."
                    else:
                        prompt = "No objects detected."
                    print(f"[MAIN] Querying Llama on frame #{frame_count} with prompt: {prompt}")
                    response = query_llama(prompt)
                    print(f"[MAIN] Llama response on frame #{frame_count}: {response}")
                    tts_queue.put(response)
                    last_announced_time = current_time
                    last_announced_objects = current_objects.copy()
                else:
                    print(f"[MAIN] No change in detected objects on frame #{frame_count}.")

            cv2.imshow("EchoPath Object Detection", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                print("[MAIN] 'q' pressed. Exiting main loop.")
                break

            print(f"[MAIN] End of frame #{frame_count} processing.\n")

    except Exception as e:
        print("[MAIN] Exception in main loop:", e)
        traceback.print_exc()
    finally:
        print("[MAIN] Releasing camera and closing OpenCV windows.")
        cap.release()
        cv2.destroyAllWindows()

        # Signal the TTS worker thread to exit.
        tts_queue.put(None)
        print("[MAIN] Waiting for TTS worker thread to terminate...")
        tts_thread.join()
        print("[MAIN] TTS worker thread terminated.")
        print("[MAIN] Application shutting down.")

if __name__ == '__main__':
    main()
