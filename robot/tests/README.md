# On-robot SDK diagnostics

These scripts read live Navel SDK data. Run them on the robot using Python 3.10
or newer in its SDK-enabled environment. They use only the standard library
and the installed Navel SDK; no HTTP server or tunnel is needed.

## Angular velocity

From the repository root:

```bash
python3 robot/tests/stream_angular_velocity.py --duration 30
```

Or copy this single file onto the robot and run it directly. For continuous
output until Ctrl-C:

```bash
python3 robot/tests/stream_angular_velocity.py --duration 0
```

The script calls `await robot.next_locomotion(timeout=...)`, reads
`packet.odometry.velocity.angular_z`, and prints every received packet without
the sensor-stream adapter or any HTTP transport. Output includes:

- Raw `angular_z` and whether it is zero, nonzero, unavailable, or invalid.
- All six standard velocity components without rounding.
- The velocity object's type, raw representation, and attributes when available.
- Raw odometry orientation and its SDK timestamp, without assuming their format.
- The SDK module location/version, when exposed, and a final sample summary.

Observe the diagnostic while the **robot base turns** using your existing
controls. The script sends no movement commands. If `angular_z` remains zero
while the base turns, save the output and inspect whether other raw velocity
fields or orientation change. A stationary run with all zeros does not establish
a fault. Nonzero samples establish that the SDK supplies changing values, but
do not verify their physical accuracy or units.

`<missing>` means an attribute is absent; `None` means it exists but has no value.
Neither is substituted with zero. SDK receive timeouts are reported and retried;
connection errors terminate the diagnostic.

To capture output on Navel:

```bash
python3 robot/tests/stream_angular_velocity.py --duration 30 > angular-velocity.log 2>&1
```

The `--timeout` option controls the maximum wait per packet (default 1 second).
The `--duration` limit caps each receive wait; startup and SDK connection/cleanup
time are additional. `--help` works on a computer without the Navel SDK.
