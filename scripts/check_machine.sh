#!/bin/bash
# Checks whether this machine can run the twin before you install: OS, GPU and driver, tools,
# disk space, network to the sites install needs, the display, and the bridge port.
# Usage: scripts/check_machine.sh      (prints PASS/FAIL per item; exit 1 if any required item fails)
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail=0
ok()   { echo "PASS  $1"; }
bad()  { echo "FAIL  $1"; fail=1; }
warn() { echo "WARN  $1"; }

# Isaac Sim 6.2 supports Ubuntu 22.04 and 24.04 (x86_64).
codename=$(. /etc/os-release 2>/dev/null; echo "${VERSION_CODENAME:-unknown}")
case "$codename" in
  noble) ok "Ubuntu 24.04 (noble)" ;;
  jammy) ok "Ubuntu 22.04 (jammy)" ;;
  *)     bad "Ubuntu 22.04 or 24.04 required, found: $codename" ;;
esac
[ "$(uname -m)" = "x86_64" ] && ok "x86_64" || bad "x86_64 required, found: $(uname -m)"

if command -v nvidia-smi >/dev/null 2>&1; then
  gpu=$(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null | head -1)
  [ -n "$gpu" ] && ok "GPU and driver: $gpu" || bad "nvidia-smi found but reports no GPU"
  drv=$(echo "$gpu" | awk -F', ' '{print $NF}' | cut -d. -f1)
  case "$gpu" in *Blackwell*|*"RTX 50"*) min=580 ;; *) min=570 ;; esac
  if [ "${drv:-0}" -ge "$min" ] 2>/dev/null; then
    ok "driver major $drv (>= $min)"
  else
    warn "driver ${drv:-unknown}: want >= $min for this GPU"
  fi
  # Verified on an RTX PRO 6000 Blackwell with driver 595.99.02: Isaac Sim 6.2 renders the
  # twin in a window and headless. (Isaac Sim 5.1 crashed in librtx.scenedb.plugin.so on that driver.)
else
  bad "nvidia-smi not found: install the NVIDIA driver"
fi

command -v curl >/dev/null && ok "curl" || bad "curl not installed (needed for the network checks below)"
command -v git >/dev/null && ok "git $(git --version | awk '{print $3}')" || bad "git not installed"
command -v uv >/dev/null && ok "uv $(uv --version | awk '{print $2}')" || bad "uv not installed (https://docs.astral.sh/uv/)"

free_gb=$(df -BG --output=avail "$ROOT" | tail -1 | tr -dc '0-9')
[ "${free_gb:-0}" -ge 40 ] && ok "free disk ${free_gb} GB (>= 40 GB for Isaac Sim and the venv)" || bad "free disk ${free_gb:-0} GB: need about 40 GB"

for url in https://github.com https://pypi.org/simple/ https://pypi.nvidia.com/; do
  code=$(curl -s -o /dev/null -m 15 -w "%{http_code}" "$url")
  case "$code" in 2*|3*) ok "network reaches $url ($code)" ;; *) bad "cannot reach $url (got '$code'): install needs it" ;; esac
done

if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
  ok "display available (DISPLAY=${DISPLAY:-unset}); MCP screenshots must use this same value, see docs/MCP-SERVER.md"
else
  warn "no display: use UR5E_DT_HEADLESS=1 for checks"
fi

if (exec 3<>/dev/tcp/127.0.0.1/8766) 2>/dev/null; then warn "port 8766 is in use: stop the running app before launching"; else ok "port 8766 free"; fi

[ -d "$ROOT/exts/ur5e-dt" ] && ok "extensions present (exts/)" || warn "exts/ur5e-dt missing"
export OMNI_KIT_ACCEPT_EULA=YES   # importing isaacsim otherwise blocks on the EULA prompt
PY="${ISAAC_MCP_PYTHON:-${UR5E_DT_PYTHON:-$ROOT/.venv/bin/python}}"
if [ -x "$PY" ]; then
  ok ".venv present ($PY)"
  pyver=$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)
  [ "$pyver" = "3.12" ] && ok "Python 3.12" || bad "Python 3.12 required, interpreter is ${pyver:-unknown}: uv sync --python 3.12"
  isv=$("$PY" -c 'import importlib.metadata as m;print(m.version("isaacsim"))' 2>/dev/null)
  [ "$isv" = "6.2.0.0" ] && ok "isaacsim $isv" || bad "isaacsim 6.2.0.0 required, found: ${isv:-none}"
  case "$codename" in jammy) rd=humble ;; *) rd=jazzy ;; esac
  site=$("$PY" -c 'import isaacsim,os;print(os.path.dirname(isaacsim.__file__))' 2>/dev/null)
  . "$ROOT/scripts/isaac_ros_libs.sh"
  isaac_ros_lib_dir "$site" "$rd" >/dev/null && ok "ROS 2 $rd libs bundled with Isaac Sim" || bad "missing ROS 2 $rd libs under $site or its install root"
  # Blackwell needs a cu128+ torch build: it imports fine on an older one and fails at the first kernel.
  tinfo=$("$PY" - <<'PYEOF' 2>&1
import torch
cap = torch.cuda.get_device_capability()
x = torch.ones(8, device="cuda") * 2          # launches a real kernel
print(f"{cap[0]}.{cap[1]} torch {torch.__version__} kernel_ok={bool((x == 2).all())}")
PYEOF
)
  case "$tinfo" in
    *kernel_ok=True*) ok "CUDA kernel runs: $tinfo" ;;
    *ModuleNotFoundError*) warn "torch not installed in this env (host-only install?): skipping CUDA kernel check" ;;
    *) bad "torch CUDA kernel failed: $(echo "$tinfo" | tail -1)" ;;
  esac
else
  warn ".venv missing: run ./install.sh (or uv sync --python 3.12)"
fi

echo
[ $fail -eq 0 ] && echo "Machine check passed." || echo "Machine check failed: fix the FAIL items first."
exit $fail
