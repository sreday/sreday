@echo off
rem LIVE talk factory app: http://localhost:8043 (double-click this file)
cd /d "%~dp0"
set PYTHONUTF8=1
"%~dp0env\Scripts\python.exe" -m live_factory serve
