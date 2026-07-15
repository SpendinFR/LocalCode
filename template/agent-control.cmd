@echo off
setlocal
set "REPO=%~dp0"
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%REPO%.microagent\control.py" %* --repo "%REPO%"
) else (
  python "%REPO%.microagent\control.py" %* --repo "%REPO%"
)
exit /b %ERRORLEVEL%
