#!/bin/bash
# Start TalkQuery (backend + frontend)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Helper: check if port is used by our own process ─────
stop_if_ours() {
    local port=$1
    local label=$2
    local PID=$(lsof -t -i :$port 2>/dev/null || true)
    if [ -z "$PID" ]; then
        return 0  # port free
    fi
    local CMD=$(ps -p $PID -o comm= 2>/dev/null || true)
    case "$CMD" in
        uvicorn|python|python3)
            echo "  Stopping our $label (PID $PID on port $port)..."
            kill $PID 2>/dev/null || true
            sleep 1
            ;;
        node|next-server)
            echo "  Stopping our $label (PID $PID on port $port)..."
            kill $PID 2>/dev/null || true
            sleep 1
            ;;
        *)
            echo "ERROR: Port $port used by foreign process \"$CMD\" (PID $PID). Stop it manually."
            exit 1
            ;;
    esac
}

# ── Stop existing instances ──────────────────────────────
echo "Checking ports..."

stop_if_ours 8001 "backend"
stop_if_ours 3001 "frontend"

# Double-check ports are free
for port in 8001 3001; do
    if lsof -t -i :$port &>/dev/null; then
        echo "ERROR: Port $port still in use after stop attempt."
        exit 1
    fi
done

echo ""

# ── Start backend ────────────────────────────────────────
echo "Starting backend..."
cd "$SCRIPT_DIR/backend"
source venv/bin/activate
python -m uvicorn main:app --host 0.0.0.0 --port 8001 --reload &
BACKEND_PID=$!

# ── Start frontend (production mode) ─────────────────────
echo "Starting frontend..."
cd "$SCRIPT_DIR/frontend"
npm run build 2>&1 | tail -1
npm run start -- -H 0.0.0.0 -p 3001 &
FRONTEND_PID=$!

echo ""
echo "Backend:  http://localhost:8001 (PID $BACKEND_PID)"
echo "Frontend: http://localhost:3001 (PID $FRONTEND_PID)"
echo ""
echo "Press Ctrl+C to stop all"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM
wait
