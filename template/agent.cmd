@echo off
setlocal
set "REPO=%~dp0"
if "%~1"=="" (
  echo Usage: agent.cmd .tasks\TASK-XXX.md
  exit /b 2
)
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%REPO%.microagent\doctor.py" "%~1" --repo "%REPO%"
  if errorlevel 1 exit /b %errorlevel%
  py -3 "%REPO%.microagent\orchestrator.py" "%~1" --repo "%REPO%"
  exit /b %errorlevel%
)
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.10+ introuvable
  exit /b 2
)
python "%REPO%.microagent\doctor.py" "%~1" --repo "%REPO%"
if errorlevel 1 exit /b %errorlevel%
python "%REPO%.microagent\orchestrator.py" "%~1" --repo "%REPO%"
exit /b %errorlevel%
