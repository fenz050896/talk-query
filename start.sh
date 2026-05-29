#!/bin/bash
# Start TalkQuery (backend + frontend)
echo "Starting TalkQuery..."

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Start backend
cd "$SCRIPT_DIR/backend"
source venv/bin/activate
python -m uvicorn main:app --host 0.0.0.0 --port 8001 --reload &
BACKEND_PID=$!

# Start frontend (production mode — dev mode has Turbopack overlay issues)
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
