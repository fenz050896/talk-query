#!/bin/bash
# Start TalkQuery backend
cd "$(dirname "$0")"
source venv/bin/activate
python -m uvicorn main:app --host 0.0.0.0 --port 8001 --reload
