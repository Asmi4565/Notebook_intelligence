#!/usr/bin/env bash
if [ -d "venv" ]; then
    source venv/bin/activate
fi
echo ""
echo "Starting Notebook Intelligence System (NIS)..."
echo "Access the Web UI at: http://localhost:8000"
echo "Access Health Check at: http://localhost:8000/health"
echo ""
python3 app.py
