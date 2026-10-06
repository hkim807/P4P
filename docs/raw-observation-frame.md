# RawObservationFrame contract and SDK mapping

The JSON body is one raw frame with exactly four top-level keys: `timestamp`,
`people`, `robot`, and `safety`. Public validation lives in
`app/domain/models.py`; `python3 -m app.domain.schema` generates
`schemas/v1/raw-observation-frame.schema.json`.

## Mapping

| JSON field | SDK source | Representation |
| --- | --- | --- |
| `timestamp` | Robot host `time.monotonic_ns()` at conversion | Integer microseconds |
| `people[].uid` | `PerceptionData.persons[].uid` | Nonnegative integer |
| `people[].distance_m` | `Person.dist_mm` | Millimetres / 1000, or null |
| `people[].gaze_overlap` | `Person.gaze_overlap` | Score in [0, 1], or null |
| `people[].optional_relative_head_position` | `Person.g_head_position` entry with `sys.name == CAM_HEAD` | Cartesian x/y/z in metres and `coordinate_frame: CAM_HEAD`; otherwise omitted |
| `robot.linear_velocity` | `LocomotionData.odometry.velocity.linear_x` | Signed forward m/s, or null |
| `robot.angular_velocity` | `LocomotionData.odometry.velocity.angular_z` | Yaw rad/s, or null |
| `safety.lidar` | `LocomotionData.distances[Sensor.LIDAR]` | Raw native-unit values, front then back |
| `safety.sonar` | `LocomotionData.distances[Sensor.SONAR]` | Raw native-unit values, front-right, front-left, back |

The SDK's Cartesian positions specify their coordinate frames and metres.
`CAM_HEAD` has its origin at the head camera. The client selects this coordinate
only and retains its label, without inventing a robot-base transformation.
The separate `Person.head_position` is a Tait-Bryan rotation in radians and is
not used as a Cartesian position. Other head coordinates are omitted.

The SDK public reference describes range ordering but does not state range
units. The arrays therefore retain the native values. Confirm those units for
the robot's installed SDK before interpreting them as physical distances.
No obstacle classification or collision decision is computed from them.

The original client used a planar linear-velocity vector; this reduced contract
uses the signed forward scalar `linear_x` and yaw scalar `angular_z`. Lateral,
vertical, roll, and pitch velocity components are outside this contract.

## Missing and invalid data

Every person with a usable SDK ID appears even if distance or gaze is missing.
Required measurement keys then contain `null`. Zero is a valid measurement and
is preserved. Duplicate IDs retain only the first person in the packet.

NaN, infinity, nonnumeric values, and booleans are unavailable measurements.
Negative distances/ranges and gaze scores outside [0, 1] become `null`, without
clamping. Invalid Cartesian components cause the optional position to be omitted.

An absent sensor key or missing locomotion packet produces a `null` array.
An empty SDK array stays `[]`. Invalid samples stay `null` at their original
indices, so front/back meanings do not shift. Lists keep their SDK length;
the collector does not add or remove sensor slots.

When the newest locomotion packet is older than `--max-locomotion-age` (default
1 second), both robot velocities and both safety arrays become `null`. Perception
continues streaming. No stationary fallback is applied.

## Timing and validation

`timestamp` is collection time on the robot host, using the monotonic clock in
microseconds. It is not the SDK's undocumented perception-time value, not a
wall-clock timestamp, and not necessarily unique. It describes the perception
collection; locomotion is the most recent non-stale packet and is not synchronized
to that exact instant. The wire format has no separate locomotion timestamp.

The receiver accepts required null measurements, empty people lists, and omitted
optional head positions. It rejects extra fields, missing required keys, duplicate
UIDs, invalid ranges, wrong scalar types, and nonfinite numeric values. Timestamp
ordering is enforced per receiver run: duplicate/backward timestamps are rejected
with HTTP 409. A fresh receiver output file starts a new recording session.
The JSON Schema describes structural constraints; unique people by
UID is additionally enforced by the Python validator.

The server stores the validated frame, preserving optional-position omission
and explicit nulls. It adds no state estimates, source IDs, or decisions.
Numeric JSON integers may be normalized to equivalent floats during validation.

## SDK references

Source fields, head coordinates, gaze/distance semantics, range sensor enum keys,
and array ordering were checked against the
[Navel SDK data reference](https://doc.navelrobotics.com/api/data_structs.html).
The receive calls use the
[Navel SDK communication reference](https://doc.navelrobotics.com/api/communication.html).
The installed robot SDK and real sensor readings should be verified during the
live check described in [runbook.md](runbook.md).
