@echo off
setlocal
cd /d "%~dp0"
title Ache Desktop
set "PY=python"
where python >nul 2>nul
if errorlevel 1 (
  where py >nul 2>nul
  if not errorlevel 1 set "PY=py -3"
)
%PY% -c "import webview, uvicorn" >nul 2>nul
if errorlevel 1 (
  echo Installing dependencies: pywebview uvicorn
  %PY% -m pip install pywebview uvicorn
  if errorlevel 1 goto :failed
)
%PY% "%~dp0desktop\app.py"
if errorlevel 1 goto :failed
exit /b 0

:failed
echo.
echo Failed to start. Check Python and dependencies:
echo   pip install -r requirements.txt
echo   pip install pywebview uvicorn
pause
exit /b 1
