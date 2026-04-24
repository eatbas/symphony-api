#!/usr/bin/env bash
# Symphony API — systemd wrapper
# Usage: ./symphony.sh [start|stop|restart|attach|status|logs]

SERVICE="symphony.service"
LOG="/root/symphony-api/symphony.log"

start() {
    systemctl start "$SERVICE"
    status
}

stop() {
    systemctl stop "$SERVICE"
    echo "[symphony] Stopped."
}

attach() {
    # Under systemd there is no tmux session. Tail the journal live; this is
    # the closest equivalent to "attach" and is detach-safe (Ctrl-C only kills
    # the tail, not the service).
    echo "[symphony] Tailing journal (Ctrl-C to exit; service keeps running)..."
    journalctl -fu "$SERVICE"
}

status() {
    if systemctl is-active --quiet "$SERVICE"; then
        echo "[symphony] RUNNING (systemd service: $SERVICE)"
        echo "[symphony] API: http://127.0.0.1:8080  Docs: http://127.0.0.1:8080/docs"
    else
        echo "[symphony] STOPPED"
        echo "[symphony] Start with: $0 start  (or: systemctl start $SERVICE)"
    fi
}

logs() {
    if [[ -f "$LOG" ]]; then
        tail -f "$LOG"
    else
        echo "[symphony] No log file yet at $LOG — falling back to journal"
        journalctl -fu "$SERVICE"
    fi
}

case "${1:-start}" in
    start)   start ;;
    stop)    stop ;;
    restart) systemctl restart "$SERVICE"; status ;;
    attach)  attach ;;
    status)  status ;;
    logs)    logs ;;
    *)
        echo "Usage: $0 {start|stop|restart|attach|status|logs}"
        exit 1
        ;;
esac
