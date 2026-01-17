@echo off
echo ========================================
echo   ScriptAgent Frontend Server
echo ========================================
echo.
echo Starting HTTP server...
echo Frontend will be available at http://localhost:8080
echo.
cd frontend
python -m http.server 8080
pause
