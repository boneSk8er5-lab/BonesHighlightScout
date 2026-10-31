@echo off
rem Builds dist\BonesHighlightScout\BonesHighlightScout.exe
rem
rem   build_exe.bat                 the app uses ffmpeg from PATH (small build)
rem   build_exe.bat --with-ffmpeg   also copies ffmpeg, ffprobe and ffplay into the build so it runs
rem                                 on PCs without ffmpeg (adds about 650 MB with a "full" ffmpeg build)
cd /d "%~dp0"

echo Installing build tools...
python -m pip install -q -r BonesHighlightScout\requirements.txt pyinstaller
if errorlevel 1 goto :fail

echo Building...
python -m PyInstaller --noconfirm --clean BonesHighlightScout.spec
if errorlevel 1 goto :fail

if /i "%~1"=="--with-ffmpeg" (
    if not exist "dist\BonesHighlightScout\ffmpeg" mkdir "dist\BonesHighlightScout\ffmpeg"
    for %%t in (ffmpeg ffprobe ffplay) do (
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
