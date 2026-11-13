# Source this file to make the old variable names work for one release:
#
#     . "$ROOT/scripts/legacy_env.sh"
#
# It runs mcp/twinsmith/legacy.py, the one file that holds the table of old names, and exports each new name
# whose old name is set. Each old name is reported once, on stderr. It does nothing when python3 or that
# file is missing, and it never fails the script that sources it.
_twinsmith_legacy="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/mcp/twinsmith/legacy.py"
if [ -f "$_twinsmith_legacy" ] && command -v python3 >/dev/null 2>&1; then
  _twinsmith_exports="$(python3 "$_twinsmith_legacy" --exports)" && eval "$_twinsmith_exports"
fi
unset _twinsmith_legacy _twinsmith_exports
