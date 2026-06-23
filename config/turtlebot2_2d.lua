include "map_builder.lua"
include "trajectory_builder.lua"

options = {
  map_builder = MAP_BUILDER,
  trajectory_builder = TRAJECTORY_BUILDER,

  -- Cartographer owns map->odom; turtlebot_bringup/kobuki owns odom->base_footprint.
  map_frame = "map",
  odom_frame = "odom",
  published_frame = "odom",
  provide_odom_frame = false,
  publish_frame_projected_to_2d = true,

  -- No repo-local bringup confirms a full IMU. Kobuki gyro-only data is not used here.
  tracking_frame = "base_link",
  use_odometry = true,
  use_nav_sat = false,
  use_landmarks = false,

  -- TurtleBot 2 baseline mapping uses one LaserScan on /scan.
  num_laser_scans = 1,
  num_multi_echo_laser_scans = 0,
  num_subdivisions_per_laser_scan = 1,
  num_point_clouds = 0,

  lookup_transform_timeout_sec = 0.2,
  submap_publish_period_sec = 0.3,
  pose_publish_period_sec = 5e-3,
  trajectory_publish_period_sec = 30e-3,

  rangefinder_sampling_ratio = 1.0,
  odometry_sampling_ratio = 1.0,
  fixed_frame_pose_sampling_ratio = 1.0,
  imu_sampling_ratio = 1.0,
  landmarks_sampling_ratio = 1.0,
}

MAP_BUILDER.use_trajectory_builder_2d = true

TRAJECTORY_BUILDER_2D.use_imu_data = false
TRAJECTORY_BUILDER_2D.min_range = 0.45
TRAJECTORY_BUILDER_2D.max_range = 4.0
TRAJECTORY_BUILDER_2D.missing_data_ray_length = 4.0
TRAJECTORY_BUILDER_2D.num_accumulated_range_data = 1
TRAJECTORY_BUILDER_2D.submaps.grid_options_2d.resolution = 0.05

-- Tune min/max range, grid resolution, optimize_every_n_nodes, and constraint
-- builder scores first if the map drifts or loop closures are too weak/strong.
POSE_GRAPH.optimize_every_n_nodes = 90

return options
