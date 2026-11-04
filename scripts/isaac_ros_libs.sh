# shellcheck shell=bash
# Source this; it only defines a function.
#
# isaac_ros_lib_dir SITE DISTRO prints the ROS 2 library folder bundled with Isaac Sim, or fails.
# SITE is dirname(isaacsim.__file__). The extensions sit beside that package in a pip install
# (<site-packages>/isaacsim/exts) and two levels above it in a standalone app
# (<app>/python_packages/isaacsim, <app>/exts). The libraries are in isaacsim.ros2.core on 6.x and
# isaacsim.ros2.bridge on 5.1.
isaac_ros_lib_dir() {
  local base ext
  for base in "$1" "$(dirname "$(dirname "$1")")"; do
    for ext in isaacsim.ros2.core isaacsim.ros2.bridge; do
      if [ -d "$base/exts/$ext/$2/lib" ]; then
        echo "$base/exts/$ext/$2/lib"
        return 0
      fi
    done
  done
  return 1
}
