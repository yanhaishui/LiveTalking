@echo off
setlocal EnableExtensions EnableDelayedExpansion

title LiveTalking Service Starter
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_ROOT=%%~fI"
set "PYTHON_EXE=%USERPROFILE%\miniconda3\envs\livetalking\python.exe"
set "LOG_DIR=%PROJECT_ROOT%\data\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
if not defined EDGE_TTS_RETRIES set "EDGE_TTS_RETRIES=8"
if not defined EDGE_TTS_ATTEMPT_TIMEOUT_SEC set "EDGE_TTS_ATTEMPT_TIMEOUT_SEC=12"
if not defined EDGE_TTS_MAX_AUDIO_SECONDS set "EDGE_TTS_MAX_AUDIO_SECONDS=20"
if not defined EDGE_TTS_PREFER_DIRECT set "EDGE_TTS_PREFER_DIRECT=1"
if not defined LT_AUDIO_QUEUE_SECONDS set "LT_AUDIO_QUEUE_SECONDS=1"
if not defined LT_AUDIO_OVERFLOW_MODE set "LT_AUDIO_OVERFLOW_MODE=auto"
if not defined LT_AUDIO_PREFER_NON_HDMI set "LT_AUDIO_PREFER_NON_HDMI=1"
if not defined LT_AUDIO_FRAMES_PER_BUFFER set "LT_AUDIO_FRAMES_PER_BUFFER=320"
if not defined LT_VCAM_AUDIO_QUEUE_SECONDS set "LT_VCAM_AUDIO_QUEUE_SECONDS=0.6"
if not defined LT_VCAM_AUDIO_KEEP_SECONDS set "LT_VCAM_AUDIO_KEEP_SECONDS=0.2"
if not defined LT_ENABLE_TRANSITION set "LT_ENABLE_TRANSITION=1"
if not defined LT_TRANSITION_SEC set "LT_TRANSITION_SEC=0.16"
if not defined LT_SPEAKING_HANGOVER_SEC set "LT_SPEAKING_HANGOVER_SEC=0.35"
if not defined LT_STATE_SWITCH_LOG_COOLDOWN_SEC set "LT_STATE_SWITCH_LOG_COOLDOWN_SEC=0.4"
if not defined LT_SPEAKER_SEGMENT_MAX_CHARS set "LT_SPEAKER_SEGMENT_MAX_CHARS=70"
if not defined LT_SPEAKER_MAX_TEXT_CHARS set "LT_SPEAKER_MAX_TEXT_CHARS=200000"
if not defined LT_SPEAKER_DISPATCH_GUARD_SEC set "LT_SPEAKER_DISPATCH_GUARD_SEC=0.8"
if not defined LT_SPEAKER_DISPATCH_GUARD_PER_CHAR set "LT_SPEAKER_DISPATCH_GUARD_PER_CHAR=0.18"
if not defined LT_SPEAKER_MUSETALK_GUARD_SEC set "LT_SPEAKER_MUSETALK_GUARD_SEC=4.0"
if not defined LT_SPEAKER_MUSETALK_GUARD_PER_CHAR set "LT_SPEAKER_MUSETALK_GUARD_PER_CHAR=0.16"
if not defined LT_SPEAKER_PREFETCH_WHILE_SPEAKING set "LT_SPEAKER_PREFETCH_WHILE_SPEAKING=auto"
if not defined LT_MUSETALK_PREFER_RELIABLE_SPEECH set "LT_MUSETALK_PREFER_RELIABLE_SPEECH=1"
if not defined LT_MUSETALK_FEAT_OVERFLOW_MODE set "LT_MUSETALK_FEAT_OVERFLOW_MODE=auto"
if not defined LT_MUSETALK_AUDIO_QUEUE_SECONDS set "LT_MUSETALK_AUDIO_QUEUE_SECONDS=4"
if not defined LT_MUSETALK_FEAT_QUEUE_SIZE set "LT_MUSETALK_FEAT_QUEUE_SIZE=8"
if not defined LT_MUSETALK_ENABLE_PACING set "LT_MUSETALK_ENABLE_PACING=auto"
if not defined MUSETALK_TARGET_FPS set "MUSETALK_TARGET_FPS=25"
if not defined LT_TTS_MAX_CHARS set "LT_TTS_MAX_CHARS=70"

if not exist "%PYTHON_EXE%" (
  echo [ERROR] Python not found: "%PYTHON_EXE%"
  echo         Please edit this file and set a valid PYTHON_EXE path.
  pause
  exit /b 1
)

