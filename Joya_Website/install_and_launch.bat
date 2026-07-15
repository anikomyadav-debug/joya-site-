@echo off
title JOYA AI OS Installer - Launcher
echo ==========================================
echo   JOYA AI OS - Initializing System
echo ==========================================
echo.

:: 1. Check if python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not added to your system PATH!
    echo Please install Python 3.10+ and select "Add python.exe to PATH" during installation.
    echo Opening Python download page...
    start https://www.python.org/downloads/
    pause
    exit /b
)

:: Set the server download URL (will be populated dynamically by the server, or defaults to localhost)
set SERVER_URL=__SERVER_URL__
if "%SERVER_URL%"=="__SERVER_URL__" set SERVER_URL=http://127.0.0.1:8000

:: Check if source files exist. If not, auto-download from server.
if exist "main.py" goto files_exist
if exist "Mark-XXXIX-main\Mark-XXXIX-main\Mark-XXXIX-main\main.py" goto files_exist

echo [INFO] Project files not found. Automatically downloading from %SERVER_URL%...
powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Write-Host 'Downloading project archive...'; Invoke-WebRequest -Uri '%SERVER_URL%/JOYA_AI_OS.zip' -OutFile 'JOYA_AI_OS.zip'; Write-Host 'Extracting project files...'; Expand-Archive -Path 'JOYA_AI_OS.zip' -DestinationPath '.' -Force; Remove-Item 'JOYA_AI_OS.zip'; Write-Host 'Download & extraction complete!'"

if exist "main.py" goto files_exist
if exist "Mark-XXXIX-main\Mark-XXXIX-main\Mark-XXXIX-main\main.py" goto files_exist

echo [ERROR] Failed to download or extract project files!
echo Please check your connection to %SERVER_URL% or download the ZIP manually.
pause
exit /b

:files_exist

:: 2. Create local virtual environment if it doesn't exist
if not exist ".venv" (
    echo [1/3] Creating virtual environment - .venv...
    python -m venv .venv
)

set VENV_PYTHON=.venv\Scripts\python.exe

:: 3. Upgrade pip and install requirements (only on first setup)
if exist ".venv\requirements_installed.txt" (
    echo [2/3] Dependencies already verified. Skipping installation steps.
    goto launch_app
)

echo [2/3] Installing/verifying dependencies (this runs only once)...
"%VENV_PYTHON%" -m pip install --upgrade pip
if exist "requirements.txt" (
    "%VENV_PYTHON%" -m pip install -r requirements.txt
) else (
    "%VENV_PYTHON%" -m pip install comtypes pycaw win10toast pyqt6 pyautogui pyperclip pygetwindow mss psutil sounddevice requests beautifulsoup4 duckduckgo-search pywinauto python-pptx plyer rapidfuzz SpeechRecognition pyttsx3 pillow numpy opencv-python google-genai google-generativeai
)

if %errorlevel% equ 0 (
    echo installed > ".venv\requirements_installed.txt"
) else (
    echo [ERROR] Failed to install dependencies! Please try running this script again.
    pause
    exit /b
)

:launch_app

:: 4. Create Desktop Shortcut for easy access
echo Creating Desktop Shortcut...
powershell -Command "$WshShell = New-Object -ComObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut(\"$HOME\Desktop\JOYA AI OS.lnk\"); $Shortcut.TargetPath = \"$PWD\install_and_launch.bat\"; $Shortcut.WorkingDirectory = \"$PWD\"; $Shortcut.IconLocation = \"$PWD\assets\app_logo.ico\"; $Shortcut.Save()"

:: 5. Launch the application
echo [3/3] Launching JOYA AI OS...
echo.
if exist "main.py" (
    "%VENV_PYTHON%" main.py
) else if exist "Mark-XXXIX-main\Mark-XXXIX-main\Mark-XXXIX-main\main.py" (
    cd Mark-XXXIX-main\Mark-XXXIX-main\Mark-XXXIX-main
    "..\..\..\.venv\Scripts\python.exe" main.py
) else (
    echo [ERROR] main.py not found! Make sure you extracted all files from the archive.
)

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] System crashed or closed with errors.
    pause
)
