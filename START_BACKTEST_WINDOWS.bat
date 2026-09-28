@echo off
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 goto python_launcher
where python >nul 2>nul
if not errorlevel 1 goto python_command
echo Python 3.12 or newer was not found.
pause
exit /b 1

:python_launcher
set "PYCMD=py -3"
goto start

:python_command
set "PYCMD=python"

:start
echo Downloading available 2026 Si and CR minute history.
echo Previously downloaded days are reused; first launch can take a while.
%PYCMD% -m backtest.server download --from 2026-01-01 --till 2026-12-31
if errorlevel 1 goto failed
echo Opening the new replay preview...
%PYCMD% -m backtest.server serve
exit /b 0

:failed
echo The history download failed. Check the error above.
pause
exit /b 1
