# shellcheck shell=bash
# Source a ROS 2 install for talking to Isaac Sim, in the order that works:
#   1. the ROS 2 install (and any overlays on top of it),
#   2. config/ros_dds.env (the simulator's DDS settings: domain, Cyclone, loopback),
#   3. `ros2 daemon stop`, so the next `ros2 topic list` starts a daemon with THESE settings.
#
# A daemon started earlier with other DDS settings answers `ros2 topic list` / `node list` with
# nothing while data flows. Step 3 is what fixes that.
#
#   source scripts/ros_env.sh                         # auto-detect (by OS: Humble on 22.04, Jazzy on 24.04)
#   source scripts/ros_env.sh /opt/ros/jazzy          # an apt install
#   source scripts/ros_env.sh ~/ros2/humble --overlay ~/ros2/humble_nav2   # RoboStack + Nav2 env
#   source scripts/ros_env.sh /opt/ros/jazzy --overlay ~/ros2/jazzy_nav2/install
#   export ROS_DOMAIN_ID=63; source scripts/ros_env.sh   # the simulator's domain (default 0);
#                                                     # export it: `VAR=x source` is temporary
#
# Options: --overlay PATH (repeatable, applied in order), --keep-daemon, --quiet, --dry-run
# (print the choice, change nothing). A path can be an apt prefix (/opt/ros/<distro>), a colcon
# workspace (its install/ folder or the folder holding install/) or a RoboStack/conda prefix
# (has conda-meta/; activated with micromamba when available, stacked for overlays).
# Auto-detection looks at ROS_DISTRO (if exported), then the OS default, in /opt/ros/<distro>
# and ~/ros2/<distro> (ISAAC_ROS_SEARCH overrides those roots, colon-separated).

# Do NOT start the MCP server, scripts/call.py or any `uv run` / `uv tool` program from a shell where this was sourced:
# a RoboStack environment puts its own Python libraries first, and those programs then fail to import (for example
# "libssl.so.3: version `OPENSSL_3.2.0' not found" from cryptography). Use one terminal for the simulator and the MCP
# client, and a second one for ROS 2 commands.
#
# zsh/sh: BASH_SOURCE, word splitting and setup.bash all differ, and dash cannot even parse the bash
# below; refuse before defining anything instead of half-working.
if [ -z "${BASH_VERSION:-}" ]; then
  echo "ros_env.sh: source it from bash (in zsh, source <install>/setup.zsh and config/ros_dds.env by hand)" >&2
  return 2 2>/dev/null || exit 2
fi

