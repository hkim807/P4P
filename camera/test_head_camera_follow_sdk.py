#!/usr/bin/env python3
"""
Standalone Navel SDK head-camera face-follow test.


This version uses navel.HeadCamera instead of cv2.VideoCapture(index), because
the robot head camera is exposed through the Navel SDK rather than /dev/video*.


Start without --move first:
 python3 test_head_camera_follow_sdk.py


Then enable head movement:
 python3 test_head_camera_follow_sdk.py --move
"""


from __future__ import annotations


import argparse
import inspect
import math
import os
import time
from datetime import datetime


import cv2
import numpy as np


import navel




EMOTION_OVERLAY_ZERO_CONFIGS = [
   ("cns_fer_cont_t1", 0, navel.DataType.U32),
   ("cns_fer_cont_t2", 0, navel.DataType.U32),
   ("cns_fer_peak_t1", 0, navel.DataType.U32),
   ("cns_fer_peak_t2", 0, navel.DataType.U32),
   ("cns_fer_overlay_inc", 0, navel.DataType.F32),
   ("cns_fer_overlay_max", 0, navel.DataType.F32),
   ("cns_fer_overlay_peak_min", 0, navel.DataType.F32),
   ("cns_fer_peak_max", 0, navel.DataType.F32),
]




def clamp(value: float, lo: float, hi: float) -> float:
   return max(lo, min(hi, value))




def choose_face(faces, frame_w: int, frame_h: int, mode: str):
   if len(faces) == 0:
       return None


   if mode == "largest":
       return max(faces, key=lambda face: face[2] * face[3])


   center_x = frame_w / 2.0
   center_y = frame_h / 2.0


   def center_distance(face) -> float:
       x, y, w, h = face
       face_x = x + w / 2.0
       face_y = y + h / 2.0
       return (face_x - center_x) ** 2 + (face_y - center_y) ** 2


   return min(faces, key=center_distance)




def sdk_frame_to_bgr(frame) -> np.ndarray:
   """Convert a Navel SDK RGB frame into an OpenCV BGR image."""
   image = np.asarray(frame.data)


   if image.ndim == 1:
       image = image.reshape((frame.height, frame.width, 3))


   if image.ndim != 3 or image.shape[2] != 3:
       raise RuntimeError(f"Unexpected camera frame shape: {image.shape}")


   return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)




