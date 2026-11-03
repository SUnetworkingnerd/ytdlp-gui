@echo off
rem Windows: builds ytdlp-gui.exe (options: --onefile --no-tools --zip)
call "%~dp0packaging\build_windows.bat" %*
exit /b %errorlevel%
