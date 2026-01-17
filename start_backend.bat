@echo off
echo ========================================
echo   ScriptAgent Backend Server
echo ========================================
echo.
echo Starting Flask API server...
echo Backend will run on http://localhost:5000
echo.
cd backend
python app.py
pause