echo [INFO] Project root: "%PROJECT_ROOT%"
echo [INFO] Python: "%PYTHON_EXE%"
echo.

call :configure_edge_tts_proxy
call :cleanup_existing_instances
call :ensure_9001
call :ensure_9100

echo.
echo [DONE] Service check complete.
echo        Control API: http://127.0.0.1:9001
echo        Web Admin  : http://127.0.0.1:9100
echo.
pause
exit /b 0

:configure_edge_tts_proxy
if defined EDGE_TTS_PROXY (
  echo [INFO] EDGE_TTS_PROXY is pre-set: %EDGE_TTS_PROXY%
  goto :eof
)

for %%P in (7897 7890 10809 1080) do (
  netstat -ano | findstr ":%%P" | findstr LISTENING >nul
  if !errorlevel! equ 0 (
    set "EDGE_TTS_PROXY=http://127.0.0.1:%%P"
    goto :proxy_found
  )
)

echo [WARN] No local proxy port detected for EdgeTTS.
echo [WARN] If EdgeTTS still fails, manually set EDGE_TTS_PROXY before startup.
goto :eof

:proxy_found
echo [INFO] Auto-detected EDGE_TTS_PROXY=!EDGE_TTS_PROXY!
goto :eof

:cleanup_existing_instances
echo [INFO] Checking existing service instances...
call :kill_port_listener 9001 "Control API"
call :kill_port_listener 9100 "Web Admin"
ping -n 2 127.0.0.1 >nul
goto :eof

:ensure_9001
call :is_listening 9001
if not errorlevel 1 (
  echo [WARN] 9001 is still occupied. Trying to kill again...
  call :kill_port_listener 9001 "Control API"
)

echo [INFO] Starting Control API on 9001...
powershell -NoLogo -NoProfile -Command "Start-Process -FilePath '%PYTHON_EXE%' -ArgumentList '-m','apps.control_api' -WorkingDirectory '%PROJECT_ROOT%' -WindowStyle Hidden -RedirectStandardOutput '%LOG_DIR%\control_api.out.log' -RedirectStandardError '%LOG_DIR%\control_api.err.log'"
call :wait_listen 9001 "Control API"
goto :eof

:ensure_9100
call :is_listening 9100
if not errorlevel 1 (
  echo [WARN] 9100 is still occupied. Trying to kill again...
  call :kill_port_listener 9100 "Web Admin"
)

echo [INFO] Starting Web Admin on 9100...
powershell -NoLogo -NoProfile -Command "Start-Process -FilePath '%PYTHON_EXE%' -ArgumentList '-m','http.server','9100' -WorkingDirectory '%PROJECT_ROOT%\apps\web_admin' -WindowStyle Hidden -RedirectStandardOutput '%LOG_DIR%\web_admin_9100.out.log' -RedirectStandardError '%LOG_DIR%\web_admin_9100.err.log'"
call :wait_listen 9100 "Web Admin"
goto :eof

:kill_port_listener
set "PORT=%~1"
set "NAME=%~2"
set "KILLED=0"
set "LAST_PID="

for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%PORT%" ^| findstr LISTENING') do (
  if /I not "%%P"=="!LAST_PID!" (
    set "LAST_PID=%%P"
    echo [INFO] Found %NAME% on port %PORT% ^(PID %%P^). Killing...
    taskkill /PID %%P /T /F >nul 2>&1
    if !errorlevel! equ 0 (
      echo [OK] Killed PID %%P.
      set "KILLED=1"
    ) else (
      echo [WARN] Failed to kill PID %%P ^(already exited or no permission^).
    )
  )
)

if "!KILLED!"=="0" (
  echo [INFO] No running %NAME% instance on port %PORT%.
)
exit /b 0

:is_listening
set "PORT=%~1"
netstat -ano | findstr ":%PORT%" | findstr "LISTENING" >nul
exit /b %ERRORLEVEL%

:wait_listen
set "PORT=%~1"
set "NAME=%~2"

for /l %%N in (1,1,12) do (
  call :is_listening %PORT%
  if not errorlevel 1 (
    echo [OK] %NAME% is listening on %PORT%.
    exit /b 0
  )
  ping -n 2 127.0.0.1 >nul
)

echo [WARN] %NAME% did not confirm listening on %PORT% within timeout.
exit /b 1
