@echo off
rem Builds dist\BonesHighlightScout\BonesHighlightScout.exe
rem
rem   build_exe.bat                 the app uses ffmpeg from PATH (small build)
rem   build_exe.bat --with-ffmpeg   also copies ffmpeg into the build so it runs on PCs without it
rem                                 (for a build to share, use tools\make_release.py instead)
cd /d "%~dp0"

rem Windows won't let a running program be replaced.
tasklist /FI "IMAGENAME eq BonesHighlightScout.exe" | find /I "BonesHighlightScout.exe" >nul
if not errorlevel 1 (
    echo Bones Highlight Scout is still open. Close it, then run this again.
    pause
    exit /b 1
)

echo Installing build tools...
python -m pip install -q -r BonesHighlightScout\requirements.txt pyinstaller
if errorlevel 1 goto :fail

echo Building...
python -m PyInstaller --noconfirm --clean BonesHighlightScout.spec
if errorlevel 1 goto :fail

if /i "%~1"=="--with-ffmpeg" (
    if not exist "dist\BonesHighlightScout\ffmpeg" mkdir "dist\BonesHighlightScout\ffmpeg"
    for %%t in (ffmpeg) do (
        for /f "delims=" %%p in ('where %%t 2^>nul') do copy /y "%%p" "dist\BonesHighlightScout\ffmpeg\" >nul
    )
    echo Copied ffmpeg into the build.
)

echo.
echo Built: dist\BonesHighlightScout\BonesHighlightScout.exe
exit /b 0

:fail
echo.
echo Build failed. See the messages above.
pause
exit /b 1
