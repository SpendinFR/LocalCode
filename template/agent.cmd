@echo off
setlocal
set "REPO=%~dp0"
if "%~1"=="" (
  echo Usage: agent.cmd .tasks\TASK-XXX.md
  echo    ou: agent.cmd -Resume latest
  exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%REPO%agent.ps1" %*
exit /b %errorlevel%
