#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="meowko"
SERVICE_FILE="$SCRIPT_DIR/meowko.service"
UV_PATH="$(command -v uv 2>/dev/null || true)"

if [[ -z "$UV_PATH" ]]; then
    echo "Error: uv not found in PATH" >&2
    exit 1
fi

if [[ ! -f "$SERVICE_FILE" ]]; then
    echo "Error: $SERVICE_FILE not found" >&2
    exit 1
fi

# Build the unit file with actual paths
UNIT=$(sed \
    -e "s|WORKING_DIR|$SCRIPT_DIR|" \
    -e "s|UV_PATH|$UV_PATH|" \
    "$SERVICE_FILE")

if [[ "$(id -u)" -eq 0 ]]; then
    # System-wide install (running as root)
    DEST="/etc/systemd/system/${SERVICE_NAME}.service"
    echo "$UNIT" > "$DEST"
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
    echo "Installed system service: $DEST"
else
    # User service install
    DEST_DIR="$HOME/.config/systemd/user"
    mkdir -p "$DEST_DIR"
    DEST="$DEST_DIR/${SERVICE_NAME}.service"
    echo "$UNIT" > "$DEST"
    systemctl --user daemon-reload
    systemctl --user enable "$SERVICE_NAME"
    systemctl --user restart "$SERVICE_NAME"
    # Enable lingering so the service runs without an active login session
    loginctl enable-linger "$USER"
    echo "Installed user service: $DEST"
fi

echo "Done. Check status with:"
if [[ "$(id -u)" -eq 0 ]]; then
    echo "  systemctl status $SERVICE_NAME"
    echo "  journalctl -u $SERVICE_NAME -f"
else
    echo "  systemctl --user status $SERVICE_NAME"
    echo "  journalctl --user -u $SERVICE_NAME -f"
fi
