import os
import time
import cv2
from datetime import datetime
from navel import ChestCamera  # Make sure this says HeadCamera or ChestCamera based on what you want

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(SCRIPT_DIR, "head_frames")

if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    print("Starting Headless Camera Capture...")
    print(f"Saving images to: {OUT_DIR}")
    print("Press Ctrl+C in the terminal to stop.")
    
    try:
        with ChestCamera() as cam:
            while True:
                # Fetch the frame
                frame = cam.get_frame(timeout_ms=500)
                
                # Convert from Navel's RGB to OpenCV's BGR format
                img_bgr = cv2.cvtColor(frame.data, cv2.COLOR_RGB2BGR)
                
                # Generate timestamp
                ts = datetime.now()
                stamp = ts.strftime("%Y%m%d_%H%M%S")
                path = os.path.join(OUT_DIR, f"head_{stamp}.png")
                
                # Save as PNG directly, NO imshow()!
                cv2.imwrite(path, img_bgr)
                print(f"Saved: {path}")
                
                # Wait 1 second before taking the next picture
                time.sleep(1.0)

    except KeyboardInterrupt:
        print("\nCapture stopped by user.")
    except Exception as e:
        print(f"Error: {e}")