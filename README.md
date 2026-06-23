# P4P

## Mapping

This package provides PHASE 1 baseline mapping for a TurtleBot 2: manually
drive the robot while Google Cartographer builds a 2D map, then save the map
for later `map_server` / AMCL use. It does not start autonomous navigation.

### Detected Platform and Assumptions

- The repo does not identify a concrete Ubuntu or ROS 1 distro. The package is
  catkin/package.xml format 2 and the generated CMake comment only says
  "ROS Kinetic and newer".
- `cartographer_ros` is released as a catkin apt package for ROS Kinetic and
  Melodic. ROS Index lists no ROS 1 Noetic `cartographer_ros` release, so a
  Noetic target needs `cartographer` and `cartographer_ros` built from source.
- The robot workspace used for validation contains `kobuki_node`,
  `hokuyo_node`, `ros_astra_camera`, and `depthimage_to_laserscan`, but not
  `turtlebot_bringup`.
- The default mapping flow should therefore reuse the existing Kobuki + Hokuyo
  bringup and launch Cartographer separately against `/scan`.
- Assumed scan topic: `/scan`.
- Depth-camera fallback scan frame: `camera_depth_frame` by default. On the
  robot checked during bringup, the Astra depth topics report
  `camera_depth_optical_frame`.
- Assumed odometry/TF provider: the existing Kobuki bringup publishes `/odom`
  and `odom -> base_footprint`.
- Kobuki gyro-only data is not treated as a full Cartographer IMU here, so the
  Cartographer config uses `tracking_frame = "base_link"` and
  `use_imu_data = false`.

### Dependencies

For ROS Kinetic or Melodic apt-based installs:

```bash
sudo apt install \
  ros-${ROS_DISTRO}-cartographer-ros \
  ros-${ROS_DISTRO}-depthimage-to-laserscan \
  ros-${ROS_DISTRO}-map-server \
  ros-${ROS_DISTRO}-rviz \
  ros-${ROS_DISTRO}-teleop-twist-keyboard
```

For ROS Noetic, build `cartographer` and `cartographer_ros` from source in the
catkin workspace first, then install the remaining packages with apt if they
are available for your platform.

### Build

From the catkin workspace root:

```bash
catkin_make
source devel/setup.bash
```

### Run Mapping

1. Start the robot bringup separately so `/odom`, `odom -> base_footprint`,
   and `/scan` already exist. For a Hokuyo-based setup, this is expected to be
   your existing Kobuki + Hokuyo launch, not `lab_navigation`.

2. Confirm the live scan topic before starting Cartographer:

   ```bash
   rostopic echo -n1 /scan/header
   ```

3. Start Cartographer mapping and RViz for the Hokuyo path:

   ```bash
   roslaunch lab_navigation mapping.launch \
     use_depthimage_to_laserscan:=false \
     scan_topic:=/scan \
     open_rviz:=true
   ```

   If you need the depth-camera fallback instead, use:

   ```bash
   roslaunch lab_navigation mapping.launch \
     use_depthimage_to_laserscan:=true \
     scan_topic:=/scan \
     scan_frame:=camera_depth_optical_frame \
     open_rviz:=true
   ```

4. In a second terminal, start teleop:

   ```bash
   roslaunch lab_navigation teleop.launch
   ```

5. Drive slowly through the lab. Prefer smooth motion, repeated views of
   distinctive areas, and paths that close loops.

6. Save the map:

   ```bash
   rosrun lab_navigation save_map.sh lab
   ```

   This writes:

   ```text
   ${HOME}/maps/lab.pbstream
   ${HOME}/maps/lab.pgm
   ${HOME}/maps/lab.yaml
   ```

Manual Cartographer save flow, equivalent to the wrapper:

```bash
mkdir -p "${HOME}/maps"
rosservice call /finish_trajectory 0
rosservice call /write_state "{filename: '${HOME}/maps/lab.pbstream'}"
rosrun cartographer_ros cartographer_pbstream_to_ros_map \
  -pbstream_filename="${HOME}/maps/lab.pbstream" \
  -map_filestem="${HOME}/maps/lab"
```

### Manual Verification Checklist

- `catkin_make` or `catkin build` succeeds with all dependencies installed.
- `/scan` publishes `sensor_msgs/LaserScan` with a frame that has valid TF to
  `base_link`.
- `/odom` publishes `nav_msgs/Odometry`.
- TF contains `map -> odom -> base_footprint -> base_link` and the scan frame.
- RViz shows the live `/map`, `/scan`, and TF while teleoperating.
- Loop closures visibly improve map alignment after revisiting areas.
- `${HOME}/maps/lab.pgm` and `${HOME}/maps/lab.yaml` load with `map_server`.
