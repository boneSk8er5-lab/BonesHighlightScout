@echo off
rem Starts Bones Highlight Scout, installing missing Python packages first.
cd /d "%~dp0BonesHighlightScout"

python -c "import ttkbootstrap, numpy, matplotlib" 2>nul
if errorlevel 1 (
    echo Installing required Python packages...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Package install failed. See the messages above.
        pause
        exit /b 1
    )
)

start "" pythonw BonesHighlightScout.py
