@echo off
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 goto use_py
where python3.13 >nul 2>nul
if not errorlevel 1 goto use_python313
where python3 >nul 2>nul
if not errorlevel 1 goto use_python3
where python >nul 2>nul
if not errorlevel 1 goto use_python
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" goto use_local313
if exist "%LocalAppData%\Microsoft\WindowsApps\python3.13.exe" goto use_store313
goto nopython

:use_py
set "PYCMD=py -3"
goto run

:use_python313
set "PYCMD=python3.13"
goto run

:use_python3
set "PYCMD=python3"
goto run

:use_python
set "PYCMD=python"
goto run

:use_local313
set PYCMD="%LocalAppData%\Programs\Python\Python313\python.exe"
goto run

:use_store313
set PYCMD="%LocalAppData%\Microsoft\WindowsApps\python3.13.exe"
goto run

:run
echo Using: %PYCMD%
if exist "Si_open_2026_5m.html" copy /y "Si_open_2026_5m.html" "Si_open_2026_5m_previous.html" >nul
if exist "Si_open_2026_5m.html" del /q "Si_open_2026_5m.html"
echo Downloading full MOEX sessions and building the research chart...
echo After the chart opens, keep this window running so annotations are saved to disk.
%PYCMD% build_si_open_chart.py --serve
if errorlevel 1 goto failed
if not exist "Si_open_2026_5m.html" goto failed
exit /b 0

:nopython
echo Python 3 was not found.
echo Install Python 3 from Microsoft Store and run this file again.
pause
exit /b 1

:failed
echo The new chart was not created. The previous version is saved as Si_open_2026_5m_previous.html
echo Check the internet connection and try again.
pause
exit /b 1
