# Source this to load an ESP-IDF environment for this firmware project.
#
# Usage:
#   cd firmware/esp32-hid
#   source ./env.sh
#
# Resolution order:
#   1. $IDF_PATH/export.sh, when IDF_PATH is already set (standard install)
#   2. the machine's custom activate script (this Mac's ESP-IDF v6.1 layout)
#   3. ~/esp/v6.1/esp-idf/export.sh (common manual install)
#
# This file contains no secrets and no project-specific toolchain paths beyond
# the documented fallbacks. Set IDF_PATH yourself if your install differs.

if [ -n "${IDF_PATH:-}" ] && [ -f "$IDF_PATH/export.sh" ]; then
    # shellcheck disable=SC1091
    . "$IDF_PATH/export.sh"
elif [ -f "$HOME/.espressif/tools/activate_idf_v6.1.sh" ]; then
    # shellcheck disable=SC1091
    . "$HOME/.espressif/tools/activate_idf_v6.1.sh"
elif [ -f "$HOME/esp/v6.1/esp-idf/export.sh" ]; then
    # shellcheck disable=SC1091
    . "$HOME/esp/v6.1/esp-idf/export.sh"
else
    echo "ESP-IDF not found. Set IDF_PATH to your ESP-IDF checkout, then source this file again." >&2
    return 1 2>/dev/null || exit 1
fi
