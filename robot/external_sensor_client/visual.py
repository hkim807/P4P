"""Optional HighGUI display; imports and windows exist only when requested."""

from __future__ import annotations

import os
import subprocess
import sys
from importlib import import_module
from typing import Any

from robot.external_sensor_client.capture import CapturedFrame
from robot.external_sensor_client.perception import PerceivedFrame, TrackedPerson


class DisplayError(RuntimeError):
    """GUI dependency or graphical session is unavailable."""


def quit_key(key: int) -> bool:
    return key & 0xff in (ord("q"), 27)


def person_label(person: TrackedPerson) -> str:
    if person.depth.position_robot_m is None:
        depth = "depth unavailable"
    else:
        x, y, z = person.depth.position_robot_m
        depth = f"d={person.depth.distance_m:.2f}m xyz=({x:.2f},{y:.2f},{z:.2f})"
    return f"{person.track_id} conf={person.confidence:.2f} {depth}"


def overlay_rgb(rgb_bgr: Any, humans: tuple[TrackedPerson, ...], cv: Any,
                runtime_text: str) -> Any:
    image = rgb_bgr.copy()
    height, width = image.shape[:2]
    cv.putText(image, runtime_text, (8, 20), cv.FONT_HERSHEY_SIMPLEX, 0.40, (0, 255, 255), 1)
    for person in humans:
        x1, y1, x2, y2 = person.bbox
        left, top = max(0, min(width-1, int(x1))), max(0, min(height-1, int(y1)))
        right, bottom = max(0, min(width-1, int(x2))), max(0, min(height-1, int(y2)))
        cv.rectangle(image, (left, top), (right, bottom), (0, 255, 0), 2)
        # Split long session IDs and measurements into two readable lines.
        label = person_label(person)
        cv.putText(image, person.track_id, (left, max(12, top-12)), cv.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 0), 1)
        cv.putText(image, label[len(person.track_id)+1:], (left, min(height-1, top+14)), cv.FONT_HERSHEY_SIMPLEX, 0.40, (0, 255, 0), 1)
    return image


class VisualDisplay:
    """Create/render/destroy windows on the single display owner thread."""

    def __init__(self):
        self.cv = None
        self.np = None

    def __enter__(self):
        if sys.platform.startswith("linux") and not (os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY")):
            raise DisplayError("--display requires a graphical session (DISPLAY/WAYLAND_DISPLAY); omit it for headless operation")
        try:
            self.cv = import_module("cv2")
            self.np = import_module("numpy")
            build = self.cv.getBuildInformation()
            gui = next((line.strip().upper() for line in build.splitlines() if line.strip().startswith("GUI:")), "GUI: NONE")
            if "NONE" in gui or gui.endswith("NO"):
                raise DisplayError("--display requires opencv-python with GUI support, not opencv-python-headless")
            if sys.platform.startswith("linux"):
                # Qt can abort the process when DISPLAY is stale or its plugin
                # cannot connect. Probe in a bounded child before opening our
                # owner-thread window so that failure becomes a clear error.
                probe = subprocess.run(
                    [sys.executable, "-c",
                     "import resource; resource.setrlimit(resource.RLIMIT_CORE, (0, 0)); "
                     "import cv2; cv2.namedWindow('D435 GUI probe'); cv2.destroyAllWindows()"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    text=True, timeout=5, check=False,
                )
                if probe.returncode:
                    raise DisplayError("--display cannot connect to the graphical session or load OpenCV GUI plugins: "
                                       + probe.stderr.strip()[-400:])
            self.cv.namedWindow("External D435", self.cv.WINDOW_NORMAL)
            return self
        except Exception as error:
            self.close()
            if isinstance(error, DisplayError):
                raise
            raise DisplayError(f"cannot open D435 display; check OpenCV GUI support and graphical session: {error}") from error

    def show(self, frame: CapturedFrame | PerceivedFrame, runtime_text: str = "") -> bool:
        cv, np = self.cv, self.np
        try:
            if isinstance(frame, PerceivedFrame):
                rgb = overlay_rgb(frame.rgb_bgr, frame.humans, cv, runtime_text)
                depth = frame.depth_m
            else:
                rgb = np.ascontiguousarray(np.asanyarray(frame.color.get_data())[:, :, ::-1])
                depth = np.asanyarray(frame.depth.get_data()).astype(float) * (frame.depth_scale_m or 0.0)
            valid = np.isfinite(depth) & (depth > 0)
            scaled = np.where(valid, np.clip(depth, 0, 6.0) * (255/6.0), 0).astype(np.uint8)
            colored = cv.applyColorMap(scaled, cv.COLORMAP_JET)
            colored[~valid] = 0
            cv.imshow("External D435", np.hstack((rgb, colored)))
            return quit_key(cv.waitKey(1))
        except Exception as error:
            raise DisplayError(f"D435 display failed: {error}") from error

    def poll_quit(self) -> bool:
        try:
            return quit_key(self.cv.waitKey(1))
        except Exception as error:
            raise DisplayError(f"D435 display event processing failed: {error}") from error

    def close(self):
        if self.cv is not None:
            try:
                self.cv.destroyAllWindows()
            except Exception:
                pass
            self.cv = None

    def __exit__(self, *_):
        self.close()
