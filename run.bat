@echo off
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
)
echo.
echo Starting Notebook Intelligence System (NIS)...
echo Access the Web UI at: http://localhost:8000
echo Access Health Check at: http://localhost:8000/health
echo.
python app.py