class HeadFollower:
   def __init__(self, args):
       self.args = args
       self.robot_context = None
       self.robot = None
       self.smooth_yaw = 0.0
       self.smooth_pitch = 0.0
       self.last_move = 0.0
       self.last_face_time = 0.0
       self.look_at_px_call = None
       self.look_at_px_failed = False


   def start_robot(self):
       if not self.needs_robot():
           return


       self.robot_context = navel.Robot()
       self.robot = self.robot_context.__enter__()
       print("[OK] Connected to Navel robot for head movement.")


       if self.args.disable_emotion_overlay:
           self.disable_emotion_overlay()


   def needs_robot(self):
       return (
           self.args.move
           or self.args.eyes
           or self.args.disable_emotion_overlay
           or self.args.list_robot_api
       )


   def stop_robot(self):
       if self.robot is not None:
           try:
               self.head_overlay(0.0, 0.0)
           except Exception:
               pass


       if self.robot_context is not None:
           self.robot_context.__exit__(None, None, None)


   def head_overlay(self, yaw_deg: float, pitch_deg: float):
       if not self.args.move or self.robot is None:
           return


       if hasattr(self.robot, "head_overlay_degrees"):
           self.robot.head_overlay_degrees(0.0, pitch_deg, yaw_deg)
           return


       overlay = navel.Bryan(
           math.radians(0.0),
           math.radians(pitch_deg),
           math.radians(yaw_deg),
       )
       self.robot.head_overlay(overlay)


   def movement_test(self):
       if not self.args.move:
           print("[INFO] Add --move to run the movement test.")
           return


       print("[OK] Running movement test: yaw right, yaw left, pitch down/up, neutral.")
       for yaw_deg, pitch_deg in [
           (8.0, 0.0),
           (-8.0, 0.0),
           (0.0, 4.0),
           (0.0, -4.0),
           (0.0, 0.0),
       ]:
           print(f"move yaw={yaw_deg:+.1f} pitch={pitch_deg:+.1f}")
           self.head_overlay(yaw_deg, pitch_deg)
           time.sleep(1.0)


   def disable_emotion_overlay(self):
       if self.robot is None:
           return


       for key, value, data_type in EMOTION_OVERLAY_ZERO_CONFIGS:
           self.robot.config_set(key, value, data_type)
       print("[OK] Disabled FER emotion overlay configs.")


   def list_robot_api(self):
       if self.robot is None:
           return


       keywords = ("eye", "eyes", "gaze", "look", "face", "emotion", "overlay")
       names = sorted(
           name for name in dir(self.robot)
           if any(keyword in name.lower() for keyword in keywords)
       )
       if not names:
           print("[INFO] No robot methods matched eye/gaze/look/face/emotion/overlay.")
           return
       print("[INFO] Matching robot API names:")
       for name in names:
           method = getattr(self.robot, name)
           try:
               signature = str(inspect.signature(method))
           except (TypeError, ValueError):
               signature = "(signature unavailable)"
           print(f"  {name}{signature}")


   def update_eyes(self, face_center_x: float, face_center_y: float, frame_w: int, frame_h: int):
       if not self.args.eyes or self.robot is None or self.look_at_px_failed:
           return


       x_px = int(clamp(face_center_x, 0, frame_w - 1))
       y_px = int(clamp(face_center_y, 0, frame_h - 1))


       if self.look_at_px_call is not None:
           self.look_at_px_call(x_px, y_px, frame_w, frame_h)
           return


       look_at_px = getattr(self.robot, "look_at_px", None)
       if look_at_px is None:
           print("[WARN] Robot SDK has no look_at_px method; eye follow disabled.")
           self.look_at_px_failed = True
           return


       candidates = [
           (
               lambda: look_at_px(navel.Point2d(x_px, y_px), self.args.eye_head),
               lambda x, y, _w, _h: look_at_px(navel.Point2d(x, y), self.args.eye_head),
           ),
           (lambda: look_at_px(x_px, y_px), lambda x, y, _w, _h: look_at_px(x, y)),
           (
               lambda: look_at_px(float(x_px), float(y_px)),
               lambda x, y, _w, _h: look_at_px(float(x), float(y)),
           ),
           (
               lambda: look_at_px(x_px, y_px, frame_w, frame_h),
               lambda x, y, w, h: look_at_px(x, y, w, h),
           ),
           (lambda: look_at_px((x_px, y_px)), lambda x, y, _w, _h: look_at_px((x, y))),
           (lambda: look_at_px([x_px, y_px]), lambda x, y, _w, _h: look_at_px([x, y])),
       ]


       last_type_error = None
       for candidate, wrapper in candidates:
           try:
               candidate()
           except TypeError as exc:
               last_type_error = exc
               continue
           except Exception as exc:
               print(f"[WARN] look_at_px failed; eye follow disabled: {exc}")
               self.look_at_px_failed = True
               return


           self.look_at_px_call = wrapper
           print("[OK] Eye follow enabled through look_at_px.")
           return


       print("[WARN] Could not call look_at_px with known argument forms; eye follow disabled.")
       if last_type_error is not None:
           print(f"[WARN] Last look_at_px TypeError: {last_type_error}")
       self.look_at_px_failed = True


   def update(self, err_x: float, err_y: float):
       self.last_face_time = time.time()
       now = self.last_face_time


       if now - self.last_move < self.args.move_interval:
           return
       self.last_move = now


       if abs(err_x) < self.args.deadband:
           err_x = 0.0
       if abs(err_y) < self.args.deadband_y:
           err_y = 0.0


       if self.args.invert_x:
           err_x *= -1.0
       if self.args.invert_y:
           err_y *= -1.0


       yaw = clamp(err_x * self.args.max_yaw, -self.args.max_yaw, self.args.max_yaw)
       pitch = clamp(err_y * self.args.max_pitch, -self.args.max_pitch, self.args.max_pitch)


       alpha = self.args.smoothing
       next_yaw = alpha * self.smooth_yaw + (1.0 - alpha) * yaw
       next_pitch = alpha * self.smooth_pitch + (1.0 - alpha) * pitch


       yaw_step = clamp(
           next_yaw - self.smooth_yaw,
           -self.args.max_yaw_step,
           self.args.max_yaw_step,
       )
       pitch_step = clamp(
           next_pitch - self.smooth_pitch,
           -self.args.max_pitch_step,
           self.args.max_pitch_step,
       )
       self.smooth_yaw += yaw_step
       self.smooth_pitch += pitch_step


       print(
           "face err_x={:+.2f} err_y={:+.2f} yaw={:+.1f} pitch={:+.1f}".format(
               err_x,
               err_y,
               self.smooth_yaw,
               self.smooth_pitch,
           )
       )


       self.head_overlay(self.smooth_yaw, self.smooth_pitch)


   def update_no_face(self):
       print("no face")


       if not self.args.neutral_on_lost:
           return


       now = time.time()
       if self.last_face_time == 0.0:
           return
       if now - self.last_face_time < self.args.lost_timeout:
           return
       if now - self.last_move < self.args.move_interval:
           return


       self.last_move = now
       self.smooth_yaw *= self.args.smoothing
       self.smooth_pitch *= self.args.smoothing
       self.head_overlay(self.smooth_yaw, self.smooth_pitch)




