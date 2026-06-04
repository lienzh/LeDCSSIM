@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo  LeDCS Viewer  -  http://127.0.0.1:5002/script
echo  DSL script editor + OPC realtime bridge (MVP main entry)
echo ============================================================
echo  Ctrl+C to stop, close this window to kill viewer
echo.
py -3.12 -m src.viewer
echo.
echo Viewer exited. Press any key to close.
pause >nul
