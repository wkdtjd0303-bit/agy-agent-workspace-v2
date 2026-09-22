#!/usr/bin/env bash
set -e

SERVICE_NAME="agy-discord-bot.service"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "$1" in
    start)
        echo "Starting $SERVICE_NAME..."
        systemctl --user start "$SERVICE_NAME"
        systemctl --user status "$SERVICE_NAME" --no-pager
        ;;
    stop)
        echo "Stopping $SERVICE_NAME..."
        systemctl --user stop "$SERVICE_NAME"
        ;;
    restart)
        echo "Restarting $SERVICE_NAME..."
        systemctl --user restart "$SERVICE_NAME"
        systemctl --user status "$SERVICE_NAME" --no-pager
        ;;
    status)
        systemctl --user status "$SERVICE_NAME" --no-pager
        ;;
    logs)
        journalctl --user -u "$SERVICE_NAME" -f
        ;;
    enable)
        echo "Enabling auto-start on boot for $SERVICE_NAME..."
        systemctl --user daemon-reload
        systemctl --user enable "$SERVICE_NAME"
        loginctl enable-linger "$USER" 2>/dev/null || true
        echo "Auto-start enabled!"
        ;;
    disable)
        echo "Disabling auto-start for $SERVICE_NAME..."
        systemctl --user disable "$SERVICE_NAME"
        echo "Auto-start disabled!"
        ;;
    install)
        echo "Installing systemd services..."
        mkdir -p "$HOME/.config/systemd/user"
        cp "$DIR/agy-discord-bot.service" "$HOME/.config/systemd/user/$SERVICE_NAME" 2>/dev/null || true
        # [🛡️ Token Protection Mandate - Solar Pro 4 Architecture Protocol]
        # surface-status.service is permanently decommissioned to prevent ghost token consumption.
        systemctl --user stop surface-status.service 2>/dev/null || true
        systemctl --user disable surface-status.service 2>/dev/null || true
        rm -f "$HOME/.config/systemd/user/surface-status.service" 2>/dev/null || true
        systemctl --user daemon-reload
        systemctl --user enable "$SERVICE_NAME"
        loginctl enable-linger "$USER" 2>/dev/null || true
        echo "Services installed and enabled! (surface-status.service is permanently decommissioned)"
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs|enable|disable|install}"
        exit 1
        ;;
esac