def parse_args():
   parser = argparse.ArgumentParser()
   parser.add_argument(
       "--demo",
       action="store_true",
       help="Use the current tuned demo settings for head and eye following.",
   )
   parser.add_argument("--move", action="store_true", help="Actually move the robot head.")
   parser.add_argument("--camera", choices=["head", "chest"], default="head")
   parser.add_argument("--select", choices=["largest", "center"], default="center")
   parser.add_argument("--max-yaw", type=float, default=12.0)
   parser.add_argument("--max-pitch", type=float, default=6.0)
   parser.add_argument("--max-yaw-step", type=float, default=4.0)
   parser.add_argument("--max-pitch-step", type=float, default=2.0)
   parser.add_argument("--deadband", type=float, default=0.12)
   parser.add_argument("--deadband-y", type=float, default=0.18)
   parser.add_argument("--smoothing", type=float, default=0.88)
   parser.add_argument("--move-interval", type=float, default=0.25)
   parser.add_argument("--invert-x", action="store_true")
   parser.add_argument("--invert-y", action="store_true")
   parser.add_argument("--scale-factor", type=float, default=1.12)
   parser.add_argument("--min-neighbors", type=int, default=6)
   parser.add_argument("--min-size", type=int, default=50)
   parser.add_argument("--neutral-on-lost", action="store_true")
   parser.add_argument("--lost-timeout", type=float, default=1.5)
   parser.add_argument("--save-debug-dir", default="")
   parser.add_argument("--save-every", type=float, default=2.0)
   parser.add_argument("--movement-test", action="store_true")
   parser.add_argument("--disable-emotion-overlay", action="store_true")
   parser.add_argument("--list-robot-api", action="store_true")
   parser.add_argument("--eyes", action="store_true", help="Try to make the robot eyes look at the detected face.")
   parser.add_argument(
       "--eye-head",
       type=float,
       default=0.0,
       help="Head movement amount for look_at_px: 0 eyes only, 1 maximum head involvement.",
   )
   args = parser.parse_args()


   if args.demo:
       args.move = True
       args.eyes = True
       args.eye_head = 0.0
       args.max_yaw = 35.0
       args.max_pitch = 18.0
       args.deadband = 0.16
       args.deadband_y = 0.24
       args.smoothing = 0.88
       args.move_interval = 0.08
       args.invert_x = True
       args.max_yaw_step = 1.5
       args.max_pitch_step = 0.75


   return args




def main():
   args = parse_args()


   cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
   face_detector = cv2.CascadeClassifier(cascade_path)
   if face_detector.empty():
       raise RuntimeError("Could not load Haar face detector")


   camera_cls = navel.HeadCamera if args.camera == "head" else navel.ChestCamera


   if args.save_debug_dir:
       os.makedirs(args.save_debug_dir, exist_ok=True)


   follower = HeadFollower(args)
   follower.start_robot()


   if args.list_robot_api:
       try:
           follower.list_robot_api()
       finally:
           follower.stop_robot()
       return


   if args.movement_test:
       try:
           follower.movement_test()
       finally:
           follower.stop_robot()
       return


   print(f"[OK] Starting {args.camera} camera through Navel SDK.")
   print("[OK] Press Ctrl+C to stop.")
   if not args.move:
       print("[INFO] Movement is disabled. Add --move after face detection works.")


   last_save = 0.0


   try:
       with camera_cls() as cam:
           while True:
               frame = cam.get_frame(timeout_ms=500)
               image_bgr = sdk_frame_to_bgr(frame)
               frame_h, frame_w = image_bgr.shape[:2]


               gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
               gray = cv2.equalizeHist(gray)


               faces = face_detector.detectMultiScale(
                   gray,
                   scaleFactor=args.scale_factor,
                   minNeighbors=args.min_neighbors,
                   minSize=(args.min_size, args.min_size),
               )


               face = choose_face(faces, frame_w, frame_h, args.select)
               if face is None:
                   follower.update_no_face()
               else:
                   x, y, face_w, face_h = face
                   center_x = x + face_w / 2.0
                   center_y = y + face_h / 2.0


                   err_x = (center_x - frame_w / 2.0) / (frame_w / 2.0)
                   err_y = (center_y - frame_h / 2.0) / (frame_h / 2.0)
                   follower.update_eyes(center_x, center_y, frame_w, frame_h)
                   follower.update(err_x, err_y)


                   if args.save_debug_dir:
                       cv2.rectangle(image_bgr, (x, y), (x + face_w, y + face_h), (0, 255, 0), 2)
                       cv2.circle(image_bgr, (int(center_x), int(center_y)), 5, (0, 255, 0), -1)


               if args.save_debug_dir and time.time() - last_save >= args.save_every:
                   last_save = time.time()
                   stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                   path = os.path.join(args.save_debug_dir, f"head_follow_{stamp}.jpg")
                   cv2.imwrite(path, image_bgr)
                   print(f"saved {path}")


   except KeyboardInterrupt:
       print("\nStopped by user.")
   finally:
       follower.stop_robot()




if __name__ == "__main__":
   main()



