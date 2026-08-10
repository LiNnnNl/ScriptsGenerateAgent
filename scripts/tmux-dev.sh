#!/usr/bin/env bash
set -euo pipefail

SESSION="${SCRIPTAGENT_TMUX_SESSION:-scriptagent}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-start}"

has_session() {
    tmux has-session -t "$SESSION" 2>/dev/null
}

start_session() {
    if has_session; then
        echo "tmux session '$SESSION' is already running."
        echo "Attach with: tmux attach -t $SESSION"
        return
    fi

    tmux new-session -d -s "$SESSION" -n backend -c "$ROOT" \
        "uv run python backend/app.py"
    tmux new-window -t "$SESSION" -n frontend -c "$ROOT/frontend" \
        "python3 -m http.server 8080"
    tmux set-option -t "$SESSION" remain-on-exit on >/dev/null

    echo "Started tmux session '$SESSION'."
    echo "Backend:  http://localhost:5001"
    echo "Frontend: http://localhost:8080"
    echo "Attach:   tmux attach -t $SESSION"
}

stop_session() {
    if has_session; then
        tmux kill-session -t "$SESSION"
        echo "Stopped tmux session '$SESSION'."
    else
        echo "tmux session '$SESSION' is not running."
    fi
}

status_session() {
    if has_session; then
        tmux list-windows -t "$SESSION"
    else
        echo "tmux session '$SESSION' is not running."
        return 1
    fi
}

attach_session() {
    if ! has_session; then
        start_session
    fi
    tmux attach -t "$SESSION"
}

case "$ACTION" in
    start)
        start_session
        ;;
    attach)
        attach_session
        ;;
    status)
        status_session
        ;;
    stop)
        stop_session
        ;;
    restart)
        stop_session
        start_session
        ;;
    *)
        echo "Usage: $0 {start|attach|status|stop|restart}"
        exit 2
        ;;
esac