_ros_env_main() {
  local here repo base="" quiet=0 keep_daemon=0 dry=0 arg
  local -a overlays=()
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  repo="$(cd "$here/.." && pwd)"
  while [ "$#" -gt 0 ]; do
    arg="$1"
    case "$arg" in
      --overlay) [ "$#" -ge 2 ] || { echo "ros_env: --overlay needs a path" >&2; return 2; }
                 overlays+=("$2"); shift 2 ;;
      --overlay=*) overlays+=("${arg#--overlay=}"); shift ;;
      --keep-daemon) keep_daemon=1; shift ;;
      --quiet) quiet=1; shift ;;
      --dry-run) dry=1; shift ;;
      -h|--help) sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; return 0 ;;
      -*) echo "ros_env: unknown option $arg" >&2; return 2 ;;
      *) [ -z "$base" ] || { echo "ros_env: one base install only (use --overlay for more)" >&2; return 2; }
         base="$arg"; shift ;;
    esac
  done
  _say() { [ "$quiet" = 1 ] || echo "ros_env: $*" >&2; }

  # What run.sh gives the simulator's bundled ROS bridge: ROS_DISTRO if exported, else by OS.
  local os_id="" os_distro=jazzy want
  if [ -r /etc/os-release ]; then os_id="$(. /etc/os-release; echo "${VERSION_ID:-}")"; fi
  [ "$os_id" = "22.04" ] && os_distro=humble
  want="${ROS_DISTRO:-$os_distro}"

  if [ -z "$base" ]; then
    local roots root distro
    roots="${ISAAC_ROS_SEARCH:-/opt/ros:$HOME/ros2}"
    local IFS_SAVE="$IFS"; IFS=":"
    for distro in "$want" humble jazzy; do
      for root in $roots; do
        if [ -f "$root/$distro/setup.bash" ]; then base="$root/$distro"; break 2; fi
      done
    done
    IFS="$IFS_SAVE"
    [ -n "$base" ] || { echo "ros_env: no ROS 2 install found (looked for $want, humble, jazzy in $roots); pass its path" >&2; return 1; }
  fi

  # Resolve each path to (kind, prefix-or-setup-file).
  _resolve() {
    local p="${1/#\~/$HOME}"
    p="${p%/}"
    if [ -d "$p/conda-meta" ] && [ -f "$p/setup.bash" ]; then echo "conda $p"
    elif [ -f "$p/setup.bash" ]; then echo "setup $p/setup.bash"
    elif [ -f "$p/install/setup.bash" ]; then echo "setup $p/install/setup.bash"
    else return 1; fi
  }
  local -a plan=() resolved
  local item
  for item in "$base" "${overlays[@]}"; do
    resolved="$(_resolve "$item")" || { echo "ros_env: $item has no setup.bash (not a ROS 2 install or workspace)" >&2; return 1; }
    plan+=("$resolved")
  done

  local distro_of_base
  distro_of_base="$(basename "${plan[0]#* }")"
  case "${plan[0]}" in setup*) distro_of_base="$(basename "$(dirname "${plan[0]#* }")")" ;; esac

  local step=0 kind path mm=""
  if command -v micromamba >/dev/null 2>&1; then mm="$(command -v micromamba)"
  elif [ -x "$HOME/.local/bin/micromamba" ]; then mm="$HOME/.local/bin/micromamba"; fi
  for item in "${plan[@]}"; do
    kind="${item%% *}"; path="${item#* }"
    if [ "$dry" = 1 ]; then
      _say "would source ($kind) $path"; step=$((step + 1)); continue
    fi
    if [ "$kind" = conda ]; then
      if [ -n "$mm" ]; then
        eval "$("$mm" shell hook -s bash)" || return 1
        if [ "$step" -gt 0 ]; then micromamba activate --stack "$path" || return 1
        else micromamba activate "$path" || return 1; fi
        if [ "$step" -gt 0 ]; then _say "activated RoboStack env $path (stacked)"
        else _say "activated RoboStack env $path"; fi
      else
        export CONDA_PREFIX="$path" PATH="$path/bin:$PATH"
        # shellcheck disable=SC1090,SC1091
        . "$path/setup.bash" || return 1
        _say "sourced $path/setup.bash (no micromamba found; PATH set by hand)"
      fi
    else
      # shellcheck disable=SC1090
      . "$path" || return 1
      _say "sourced $path"
    fi
    step=$((step + 1))
  done

  # Stacking a second conda env runs its activation, which resets AMENT_PREFIX_PATH to that env
  # alone (RoboStack Humble + a Nav2 env: rviz2 was "Package not found"). Re-apply every prefix's
  # local_setup in order, so the overlay comes first and the base stays visible.
  if [ "$dry" = 0 ] && [ "${#plan[@]}" -gt 1 ] && [[ "${plan[*]}" == *"conda "* ]]; then
    unset AMENT_PREFIX_PATH
    for item in "${plan[@]}"; do
      kind="${item%% *}"; path="${item#* }"
      [ "$kind" = conda ] || path="$(dirname "$path")"
      [ -f "$path/local_setup.bash" ] || continue
      # shellcheck disable=SC1090,SC1091
      . "$path/local_setup.bash" || return 1
    done
  fi

  if [ "$dry" = 1 ]; then
    _say "would source $repo/config/ros_dds.env, then $([ "$keep_daemon" = 1 ] && echo 'keep' || echo 'stop') the ros2 daemon"
  else
    # shellcheck disable=SC1091
    . "$repo/config/ros_dds.env" || return 1
  fi
  local active="${ROS_DISTRO:-$distro_of_base}"
  _say "ROS_DISTRO=$active ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-0} RMW_IMPLEMENTATION=${RMW_IMPLEMENTATION:-?} ROS_LOCALHOST_ONLY=${ROS_LOCALHOST_ONLY:-?}"
  if [ "$active" != "$os_distro" ] && [ "$os_id" != "" ]; then
    _say "WARNING: Ubuntu $os_id runs Isaac Sim's bundled $os_distro bridge unless ROS_DISTRO is exported for run.sh; $active and $os_distro do not interoperate with these DDS settings"
  fi
  if [ "$dry" = 1 ] || [ "$keep_daemon" = 1 ]; then
    return 0
  fi
  if command -v ros2 >/dev/null 2>&1; then
    if timeout -s KILL 15 ros2 daemon stop >/dev/null 2>&1; then
      _say "stopped any ros2 daemon (the next ros2 command starts one with these settings)"
    else
      _say "ros2 daemon stop failed or timed out; use --no-daemon with ros2 topic/node list"
    fi
  else
    _say "WARNING: ros2 is not on PATH after sourcing; check the install"
  fi
  return 0
}

if (return 0 2>/dev/null); then
  _ros_env_main "$@"
  _ros_env_rc=$?
  unset -f _ros_env_main _say _resolve
  # Not `set --`: that would replace the positional parameters of the shell that sourced this.
  eval "unset _ros_env_rc; return $_ros_env_rc"
else
  echo "ros_env.sh must be sourced, not run: source ${0} [install] [--overlay path]" >&2
  exit 2
fi
