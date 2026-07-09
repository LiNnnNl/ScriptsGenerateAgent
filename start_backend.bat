@echo off
echo ========================================
echo   ScriptAgent Backend Server
echo ========================================
echo.
echo Starting Flask API server...
echo Backend API will run on http://localhost:5001
echo.
cd /d "%~dp0"
uv run python backend/app.py
pause
