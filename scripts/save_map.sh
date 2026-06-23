#!/usr/bin/env bash
set -euo pipefail

map_name="${1:-lab}"
map_dir="${2:-${HOME}/maps}"
trajectory_id="${TRAJECTORY_ID:-0}"

mkdir -p "${map_dir}"

pbstream="${map_dir}/${map_name}.pbstream"
map_filestem="${map_dir}/${map_name}"

echo "Finishing Cartographer trajectory ${trajectory_id}..."
rosservice call /finish_trajectory "${trajectory_id}"

echo "Writing ${pbstream}..."
rosservice call /write_state "{filename: '${pbstream}'}"

echo "Converting ${pbstream} to ${map_filestem}.pgm and ${map_filestem}.yaml..."
rosrun cartographer_ros cartographer_pbstream_to_ros_map \
  -pbstream_filename="${pbstream}" \
  -map_filestem="${map_filestem}"

echo "Saved ${map_filestem}.pgm and ${map_filestem}.yaml"
