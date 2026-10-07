# Additional Navel SDK data to capture for scenario calibration

The current receiver's v1 `RawObservationFrame` retains UID, person distance,
gaze overlap, optional `CAM_HEAD` head position, two base velocity components,
and lidar/sonar arrays. The robot adapter discards other SDK fields. This is
a capture checklist for the eight planned scenario recordings, based on the
public [Navel SDK data structures](https://doc.navelrobotics.com/api/data_structs.html),
[communication API](https://doc.navelrobotics.com/api/communication.html), and
[camera example](https://doc.navelrobotics.com/python_snippets.html#read-camera-frame).
Availability and exact timing/units must be checked on the installed robot.

## Highest-value additional fields

| SDK source | Capture | Why |
| --- | --- | --- |
| `PerceptionData.time` | Raw value and local monotonic receipt time | The SDK calls this the timestamp of the image underlying `persons`. It is absent from v1; v1's timestamp is assigned later by the adapter. Check the SDK time unit and clock against odometry on the robot. |
| `LocomotionData.odometry.time` | Raw value and local monotonic receipt time | Distinguishes a current pose/velocity from a stale cached packet. Log locomotion packets as their own stream in addition to the copy associated with a perception frame. |
| `odometry.position`, `odometry.orientation`, full `odometry.velocity` | Every available component, with measured types and coordinate convention | Enables short-window compensation for the robot moving or turning; v1 keeps only forward velocity and yaw rate. The public class lists these members but does not describe all component units/frames. |
| `Person.face` | Pixel box `x1,y1,x2,y2` and an explicit null when absent | Shows where each face appears and helps audit UID switches and whether the camera is seeing the intended actor. Requires image size/calibration before it can become a bearing. |
| `Person.landmarks` | Eye, nose, and mouth pixel points | Helps diagnose face/gaze availability and orientation, including eye-only versus head-facing trials. Preserve nulls. |
| `Person.head_position` | Three Tait-Bryan rotation components | This is **the person's head orientation**, not a measurement of robot head pan/tilt. It can be compared with gaze overlap. |
| `Person.gaze` | Raw 3D direction vector | Independent gaze evidence to compare against the scalar `gaze_overlap`; verify its reference frame on the installed SDK. |
| `Person.g_gaze`, `g_eye_left`, `g_eye_right`, `g_nose`, `g_head_position` | All valid `(coordinate_frame,x,y,z)` entries, not only `CAM_HEAD` | The SDK exposes gaze targets and face parts in coordinate-labeled 3D lists. Current adapter retains only a `CAM_HEAD` head-position entry, which was absent in all S1–S5 recordings. Inspect actual frame availability before selecting a transform. |
| `Person.id_score` | Raw nullable number | May help explain UID instability, but its meaning and scale are undocumented. Do not use it as calibrated identity confidence until measured/confirmed. |

The SDK describes `Person.gaze_overlap` as 0 for not looking and 1 for directly
looking at the robot. Its S1/S4 values were nevertheless high in the acted
no-gaze trials. Capture the additional head, eye, gaze-vector, gaze-target,
and face-detection fields on each trial to investigate whether this is a
sensor-semantic, camera-motion, or target-attribution issue. The SDK's
`CoordSystem` labels include `CAM_HEAD` and several head/eye frames; retain the
labels rather than inventing one common coordinate frame.

## Useful context outside the person object

- If the fixed path uses SDK navigation, log the active
  `NavigationAction.state`, feedback sequence, remaining path length, elapsed
  time, and terminal result separately. These are action/route events, not
  `PerceptionData` fields. Log pause/resume/stop requests and acknowledgements
  from the route controller too.
- Log local `look_at_person` requests, selected UID, and time. The public
  communication API documents head commands but does not expose a confirmed
  measured robot head pose in the data classes above. A head command log is
  useful context but does not prove where the camera was pointing.
- The SDK exposes `HeadCamera` and `ChestCamera` RGB frames with `timestamp_us`.
  Save a short, time-indexed image/video sidecar if visual adjudication of
  gaze and UID is needed; keep it separate from JSONL sensor records. Verify
  the camera time base before aligning it with perception and odometry.
- `PerceptionData.sst_time_latest` and `sst_tracks_latest` provide tracked
  sound-source metadata. They are useful only if an evaluation scenario
  includes speech or sound location. Sound direction cannot identify a
  visually tracked person without another matching step.
- `Person.facial_expression` is available, but has no established role in the
  current attention/approach rule and is lower priority for this capture.
- Record the existing lidar and sonar arrays with packet receipt time, and
  verify their units and what zero readings mean on the installed robot.

## Capture arrangement before running the eight scenarios

1. Run a short robot-side availability probe for all the fields above. Record
   which are populated when a person is visible, the robot turns, and the head
   follows someone. A documented attribute can still be null/empty in practice.
2. Add a versioned **robot-local diagnostic sidecar** tapped immediately after
   `next_frame()` and `next_locomotion()`, before v1 adapter filtering or the
   rate-limited newest-frame HTTP queue. Keep the existing v1 raw stream for
   replay. This sidecar should retain null/zero UIDs and every reported person
   entry so UID failures can be diagnosed, while the validated v1 contract can
   continue skipping unusable IDs.
3. Record SDK source times and host monotonic receipt times separately.
   Capture the perception receipt time before the optional head-focus call;
   the current adapter assigns its timestamp after that call. Keep the raw
   source time until its unit and clock relation are verified.
4. Name each run with scenario ID, repetition, intended actor, approximate
   position/path, gaze/head instruction, route speed/direction, and whether
   head focus is enabled. Add a visible or logged start/end marker and a short
   stationary baseline. These are ground-truth labels, not SDK inferences.
5. Confirm file counts, disk space, monotonic local timestamps, and that
   diagnostic fields are actually populated on a short pilot before completing
   the eight full recordings. Preserve each raw run for later replay and tuning.

The first priority is the image/odometry source times, full odometry,
face/gaze/head evidence, and all reported 3D coordinate entries. Those directly
address the S1/S4 false-high gaze scores, the S2 distance/UID ambiguity, and
the distinction between person movement and robot movement. Sound and facial
expression can wait unless one of the evaluation scenarios uses them.
