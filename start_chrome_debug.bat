@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "CHROME_EXE=C:\Users\Administrator\AppData\Local\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" set "CHROME_EXE=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" set "CHROME_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" set "CHROME_EXE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"

if not exist "%CHROME_EXE%" goto :missing

netstat -ano | findstr /R /C:":9222 .*LISTENING" >nul
if not errorlevel 1 goto :already_debug

echo Starting Chrome in remote debugging mode...
echo Profile: %~dp0ChromeRecorderProfile
start "" "%CHROME_EXE%" --remote-debugging-port=9222 --user-data-dir="%~dp0ChromeRecorderProfile" --no-first-run

echo.
echo Chrome should now be open on port 9222.
echo Log in to Migu and Feisu in this Chrome window, then test the recorder.
pause
exit /b 0

:already_debug
echo Chrome remote debugging is already listening on port 9222.
echo Use the existing debug Chrome window.
pause
exit /b 0

:missing
echo Chrome executable was not found.
echo Expected locations include:
echo %ProgramFiles%\Google\Chrome\Application\chrome.exe
echo %LocalAppData%\Google\Chrome\Application\chrome.exe
pause
exit /b 1
