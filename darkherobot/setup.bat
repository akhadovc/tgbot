@echo off
chcp 65001 >nul
cd /d "%~dp0"
set LOG=setup_log.txt
echo [%date% %time%] Diddy 2.0 setup> %LOG%

call :findpy
if defined PYEXE goto havepy
echo PYTHON_NOT_FOUND - installing Python 3.14 with winget>> %LOG%
winget install -e --id Python.Python.3.14 --scope user --silent --accept-package-agreements --accept-source-agreements >> %LOG% 2>&1
echo WINGET_EXIT=%errorlevel%>> %LOG%
call :findpy
if not defined PYEXE (
  echo PYTHON_STILL_NOT_FOUND>> %LOG%
  goto end
)

:havepy
echo PYEXE=%PYEXE%>> %LOG%
"%PYEXE%" --version >> %LOG% 2>&1
if not exist venv\Scripts\python.exe "%PYEXE%" -m venv venv >> %LOG% 2>&1
echo VENV_EXIT=%errorlevel%>> %LOG%
venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.txt >> %LOG% 2>&1
echo PIP_EXIT=%errorlevel%>> %LOG%
venv\Scripts\python.exe -c "import telegram, openai, dotenv; print('LIBS', telegram.__version__, openai.__version__)" >> %LOG% 2>&1
venv\Scripts\python.exe -m unittest discover -s tests >> %LOG% 2>&1
echo TESTS_EXIT=%errorlevel%>> %LOG%

:end
echo DONE>> %LOG%
echo.
echo Tayyor! Natija: setup_log.txt
timeout /t 5 >nul
exit /b

:findpy
set PYEXE=
for %%P in ("%LOCALAPPDATA%\Programs\Python\Python314\python.exe" "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" "%ProgramFiles%\Python314\python.exe") do (
  if not defined PYEXE if exist %%P set PYEXE=%%~P
)
if defined PYEXE exit /b
for /f "delims=" %%P in ('where python 2^>nul') do (
  if not defined PYEXE echo %%P | find /i "WindowsApps" >nul || set PYEXE=%%P
)
exit /b
