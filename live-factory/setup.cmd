@echo off
rem LIVE talk factory - one-time setup on Windows (double-click, or run from a terminal).
rem Creates live-factory\env with the Python packages (faster-whisper; on an NVIDIA PC also its CUDA libraries),
rem installs ffmpeg if it is missing, asks for the Descript token once, puts a launcher on the Desktop, checks the
rem machine, then opens the dashboard.
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1

if exist env\Scripts\python.exe goto :haveenv
set "PY="
for /f "delims=" %%p in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%p"
if not defined PY (
  echo Install Python 3.12 first: winget install -e --id Python.Python.3.12
  pause
  exit /b 1
)
echo Creating the Python environment with %PY% ...
"%PY%" -m venv env || goto :fail
:haveenv
echo Installing Python packages ...
env\Scripts\python.exe -m pip install -q --upgrade pip
env\Scripts\python.exe -m pip install -q -r requirements.txt || goto :fail
where nvidia-smi >nul 2>nul && (
  echo NVIDIA card found - installing the CUDA libraries Whisper uses ...
  env\Scripts\python.exe -m pip install -q nvidia-cublas-cu12 nvidia-cudnn-cu12==9.*
)
env\Scripts\python.exe -c "from live_factory import machine;import sys;sys.exit(0 if machine.find_tool('ffmpeg') else 1)" || (
  echo Installing ffmpeg ...
  winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
)
env\Scripts\python.exe -m live_factory setup
if errorlevel 1 (
  echo.
  echo Fix the items marked FIX above, then run setup.cmd again.
  pause
  exit /b 1
)
if defined LF_NO_LAUNCH exit /b 0
echo.
echo All set. Opening the dashboard ...
start "" "%~dp0run.cmd"
exit /b 0

:fail
echo Setup failed - see the messages above.
pause
exit /b 1
