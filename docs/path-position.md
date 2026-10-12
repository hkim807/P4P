# Nose-based path position

The `temporal-social-v4` estimator uses the current `Person.g_nose` point in
`CAM_HEAD` to estimate whether a person lies inside the robot's forward travel
corridor. This supports distinguishing someone passing beside the robot from
someone closing in front of it while the robot roams with its head fixed.

The adapter sends `optional_relative_nose_position` in metres. It selects one
finite, nonzero CAM_HEAD point, retains its frame label and never substitutes
`g_head_position`, head rotation angles or another coordinate frame. Full SDK
replay performs the same conversion, so live and replay states agree.

## Geometry and configuration

The geometry follows the planar offset/yaw transform used by
`robot/navel_client/approach.py:nose_position`. APPROACH selects HEAD_STRAIGHT
(SDK frame 3); this classifier explicitly selects CAM_HEAD. The two frames
are not relabelled or treated as interchangeable.

With camera x treated as forward and y as lateral, the configured transform is:

```text
a = radians(path_camera_yaw_deg)
forward = path_camera_x_m + cos(a) * nose.x - sin(a) * nose.y
lateral = path_camera_y_m + sin(a) * nose.x + cos(a) * nose.y
```

The development and default configuration files expose these parameters:

| Parameter | Default | Meaning |
| --- | ---: | --- |
| `path_assume_fixed_head` | `true` | Enable the fixed-head interpretation. Set false if the head is moving or its orientation is unsuitable. |
| `path_half_width_m` | `0.5` | Maximum absolute lateral offset on initial classification. Includes the desired clearance around the robot/person. |
| `path_hysteresis_m` | `0.1` | Expand the boundary when already IN_WAY; contract it when already TO_SIDE. Must be smaller than the half-width. |
| `path_camera_x_m` | `0.0` | Forward offset of the head camera relative to the travel reference. |
| `path_camera_y_m` | `0.0` | Lateral offset of the head camera relative to the travel reference. |
| `path_camera_yaw_deg` | `0.0` | Fixed camera-to-travel yaw correction. |

The defaults are provisional. They assume the roaming head points along the
travel direction and use a one-metre-wide corridor. This is a nose-point
heuristic, not a measured body envelope, lateral-motion predictor or swept-path
collision check. No dynamic head compensation is performed. Set the camera
offsets/yaw to match the fixed roaming pose and choose clearance for the robot
and environment; do not tune thresholds to force expected evaluation labels.

## State and lifetime

Each SocialState person exposes `relative_nose_position` (the original point),
`nose_forward_m`, `nose_lateral_m` (transformed metres) and `path_position`:

| Category | Meaning |
| --- | --- |
| `IN_WAY` | Forward > 0 and absolute lateral offset inside the current corridor boundary. |
| `TO_SIDE` | Forward > 0 and outside that boundary. |
| `BEHIND` | Forward ≤ 0; outside the forward corridor. |
| `UNKNOWN` | Missing/invalid geometry, missing person, or fixed-head interpretation disabled. |

Initially, exactly 0.5 m counts as IN_WAY. Once IN_WAY, the person remains in
that category through 0.6 m; once TO_SIDE, re-entry requires an offset ≤0.4 m.
The same thresholds apply on both sides. Hysteresis is isolated by session and
track epoch. Missing geometry/detections or a gap exceeding `max_gap_s` reset
it. Missing people immediately lose their current nose point, transformed
coordinates and position category. Changes appear in `cue_changes`.

The corridor has no separate depth cutoff. Yield eligibility continues to use
the measured current distance and `yield_closing_max_distance_m` (3 m in the
development config). A nose far ahead can therefore be IN_WAY without requiring
the robot to yield.

## Decisions

Rules `social-rules-v6` and LLM prompt `social-state-llm-v11` require IN_WAY in
addition to the existing detected-face, valid closing trend, distance cutoff,
and established low-gaze evidence before yielding. With the same evidence,
TO_SIDE supports CONTINUE. IN_WAY alone does not bypass readiness or cause a
yield. Attentive people still follow APPROACH/ENGAGE rules. UNKNOWN and BEHIND
cannot activate this corridor-based yield trigger; UNKNOWN does not prove that
the route is clear.

The unsupported path-relation and pass-gesture cues have been removed. The
corridor cue is derived from measured nose position and does not claim a verified
path conflict or clearance. No physical APPROACH or YIELD manoeuvre is changed
by this classifier.

Use the updated robot adapter and server together to supply live nose geometry.
Old raw/SocialState captures without nose geometry remain readable and expose
UNKNOWN; full SDK captures containing `g_nose` can reconstruct the new feature.
Saved historical states are not retroactively re-estimated by the social-state
input loader. Frozen study manifests describe their original versions and need
regeneration for a study using the new feature.

Run the focused checks with:

```bash
python3 -m unittest tests.test_path_position tests.test_yield_closing_low_gaze
```
