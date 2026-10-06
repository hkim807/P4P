import cv2
from flask import Flask, Response
from navel import HeadCamera  # Swap to ChestCamera if needed

app = Flask(__name__)

def generate_frames():
    # Start the camera when a user connects
    with HeadCamera() as cam:
        while True:
            try:
                # 1. Fetch the frame
                frame = cam.get_frame(timeout_ms=500)
                
                # 2. Convert RGB to BGR
                img_bgr = cv2.cvtColor(frame.data, cv2.COLOR_RGB2BGR)
                
                # 3. Compress the image into a JPEG format for web streaming
                ret, buffer = cv2.imencode('.jpg', img_bgr)
                frame_bytes = buffer.tobytes()
                
                # 4. Yield the frame in MJPEG format (standard for web streams)
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
            except Exception as e:
                print(f"Frame error: {e}")
                break

@app.route('/')
def video_feed():
    # When you visit the website, this route sends the continuous stream
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == "__main__":
    print("\n--- Starting Robot Video Server ---")
    print("If you are on the same Wi-Fi, open your browser and go to:")
    print("http://<ROBOT_IP_ADDRESS>:5000")
    print("(Replace <ROBOT_IP_ADDRESS> with the IP you use to SSH into the robot)")
    print("Press Ctrl+C to stop the server.\n")
    
    # host='0.0.0.0' allows external computers to connect
    app.run(host='0.0.0.0', port=5000, threaded=True)