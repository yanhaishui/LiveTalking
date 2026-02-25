@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ============================================================
REM LiveTalking one-click launcher for Windows
REM Place this file at project root and double-click to run.
REM ============================================================

REM ----- User-configurable settings -----
set "LIVETALKING_ENV=livetalking"
set "TRANSPORT=webrtc"
set "MODEL=wav2lip"
set "AVATAR_ID=wav2lip256_avatar1"
set "LISTEN_PORT=8010"
REM Optional: set absolute python path to bypass conda activation
REM set "LIVETALKING_PYTHON=C:\Python310\python.exe"

REM Resolve project root from script location
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%.") do set "PROJECT_DIR=%%~fI"

echo [INFO] Project dir: %PROJECT_DIR%
if not exist "%PROJECT_DIR%\app.py" (
  echo [ERROR] app.py not found. Put this file in LiveTalking project root.
  exit /b 1
)

if not exist "%PROJECT_DIR%\models\wav2lip.pth" (
  echo [WARN] models\wav2lip.pth not found.
  echo [WARN] Please confirm model file is ready before live usage.
)

if not exist "%PROJECT_DIR%\data\avatars\%AVATAR_ID%" (
  echo [WARN] Avatar directory not found: data\avatars\%AVATAR_ID%
  echo [WARN] Please confirm avatar assets are ready.
)

pushd "%PROJECT_DIR%" >nul

set "PYTHON_CMD="

if defined LIVETALKING_PYTHON (
  set "PYTHON_CMD=%LIVETALKING_PYTHON%"
  goto :run_app
)

where conda >nul 2>nul
if %ERRORLEVEL%==0 (
  call conda activate "%LIVETALKING_ENV%" >nul 2>nul
  if %ERRORLEVEL%==0 (
    set "PYTHON_CMD=python"
    goto :run_app
  )
  echo [WARN] conda found but activate failed: %LIVETALKING_ENV%
)

if exist "%USERPROFILE%\miniconda3\Scripts\activate.bat" (
  call "%USERPROFILE%\miniconda3\Scripts\activate.bat" "%USERPROFILE%\miniconda3" >nul 2>nul
  call conda activate "%LIVETALKING_ENV%" >nul 2>nul
  if %ERRORLEVEL%==0 (
    set "PYTHON_CMD=python"
    goto :run_app
  )
  echo [WARN] miniconda3 found but activate failed: %LIVETALKING_ENV%
)

if exist "%USERPROFILE%\anaconda3\Scripts\activate.bat" (
  call "%USERPROFILE%\anaconda3\Scripts\activate.bat" "%USERPROFILE%\anaconda3" >nul 2>nul
  call conda activate "%LIVETALKING_ENV%" >nul 2>nul
  if %ERRORLEVEL%==0 (
    set "PYTHON_CMD=python"
    goto :run_app
  )
  echo [WARN] anaconda3 found but activate failed: %LIVETALKING_ENV%
)

echo [WARN] Conda env not activated. Fallback to python from PATH.
set "PYTHON_CMD=python"

:run_app
"%PYTHON_CMD%" --version >nul 2>nul
if %ERRORLEVEL% neq 0 (
  echo [ERROR] Python not available. Please install Python/Conda first.
  popd >nul
  exit /b 2
)

echo [INFO] Starting LiveTalking...
echo [INFO] Command:
echo        %PYTHON_CMD% app.py --transport %TRANSPORT% --model %MODEL% --avatar_id %AVATAR_ID% --listenport %LISTEN_PORT%
echo.
echo [INFO] Open in browser after startup:
echo        http://127.0.0.1:%LISTEN_PORT%/dashboard.html
echo.

"%PYTHON_CMD%" app.py --transport "%TRANSPORT%" --model "%MODEL%" --avatar_id "%AVATAR_ID%" --listenport "%LISTEN_PORT%"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo [INFO] LiveTalking process exited with code: %EXIT_CODE%
popd >nul
exit /b %EXIT_CODE%

