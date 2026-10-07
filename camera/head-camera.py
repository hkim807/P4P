import os
import time
from datetime import datetime

from navel import HeadCamera


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(SCRIPT_DIR, "head_frames")

def _write_ppm(path, data, width, height):
    header = f"P6\n{width} {height}\n255\n".encode("ascii")
    with open(path, "wb") as f:
        f.write(header)
        f.write(data.tobytes())

wwwwwwww
if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    try:
        with HeadCamera() as cam:
            while True:
                frame = cam.get_frame(timeout_ms=500)
                ts = datetime.fromtimestamp(
                    frame.timestamp_us / 1_000_000
                )
                stamp = ts.strftime("%Y%m%d_%H%M%S")
                path = os.path.join(
                    OUT_DIR, f"head_{stamp}.ppm"
                )
                _write_ppm(path, frame.data, frame.width, frame.height)
                print(f"wrote {path}")
                time.sleep(1.0)
    except KeyboardInterrupt:
        pass