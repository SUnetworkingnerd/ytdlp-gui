@echo off
rem Builds ytdlp-gui.exe (with the yt-dlp logo) from this project.
rem If Python 3.10+ is missing it is installed automatically (winget, else python.org).
rem Usage:  packaging\build_windows.bat [--onefile] [--no-tools] [--zip]
rem Set YTDLP_GUI_NO_AUTO_INSTALL=1 to turn off the automatic Python install.
setlocal
cd /d "%~dp0.."

call :find_python
if not defined PY (
    if "%YTDLP_GUI_NO_AUTO_INSTALL%"=="1" goto :no_python
    echo Python 3.10 or newer was not found. Installing it automatically...
    call :install_python
    call :find_python
)
if not defined PY goto :no_python
echo Using Python: %PY%

if not exist ".venv-build\Scripts\python.exe" (
    echo Creating build environment...
    %PY% -m venv .venv-build
    if errorlevel 1 exit /b 1
)
set "VPY=.venv-build\Scripts\python.exe"

echo Installing build requirements...
"%VPY%" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
"%VPY%" -m pip install -r requirements.txt pyinstaller
if errorlevel 1 exit /b 1

"%VPY%" packaging\build_windows.py %*
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo.
echo Build finished. Look in the dist folder.
endlocal
exit /b 0

:no_python
echo Could not find or install Python 3.10 or newer.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH", then run this again.
exit /b 1

rem ---------------------------------------------------------------------------
rem Sets PY to a working Python 3.10+ command, or leaves it empty.
rem Each candidate is really run, so the Microsoft Store "python.exe" stub does not count.
:find_python
set "PY="
for %%C in ("py -3" "python" "python3") do (
    if not defined PY (
        %%~C -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
        if not errorlevel 1 set "PY=%%~C"
    )
)
if not defined PY (
    rem A fresh install is not on PATH until a new terminal is opened, so look in the usual places.
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*" "%ProgramFiles%\Python3*") do (
        if exist "%%~D\python.exe" (
            "%%~D\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
            if not errorlevel 1 set "PY="%%~D\python.exe""
        )
    )
)
exit /b 0

rem ---------------------------------------------------------------------------
rem Installs Python 3.12 for the current user (no admin needed): winget first, else python.org.
:install_python
where winget >nul 2>nul
if not errorlevel 1 (
    echo Installing Python 3.12 with winget...
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
    call :find_python
    if defined PY exit /b 0
)
echo Downloading the Python 3.12 installer from python.org...
set "PYINST=%TEMP%\python-3.12.10-amd64.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile '%PYINST%'"
if errorlevel 1 exit /b 1
echo Installing Python 3.12 for the current user...
start /wait "" "%PYINST%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0
del "%PYINST%" >nul 2>nul
exit /b 0
