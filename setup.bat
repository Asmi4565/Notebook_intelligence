@echo off
echo ===================================================
echo Setting up Notebook Intelligence System (NIS)...
echo ===================================================

python -m venv venv
if %errorlevel% neq 0 (
    echo [ERROR] Failed to create virtual environment. Ensure Python 3.10+ is installed.
    exit /b %errorlevel%
)

call venv\Scripts\activate.bat

echo Installing dependencies from requirements.txt...
pip install -r requirements.txt

if not exist .env (
    echo Copying .env.example to .env...
    copy .env.example .env
)

echo.
echo ===================================================
echo Setup complete! To start the application:
echo Run: run.bat
echo ===================================================
