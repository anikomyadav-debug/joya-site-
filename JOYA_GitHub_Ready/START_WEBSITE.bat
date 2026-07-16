@echo off
title JOYA Website Server
color 0b
echo ==========================================================
echo    JOYA Mark XXXIX - Website Server
echo ==========================================================
echo.
echo   Starting the login-protected website server...
echo   (real server with database - like any real website)
echo.
echo   When it starts, open your browser and go to:
echo.
echo        http://localhost:8000/
echo.
echo   - The FIRST person to sign up becomes the ADMIN.
echo   - Admin can open http://localhost:8000/admin.html
echo     to see ALL registered users and their data.
echo.
echo   Keep this window OPEN while using the website.
echo   Press Ctrl+C here (or close window) to STOP the server.
echo ==========================================================
echo.

cd /d "%~dp0"

REM Try common Python commands until one works
where python >nul 2>nul
if %errorlevel%==0 (
    python server.py
    goto :end
)

where py >nul 2>nul
if %errorlevel%==0 (
    py server.py
    goto :end
)

echo [ERROR] Python not found on this PC.
echo Please install Python 3.11+ from https://python.org
echo (During install, tick "Add Python to PATH".)

:end
echo.
echo Server stopped. Press any key to close.
pause >nul
